"""Selected-evidence report: the analyst's pinned items, notes and their exact provenance.

It is a second, clearly labelled report scope beside the full investigation report. Every
pinned row is resolved again from the saved bundle and checked against the content hash in
the pin; a missing or changed row is reported, never replaced. Coverage limitations of the
sources involved are included. Building or opening it reads saved files only: no model
request, no Velociraptor request, no network access.
"""

from __future__ import annotations

import html
import json
from collections import Counter

from vr_dfir.errors import DfirError
from vr_dfir.evidence.bundle import EvidenceBundle, now_iso
from vr_dfir.jsonio import read_json
from vr_dfir.reporting.fonts import embedded_fonts
from vr_dfir.reporting.render import _CSS, _md_code, _md_escape
from vr_dfir.services.reviews import load_reviews, review_of
from vr_dfir.services.selection import SelectionStore
from vr_dfir.version import __version__

REPORT_VERSION = 1
SCOPE_LABEL = "Selected evidence"
STATEMENT = ("This report contains only the items an analyst selected, with their notes. It is "
             "not a complete account of the investigation, it does not show that other activity "
             "is absent, and a reviewed or confirmed item is not a declaration that a host is "
             "compromised or clean. The full investigation report is a separate scope.")


def _bundle(store, bundles: dict, inv_id: str) -> EvidenceBundle:
    """Each bundle is opened once per report (pins often cite rows of the same bundle)."""
    if inv_id not in bundles:
        try:
            bundles[inv_id] = EvidenceBundle.open(store.get(inv_id).evidence_dir)
        except DfirError as exc:
            bundles[inv_id] = exc
    found = bundles[inv_id]
    if isinstance(found, DfirError):
        raise found
    return found


def _resolve(store, bundles: dict, inv_id: str, eid: str, expected_hash: str | None) -> dict:
    try:
        rec = _bundle(store, bundles, inv_id).get_record(eid)
    except DfirError as exc:
        return {"evidence_id": eid, "investigation_id": inv_id, "resolved": False,
                "problem": exc.message}
    out = {k: rec[k] for k in ("evidence_id", "org_id", "client_id", "hostname", "flow_id",
                               "hunt_id", "artifact", "source", "full_name", "row_index",
                               "raw_sha256", "collected_at", "retrieved_at")}
    out.update(investigation_id=inv_id, resolved=True, row=rec["row"],
               hash_matches_pin=(expected_hash is None or expected_hash == rec["raw_sha256"]))
    return out


def build_selected(inv, store) -> dict:
    meta = inv.reload()
    pins = SelectionStore(inv.path).list()
    reviews = load_reviews(inv.path)
    report_path = inv.report_dir / "report.json"
    full = read_json(report_path) if report_path.is_file() else None
    findings = {f["finding_id"]: f for f in (full or {}).get("findings") or []}
    items, sources_used, policies, bundles = [], set(), {}, {}
    for p in pins:
        snap = p.get("snapshot") or {}
        item = {"pin_id": p["pin_id"], "kind": p["kind"], "title": p.get("title"),
                "note": p.get("note"), "pinned_at": p.get("created_at"),
                "note_updated_at": p.get("updated_at")}
        if p["kind"] == "evidence":
            ev = _resolve(store, bundles, inv.id, p["ref"], snap.get("raw_sha256"))
            item["evidence"] = [ev]
            if ev.get("resolved"):
                sources_used.add((inv.id, f"{ev['org_id']}/{ev['client_id']}/{ev['flow_id']}/"
                                          f"{ev['artifact']}/{ev.get('source') or ''}"))
        elif p["kind"] == "finding":
            f = findings.get(p["ref"])
            review = review_of(reviews, p["ref"])
            item["finding"] = ({k: f.get(k) for k in (
                "finding_id", "title", "severity", "confidence", "origin", "observation",
                "interpretation", "benign_explanations", "missing_context", "next_steps",
                "rule_id", "evidence_ids")} if f else {"finding_id": p["ref"],
                                                       "missing": True})
            item["review"] = {"state": review["state"], "label": review["label"],
                              "note": review.get("note"), "updated_at": review.get("updated_at")}
            if f and snap.get("evidence_ids") and list(snap["evidence_ids"]) != list(
                    f.get("evidence_ids") or []):
                item["finding"]["note"] = ("The finding's cited rows differ from when it was "
                                           "pinned; the current report version is shown.")
            item["evidence"] = [_resolve(store, bundles, inv.id, e, None)
                                for e in (snap.get("evidence_ids") or [])[:20]]
            for ev in item["evidence"]:
                if ev.get("resolved"):
                    sources_used.add((inv.id, f"{ev['org_id']}/{ev['client_id']}/{ev['flow_id']}/"
                                              f"{ev['artifact']}/{ev.get('source') or ''}"))
        else:
            item["change"] = {k: snap.get(k) for k in (
                "comparison_id", "item_key", "state", "key_display", "changed", "reason",
                "before_values", "after_values", "policy_version", "adapter_label")}
            policies[snap.get("comparison_id")] = snap.get("policy_version")
            item["baseline_capture"] = snap.get("baseline")
            item["current_capture"] = snap.get("current")
            item["evidence"] = (
                [_resolve(store, bundles, (snap.get("baseline") or {}).get("investigation_id", inv.id),
                          e, None) | {"side": "before"} for e in snap.get("before_eids") or []]
                + [_resolve(store, bundles, (snap.get("current") or {}).get("investigation_id", inv.id),
                            e, None) | {"side": "after"} for e in snap.get("after_eids") or []])
            for side in ("baseline", "current"):
                cap = snap.get(side) or {}
                if cap.get("source_key"):
                    sources_used.add((cap.get("investigation_id"), cap["source_key"]))
        items.append(item)
    coverage = _coverage(store, sources_used, bundles)
    return {
        "report_version": REPORT_VERSION, "report_scope": "selected_evidence",
        "report_scope_label": SCOPE_LABEL,
        "generator": f"velociraptor-dfir-agent {__version__}", "generated_at": now_iso(),
        "synthetic": bool(meta.get("synthetic")),
        "data_label": ("SYNTHETIC TEST DATA - not collected from real endpoints"
                       if meta.get("synthetic") else None),
        "investigation": {"id": meta.get("investigation_id"), "title": meta.get("title"),
                          "mode": meta.get("mode"), "outcome": meta.get("outcome"),
                          "created_at": meta.get("created_at")},
        "scope": meta.get("scope") or {},
        "selection": {"items": len(items), "kinds": dict(Counter(i["kind"] for i in items))},
        "items": items, "coverage": coverage,
        "comparison_policies": [{"comparison_id": k, "policy_version": v}
                                for k, v in policies.items() if k],
        "full_report_generated_at": (full or {}).get("generated_at"),
        "statement": STATEMENT,
        "inference": "No model request was made to build this report.",
    }


def _coverage(store, used: set, bundles: dict) -> dict:
    out, gaps = [], []
    for inv_id, key in sorted(used, key=lambda x: (str(x[0]), x[1])):
        try:
            entry = _bundle(store, bundles, inv_id).source(key)
        except DfirError:
            gaps.append(f"{key}: the source could not be read back.")
            continue
        cov = entry.coverage
        out.append({"investigation_id": inv_id, "source_key": key, "full_name": entry.full_name,
                    "hostname": entry.hostname, "client_id": entry.client_id,
                    "collected_at": entry.collected_at, "read_status": cov.read_status,
                    "result_status": cov.result_status, "persisted_rows": cov.persisted_rows,
                    "stop_reason": cov.stop_reason})
        if cov.read_status != "complete":
            gaps.append(f"{entry.full_name} on {entry.hostname or entry.client_id}: read "
                        f"{cov.read_status.replace('_', ' ')} ({cov.persisted_rows} rows saved).")
        if cov.result_status not in ("rows",):
            gaps.append(f"{entry.full_name} on {entry.hostname or entry.client_id}: result "
                        f"{cov.result_status.replace('_', ' ')}.")
    return {"sources": out, "limitations": gaps,
            "note": "Coverage of the sources that the selected items come from. Sources not "
                    "involved are summarized in the full investigation report."}


# ----------------------------------------------------------------------- rendering

def render_selected_markdown(r: dict) -> str:
    out = [f"# {SCOPE_LABEL}: {_md_escape(r['investigation']['title'])}"]
    if r.get("data_label"):
        out.append(f"\n> **{_md_escape(r['data_label'])}**")
    out += ["", f"- Report scope: **{SCOPE_LABEL}** ({r['selection']['items']} item(s))",
            f"- Investigation ID: `{_md_code(r['investigation']['id'])}`",
            f"- Organization: `{_md_code(r['scope'].get('org_id'))}`",
            f"- Generated: {_md_escape(r['generated_at'])} by {_md_escape(r['generator'])}",
            f"- {_md_escape(r['inference'])}", "", _md_escape(r["statement"]), ""]
    for i, item in enumerate(r["items"], 1):
        out.append(f"## {i}. {_md_escape(item['title'])}")
        out.append(f"- Kind: {_md_escape(item['kind'])}; pinned {_md_escape(item['pinned_at'])}")
        if item.get("note"):
            out.append(f"- Analyst note: {_md_escape(item['note'])}")
        if item.get("finding"):
            f = item["finding"]
            if f.get("missing"):
                out.append("- The finding is no longer in the investigation report.")
            else:
                out.append(f"- Finding `{_md_code(f['finding_id'])}`: severity "
                           f"{_md_escape(f['severity'])}, confidence {_md_escape(f['confidence'])}, "
                           f"origin {_md_escape(f['origin'])}")
                out.append(f"- Review decision: {_md_escape(item['review']['label'])}"
                           + (f" ({_md_escape(item['review']['note'])})"
                              if item["review"].get("note") else ""))
                out.append(f"- Observation: {_md_escape(f['observation'])}")
                out.append(f"- Interpretation: {_md_escape(f['interpretation'])}")
        if item.get("change"):
            c = item["change"]
            out.append(f"- Change: **{_md_escape(c['state'])}** {_md_escape(c['key_display'])} "
                       f"({_md_escape(c.get('adapter_label'))}, policy "
                       f"`{_md_code(c.get('policy_version'))}`)")
            for f in c.get("changed") or []:
                before = (c.get("before_values") or {}).get(f)
                after = (c.get("after_values") or {}).get(f)
                out.append(f"  - {_md_escape(f)}: {_md_escape(json.dumps(before, ensure_ascii=False))}"
                           f" → {_md_escape(json.dumps(after, ensure_ascii=False))}")
            if c.get("reason"):
                out.append(f"  - Reason: {_md_escape(c['reason'])}")
            for side in ("baseline_capture", "current_capture"):
                cap = item.get(side) or {}
                out.append(f"- {side.split('_')[0].title()} capture: "
                           f"{_md_escape(cap.get('collected_at'))}, flow "
                           f"`{_md_code(cap.get('flow_id'))}`, investigation "
                           f"`{_md_code(cap.get('investigation_id'))}`")
        for ev in item.get("evidence") or []:
            if not ev.get("resolved"):
                out.append(f"- Evidence `{_md_code(ev['evidence_id'])}`: NOT RESOLVED "
                           f"({_md_escape(ev.get('problem'))})")
                continue
            side = f"{ev['side']}: " if ev.get("side") else ""
            out.append(f"- {side}Evidence `{_md_code(ev['evidence_id'])}`: "
                       f"{_md_escape(ev['full_name'])} row {ev['row_index']} on "
                       f"{_md_escape(ev['hostname'] or ev['client_id'])}, flow "
                       f"`{_md_code(ev['flow_id'])}`, SHA-256 `{_md_code(ev['raw_sha256'])}`"
                       + ("" if ev.get("hash_matches_pin", True) else
                          " (CONTENT DIFFERS FROM WHEN IT WAS PINNED)"))
        out.append("")
    out.append("## Coverage of the involved sources")
    for s in r["coverage"]["sources"]:
        out.append(f"- {_md_escape(s['full_name'])} on {_md_escape(s['hostname'] or s['client_id'])}: "
                   f"read {_md_escape(s['read_status'])}, result {_md_escape(s['result_status'])}, "
                   f"{s['persisted_rows']} rows, captured {_md_escape(s['collected_at'])}")
    for g in r["coverage"]["limitations"]:
        out.append(f"- Limitation: {_md_escape(g)}")
    return "\n".join(out) + "\n"


def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _row_table(row) -> str:
    if not isinstance(row, dict):
        return f'<pre class="mono">{_e(json.dumps(row, ensure_ascii=False, indent=2))}</pre>'
    cells = []
    for k, v in row.items():
        shown = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        cells.append(f'<tr><th class="mono">{_e(k)}</th><td class="v mono" dir="auto">{_e(shown)}'
                     "</td></tr>")
    return "<table><tbody>" + "".join(cells) + "</tbody></table>"


def render_selected_html(r: dict) -> str:
    font_css, notices = embedded_fonts()
    inv = r["investigation"]
    parts = ["<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
             "style-src 'unsafe-inline'; font-src data:; img-src 'none'; script-src 'none'\">",
             f"<title>{SCOPE_LABEL} report {_e(inv['id'])}</title>",
             f"<style>{font_css}\n{_CSS}</style></head><body><main>"]
    if r.get("data_label"):
        parts.append(f'<p class="synthetic">{_e(r["data_label"])}</p>')
    parts.append(f"<h1>{SCOPE_LABEL}: {_e(inv['title'])}</h1>")
    parts.append('<div class="card kv">'
                 f'<div class="meta">Report scope</div><div><strong>{SCOPE_LABEL}</strong> '
                 f'({_e(r["selection"]["items"])} item(s))</div>'
                 f'<div class="meta">Investigation</div><div><code>{_e(inv["id"])}</code></div>'
                 f'<div class="meta">Organization</div><div><code>{_e(r["scope"].get("org_id"))}'
                 f'</code></div><div class="meta">Generated</div><div>{_e(r["generated_at"])} - '
                 f'{_e(r["generator"])}</div></div>')
    parts.append(f'<p class="meta">{_e(r["statement"])} {_e(r["inference"])}</p>')
    for n, item in enumerate(r["items"], 1):
        parts.append(f'<section class="card" id="{_e(item["pin_id"])}"><h2>{n}. {_e(item["title"])}'
                     f'</h2><p class="meta">{_e(item["kind"])} · pinned {_e(item["pinned_at"])}</p>')
        if item.get("note"):
            parts.append(f'<p><strong>Analyst note.</strong> <span dir="auto">{_e(item["note"])}'
                         "</span></p>")
        f = item.get("finding")
        if f and not f.get("missing"):
            parts.append(f'<p>Finding <code>{_e(f["finding_id"])}</code> · severity '
                         f'{_e(f["severity"])} · confidence {_e(f["confidence"])} · '
                         f'{_e(f["origin"])} · review: <strong>{_e(item["review"]["label"])}'
                         f'</strong></p><p><strong>Observation.</strong> <span dir="auto">'
                         f'{_e(f["observation"])}</span></p><p><strong>Interpretation.</strong> '
                         f'{_e(f["interpretation"])}</p>')
        elif f:
            parts.append('<p class="bad">The finding is no longer in the investigation report.</p>')
        c = item.get("change")
        if c:
            rows = "".join(f"<tr><th>{_e(k)}</th><td class=\"v mono\">"
                           f"{_e(json.dumps((c.get('before_values') or {}).get(k), ensure_ascii=False))}"
                           f"</td><td class=\"v mono\">"
                           f"{_e(json.dumps((c.get('after_values') or {}).get(k), ensure_ascii=False))}"
                           "</td></tr>" for k in (c.get("changed") or []))
            parts.append(f'<p><strong>{_e(c["state"])}</strong> {_e(c["key_display"])} '
                         f'({_e(c.get("adapter_label"))}; policy <code>{_e(c.get("policy_version"))}'
                         "</code>)</p>"
                         + (f"<table><thead><tr><th>Field</th><th>Before</th><th>After</th></tr>"
                            f"</thead><tbody>{rows}</tbody></table>" if rows else "")
                         + (f'<p class="meta">{_e(c["reason"])}</p>' if c.get("reason") else ""))
            for side in ("baseline_capture", "current_capture"):
                cap = item.get(side) or {}
                parts.append(f'<p class="meta">{_e(side.split("_")[0].title())} capture: '
                             f'{_e(cap.get("collected_at"))} · flow <code>{_e(cap.get("flow_id"))}'
                             f'</code> · investigation <code>{_e(cap.get("investigation_id"))}'
                             "</code></p>")
        for ev in item.get("evidence") or []:
            if not ev.get("resolved"):
                parts.append(f'<p class="bad">Evidence <code>{_e(ev["evidence_id"])}</code> was '
                             f'not resolved: {_e(ev.get("problem"))}</p>')
                continue
            label = f'{_e(ev["side"]).title()}: ' if ev.get("side") else ""
            warn = "" if ev.get("hash_matches_pin", True) else \
                ' <strong class="bad">content differs from when it was pinned</strong>'
            parts.append(f'<details open><summary>{label}<code>{_e(ev["evidence_id"])}</code> · '
                         f'{_e(ev["full_name"])} row {_e(ev["row_index"])} · '
                         f'<span dir="auto">{_e(ev["hostname"] or ev["client_id"])}</span>{warn}'
                         f'</summary><p class="meta">flow <code>{_e(ev["flow_id"])}</code> · '
                         f'captured {_e(ev["collected_at"])} · SHA-256 <code>{_e(ev["raw_sha256"])}'
                         f'</code></p>{_row_table(ev["row"])}</details>')
        parts.append("</section>")
    parts.append("<h2>Coverage of the involved sources</h2><table><thead><tr><th>Source</th>"
                 "<th>Host</th><th>Read</th><th>Result</th><th>Rows</th><th>Captured</th></tr>"
                 "</thead><tbody>")
    for s in r["coverage"]["sources"]:
        parts.append(f'<tr><td><code>{_e(s["full_name"])}</code></td><td dir="auto">'
                     f'{_e(s["hostname"] or s["client_id"])}</td><td>{_e(s["read_status"])}</td>'
                     f'<td>{_e(s["result_status"])}</td><td>{_e(s["persisted_rows"])}</td>'
                     f'<td>{_e(s["collected_at"])}</td></tr>')
    parts.append("</tbody></table>")
    if r["coverage"]["limitations"]:
        parts.append('<div class="card"><strong>Limitations</strong><ul>'
                     + "".join(f"<li>{_e(g)}</li>" for g in r["coverage"]["limitations"])
                     + "</ul></div>")
    parts.append(f'<p class="meta">{_e(r["coverage"]["note"])}</p>')
    if notices:
        parts.append(f'<template id="font-licenses"><pre>{_e(notices)}</pre></template>')
    parts.append("</main></body></html>")
    return "\n".join(parts)
