"""End-to-end service tests over the synthetic server (the same code paths as CLI/MCP/UI)."""

import json
import re
from pathlib import Path

import pytest

from support.scenarios import C_APP, C_DB, C_WEB, HUNT, ORG
from support.synthetic import start_scenario
from vr_dfir.errors import DfirError, StaleRequestError
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.jsonio import read_json

ROOT = Path(__file__).resolve().parents[1]


def report(inv):
    return read_json(inv.report_dir / "report.json")


def test_existing_flow_never_collects(synth):
    plan = synth.plan_existing_flow(org_id=ORG, client_id=C_APP, flow_id="F.SYNTH0002",
                                   profile_ref="lth-baseline")
    assert plan.launches == 0
    inv = synth.create(plan)
    meta = synth.execute(inv.id)
    assert synth.runtime.fake_server.launches == []
    assert meta["collection_status"] == "not_applicable"
    assert meta["retrieval_status"] == "complete" and meta["phase"] == "report_ready"
    queries = " ".join(q for _, q in synth.runtime.fake_server.queries)
    assert "collect_client" not in queries and "hunt(" not in queries


def test_existing_hunt_never_creates_hunts(synth):
    plan = synth.plan_existing_hunt(org_id=ORG, hunt_id=HUNT)
    inv = synth.create(plan, no_llm=True)
    meta = synth.execute(inv.id)
    assert synth.runtime.fake_server.launches == []
    r = report(inv)
    assert r["hunt"]["flow_states"] == {"completed": 2, "failed": 1, "in_progress": 1}
    assert meta["outcome"] == "partial"  # an in-progress flow was not read
    clients = {s["client_id"] for s in r["coverage"]["sources"]}
    assert clients == {C_WEB, C_APP, C_DB, "C.SYNTH0000000007"}
    pattern = [f for f in r["findings"] if f.get("rule_id") == "cross_client_hash"]
    assert pattern and set(pattern[0]["client_ids"]) == {C_APP, C_DB}
    # Provenance: each cited row resolves to its own client and flow.
    for row in r["evidence"]["cited_rows"]:
        assert row["hunt_id"] == HUNT and row["flow_id"].startswith("F.SYNTHH")
    assert "whole-fleet" in r["hunt"]["coverage_note"]




def test_model_outage_then_retry(synth):
    synth.runtime.fake_mode = "outage"
    inv = start_scenario(synth, "review")
    meta = synth.execute(inv.id)
    assert meta["analysis_status"] == "failed" and meta["retrieval_status"] == "complete"
    assert meta["outcome"] == "partial"
    assert any("outage" in e.lower() for e in meta["errors"]), json.dumps(meta["errors"])
    ids_before = EvidenceBundle.open(inv.evidence_dir).evidence_ids()
    synth.runtime.fake_mode = "ok"
    meta = synth.retry_analysis(inv.id)
    assert meta["analysis_status"] == "completed"
    assert EvidenceBundle.open(inv.evidence_dir).evidence_ids() == ids_before
    assert synth.runtime.fake_server.launches == []


def test_partial_failure_resume_stable_ids(synth):
    inv = start_scenario(synth, "partial")
    meta = synth.execute(inv.id)
    assert meta["retrieval_status"] == "incomplete"
    r1 = report(inv)
    net = next(s for s in r1["coverage"]["sources"] if s["full_name"].endswith("NetworkConnections"))
    assert net["read_status"] == "incomplete" and net["persisted_rows"] == 500
    bundle = EvidenceBundle.open(inv.evidence_dir)
    key = net["source_key"]
    first_500 = [r["evidence_id"] for _, r in bundle.iter_records(key)]
    meta = synth.resume(inv.id)
    assert meta["retrieval_status"] == "complete"
    bundle = EvidenceBundle.open(inv.evidence_dir)
    all_ids = [r["evidence_id"] for _, r in bundle.iter_records(key)]
    assert all_ids[:500] == first_500 and len(all_ids) == len(set(all_ids)) == 1200
    assert synth.runtime.fake_server.launches == []
    r2 = report(inv)
    assert r2["investigation"]["outcome"] == "partial"  # Services still failed; never "complete"


def test_no_llm_outcome_evidence_only(synth):
    plan = synth.plan_existing_flow(org_id=ORG, client_id=C_WEB, flow_id="F.SYNTH0006",
                                   profile_ref="custom-example")
    inv = synth.create(plan, no_llm=True)
    meta = synth.execute(inv.id)
    assert meta["analysis_status"] == "not_requested" and meta["outcome"] == "evidence_only"
    assert not (inv.path / "analysis" / "requests").exists() or not any(
        (inv.path / "analysis" / "requests").iterdir())


def test_bundle_analysis_needs_no_velociraptor(tmp_path, synth):
    from vr_dfir.config import load_settings
    from vr_dfir.profiles.store import ProfileStore
    from vr_dfir.services.investigations import InvestigationService
    from vr_dfir.services.runstore import RunStore
    from vr_dfir.services.runtime import live_runtime
    settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "s")}, load_dotenv=False)
    rt = live_runtime(settings)  # no API config: any Velociraptor call would raise
    svc = InvestigationService(rt, RunStore(tmp_path / "s" / "inv"), ProfileStore(tmp_path / "s"))
    inv = svc.analyze_bundle(ROOT / "tests" / "fixtures" / "bundle", no_llm=True)
    meta = svc.execute(inv.id)
    assert meta["outcome"] in ("evidence_only", "partial")
    assert rt._service is None  # the live service was never constructed
    assert report(inv)["findings"]


def test_stale_plan_rejected(synth):
    plan = synth.plan_existing_flow(org_id=ORG, client_id=C_APP, flow_id="F.SYNTH0002")
    with pytest.raises(StaleRequestError):
        synth.create(plan, expected_fingerprint="0" * 64)


def test_unreadable_api_config_is_a_clear_error(tmp_path):
    from vr_dfir.config import load_settings
    from vr_dfir.profiles.store import ProfileStore
    from vr_dfir.services.investigations import InvestigationService
    from vr_dfir.services.runstore import RunStore
    from vr_dfir.services.runtime import live_runtime
    settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "l"),
                              "VELOCIRAPTOR_API_CONFIG": str(tmp_path / "x.yaml")}, load_dotenv=False)
    live = InvestigationService(live_runtime(settings), RunStore(tmp_path / "l" / "i"),
                                ProfileStore(tmp_path / "l"))
    with pytest.raises(DfirError) as exc:
        live.plan_existing_flow(org_id="O1", client_id="C.1a2b3c", flow_id="F.ABC123")
    assert exc.value.code == "configuration_error"
    assert not list((tmp_path / "l" / "i").iterdir())  # nothing was created


def test_reports_render_safely(synth):
    from vr_dfir.reporting.render import render_html, render_markdown
    from vr_dfir.reporting.report import build_report
    inv = start_scenario(synth, "review")
    synth.execute(inv.id)
    bundle = EvidenceBundle.open(inv.evidence_dir)
    key = next(e.source_key for e in bundle.sources() if e.source == "Processes")
    hostile = next(r for _, r in bundle.iter_records(key) if r["row_index"] == 98)
    users = next(e.source_key for e in bundle.sources() if e.source == "Users")
    persian = next(r for _, r in bundle.iter_records(users) if r["row"]["User"] == "admin")
    finding = {"finding_id": "A-001", "title": "<b>hostile</b> title", "observation": "o",
               "interpretation": "i", "evidence_ids": [hostile["evidence_id"], persian["evidence_id"]],
               "severity": "low", "confidence": "low", "confidence_rationale": "r",
               "benign_explanations": ["b"], "missing_context": [], "next_steps": [],
               "origin": "analyst_input", "client_ids": [], "sources": []}
    r = build_report(inv.reload(), bundle=bundle, analysis={"model_findings": [finding],
                                                           "deterministic_findings": []})
    html, md = render_html(r), render_markdown(r)
    assert "<script>alert" not in html and "&lt;script&gt;alert" in html
    assert "<img src=x" not in html and "<b>hostile" not in html
    assert not re.search(r"(src|href)=\"https?://", html)  # no external requests
    assert "script-src 'none'" in html
    assert f'id="{hostile["evidence_id"]}"' in html and f'href="#{hostile["evidence_id"]}"' in html
    assert "مدیر سیستم" in html and 'dir="auto"' in html
    assert "<script>" not in md and "<b>hostile" not in md
    full = (inv.report_dir / "report.html").read_text()
    assert "SYNTHETIC TEST DATA" in full and "FAKE MODEL" in full
    statements = " ".join(report(inv)["summary_statements"])
    assert "not evidence that the target is clean" in statements


def test_report_statuses_separate(synth):
    inv = start_scenario(synth, "partial")
    synth.execute(inv.id)
    r = report(inv)
    assert set(r["status"]) >= {"collection", "retrieval", "analysis", "outcome"}
    assert any("Resume reading" in s for s in r["next_steps"])
    assert r["coverage"]["gaps"]


def test_scenarios_ground_truth(tmp_path, no_sleep):
    from support.synthetic import run_all_scenarios
    result = run_all_scenarios(tmp_path / "synth")
    assert result["evaluation"]["passed"], result["evaluation"]["summary"]
    assert result["synthetic_launches"] == 0
    assert {s["scenario"] for s in result["scenarios"]} == {"ordinary", "review", "partial"}
