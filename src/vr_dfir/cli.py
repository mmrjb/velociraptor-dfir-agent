"""``vr-dfir`` command-line interface.

The CLI uses the same services, evidence plans, and run store as the UI. stdout carries
the result (a readable table/summary, or one JSON document with ``--json``); progress,
warnings, and errors go to stderr. Only existing evidence is read from Velociraptor.

Exit codes:

* 0 - success
* 1 - failure (an error, a blocking plan problem, or a failed investigation)
* 2 - usage error (invalid arguments)
* 3 - incomplete work (partial evidence or analysis; resumable)
* 4 - configuration problem (missing/invalid configuration, ``doctor`` errors)
* 5 - refused (allowlist/policy block, missing confirmation, or the reviewed plan changed)
* 130 - interrupted (Ctrl+C); local state is saved and can be resumed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

from vr_dfir import ids
from vr_dfir.config import load_environment, load_settings
from vr_dfir.errors import DfirError, NotFoundError, PolicyError, ValidationError
from vr_dfir.version import __version__

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_PARTIAL, EXIT_CONFIG, EXIT_DENIED = 0, 1, 2, 3, 4, 5
EXIT_INTERRUPTED = 130
_REFUSED_CODES = {"policy_refused", "stale_plan", "confirmation_required"}




class Output:
    def __init__(self, json_mode: bool, quiet: bool = False):
        self.json_mode = json_mode
        self.quiet = quiet

    def progress(self, kind: str, data: dict) -> None:
        if self.quiet:
            return
        detail = ", ".join(f"{k}={v}" for k, v in data.items() if not isinstance(v, (dict, list)))
        print(f"[{kind}] {detail}", file=sys.stderr, flush=True)

    def note(self, text: str) -> None:
        if not self.quiet:
            print(text, file=sys.stderr, flush=True)

    def result(self, data, text: str | None = None) -> None:
        if self.json_mode or text is None:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        else:
            print(text)

    def error(self, exc: DfirError) -> None:
        if self.json_mode:
            print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2))
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.next_action:
            print(f"Next: {exc.next_action}", file=sys.stderr)
        details = exc.details if isinstance(exc.details, dict) else {}
        for issue in details.get("issues") or []:
            print(f"  - {issue['location']}: {issue['message']}", file=sys.stderr)
        for item in (details.get("errors") or details.get("problems") or [])[:10]:
            print(f"  - {item}", file=sys.stderr)
        for cand in (details.get("candidates") or [])[:10]:
            print(f"  - candidate: {cand}", file=sys.stderr)


def _cell(value, width: int) -> str:
    text = "-" if value is None or value == "" else str(value)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: max(1, width - 1)] + "~"


def table(headers: list[str], rows: list[list], *, max_width: int = 48) -> str:
    """Fixed-width text table; long cells are truncated (use --json for full values)."""
    cells = [[_cell(v, max_width) for v in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip(),
             "  ".join("-" * w for w in widths)]
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in cells]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- context


class Context:
    def __init__(self, args):
        if getattr(args, "env_file", None):
            os.environ["VELOCIRAPTOR_ENV_FILE"] = args.env_file
        load_environment(force=True)
        if getattr(args, "state_dir", None):
            os.environ["VR_DFIR_STATE_DIR"] = args.state_dir
        if getattr(args, "api_timeout", None):
            os.environ["VR_DFIR_GRPC_TIMEOUT_SECONDS"] = str(args.api_timeout)
        self.settings = load_settings()
        self.args = args
        self._runtime = None

    def runtime(self):
        from vr_dfir.services.runtime import live_runtime
        if self._runtime is None:
            self._runtime = live_runtime(self.settings)
        return self._runtime

    def vr(self):
        """The Velociraptor service; a clear configuration error when it is not set up."""
        from vr_dfir.errors import ConfigurationError
        rt = self.runtime()
        if not rt.live_configured:
            raise ConfigurationError(
                "No Velociraptor API configuration is set.",
                next_action="Set VELOCIRAPTOR_API_CONFIG (or pass --env-file) and run "
                            "'vr-dfir doctor --probe'. Saved investigations and evidence "
                            "bundles remain available without it.")
        return rt.service

    def runs_root(self, override: str | None = None) -> Path:
        if override:
            return Path(override)
        return self.settings.state_dir / "investigations"

    def service(self, runs_dir: str | None = None):
        from vr_dfir.profiles.store import ProfileStore
        from vr_dfir.services.investigations import InvestigationService
        from vr_dfir.services.runstore import RunStore
        return InvestigationService(self.runtime(), RunStore(self.runs_root(runs_dir)),
                                    ProfileStore(self.settings.state_dir, self.settings.profile_dirs))

    def investigation(self, inv_id: str):
        from vr_dfir.services.runstore import RunStore
        return RunStore(self.runs_root(getattr(self.args, "runs_dir", None))).get(inv_id)


def _exit_for_outcome(outcome: str | None) -> int:
    if outcome in ("complete", "evidence_only"):
        return EXIT_OK
    if outcome in ("partial", "interrupted", "running"):
        return EXIT_PARTIAL
    return EXIT_FAIL


def _inv_text(meta: dict, run_dir: Path | None = None) -> str:
    provider = meta.get("provider") or {}
    lines = [
        f"Investigation {meta['investigation_id']} ({meta['mode']})"
        + (" [SYNTHETIC TEST DATA]" if meta.get("synthetic") else ""),
        f"  Title:       {meta.get('title')}",
        f"  Outcome:     {meta.get('outcome')} (phase {meta.get('phase')})",
        f"  Collection:  {meta.get('collection_status')}",
        f"  Retrieval:   {meta.get('retrieval_status')}",
        f"  Analysis:    {meta.get('analysis_status')}"
        + (f" ({provider.get('name')}{' FAKE MODEL' if provider.get('fake') else ''})"
           if provider else ""),
    ]
    counts = meta.get("counts") or {}
    if counts:
        lines.append(f"  Sources:     {counts.get('sources_complete', 0)}/"
                     f"{counts.get('sources_total', 0)} read completely, "
                     f"{counts.get('rows_persisted', 0)} rows saved")
        lines.append(f"  Findings:    {counts.get('findings', 0)} accepted "
                     f"({counts.get('model_findings', 0)} model, "
                     f"{counts.get('deterministic_findings', 0)} deterministic), "
                     f"{counts.get('rejected_findings', 0)} rejected")
    if meta.get("flow_ids"):
        lines.append(f"  Flow IDs:    {', '.join(meta['flow_ids'][:10])}")
    for err in (meta.get("errors") or [])[-5:]:
        lines.append(f"  ! {err}")
    if run_dir:
        lines.append(f"  Run folder:  {run_dir}")
        lines.append(f"  Report:      {run_dir / 'report' / 'report.html'}")
    inv_id = meta["investigation_id"]
    if meta.get("outcome") in ("partial", "interrupted"):
        lines.append(f"  Resume:      vr-dfir resume --id {inv_id}")
    if meta.get("outcome") != "running" and (counts.get("rows_persisted") or 0) > 0:
        lines.append(f"  Findings:    vr-dfir findings list {inv_id}")
    if meta.get("collection_status") in ("waiting", "wait_timeout", "interrupted"):
        lines.append("  Note: stopping this program does not cancel the endpoint collection.")
    return "\n".join(lines)


def _plan_text(plan: dict) -> str:
    lines = [f"Plan ({plan['mode']}, {'offline' if plan['offline'] else 'live metadata'})"
             + (" [SYNTHETIC TEST DATA]" if plan.get("synthetic") else "")]
    t = plan["target"]
    lines.append(f"  Org: {plan['org_id']}  Target: {t.get('client_id') or t.get('hunt_id') or '-'} "
                 f"{t.get('hostname') or ''} {('(' + t['platform'] + ')') if t.get('platform') else ''}")
    if plan.get("profile"):
        lines.append(f"  Profile: {plan['profile'].get('name')} ({plan['profile'].get('id')})")
    for c in plan["collections"]:
        lines.append(f"  - {c['artifact']} [{', '.join(c['collection_ids'])}] "
                     f"{'WILL LAUNCH' if c['will_launch'] else 'no launch'}; metadata: "
                     f"{c['metadata_status']}; allowlisted: {c['allowlisted']}")
        for s in c["sources"]:
            lines.append(f"      source {s['name'] or '(default)'}: {s['status']}"
                         + (" (counts only)" if s.get("counts_only") else ""))
        if c.get("parameters"):
            lines.append(f"      parameters: {json.dumps(c['parameters'], ensure_ascii=False)}")
        if c.get("limits"):
            lim = c["limits"]
            lines.append(f"      limits: server timeout {lim.get('collection_timeout_seconds')}s, "
                         f"wait {lim.get('wait_timeout_seconds')}s, page {lim.get('page_size')}, "
                         f"max rows/source {lim.get('max_rows_per_source')}")
        for b in c.get("behavior") or []:
            lines.append(f"      behaviour: {b}")
        for e in c.get("errors") or []:
            lines.append(f"      ERROR: {e}")
        for w in c.get("warnings") or []:
            lines.append(f"      warning: {w}")
    for e in plan["errors"]:
        lines.append(f"  BLOCKING: {e}")
    for w in plan["warnings"]:
        lines.append(f"  warning: {w}")
    lines.append(f"  New endpoint collections: {plan['launches']}")
    lines.append(f"  Plan fingerprint: {plan['fingerprint'][:16]}")
    lines.append("  Result: " + ("ready" if plan["ok"] else
                                 "REFUSED by collection policy" if plan.get("policy_denied")
                                 else "NOT READY"))
    return "\n".join(lines)


def _blocked_plan(out: Output, plan) -> int:
    data = plan.to_dict()
    out.result({"ok": False, "plan": data}, _plan_text(data))
    return EXIT_DENIED if plan.policy_denied else EXIT_FAIL


# ---------------------------------------------------------------------------- setup


def cmd_doctor(ctx: Context, out: Output) -> int:
    from vr_dfir.services.doctor import run_doctor
    report = run_doctor(ctx.settings, probe=ctx.args.probe, probe_model=ctx.args.probe_model)
    lines = ["vr-dfir doctor (" + ("with bounded probes" if ctx.args.probe or ctx.args.probe_model
                                   else "offline checks only") + ")"]
    for check in report["checks"]:
        lines.append(f"  [{check['status'].upper():5}] {check['name']}: {check['detail']}")
    lines.append(f"Result: {report['result']}")
    out.result(report, "\n".join(lines))
    return EXIT_OK if report["result"] != "error" else EXIT_CONFIG


def cmd_preflight(ctx: Context, out: Output) -> int:
    from vr_dfir.services.doctor import run_preflight
    report = run_preflight(ctx.settings, require_ui=not ctx.args.no_ui)
    lines = ["vr-dfir preflight (destination checks; no network access)"]
    for check in report["checks"]:
        lines.append(f"  [{check['status'].upper():5}] {check['name']}: {check['detail']}")
    lines.append(f"Result: {report['result']}")
    out.result(report, "\n".join(lines))
    return EXIT_OK if report["result"] != "error" else EXIT_CONFIG


# ---------------------------------------------------------------------------- discovery


def cmd_orgs_list(ctx: Context, out: Output) -> int:
    orgs = ctx.vr().list_orgs()
    out.result({"orgs": orgs}, table(["ORG ID", "NAME"], [[o["OrgId"], o["Name"]] for o in orgs])
               if orgs else "No orgs are visible to this API account.")
    return EXIT_OK


def cmd_clients_search(ctx: Context, out: Output) -> int:
    svc = ctx.vr()
    limit = max(1, min(ctx.args.limit, 100))
    found = svc.search_clients(ctx.args.text or "", ctx.args.org_id, limit=limit)
    org = svc.resolve_org(ctx.args.org_id)
    data = {"org_id": org, "clients": [c.to_dict() for c in found]}
    if not found:
        text = f"No clients in org {org} match {ctx.args.text or '(any)'}."
    else:
        text = f"Org {org}: {len(found)} client(s)\n" + table(
            ["CLIENT ID", "HOSTNAME", "PLATFORM", "LAST SEEN", "LABELS"],
            [[c.client_id, c.fqdn or c.hostname, c.platform, c.last_seen,
              ",".join(c.labels)] for c in found])
    out.result(data, text)
    return EXIT_OK






def cmd_flows_list(ctx: Context, out: Output) -> int:
    svc = ctx.vr()
    cid = ids.client_id(ctx.args.client_id)
    flows = svc.recent_flows(cid, ctx.args.org_id, limit=max(1, min(ctx.args.limit, 100)))
    data = {"org_id": svc.resolve_org(ctx.args.org_id), "client_id": cid, "flows": flows}
    text = (f"Recent flows for {cid} (org {data['org_id']}):\n" + table(
        ["FLOW ID", "STATE", "CREATED", "ROWS", "ARTIFACTS"],
        [[f["flow_id"], f["state"], f["created"], f["total_collected_rows"],
          ", ".join(f["artifacts"])] for f in flows], max_width=60)
        + f"\nAnalyze one: vr-dfir analyze flow --client-id {cid} --flow-id <FLOW ID>"
        if flows else f"No flows found for {cid} in org {data['org_id']}.")
    out.result(data, text)
    return EXIT_OK


def cmd_flow_status(ctx: Context, out: Output) -> int:
    from vr_dfir.services.investigations import flow_status_snapshot
    data = flow_status_snapshot(ctx.vr(), ctx.args.org_id, ids.client_id(ctx.args.client_id),
                                ids.flow_id(ctx.args.flow_id))
    out.result(data, f"{data['flow_id']}: {data['state_label']} (raw {data['raw_state']}, "
                     f"shape {data['response_shape']}) {data['diagnostic']}")
    return EXIT_OK


def cmd_hunts_list(ctx: Context, out: Output) -> int:
    svc = ctx.vr()
    hunts = svc.list_hunts(ctx.args.org_id, limit=max(1, min(ctx.args.limit, 100)))
    org = svc.resolve_org(ctx.args.org_id)
    text = (f"Hunts in org {org}:\n" + table(
        ["HUNT ID", "STATE", "CREATED", "ARTIFACTS", "DESCRIPTION"],
        [[h["hunt_id"], h["state"], h["created"], ", ".join(h["artifacts"]), h["description"]]
         for h in hunts]) if hunts else f"No hunts found in org {org}.")
    out.result({"org_id": org, "hunts": hunts}, text)
    return EXIT_OK


def cmd_hunts_show(ctx: Context, out: Output) -> int:
    info = ctx.vr().hunt_info(ids.hunt_id(ctx.args.hunt_id), ctx.args.org_id)
    st = info["stats"]
    lines = [f"Hunt {info['hunt_id']} (org {info['org_id']})",
             f"  State:       {info['state']}",
             f"  Created:     {info['created']}  Started: {info['started']}",
             f"  Creator:     {info['creator']}",
             f"  Artifacts:   {', '.join(info['artifacts'])}",
             f"  Clients:     {st['total_clients_scheduled']} scheduled, "
             f"{st['total_finished_clients']} finished, {st['total_clients_with_results']} with "
             f"results, {st['total_clients_with_errors']} with errors",
             f"  Description: {info['description']}",
             f"Analyze it: vr-dfir analyze hunt --hunt-id {info['hunt_id']} --no-llm"]
    out.result(info, "\n".join(lines))
    return EXIT_OK


# ---------------------------------------------------------------------------- profiles


















# ---------------------------------------------------------------------------- investigations


def _run_and_report(ctx: Context, out: Output, svc, inv, *, resume: bool = False) -> int:
    stop = threading.Event()
    interrupted = False
    try:
        if resume:
            meta = svc.resume(inv.id, stop_event=stop, progress=out.progress,
                              org_id=ctx.args.org_id, client_id=ctx.args.client_id)
        else:
            meta = svc.execute(inv.id, stop_event=stop, progress=out.progress)
    except KeyboardInterrupt:
        stop.set()
        interrupted = True
        meta = inv.reload()
        print(f"Interrupted. Local state was saved. Resume with 'vr-dfir resume --id {inv.id}'.", file=sys.stderr)
    out.result({"ok": meta.get("outcome") in ("complete", "evidence_only"), "investigation": meta,
                "run_dir": str(inv.path)}, _inv_text(meta, inv.path))
    return EXIT_INTERRUPTED if interrupted else _exit_for_outcome(meta.get("outcome"))






def cmd_analyze_flow(ctx: Context, out: Output) -> int:
    a = ctx.args
    ctx.vr()
    svc = ctx.service(a.output)
    plan = svc.plan_existing_flow(org_id=a.org_id, client_id=a.client_id, flow_id=a.flow_id,
                                  profile_ref=a.profile, selected_sources=a.source)
    if not plan.ok:
        return _blocked_plan(out, plan)
    inv = svc.create(plan, no_llm=a.no_llm, title=a.title)
    return _run_and_report(ctx, out, svc, inv)


def cmd_analyze_hunt(ctx: Context, out: Output) -> int:
    a = ctx.args
    ctx.vr()
    svc = ctx.service(a.output)
    plan = svc.plan_existing_hunt(org_id=a.org_id, hunt_id=a.hunt_id, client_filter=a.client_id,
                                  artifact_filter=a.artifact, selected_sources=a.source,
                                  profile_ref=a.profile, max_clients=a.max_clients)
    if not plan.ok:
        return _blocked_plan(out, plan)
    inv = svc.create(plan, no_llm=a.no_llm, title=a.title)
    return _run_and_report(ctx, out, svc, inv)


def cmd_analyze_bundle(ctx: Context, out: Output) -> int:
    a = ctx.args
    svc = ctx.service(a.output)
    inv = svc.analyze_bundle(Path(a.input), no_llm=a.no_llm, title=a.title, profile_ref=a.profile)
    return _run_and_report(ctx, out, svc, inv)


def _target(ctx: Context):
    """(service, investigation) from --id or --checkpoint."""
    a = ctx.args
    if a.id:
        svc = ctx.service(a.runs_dir)
        return svc, svc.store.get(a.id)
    from vr_dfir.services.checkpoints import Checkpoint
    cp = Checkpoint.load(Path(a.checkpoint))
    svc = ctx.service(str(cp.path.parent.parent))
    return svc, svc.store.get(cp.data["investigation_id"])


def cmd_resume(ctx: Context, out: Output) -> int:
    svc, inv = _target(ctx)
    if inv.reload().get("mode") in ("existing_flow", "existing_hunt", "new_collection"):
        ctx.vr()
    return _run_and_report(ctx, out, svc, inv, resume=True)


def cmd_retry_analysis(ctx: Context, out: Output) -> int:
    svc, inv = _target(ctx)
    try:
        meta = svc.retry_analysis(inv.id, no_llm=True if ctx.args.no_llm else None,
                                  progress=out.progress)
    except KeyboardInterrupt:
        print("Interrupted. Saved evidence is unchanged; retry the analysis later.",
              file=sys.stderr)
        return EXIT_INTERRUPTED
    out.result({"investigation": meta, "run_dir": str(inv.path)}, _inv_text(meta, inv.path))
    return _exit_for_outcome(meta.get("outcome"))


def cmd_investigations_list(ctx: Context, out: Output) -> int:
    from vr_dfir.services.runstore import RunStore
    items = RunStore(ctx.runs_root(ctx.args.runs_dir)).list(
        search=ctx.args.search, status=ctx.args.status, limit=max(1, min(ctx.args.limit, 1000)))
    items.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    rows = []
    for s in items:
        scope = s.get("scope") or {}
        counts = s.get("counts") or {}
        rows.append([s["investigation_id"], s.get("outcome"), s.get("mode"),
                     scope.get("hostname") or scope.get("client_id") or scope.get("hunt_id"),
                     (s.get("profile") or {}).get("id"), counts.get("findings"),
                     "synthetic" if s.get("synthetic") else "", s.get("title")])
    out.result({"investigations": items},
               table(["ID", "OUTCOME", "MODE", "TARGET", "PROFILE", "FINDINGS", "DATA", "TITLE"],
                     rows, max_width=40) if items else "No investigations found.")
    return EXIT_OK


def cmd_investigations_show(ctx: Context, out: Output) -> int:
    inv = ctx.investigation(ctx.args.id)
    meta = inv.reload()
    data = {"investigation": meta, "run_dir": str(inv.path)}
    text = _inv_text(meta, inv.path)
    if ctx.args.events:
        n = max(1, min(ctx.args.events, 500))
        events = inv.events(after=max(0, int(meta.get("event_seq") or 0) - n), limit=n)
        data["events"] = events
        text += "\n  Recent events:\n" + "\n".join(
            f"    {e.get('time')} {e.get('kind')}" for e in events)
    out.result(data, text)
    return _exit_for_outcome(meta.get("outcome"))


def _report_data(inv) -> dict:
    from vr_dfir.jsonio import read_json
    path = inv.report_dir / "report.json"
    if not path.is_file():
        raise NotFoundError(
            f"Investigation {inv.id} has no report yet (phase {inv.reload().get('phase')}).",
            next_action=f"Check progress with 'vr-dfir investigations show {inv.id}'.")
    return read_json(path)


def _status_line(report: dict) -> str:
    st = report.get("status") or {}
    return (f"Outcome {st.get('outcome')}; retrieval {st.get('retrieval')}; analysis "
            f"{st.get('analysis')}"
            + (" [SYNTHETIC TEST DATA]" if report.get("synthetic") else ""))


def cmd_findings_list(ctx: Context, out: Output) -> int:
    from vr_dfir.services.reviews import load_reviews, review_of
    inv = ctx.investigation(ctx.args.id)
    report = _report_data(inv)
    reviews = load_reviews(inv.path)
    findings = [{**f, "review": review_of(reviews, f.get("finding_id"))}
                for f in report["findings"]]
    if ctx.args.severity:
        findings = [f for f in findings if f.get("severity") == ctx.args.severity]
    if ctx.args.origin:
        findings = [f for f in findings if ctx.args.origin in str(f.get("origin"))]
    if ctx.args.review:
        findings = [f for f in findings if f["review"]["state"] == ctx.args.review]
    data = {"investigation_id": inv.id, "status": report.get("status"),
            "synthetic": report.get("synthetic"), "findings": findings,
            "rejected_findings": len(report.get("rejected_findings") or []),
            "coverage_gaps": (report.get("coverage") or {}).get("gaps", [])}
    lines = [f"{inv.id}: {len(findings)} finding(s). {_status_line(report)}"]
    if findings:
        lines.append(table(["FINDING", "SEVERITY", "REVIEW STATUS", "CONFIDENCE", "ORIGIN",
                            "EVIDENCE", "TITLE"],
                           [[f.get("finding_id"), f.get("severity"), f["review"]["label"],
                             f.get("confidence"), f.get("origin"),
                             len(f.get("evidence_ids") or []), f.get("title")]
                            for f in findings], max_width=60))
        lines.append(f"Details: vr-dfir findings show {inv.id} <FINDING>")
    if data["rejected_findings"]:
        lines.append(f"{data['rejected_findings']} model proposal(s) were rejected by validation "
                     "(see the JSON report).")
    for gap in data["coverage_gaps"][:10]:
        lines.append(f"  gap: {gap}")
    lines.append("Findings are review aids; the absence of a finding is not evidence that the "
                 "target is clean.")
    out.result(data, "\n".join(lines))
    return _exit_for_outcome((report.get("status") or {}).get("outcome"))


def cmd_findings_show(ctx: Context, out: Output) -> int:
    inv = ctx.investigation(ctx.args.id)
    report = _report_data(inv)
    match = [f for f in report["findings"] if f.get("finding_id") == ctx.args.finding_id]
    if not match:
        raise NotFoundError(f"Finding {ctx.args.finding_id} is not in investigation {inv.id}.")
    from vr_dfir.services.reviews import load_reviews, review_of
    f = {**match[0], "review": review_of(load_reviews(inv.path), ctx.args.finding_id)}
    review = f["review"]
    lines = [f"{f['finding_id']}: {f.get('title')}",
             f"  Severity {f.get('severity')}, confidence {f.get('confidence')}, origin "
             f"{f.get('origin')}" + (" [SYNTHETIC TEST DATA]" if report.get("synthetic") else ""),
             f"  Review status: {review['label']}"
             + (f" ({review['by']}, {review['updated_at']})" if review.get("updated_at") else "")
             + (f" — {review['note']}" if review.get("note") else "")]
    for key, label in (("observation", "Observation"), ("interpretation", "Interpretation"),
                       ("confidence_rationale", "Confidence")):
        if f.get(key):
            lines.append(f"  {label}: {f[key]}")
    for key, label in (("benign_explanations", "Benign explanation"),
                       ("missing_context", "Missing context"), ("next_steps", "Next step")):
        for item in f.get(key) or []:
            lines.append(f"  {label}: {item}")
    lines.append("  Evidence:")
    for ref in f.get("evidence_refs") or []:
        if ref.get("unresolved"):
            lines.append(f"    {ref['evidence_id']} (UNRESOLVED)")
        else:
            lines.append(f"    {ref['evidence_id']}  {ref.get('hostname') or ref.get('client_id')} "
                         f"{ref.get('flow_id')} {ref.get('artifact')}/{ref.get('source') or ''} "
                         f"row {ref.get('row_index')}")
    lines.append(f"Row: vr-dfir evidence show {inv.id} <EVIDENCE ID>")
    out.result({"investigation_id": inv.id, "finding": f}, "\n".join(lines))
    return EXIT_OK


# ---------------------------------------------------------------------------- evidence


def _bundle(inv):
    from vr_dfir.evidence.bundle import EvidenceBundle
    if not (inv.evidence_dir / "manifest.json").is_file():
        raise NotFoundError(f"Investigation {inv.id} has no saved evidence yet.")
    return EvidenceBundle.open(inv.evidence_dir)


def _resolve_source(bundle, ref: str) -> str:
    """A source key from a row number (as listed by 'evidence sources'), an exact source key,
    or an ARTIFACT/SOURCE name that is unique in the investigation."""
    sources = bundle.sources()
    ref = (ref or "").strip()
    if ref.isdigit():
        n = int(ref)
        if not 1 <= n <= len(sources):
            raise ValidationError(f"Source #{n} does not exist (1-{len(sources)}).")
        return sources[n - 1].source_key
    if any(e.source_key == ref for e in sources):
        return ref
    matches = [i for i, e in enumerate(sources, 1) if e.full_name == ref]
    if len(matches) == 1:
        return sources[matches[0] - 1].source_key
    if matches:
        raise ValidationError(f"{ref} exists for several clients or flows; use its number "
                              f"({', '.join(f'#{i}' for i in matches[:20])}).")
    raise NotFoundError(f"Source {ref!r} is not in this investigation.",
                        next_action="List the sources with 'vr-dfir evidence sources <ID>'.")


def cmd_evidence_sources(ctx: Context, out: Output) -> int:
    inv = ctx.investigation(ctx.args.id)
    summary = _bundle(inv).summary()
    rows = [[i, s["full_name"], s["hostname"] or s["client_id"], s["flow_id"],
             s["coverage"]["result_status"], s["coverage"]["read_status"],
             s["coverage"]["persisted_rows"]] for i, s in enumerate(summary["sources"], 1)]
    text = (f"{inv.id}: {len(rows)} source(s)"
            + (" [SYNTHETIC TEST DATA]" if summary.get("synthetic") else "") + "\n"
            + table(["#", "ARTIFACT/SOURCE", "HOST", "FLOW", "RESULT", "READ", "ROWS"], rows,
                    max_width=60)
            + f"\nRows: vr-dfir evidence rows {inv.id} --source <# or ARTIFACT/SOURCE>")
    out.result(summary, text)
    return EXIT_OK


def cmd_evidence_rows(ctx: Context, out: Output) -> int:
    inv = ctx.investigation(ctx.args.id)
    a = ctx.args
    bundle = _bundle(inv)
    key = _resolve_source(bundle, a.source)
    page = bundle.read_page(key, offset=a.offset, limit=a.limit, search=a.search)
    page["source_key"] = key
    rows = [[r["evidence_id"], r["row_index"], json.dumps(r["row"], ensure_ascii=False)]
            for r in page["records"]]
    end = a.offset + len(rows)
    name = bundle.source(key).full_name
    text = (f"{name}: rows {a.offset + 1 if rows else 0}-{end} of {page['total_matched']}"
            + (f" matching {a.search!r}" if a.search else "") + "\n"
            + (table(["EVIDENCE ID", "ROW", "DATA"], rows, max_width=90) if rows else "No rows."))
    if page["total_matched"] > end:
        text += f"\nNext page: --offset {end}"
    out.result(page, text)
    return EXIT_OK


def cmd_evidence_show(ctx: Context, out: Output) -> int:
    inv = ctx.investigation(ctx.args.id)
    rec = _bundle(inv).get_record(ctx.args.evidence_id)
    keys = ("evidence_id", "org_id", "client_id", "hostname", "flow_id", "hunt_id", "artifact",
            "source", "row_index", "collected_at", "retrieved_at", "event_time", "raw_sha256",
            "file", "line")
    lines = [f"  {k:13} {rec.get(k)}" for k in keys if rec.get(k) is not None]
    if rec.get("synthetic"):
        lines.insert(0, "  [SYNTHETIC TEST DATA]")
    lines.append("  row:")
    lines += ["    " + ln for ln in json.dumps(rec["row"], ensure_ascii=False,
                                               indent=2).splitlines()]
    out.result(rec, "\n".join(lines))
    return EXIT_OK


def cmd_evidence_verify(ctx: Context, out: Output) -> int:
    from vr_dfir.evidence.bundle import EvidenceBundle
    bundle = EvidenceBundle.open(Path(ctx.args.input))
    problems = bundle.verify()
    out.result({"bundle_id": bundle.bundle_id, "problems": problems},
               "OK" if not problems else "\n".join(problems))
    return EXIT_OK if not problems else EXIT_FAIL


def cmd_evidence_migrate(ctx: Context, out: Output) -> int:
    from vr_dfir.evidence.bundle import migrate_one_based
    out.result(migrate_one_based(Path(ctx.args.input)))
    return EXIT_OK


# ---------------------------------------------------------------------------- reports


def cmd_report(ctx: Context, out: Output) -> int:
    from vr_dfir.services.runstore import Investigation
    a = ctx.args
    if a.id:
        svc = ctx.service(a.runs_dir)
        inv = svc.store.get(a.id)
    else:
        path = Path(a.input)
        if path.is_file():
            path = path.parent
        if not (path / "investigation.json").is_file():
            raise NotFoundError(f"{path} is not an investigation run folder.")
        inv = Investigation(path)
        svc = ctx.service(str(path.parent))
    report = svc.rebuild_report(inv.id)
    target = "-" if a.stdout else a.output
    if target:
        if a.format == "all":
            raise ValidationError("--output and --stdout need a single --format (md, json, html).")
        content = (inv.report_dir / f"report.{a.format}").read_text(encoding="utf-8")
        if target == "-":
            sys.stdout.write(content if content.endswith("\n") else content + "\n")
        else:
            Path(target).write_text(content, encoding="utf-8")
            out.note(f"Wrote {a.format} report to {target}")
    formats = ["json", "md", "html"] if a.format == "all" else [a.format]
    files = {f: str(inv.report_dir / f"report.{f}") for f in formats}
    status = report.get("status") or {}
    if target != "-":
        out.result({"investigation_id": inv.id, "report_files": files, "status": status},
                   "\n".join(f"{k}: {v}" for k, v in files.items())
                   + f"\nStatus: {_status_line(report)}")
    return _exit_for_outcome(status.get("outcome"))


def cmd_ui(ctx: Context, out: Output) -> int:
    from vr_dfir.web.server import serve
    host = ctx.args.host or ctx.settings.ui_host
    port = ctx.args.port or ctx.settings.ui_port
    return serve(ctx.settings, host=host, port=port, open_browser=ctx.args.open,
                 opener=webbrowser.open)


# ---------------------------------------------------------------------------- parser

class _Formatter(argparse.RawDescriptionHelpFormatter):
    pass


EXAMPLES = """Examples:
  vr-dfir doctor --probe --probe-model
  vr-dfir analyze flow --client-id C.EXAMPLE01 --flow-id F.EXAMPLE01
  vr-dfir analyze hunt --hunt-id H.EXAMPLE01 --no-llm
  vr-dfir analyze bundle --input ./evidence --no-llm
  vr-dfir ui --port 8766
"""


def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("global options")
    g.add_argument("--json", action="store_true", help="one JSON document on stdout")
    g.add_argument("--env-file", help="dotenv file (sets VELOCIRAPTOR_ENV_FILE)")
    g.add_argument("--state-dir", help="state directory (overrides VR_DFIR_STATE_DIR)")
    g.add_argument("--api-timeout", type=_bounded(5, 3600), metavar="SECONDS",
                   help="Velociraptor API timeout per query, 5-3600 "
                        "(overrides VR_DFIR_GRPC_TIMEOUT_SECONDS)")
    g.add_argument("-q", "--quiet", action="store_true", help="no progress on stderr")


def _bounded(lo: int, hi: int):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
        if not lo <= value <= hi:
            raise argparse.ArgumentTypeError(f"must be between {lo} and {hi}")
        return value
    return parse


def _runs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--runs-dir", help="investigations directory (default: <state>/investigations)")


def _add(sub, name: str, help_text: str, func, examples: str = "", **kw):
    p = sub.add_parser(name, help=help_text, description=help_text[:1].upper() + help_text[1:] + ".",
                       epilog=("examples:\n" + examples) if examples else None,
                       formatter_class=_Formatter, **kw)
    _common(p)
    p.set_defaults(func=func)
    return p


def _group(sub, name: str, help_text: str):
    return sub.add_parser(name, help=help_text, description=help_text[:1].upper() + help_text[1:] + ".",
                          formatter_class=_Formatter).add_subparsers(
        dest="sub", required=True, metavar="ACTION")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vr-dfir", formatter_class=_Formatter, epilog=EXAMPLES + "\nExit codes: 0 success; 1 failed; 2 usage; 3 partial; 4 configuration; 5 refused; 130 interrupted.",
        description="Velociraptor DFIR Agent: find endpoints and existing evidence, run "
                    "read-only investigations, review citation-checked findings, and export "
                    "reports. The same services and policies back the local UI.")
    parser.add_argument("--version", action="version", version=f"vr-dfir {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = _add(sub, "doctor", "check configuration (offline unless a probe is requested)",
             cmd_doctor, "  vr-dfir doctor\n  vr-dfir doctor --probe --probe-model")
    p.add_argument("--probe", action="store_true",
                   help="bounded read-only Velociraptor API probe (no collections)")
    p.add_argument("--probe-model", action="store_true",
                   help="bounded model server probe (lists models; never downloads)")
    p = _add(sub, "preflight", "destination-server checks (platform, Python, paths, "
                               "dependencies, configuration)", cmd_preflight)
    p.add_argument("--no-ui", action="store_true", help="do not require the UI dependencies")

    # discovery
    org = _group(sub, "orgs", "Velociraptor orgs")
    _add(org, "list", "list orgs visible to the API account", cmd_orgs_list)

    cl = _group(sub, "clients", "endpoint discovery")
    p = _add(cl, "search", "search endpoints by hostname, FQDN, or client ID", cmd_clients_search,
             "  vr-dfir clients search web01\n  vr-dfir clients search --org-id O123 --limit 50")
    p.add_argument("text", nargs="?", default="", help="search text (empty lists recent clients)")
    p.add_argument("--org-id", help="org (default: VELOCIRAPTOR_ORG_ID or root)")
    p.add_argument("--limit", type=int, default=25, help="1-100 (default 25)")

    fl = _group(sub, "flows", "existing collections (flows) on an endpoint")
    p = _add(fl, "list", "recent flows for one client", cmd_flows_list,
             "  vr-dfir flows list --client-id C.1a2b3c4d5e6f7a8b")
    p.add_argument("--client-id", required=True)
    p.add_argument("--org-id")
    p.add_argument("--limit", type=int, default=30, help="1-100 (default 30)")
    p = _add(sub, "flow-status", "normalized flow status (read-only)", cmd_flow_status)
    p.add_argument("--org-id")
    p.add_argument("--client-id", required=True)
    p.add_argument("--flow-id", required=True)

    hu = _group(sub, "hunts", "existing hunts")
    p = _add(hu, "list", "recent hunts in an org", cmd_hunts_list, "  vr-dfir hunts list")
    p.add_argument("--org-id")
    p.add_argument("--limit", type=int, default=30, help="1-100 (default 30)")
    p = _add(hu, "show", "hunt details and client counts", cmd_hunts_show,
             "  vr-dfir hunts show H.CRUU3KIE5D73G")
    p.add_argument("hunt_id")
    p.add_argument("--org-id")

    # Analyze evidence that already exists.
    an = _group(sub, "analyze", "analyze existing evidence (never collects)")
    p = _add(an, "flow", "analyze an existing flow", cmd_analyze_flow,
             "  vr-dfir analyze flow --client-id C.1a2b3c4d5e6f7a8b --flow-id F.CRUU3KIE5D73G")
    p.add_argument("--org-id")
    p.add_argument("--client-id", required=True)
    p.add_argument("--flow-id", required=True)
    p.add_argument("--profile", help="optional analysis profile (analysis settings only)")
    p.add_argument("--source", action="append", help="limit to source (repeatable)")
    p.add_argument("--output")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--title")
    p = _add(an, "hunt", "analyze an existing hunt", cmd_analyze_hunt,
             "  vr-dfir analyze hunt --hunt-id H.CRUU3KIE5D73G --max-clients 50 --no-llm")
    p.add_argument("--org-id")
    p.add_argument("--hunt-id", required=True)
    p.add_argument("--client-id", action="append", help="client filter (repeatable)")
    p.add_argument("--artifact", help="artifact filter")
    p.add_argument("--source", action="append")
    p.add_argument("--max-clients", type=int, default=200)
    p.add_argument("--profile")
    p.add_argument("--output")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--title")
    p = _add(an, "bundle", "analyze a saved evidence bundle; no Velociraptor access",
             cmd_analyze_bundle, "  vr-dfir analyze bundle --input ./evidence --no-llm")
    p.add_argument("--input", required=True)
    p.add_argument("--output")
    p.add_argument("--profile")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--title")

    for name, func, text in (("resume", cmd_resume, "continue from the checkpoint (never "
                                                    "recollects)"),
                             ("retry-analysis", cmd_retry_analysis, "re-run analysis on saved "
                                                                     "evidence")):
        p = _add(sub, name, text, func, f"  vr-dfir {name} --id inv-20260101-120000-abc123")
        which = p.add_mutually_exclusive_group(required=True)
        which.add_argument("--id", help="investigation ID")
        which.add_argument("--checkpoint", help="path to checkpoint.json")
        _runs(p)
        if name == "resume":
            p.add_argument("--org-id", help="scope guard")
            p.add_argument("--client-id", help="scope guard")
        else:
            p.add_argument("--no-llm", action="store_true")

    inv = _group(sub, "investigations", "saved investigations")
    p = _add(inv, "list", "list investigations", cmd_investigations_list,
             "  vr-dfir investigations list --search web01 --status partial")
    p.add_argument("--search", help="text in ID, title, host, client, flow, hunt, or profile")
    p.add_argument("--status", help="outcome or phase (complete, partial, failed, running, ...)")
    p.add_argument("--limit", type=int, default=50)
    _runs(p)
    p = _add(inv, "show", "status, counts, and next actions", cmd_investigations_show)
    p.add_argument("id")
    p.add_argument("--events", type=int, default=0, metavar="N", help="also show the last N events")
    _runs(p)

    fi = _group(sub, "findings", "findings of an investigation")
    p = _add(fi, "list", "accepted findings with status and coverage gaps", cmd_findings_list)
    p.add_argument("id")
    p.add_argument("--severity", choices=["informational", "low", "medium", "high"])
    p.add_argument("--origin", choices=["model", "deterministic"])
    p.add_argument("--review", choices=["unreviewed", "in_review", "needs_follow_up",
                                        "confirmed", "benign"], help="filter by review status")
    _runs(p)
    p = _add(fi, "show", "one finding with its cited evidence", cmd_findings_show)
    p.add_argument("id")
    p.add_argument("finding_id")
    _runs(p)
    from vr_dfir.cli_reviews import add_findings_parsers
    add_findings_parsers(fi, _add, _runs)

    ev = _group(sub, "evidence", "saved evidence")
    p = _add(ev, "sources", "sources and coverage of an investigation", cmd_evidence_sources)
    p.add_argument("id")
    _runs(p)
    p = _add(ev, "rows", "page through saved rows of one source", cmd_evidence_rows,
             "  vr-dfir evidence rows <ID> --source 5 --search sshd --limit 20\n"
             "  vr-dfir evidence rows <ID> --source LTH.SystemBaseline/Processes --json")
    p.add_argument("id")
    p.add_argument("--source", required=True,
                   help="source number or ARTIFACT/SOURCE from 'evidence sources' (or its key)")
    p.add_argument("--offset", type=_bounded(0, 10**9), default=0)
    p.add_argument("--limit", type=_bounded(1, 500), default=50, help="1-500 (default 50)")
    p.add_argument("--search", help="case-insensitive text match over the whole source")
    _runs(p)
    p = _add(ev, "show", "one evidence row with full provenance", cmd_evidence_show)
    p.add_argument("id")
    p.add_argument("evidence_id")
    _runs(p)
    p = _add(ev, "verify", "integrity check of an evidence bundle", cmd_evidence_verify)
    p.add_argument("--input", required=True)
    p = _add(ev, "migrate-index-base", "convert one-based row indexes explicitly",
             cmd_evidence_migrate)
    p.add_argument("--input", required=True)

    p = _add(sub, "report", "render and export a report", cmd_report,
             "  vr-dfir report --id <ID> --format md --output report.md\n"
             "  vr-dfir report --id <ID> --format json --output -")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--id", help="investigation ID")
    which.add_argument("--input", help="run directory")
    p.add_argument("--format", choices=["json", "md", "html", "all"], default="all")
    p.add_argument("--output", metavar="FILE", help="write one format to FILE ('-' for stdout)")
    p.add_argument("--stdout", action="store_true", help="same as --output -")
    _runs(p)


    p = _add(sub, "ui", "local analyst UI (loopback only, default 127.0.0.1:8766)", cmd_ui)
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help="open a browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = Output(getattr(args, "json", False), getattr(args, "quiet", False))
    try:
        ctx = Context(args)
        return int(args.func(ctx, out) or 0)
    except DfirError as exc:
        out.error(exc)
        if exc.code == "configuration_error":
            return EXIT_CONFIG
        if exc.code in _REFUSED_CODES or isinstance(exc, PolicyError):
            return EXIT_DENIED
        return EXIT_FAIL
    except FileNotFoundError as exc:
        out.error(DfirError(f"File not found: {exc.filename}"))
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        # Output piped into a closed reader (e.g. `| head`): exit quietly.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
