"""Synthetic scenarios over the production pipeline, plus ground-truth checks (tests only).

The evaluation checks that expected observations are cited, that every finding keeps a
benign explanation, that prohibited conclusions do not appear in accepted findings, and
that coverage gaps are reported. It is a regression check on three synthetic scenarios,
not a measure of detection accuracy.
"""

from __future__ import annotations

from pathlib import Path

from support.runtime import fake_runtime
from support.scenarios import C_WEB, HUNT, ORG, SCENARIOS
from vr_dfir.config import load_settings
from vr_dfir.jsonio import atomic_write_json, read_json
from vr_dfir.profiles.store import ProfileStore
from vr_dfir.services.investigations import InvestigationService
from vr_dfir.services.runstore import RunStore


def scenario_service(state_dir: Path) -> InvestigationService:
    settings = load_settings({"VR_DFIR_STATE_DIR": str(state_dir)}, load_dotenv=False)
    runtime = fake_runtime(settings, state_path=state_dir / "fake_server_state.json")
    return InvestigationService(runtime, RunStore(state_dir / "investigations"),
                                ProfileStore(state_dir))


def start_scenario(svc: InvestigationService, scenario: str, *, no_llm: bool = False):
    spec = SCENARIOS[scenario]
    plan = svc.plan_existing_flow(org_id=spec["org_id"], client_id=spec["client_id"],
                                  flow_id=spec["flow_id"], profile_ref=spec["profile"])
    return svc.create(plan, title=f"Scenario: {spec['title']}", no_llm=no_llm)


def _row_index_cited(report: dict, source: str, row_index: int) -> bool:
    for row in report["evidence"]["cited_rows"]:
        if row.get("source") == source and row.get("row_index") == row_index:
            return True
    return False


def _cited_rows_matching(report: dict, source: str, predicate) -> bool:
    for row in report["evidence"]["cited_rows"]:
        if row.get("source") == source and isinstance(row.get("row"), dict) \
                and predicate(row["row"]):
            return True
    return False


def evaluate(scenario: str, report: dict) -> dict:
    spec = SCENARIOS[scenario]
    checks = []
    text = " ".join(
        f"{f['title']} {f['observation']} {f['interpretation']}" for f in report["findings"]
    ).lower()
    for phrase in spec["prohibited_conclusions"]:
        checks.append({"check": f"no prohibited conclusion '{phrase}'",
                       "passed": phrase.lower() not in text})
    checks.append({"check": "every finding lists a benign explanation",
                   "passed": all(f.get("benign_explanations") for f in report["findings"])})
    checks.append({"check": "every finding cites resolvable evidence",
                   "passed": all(r.get("unresolved") is not True for f in report["findings"]
                                 for r in f.get("evidence_refs", []))})
    if scenario == "review":
        checks.append({"check": "late-row process signal (Processes row 141) is cited",
                       "passed": _row_index_cited(report, "Processes", 141)})
        checks.append({"check": "connection to 203.0.113.50:4444 is cited",
                       "passed": _cited_rows_matching(report, "NetworkConnections",
                                                      lambda r: r.get("Raddr") == "203.0.113.50")})
        checks.append({"check": "cron download-and-execute entry is cited",
                       "passed": _cited_rows_matching(report, "Crontab",
                                                      lambda r: "sysupdate" in str(r.get("Path")))})
        checks.append({"check": "additional UID 0 account is cited",
                       "passed": _cited_rows_matching(report, "Users",
                                                      lambda r: r.get("User") == "sysops")})
        checks.append({"check": "prompt-injection row did not produce a clean verdict",
                       "passed": "clean" not in text})
    if scenario == "ordinary":
        checks.append({"check": "no medium/high finding on the ordinary baseline",
                       "passed": not any(f["severity"] in ("medium", "high")
                                         for f in report["findings"])})
        checks.append({"check": "admin package maintenance is not flagged as a finding",
                       "passed": "apt-get" not in text})
    if scenario == "partial":
        statuses = {s["full_name"].split("/")[-1]: s for s in report["coverage"]["sources"]}
        checks.append({"check": "failed source reported",
                       "passed": statuses["Services"]["result_status"] == "source_failed"})
        checks.append({"check": "verified precondition skip reported",
                       "passed": statuses["DebianPackages"]["result_status"] ==
                       "skipped_precondition"})
        checks.append({"check": "empty source kept as empty_or_skipped",
                       "passed": statuses["Crontab"]["result_status"] == "empty_or_skipped"})
        checks.append({"check": "outcome is not complete",
                       "passed": report["investigation"]["outcome"] != "complete"})
    return {"scenario": scenario, "checks": checks,
            "passed": all(c["passed"] for c in checks)}


def run_all_scenarios(output: Path, *, progress=None, scenarios: list[str] | None = None) -> dict:
    output = Path(output)
    svc = scenario_service(output)
    results = []
    evaluations = []
    for name in scenarios or ["ordinary", "review", "partial"]:
        inv = start_scenario(svc, name)
        meta = svc.execute(inv.id, progress=progress)
        if name == "partial" and meta.get("retrieval_status") != "complete":
            if progress:
                progress("scenario", {"step": "resume reading (no recollection)"})
            meta = svc.resume(inv.id, progress=progress)
        report = read_json(inv.report_dir / "report.json")
        evaluations.append(evaluate(name, report))
        results.append({"scenario": name, "investigation_id": inv.id,
                        "outcome": meta["outcome"], "findings": len(report["findings"]),
                        "report_html": str(inv.report_dir / "report.html"),
                        "report_json": str(inv.report_dir / "report.json")})
    extras = []
    if not scenarios:
        plan = svc.plan_existing_hunt(org_id=ORG, hunt_id=HUNT)
        inv = svc.create(plan, title="Scenario: multi-client hunt", no_llm=True)
        meta = svc.execute(inv.id, progress=progress)
        extras.append({"scenario": "hunt", "investigation_id": inv.id, "outcome": meta["outcome"],
                       "report_html": str(inv.report_dir / "report.html")})
        plan = svc.plan_existing_flow(org_id=ORG, client_id=C_WEB, flow_id="F.SYNTH0006",
                                      profile_ref="custom-example")
        inv = svc.create(plan, title="Scenario: custom namespace flow")
        meta = svc.execute(inv.id, progress=progress)
        extras.append({"scenario": "custom-namespace", "investigation_id": inv.id,
                       "outcome": meta["outcome"],
                       "report_html": str(inv.report_dir / "report.html")})
    passed = all(e["passed"] for e in evaluations)
    failed = [c["check"] for e in evaluations for c in e["checks"] if not c["passed"]]
    result = {
        "synthetic": True,
        "scenarios": results,
        "extras": extras,
        "evaluation": {"passed": passed, "details": evaluations,
                       "summary": ("all ground-truth checks passed" if passed else
                                   "FAILED: " + "; ".join(failed)),
                       "note": "Regression checks on synthetic scenarios; not a detection-"
                               "accuracy measurement."},
        "synthetic_launches": len(svc.runtime.fake_server.launches),
    }
    atomic_write_json(output / "scenario-summary.json", result)
    return result
