"""MCP tools: compatibility, envelopes, and stdout protocol hygiene."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vr_dfir import mcp_server as m

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ctx(tmp_path, no_sleep):
    from support.runtime import fake_runtime
    from vr_dfir.config import load_settings
    settings = load_settings({"VR_DFIR_STATE_DIR": str(tmp_path)}, load_dotenv=False)
    return m.ToolContext(settings, fake_runtime(settings))


def env(text):
    data = json.loads(text)
    assert set(data) <= {"ok", "data", "error", "meta"}
    return data


def test_registered_tool_names(ctx):
    server = m.build_server(ctx)
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert set(m.LEGACY_TOOLS) <= names and set(m.NEW_TOOLS) <= names
    dangerous = {"run_vql", "kill_process", "quarantine_host", "collect_artifact"}
    assert not names & dangerous




def test_get_collection_results_waits_for_whole_flow(ctx):
    rows = env(m.t_get_collection_results(ctx, "C.SYNTH0000000002", "F.SYNTH0002",
                                          "LTH.SystemBaseline/Users", "OSYNTHLAB", "User,Uid", 1, 0))
    assert rows["ok"] and {"User": "sysops", "Uid": 0} in rows["data"]
    from support.fake_server import FakeFlow
    flow = FakeFlow(client_id="C.SYNTH0000000001", flow_id="F.SYNTHRUN", artifacts=["Custom.Example"],
                    final_state="IN_PROGRESS", results={"Custom.Example/Inventory": [{"a": 1}]},
                    logs=[{"message": "INFO:Collection Custom.Example/Inventory is done after 1s"}])
    ctx.runtime.fake_server.flows["OSYNTHLAB"][(flow.client_id, flow.flow_id)] = flow
    res = env(m.t_get_collection_results(ctx, flow.client_id, flow.flow_id,
                                         "Custom.Example/Inventory", "OSYNTHLAB", "*", 2, 0))
    assert res["ok"] is False and "not finished" in res["error"]


def test_hunt_results_keep_provenance(ctx):
    res = env(m.t_get_hunt_results(ctx, "H.SYNTH0001", "Custom.Example/Inventory", "OSYNTHLAB",
                                   "*", 50))
    assert res["ok"] and all({"ClientId", "FlowId"} <= set(r) for r in res["data"])
    assert res["meta"]["flows_not_terminal"] == ["F.SYNTHH0104"]




def test_analysis_tools_never_collect(ctx):
    res = env(m.t_analyze_existing_flow(ctx, "C.SYNTH0000000002", "F.SYNTH0002", "OSYNTHLAB",
                                        "lth-baseline"))
    inv_id = res["data"]["investigation_id"]
    ctx.jobs[inv_id].join(60)
    info = env(m.t_get_investigation(ctx, inv_id))
    assert info["data"]["phase"] == "report_ready"
    assert ctx.runtime.fake_server.launches == []
    rep = env(m.t_get_report(ctx, inv_id, "json"))
    eid = rep["data"]["findings"][0]["evidence_ids"][0]
    row = env(m.t_get_evidence(ctx, inv_id, eid))
    assert row["data"]["evidence_id"] == eid




def _stdio_call(cmd: list[str], tmp_path) -> dict:
    """Drive a real MCP server process; every stdout line must be JSON-RPC."""
    environ = {**os.environ, "VR_DFIR_STATE_DIR": str(tmp_path),
               "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")])}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=environ, cwd=tmp_path)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "list_clients", "arguments": {"search": "."}}},
    ]
    try:
        for msg in msgs:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            proc.stdin.flush()
        lines = []
        deadline = time.time() + 60
        while time.time() < deadline and len([x for x in lines if x.get("id") == 3]) == 0:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(json.loads(line))  # raises if anything non-JSON reaches stdout
    finally:
        proc.kill()
        proc.wait()
    assert all(x.get("jsonrpc") == "2.0" for x in lines)
    result = next(x for x in lines if x.get("id") == 3)["result"]
    return json.loads(result["content"][0]["text"])


def test_stdio_stdout_is_protocol_only(tmp_path):
    payload = _stdio_call([sys.executable, str(ROOT / "tests" / "support" / "mcp_stdio.py")],
                          tmp_path)
    assert payload["ok"] and payload["data"]


def test_stdio_without_api_config_reports_configuration_error(tmp_path):
    payload = _stdio_call([sys.executable, "-m", "vr_dfir.mcp_server"], tmp_path)
    assert payload["ok"] is False
    assert "VELOCIRAPTOR_API_CONFIG" in json.dumps(payload)
