"""The frontend's TypeScript API types must match real API responses.

Interfaces marked ``// CONTRACT: <name>`` in frontend/src/api/types.ts are parsed and every
required (non-optional) top-level field is checked against a response from the API served
over the synthetic test runtime.
"""

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support.runtime import fake_runtime
from vr_dfir.config import load_settings
from vr_dfir.web.api import create_app

ROOT = Path(__file__).resolve().parents[1]
TYPES = ROOT / "frontend" / "src" / "api" / "types.ts"


def contract_fields() -> dict[str, list[str]]:
    text = TYPES.read_text()
    out = {}
    for m in re.finditer(r"// CONTRACT: (\w+)\nexport interface \w+(?: extends [\w, ]+)? \{", text):
        depth, pos, fields = 1, m.end(), []
        line_start = pos
        while depth and pos < len(text):
            ch = text[pos]
            if ch == "{":
                if depth == 1:
                    segment = text[line_start:pos]
                    fm = re.match(r"\s*(\w+)(\??):", segment.splitlines()[-1] if segment else "")
                    if fm and not fm.group(2):
                        fields.append(fm.group(1))
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "\n" and depth == 1:
                line = text[line_start:pos]
                fm = re.match(r"\s*(\w+)(\??):", line)
                if fm and not fm.group(2) and fm.group(1) not in fields:
                    fields.append(fm.group(1))
                line_start = pos + 1
            pos += 1
        out[m.group(1)] = fields
    return out


@pytest.fixture()
def samples(tmp_path):
    settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "state")}, load_dotenv=False)
    app = create_app(settings, runtime=fake_runtime(settings))
    c = TestClient(app, base_url="http://127.0.0.1:8766")
    session = c.get("/api/v1/session").json()["data"]
    c.headers["X-CSRF-Token"] = session["csrf_token"]
    review = c.post("/api/v1/plans", json={"mode": "existing_flow", "org_id": "OSYNTHLAB",
        "client_id": "C.SYNTH0000000002", "flow_id": "F.SYNTH0002"}).json()["data"]
    inv = c.post("/api/v1/investigations", json={"plan_token": review["plan_token"]}).json()["data"]["investigation_id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        d = c.get(f"/api/v1/investigations/{inv}").json()["data"]
        if not d["job_active"]:
            break
        time.sleep(0.01)
    findings = c.get(f"/api/v1/investigations/{inv}/findings").json()["data"]
    eid = findings["findings"][0]["evidence_ids"][0]
    return {
        "session": session,
        "status": c.get("/api/v1/status").json()["data"],
        "client": c.get("/api/v1/clients", params={"org_id": "OSYNTHLAB"}).json()["data"]["clients"][0],
        "plan": review["plan"],
        "investigation": c.get("/api/v1/investigations").json()["data"][0],
        "finding": findings["findings"][0],
        "coverage_source": findings["coverage"]["sources"][0],
        "evidence_record": c.get(f"/api/v1/investigations/{inv}/evidence/{eid}").json()["data"],
    }


def test_contract_parser_finds_all():
    fields = contract_fields()
    assert set(fields) == {"session", "status", "client", "plan", "investigation", "finding", "coverage_source", "evidence_record"}
    assert "csrf_token" in fields["session"] and "evidence_ids" in fields["finding"]


@pytest.mark.parametrize("name", ["session", "status", "client", "plan", "investigation", "finding", "coverage_source", "evidence_record"])
def test_typescript_contract_matches_api(name, samples):
    required = contract_fields()[name]
    missing = [f for f in required if f not in samples[name]]
    assert not missing, f"{name}: API response lacks fields declared in types.ts: {missing}"
