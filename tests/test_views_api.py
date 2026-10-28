"""UI API for views, changes, cohorts, pins and the selected-evidence report."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support.runtime import fake_runtime
from support.scenarios import SCENARIOS
from vr_dfir.config import load_settings
from vr_dfir.web.api import create_app

BASE = "http://127.0.0.1:8766"


class CountingProvider:
    """A provider that records every model request (none are expected while navigating)."""

    name, model, is_fake, is_hosted = "ollama", "test-model", True, False
    calls = 0

    def options(self):
        return {}

    def complete(self, messages, max_output_tokens=1024):
        CountingProvider.calls += 1
        raise AssertionError("a model request was made")

    def probe(self):
        return {"reachable": True}


@pytest.fixture
def client(tmp_path, no_sleep):
    def make():
        settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "state"),
                                  "VELOCIRAPTOR_MODEL_PROVIDER": "ollama",
                                  "OLLAMA_MODEL": "test-model"}, load_dotenv=False)
        runtime = fake_runtime(settings, state_path=tmp_path / "fake.json")
        from vr_dfir.analysis.providers import DisabledProvider
        runtime.provider_override = lambda no_llm: DisabledProvider() if no_llm else \
            CountingProvider()
        app = create_app(settings, runtime=runtime)
        c = TestClient(app, base_url=BASE)
        c.headers["X-CSRF-Token"] = c.get("/api/v1/session").json()["data"]["csrf_token"]
        return c
    return make


def start(c, scenario):
    spec = SCENARIOS[scenario]
    plan = c.post("/api/v1/plans", json={"mode": "existing_flow", "org_id": spec["org_id"],
                                         "client_id": spec["client_id"],
                                         "flow_id": spec["flow_id"], "no_llm": True}).json()
    inv = c.post("/api/v1/investigations",
                 json={"plan_token": plan["data"]["plan_token"]}).json()["data"]["investigation_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        d = c.get(f"/api/v1/investigations/{inv}").json()["data"]
        if not d["job_active"] and d["investigation"]["job"]["state"] not in ("queued", "running"):
            return inv
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def view(c, path, **params):
    deadline = time.time() + 30
    while time.time() < deadline:
        r = c.get(path, params=params)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        if data.get("state") != "building":
            return data
        time.sleep(0.1)
    raise AssertionError("view not ready")


def test_navigation_never_requests_a_model(client):
    c = client()
    CountingProvider.calls = 0
    inv = start(c, "review")
    base = f"/api/v1/investigations/{inv}"
    ov = view(c, f"{base}/views/overview")
    assert ov["state"] == "ready" and len(ov["metrics"]) <= 3 and len(ov["charts"]) <= 2
    rows = view(c, f"{base}/views/rows", severity="medium", limit=5)
    eid = rows["records"][0]["evidence_id"]
    view(c, f"{base}/views/inspect/{eid}")
    view(c, f"{base}/views/timeline")
    view(c, f"{base}/views/hosts")
    view(c, f"{base}/views/where-else/{eid}")
    c.get(f"{base}/changes/sources")
    c.get(f"{base}/findings")
    c.post(f"{base}/selected-report")
    c.get(f"{base}/report", params={"format": "html", "scope": "selected"})
    assert CountingProvider.calls == 0


def test_view_parameters_are_validated(client):
    c = client()
    inv = start(c, "review")
    base = f"/api/v1/investigations/{inv}"
    view(c, f"{base}/views/overview")
    assert c.get(f"{base}/views/rows", params={"severity": "drop table"}).status_code == 422
    assert c.get(f"{base}/views/rows", params={"entity_kind": "path"}).status_code == 422
    assert c.get(f"{base}/views/rows", params={"source_key": "../../etc"}).status_code == 404
    assert c.get(f"{base}/views/rows", params={"limit": 5000}).status_code == 422
    assert c.get(f"{base}/views/inspect/not-an-id").status_code == 422
    assert c.get(f"{base}/views/inspect/ev-00000000000000000000").status_code == 404
    assert c.get(f"{base}/views/hosts", params={"scope": "everything"}).status_code == 422
    assert c.get("/api/v1/investigations/../views/overview").status_code in (404, 422)


def test_state_changing_routes_keep_csrf_and_origin_checks(client):
    c = client()
    inv = start(c, "review")
    base = f"/api/v1/investigations/{inv}"
    anon = TestClient(c.app, base_url=BASE)
    body = {"kind": "finding", "ref": "R-001"}
    assert anon.post(f"{base}/selection", json=body).status_code == 403
    assert c.post(f"{base}/selection", json=body,
                  headers={"Origin": "http://evil.example"}).status_code == 403
    assert anon.post(f"{base}/selected-report").status_code == 403
    assert anon.post(f"{base}/views/index/cancel").status_code == 403
    assert anon.post(f"{base}/views/index/rebuild").status_code == 403
    assert anon.delete(f"{base}/selection/pin-0000000000000000").status_code == 403
    assert TestClient(c.app, base_url="http://evil.example").get(
        f"{base}/views/overview").status_code == 421


def test_pins_survive_restart_and_build_the_selected_report(client, tmp_path):
    c = client()
    inv = start(c, "review")
    base = f"/api/v1/investigations/{inv}"
    rows = view(c, f"{base}/views/rows", q="kworkerd -o", limit=1)
    eid = rows["records"][0]["evidence_id"]
    fid = rows["records"][0]["cited_by"][0]
    p1 = c.post(f"{base}/selection", json={"kind": "evidence", "ref": eid, "note": "x"})
    assert p1.status_code == 201
    pin = p1.json()["data"]
    assert pin["snapshot"]["raw_sha256"] and pin["snapshot"]["evidence_id"] == eid
    assert c.post(f"{base}/selection", json={"kind": "finding", "ref": fid}).status_code == 201
    assert c.post(f"{base}/selection", json={"kind": "finding",
                                             "ref": "R-999"}).status_code == 404
    note = "Owner says <b>planned</b>\r# Forged heading\r\n```"
    assert c.put(f"{base}/selection/{pin['pin_id']}", json={"note": note}).status_code == 200
    c2 = client()  # new application process state, same state directory
    pins = c2.get(f"{base}/selection").json()["data"]["pins"]
    assert {p["kind"] for p in pins} == {"evidence", "finding"}
    insp = view(c2, f"{base}/views/inspect/{eid}")
    assert insp["actions"]["pin"]["pinned"] is True
    assert c2.post(f"{base}/selected-report").status_code == 200
    report = c2.get(f"{base}/report", params={"format": "json", "scope": "selected"}).json()
    assert report["report_scope"] == "selected_evidence"
    ev = next(i for i in report["items"] if i["kind"] == "evidence")["evidence"][0]
    assert ev["resolved"] and ev["hash_matches_pin"] and ev["evidence_id"] == eid
    assert report["coverage"]["sources"] and "No model request" in report["inference"]
    md = c2.get(f"{base}/report", params={"format": "md", "scope": "selected"}).text
    assert "\n# Forged" not in md and "\r" not in md and "\n```" not in md
    html = c2.get(f"{base}/report", params={"format": "html", "scope": "selected"})
    assert "<b>planned</b>" not in html.text and "&lt;b&gt;planned" in html.text
    assert "script-src 'none'" in html.headers["content-security-policy"]
    full = c2.get(f"{base}/report", params={"format": "html"})
    assert full.status_code == 200 and "Selected evidence" not in full.text
    assert c2.delete(f"{base}/selection/{pin['pin_id']}").json()["data"]["removed"]


def test_change_routes_and_inspector_actions(client):
    c = client()
    base_inv = start(c, "review")
    later = start(c, "review_later")
    base = f"/api/v1/investigations/{later}"
    sources = c.get(f"{base}/changes/sources").json()["data"]["sources"]
    users = next(s for s in sources if s["full_name"].endswith("/Users"))
    assert users["default_baseline"]["investigation_id"] == base_inv
    res = c.post(f"{base}/changes", json={
        "source_key": users["source_key"], "baseline_investigation_id": base_inv,
        "baseline_source_key": users["default_baseline"]["source_key"]}).json()["data"]
    while res["state"] == "building":
        time.sleep(0.1)
        res = c.get(f"{base}/changes/{res['comparison_id']}").json()["data"]
    assert res["summary"]["counts"]["added"] == 1
    items = c.get(f"{base}/changes/{res['comparison_id']}/items").json()["data"]
    assert {i["state"] for i in items["items"]} == {"added", "removed", "modified"}
    item = items["items"][0]
    pin = c.post(f"{base}/selection", json={"kind": "change",
                                            "comparison_id": res["comparison_id"],
                                            "item_key": item["item_key"]}).json()["data"]
    assert pin["snapshot"]["policy_version"] and pin["snapshot"]["baseline"]["investigation_id"]
    assert pin["snapshot"]["current"]["source_key"] == users["source_key"]  # pin navigation
    one = c.get(f"{base}/changes/{res['comparison_id']}/items/{item['item_key']}").json()
    assert one["data"] == item
    assert c.get(f"{base}/changes/{res['comparison_id']}/items/../x").status_code in (404, 422)
    assert c.get(f"{base}/changes/{res['comparison_id']}/items/{'0' * 32}").status_code == 404
    rows = view(c, f"{base}/views/rows", source_key=users["source_key"], limit=1)
    insp = view(c, f"{base}/views/inspect/{rows['records'][0]['evidence_id']}")
    assert insp["actions"]["compare"]["available"]
    old = view(c, f"/api/v1/investigations/{base_inv}/views/rows",
               source_key=users["default_baseline"]["source_key"], limit=1)
    insp = view(c, f"/api/v1/investigations/{base_inv}/views/inspect/"
                   f"{old['records'][0]['evidence_id']}")
    assert not insp["actions"]["compare"]["available"]  # no older capture to compare with
    assert c.post(f"{base}/changes", json={
        "source_key": users["source_key"], "baseline_investigation_id": later,
        "baseline_source_key": users["source_key"]}).status_code == 422
    hist = c.get(f"{base}/views/captures", params={"source_key": users["source_key"]}).json()
    assert hist["data"]["enough_history"] and len(hist["data"]["captures"]) == 2


def test_full_markdown_report_resists_line_break_injection(tmp_path):
    from vr_dfir.reporting.render import render_markdown
    report = json.loads((Path(__file__).parent / "fixtures" / "bundle" / "manifest.json")
                        .read_text())
    assert report  # fixture present
    hostile = "x\r# Forged ## also\r\n```"
    rep = {"investigation": {"id": "inv-1", "title": hostile, "mode": "bundle",
                             "outcome_label": hostile},
           "data_label": None, "status": {}, "provider": {"name": hostile}, "scope": {
               "org_id": "o`\r# x"}, "generated_at": "t", "generator": "g",
           "summary_statements": [hostile], "coverage": {"sources": [], "gaps": [hostile]},
           "findings": [], "rejected_findings": [], "citation_validation_note": hostile}
    md = render_markdown(rep)
    assert "\r" not in md and "\n# Forged" not in md and "\n## also" not in md


def test_explicit_ai_actions_are_bounded_and_failures_leave_results(client):
    from support.fake_model import FakeProvider
    c = client()
    start(c, "review")
    later = start(c, "review_later")
    base = f"/api/v1/investigations/{later}"
    provider = FakeProvider()
    c.app.state.vr.runtime.provider_override = lambda no_llm: provider
    finding = c.get(f"{base}/findings").json()["data"]["findings"][0]["finding_id"]
    res = c.post(f"{base}/findings/{finding}/explain", json={"action": "next_question"}).json()
    assert res["data"]["action"] == "next_question" and res["data"]["status"] == "ok"
    assert c.post(f"{base}/findings/{finding}/explain",
                  json={"action": "rewrite_evidence"}).status_code == 422
    users = next(s for s in c.get(f"{base}/changes/sources").json()["data"]["sources"]
                 if s["full_name"].endswith("/Users"))
    cmp_ = c.post(f"{base}/changes", json={
        "source_key": users["source_key"],
        "baseline_investigation_id": users["default_baseline"]["investigation_id"],
        "baseline_source_key": users["default_baseline"]["source_key"]}).json()["data"]
    summary = c.post(f"{base}/changes/{cmp_['comparison_id']}/explain", json={}).json()["data"]
    assert summary["action"] == "changes_summary" and summary["status"] == "ok"
    assert summary["records_supplied"] == 3  # only the meaningful changes are supplied
    assert all(cit.get("comparison_id") == cmp_["comparison_id"]
               for p in summary["points"] for cit in p["citations"])
    before = c.get(f"{base}/changes/{cmp_['comparison_id']}/items").json()["data"]
    provider.mode = "outage"
    failed = c.post(f"{base}/changes/{cmp_['comparison_id']}/explain",
                    json={"refresh": True}).json()["data"]
    assert failed["status"] == "unavailable"
    assert c.get(f"{base}/changes/{cmp_['comparison_id']}/items").json()["data"] == before
    assert c.app.state.vr.runtime.fake_server.launches == []
