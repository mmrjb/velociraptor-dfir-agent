"""UI API: security, shared-service enforcement, idempotency, recovery, imports."""

import io
import tarfile
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support.runtime import fake_runtime
from support.scenarios import SCENARIOS
from vr_dfir.config import load_settings
from vr_dfir.web.api import create_app

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def test_preview_has_no_backend_write_or_workspace_routes(make_client):
    client, app = make_client()
    for mode in ("new_collection", "raw_vql", "notebook"):
        result = client.post("/api/v1/plans", json={"mode": mode})
        assert result.status_code == 422
    for path in ("/api/v1/collect", "/api/v1/hunts", "/api/v1/notebooks",
                 "/api/v1/triage/fetch", "/api/v1/profiles", "/api/v1/vql",
                 "/api/v1/quarantine"):
        assert client.post(path, json={}).status_code in (404, 405)
    for path in ("/api/v1/notebooks", "/api/v1/triage/baselines", "/api/v1/profiles"):
        assert client.get(path).status_code == 404
    assert app.state.vr.runtime.fake_server.launches == []


@pytest.fixture
def make_client(tmp_path, no_sleep):
    def _make(synthetic=True, env=None):
        settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "state"), **(env or {})},
                                 load_dotenv=False)
        runtime = fake_runtime(settings, state_path=tmp_path / "fake_server_state.json") \
            if synthetic else None
        app = create_app(settings, runtime=runtime)
        client = TestClient(app, base_url=BASE)
        token = client.get("/api/v1/session").json()["data"]["csrf_token"]
        client.headers.update({"X-CSRF-Token": token})
        return client, app
    return _make


def start_scenario(client, scenario, headers=None):
    """Plan and start an existing-flow investigation of a synthetic scenario via the API."""
    spec = SCENARIOS[scenario]
    plan = client.post("/api/v1/plans", json={
        "mode": "existing_flow", "org_id": spec["org_id"], "client_id": spec["client_id"],
        "flow_id": spec["flow_id"], "profile_id": spec["profile"]}).json()["data"]
    return client.post("/api/v1/investigations", json={"plan_token": plan["plan_token"]},
                       headers=headers or {})


def wait(client, inv_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/v1/investigations/{inv_id}").json()["data"]
        if not d["job_active"] and d["investigation"]["job"]["state"] not in ("queued", "running"):
            return d
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_host_origin_csrf(make_client):
    client, app = make_client()
    assert TestClient(app, base_url="http://evil.example").get("/api/v1/status").status_code == 421
    assert TestClient(app, base_url="http://localhost:18765").get("/api/v1/status").status_code == 200
    anon = TestClient(app, base_url=BASE)
    body = {"mode": "existing_flow", "client_id": "C.SYNTH0000000002", "flow_id": "F.SYNTH0002"}
    assert anon.post("/api/v1/plans", json=body).status_code == 403
    r = client.post("/api/v1/plans", json=body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "origin_rejected"
    cookie = client.get("/api/v1/session").headers.get("set-cookie", "")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    r = client.get("/api/v1/status")
    assert r.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in \
        r.headers["content-security-policy"]


def test_no_secrets_reach_browser(make_client, tmp_path):
    cfg = tmp_path / "api_client.yaml"
    cfg.write_text("client_private_key: SECRETKEYMATERIAL\n")
    client, _ = make_client(synthetic=False, env={
        "VELOCIRAPTOR_API_CONFIG": str(cfg), "VELOCIRAPTOR_MODEL_PROVIDER": "none",
        "AZURE_OPENAI_API_KEY": "sk-SECRET-AZURE", "AZURE_OPENAI_ENDPOINT": "https://x.example",
        "AZURE_OPENAI_MODEL": "m"})
    blobs = [client.get(p).text for p in ("/api/v1/status", "/api/v1/settings", "/api/v1/session")]
    blobs.append(client.post("/api/v1/status/test").text)
    for text in blobs:
        assert "sk-SECRET-AZURE" not in text and "SECRETKEYMATERIAL" not in text


def test_browser_cannot_submit_vql_or_paths(make_client):
    client, _ = make_client()
    r = client.post("/api/v1/plans", json={"mode": "existing_flow", "vql": "SELECT * FROM info()"})
    assert r.status_code == 422
    r = client.get("/api/v1/investigations/..%2F..%2Fetc/report")
    assert r.status_code in (404, 422)
    r = client.get("/api/v1/investigations/inv-20260101-000000-abc123/report", params={"format": "../x"})
    assert r.status_code in (404, 422)


def test_journey_findings_evidence_report(make_client):
    client, _ = make_client()
    r = start_scenario(client, "review", headers={"Idempotency-Key": "k1"})
    inv = r.json()["data"]["investigation_id"]
    dup = start_scenario(client, "review", headers={"Idempotency-Key": "k1"})
    assert dup.json()["data"] == {"investigation_id": inv, "duplicate": True}
    d = wait(client, inv)
    assert d["investigation"]["outcome"] == "complete" and d["actions"]["export_report"]
    f = client.get(f"/api/v1/investigations/{inv}/findings").json()["data"]
    eid = f["findings"][0]["evidence_ids"][0]
    row = client.get(f"/api/v1/investigations/{inv}/evidence/{eid}").json()["data"]
    assert row["evidence_id"] == eid and row["org_id"] == "OSYNTHLAB"
    bad = client.get(f"/api/v1/investigations/{inv}/evidence/ev-00000000000000000000")
    assert bad.status_code == 404  # no nearby row is substituted
    html = client.get(f"/api/v1/investigations/{inv}/report", params={"format": "html",
                                                                      "download": True})
    assert html.status_code == 200 and "attachment" in html.headers["content-disposition"]
    assert "sandbox" in html.headers["content-security-policy"]
    assert "font-src data:" in html.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in html.headers["content-security-policy"]
    assert html.headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get("/api/v1/status").headers["x-frame-options"] == "DENY"
    reports = client.get("/api/v1/reports").json()["data"]
    assert any(x["investigation_id"] == inv for x in reports)






def test_existing_flow_and_hunt_never_collect(make_client):
    client, app = make_client()
    for body in ({"mode": "existing_flow", "org_id": "OSYNTHLAB", "client_id": "C.SYNTH0000000003",
                  "flow_id": "F.SYNTH0003", "profile_id": "lth-baseline"},
                 {"mode": "existing_hunt", "org_id": "OSYNTHLAB", "hunt_id": "H.SYNTH0001",
                  "no_llm": True}):
        plan = client.post("/api/v1/plans", json=body).json()["data"]
        assert plan["primary_action"] == "Analyze Existing Evidence"
        assert plan["plan"]["will_start_endpoint_collection"] is False
        inv = client.post("/api/v1/investigations", json={"plan_token": plan["plan_token"]}).json()["data"]["investigation_id"]
        wait(client, inv)
    assert app.state.vr.runtime.fake_server.launches == []


def test_partial_resume_and_pagination(make_client):
    client, app = make_client()
    inv = start_scenario(client, "partial").json()["data"]["investigation_id"]
    d = wait(client, inv)
    assert d["investigation"]["retrieval_status"] == "incomplete" and d["actions"]["resume_reading"]
    src = client.get(f"/api/v1/investigations/{inv}/sources").json()["data"]["sources"]
    net = next(s for s in src if s["full_name"].endswith("NetworkConnections"))
    page = client.get(f"/api/v1/investigations/{inv}/evidence",
                      params={"source_key": net["source_key"], "offset": 450, "limit": 100}).json()["data"]
    first_ids = [r["evidence_id"] for r in page["records"]]
    assert page["total_matched"] == 500 and len(first_ids) == 50
    client.post(f"/api/v1/investigations/{inv}/resume")
    d = wait(client, inv)
    assert d["investigation"]["retrieval_status"] == "complete"
    page2 = client.get(f"/api/v1/investigations/{inv}/evidence",
                       params={"source_key": net["source_key"], "offset": 450, "limit": 100}).json()["data"]
    assert [r["evidence_id"] for r in page2["records"]][:50] == first_ids
    assert page2["total_matched"] == 1200
    search = client.get(f"/api/v1/investigations/{inv}/evidence",
                        params={"source_key": net["source_key"], "q": "10.20.1.249"}).json()["data"]
    assert search["search_scope"] == "entire persisted source" and search["total_matched"] >= 4
    assert app.state.vr.runtime.fake_server.launches == []




def test_events_reconnect_safe(make_client):
    client, _ = make_client()
    inv = start_scenario(client, "ordinary").json()["data"]["investigation_id"]
    wait(client, inv)
    ev1 = client.get(f"/api/v1/investigations/{inv}/events", params={"after": 0}).json()["data"]
    last = ev1["events"][-1]["seq"]
    ev2 = client.get(f"/api/v1/investigations/{inv}/events", params={"after": last}).json()["data"]
    assert ev2["events"] == []
    assert len(client.get("/api/v1/investigations").json()["data"]) == 1  # polling created nothing


def _tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_imports(make_client):
    client, _ = make_client()
    bundle = ROOT / "tests" / "fixtures" / "bundle"
    entries = [(f"bundle/{p.relative_to(bundle)}", p.read_bytes(), "file")
               for p in sorted(bundle.rglob("*")) if p.is_file()]
    ok = client.post("/api/v1/imports", content=_tar(entries),
                     headers={"Content-Type": "application/gzip"})
    assert ok.status_code == 201
    inv = ok.json()["data"]["investigation_id"]
    d = wait(client, inv)
    assert d["investigation"]["mode"] == "bundle" and d["investigation"]["outcome"] == "evidence_only"
    trav = client.post("/api/v1/imports", content=_tar([("../evil.txt", b"x", "file")]))
    assert trav.status_code == 422 and "Unsafe path" in trav.text
    link = client.post("/api/v1/imports", content=_tar([("bundle/link", b"", "sym")]))
    assert link.status_code == 422
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("/abs/manifest.json", "{}")
    assert client.post("/api/v1/imports", content=zbuf.getvalue()).status_code == 422
    assert client.post("/api/v1/imports", content=b"plain text").status_code == 422
    tampered = [(n, d.replace(b'"Pid":', b'"PID":') if n.endswith(".jsonl") else d, k)
                for n, d, k in entries]
    bad = client.post("/api/v1/imports", content=_tar(tampered))
    assert bad.status_code == 422 and "integrity" in bad.text


def test_import_size_limit(make_client):
    client, _ = make_client(env={"VR_DFIR_IMPORT_MAX_BYTES": "2048"})
    r = client.post("/api/v1/imports", content=b"\x1f\x8b" + b"0" * 5000)
    assert r.status_code in (413, 422)




def test_spa_fallback_serves_built_assets(make_client):
    client, _ = make_client()
    from vr_dfir.web.assets import static_dir
    found = static_dir()
    static = (found / "index.html") if found else ROOT / "missing"
    for path in ("/", "/investigations/inv-20260101-000000-abc123", "/findings/x?tab=evidence"):
        r = client.get(path)
        if static.is_file():
            assert r.status_code == 200 and '<div id="root">' in r.text
        else:
            assert r.status_code == 503
    assert client.get("/api/v1/nope").status_code == 404


def test_live_mode_without_config(make_client):
    client, _ = make_client(synthetic=False)
    st = client.get("/api/v1/status").json()["data"]
    assert st["setup_hint"] and st["velociraptor"]["configured"] is False
    assert st["velociraptor"]["synthetic"] is False
    for path in ("/api/v1/orgs", "/api/v1/clients", "/api/v1/hunts"):
        r = client.get(path)
        assert r.status_code == 503 and r.json()["error"]["code"] == "configuration_error", path
    r = client.post("/api/v1/plans", json={"mode": "existing_flow",
                                            "client_id": "C.1a", "flow_id": "F.1b"})
    assert r.status_code == 503 and r.json()["error"]["next_action"]
    # Saved evidence stays usable without Velociraptor or a model.
    bundle = ROOT / "tests" / "fixtures" / "bundle"
    entries = [(f"bundle/{p.relative_to(bundle)}", p.read_bytes(), "file")
               for p in sorted(bundle.rglob("*")) if p.is_file()]
    ok = client.post("/api/v1/imports", content=_tar(entries))
    assert ok.status_code == 201
    d = wait(client, ok.json()["data"]["investigation_id"])
    assert d["investigation"]["outcome"] == "evidence_only"
    assert client.get("/api/v1/investigations").json()["data"]




def test_no_demo_surface(make_client):
    client, _ = make_client(synthetic=False)
    assert client.post("/api/v1/demo", json={"scenario": "review"}).status_code in (404, 405)
    session = client.get("/api/v1/session").json()["data"]
    status = client.get("/api/v1/status").json()["data"]
    text = (str(session) + str(status)).lower()
    assert "demo" not in text and "data_mode" not in text


def test_full_job_queue_never_leaves_queued_job(make_client):
    client, app = make_client()
    app.state.vr.jobs.max_queued = 0
    r = start_scenario(client, "ordinary")
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_jobs"
    items = client.get("/api/v1/investigations").json()["data"]
    assert len(items) == 1 and items[0]["job"]["state"] == "not_started"
    assert items[0]["outcome"] == "failed" and app.state.vr.runtime.fake_server.launches == []
    assert items[0]["job"]["error"]


def _data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]

def test_finding_review_status_and_exact_evidence_location(make_client, tmp_path):
    api, _ = make_client()
    from test_web_api import start_scenario, wait
    inv = start_scenario(api, "review").json()["data"]["investigation_id"]
    wait(api, inv)
    findings = _data(api.get(f"/api/v1/investigations/{inv}/findings"))["findings"]
    fid = findings[0]["finding_id"]
    entry = _data(api.put(f"/api/v1/investigations/{inv}/findings/{fid}/review",
                         json={"state": "needs_follow_up", "note": "check parent"}))
    assert entry["state"] == "needs_follow_up" and entry["label"] == "Needs follow-up"
    reviews = _data(api.get(f"/api/v1/investigations/{inv}/reviews"))
    assert reviews[fid]["note"] == "check parent"
    assert api.put(f"/api/v1/investigations/{inv}/findings/{fid}/review",
                   json={"state": "malicious!"}).status_code == 422
    assert api.put(f"/api/v1/investigations/{inv}/findings/NOPE/review",
                   json={"state": "benign"}).status_code == 404
    eid = findings[0]["evidence_ids"][0]
    loc = _data(api.get(f"/api/v1/investigations/{inv}/evidence/{eid}/locate?limit=50"))
    page = _data(api.get(f"/api/v1/investigations/{inv}/evidence",
                        params={"source_key": loc["source_key"], "offset": loc["offset"],
                                "limit": 50}))
    assert eid in [r["evidence_id"] for r in page["records"]]
    assert loc["offset"] == (loc["row_index"] // 50) * 50
    missing = api.get(f"/api/v1/investigations/{inv}/evidence/ev-00000000000000000000/locate")
    assert missing.status_code == 404


def test_finding_explanation_is_explicit_and_preserves_report(make_client):
    from support.fake_model import FakeProvider
    client, app = make_client()
    inv_id = start_scenario(client, "review").json()["data"]["investigation_id"]
    wait(client, inv_id)
    inv = app.state.vr.service.store.get(inv_id)
    before = (inv.report_dir / "report.json").read_bytes()
    provider = FakeProvider()
    app.state.vr.runtime.provider_override = lambda no_llm: provider
    finding = _data(client.get(f"/api/v1/investigations/{inv_id}/findings"))["findings"][0]
    assert provider.calls == 0
    path = f"/api/v1/investigations/{inv_id}/findings/{finding['finding_id']}/explain"
    anon = TestClient(app, base_url=BASE)
    assert anon.post(path, json={}).status_code == 403
    result = _data(client.post(path, json={}))
    assert result["status"] == "ok" and result["citations_checked"]
    assert result["points"] and result["points"][0]["citations"][0]["evidence_id"] in finding["evidence_ids"]
    assert provider.calls == 1
    assert _data(client.post(path, json={}))["cached"] is True
    assert provider.calls == 1
    assert (inv.report_dir / "report.json").read_bytes() == before
    assert app.state.vr.runtime.fake_server.launches == []
