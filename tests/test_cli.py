"""CLI commands (in-process). stdout carries the result; diagnostics go to stderr.

Commands that need Velociraptor run against the synthetic test server by replacing
``Context.runtime`` (the CLI's only runtime seam); production code has no synthetic mode.
"""

import json
from pathlib import Path

import pytest

from vr_dfir.cli import (
    EXIT_CONFIG,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "tests" / "fixtures" / "bundle"
ORG = "OSYNTHLAB"


@pytest.fixture
def run(tmp_path, capsys, monkeypatch, no_sleep):
    monkeypatch.chdir(tmp_path)

    def _run(*args):
        try:
            code = main(list(args))
        except SystemExit as exc:  # argparse usage errors and --help
            code = exc.code
        out = capsys.readouterr()
        return code, out.out, out.err
    return _run


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    """Serve CLI Velociraptor calls from the synthetic test server (launches persist)."""
    from support.runtime import TEST_ALLOWLIST, fake_runtime
    from vr_dfir import cli
    state = {"allowlist": TEST_ALLOWLIST, "runtimes": []}

    def runtime(self):
        if self._runtime is None:
            self._runtime = fake_runtime(self.settings, state_path=tmp_path / "fake_state.json",
                                         allowlist=state["allowlist"])
            state["runtimes"].append(self._runtime)
        return self._runtime

    monkeypatch.setattr(cli.Context, "runtime", runtime)
    state["launches"] = lambda: len(state["runtimes"][-1].fake_server.launches) \
        if state["runtimes"] else 0
    return state


def js(out):
    return json.loads(out)


# ------------------------------------------------------------------ setup and errors


def test_doctor_offline(run, tmp_path):
    code, out, _ = run("doctor", "--json", "--state-dir", str(tmp_path / "s"),
                       "--api-timeout", "30")
    data = js(out)
    assert code == EXIT_OK and data["result"] in ("ok", "warn")
    names = {c["name"] for c in data["checks"]}
    assert {"python", "velociraptor_api_config", "model", "backend_access"} <= names
    assert data["settings"]["velociraptor"]["api_timeout_seconds"] == 30
    assert "PRIVATE KEY" not in out


def test_doctor_reports_bad_config_without_secrets(run, tmp_path, monkeypatch):
    cfg = tmp_path / "api.yaml"
    cfg.write_text("client_private_key: |\n  -----BEGIN PRIVATE KEY-----\n  secretvalue\n")
    monkeypatch.setenv("VELOCIRAPTOR_API_CONFIG", str(cfg))
    code, out, _ = run("doctor", "--json", "--state-dir", str(tmp_path / "s"))
    assert code == EXIT_CONFIG and "secretvalue" not in out and "missing fields" in out


def test_demo_is_gone(run, tmp_path):
    assert run("demo")[0] == EXIT_USAGE
    assert run("artifacts", "list", "--demo")[0] == EXIT_USAGE
    assert run("ui", "--demo")[0] == EXIT_USAGE
    code, out, _ = run("--help")
    assert code == EXIT_OK and "demo" not in out.lower() and "Exit codes" in out


def test_usage_errors_exit_2(run):
    assert run("evidence", "rows", "inv-20260101-000000-abc123", "--source", "x",
               "--limit", "0")[0] == EXIT_USAGE
    assert run("doctor", "--api-timeout", "1")[0] == EXIT_USAGE
    assert run("collect", "--profile", "x", "--wait-timeout", "abc")[0] == EXIT_USAGE


def test_live_commands_without_config_fail_cleanly(run, tmp_path):
    s = str(tmp_path / "s")
    for args in (("clients", "search", "web"), ("hunts", "list"),
                 ("flows", "list", "--client-id", "C.1a2b3c"), ("orgs", "list"),
                 ("analyze", "flow", "--client-id", "C.1a2b3c", "--flow-id", "F.ABC")):
        code, out, err = run(*args, "--state-dir", s)
        assert code == EXIT_CONFIG, args
        assert "VELOCIRAPTOR_API_CONFIG" in err and "Traceback" not in err and out == ""
    code, out, _ = run("investigations", "list", "--state-dir", s)
    assert code == EXIT_OK and "No investigations found." in out


def test_json_errors_on_stdout_and_diagnostics_on_stderr(run, tmp_path):
    code, out, err = run("investigations", "show", "inv-20260101-000000-abc123", "--json",
                         "--state-dir", str(tmp_path))
    assert code == EXIT_FAIL and js(out)["error"]["code"] == "not_found"
    assert err.startswith("Error:")


# ------------------------------------------------------------------ profiles






def test_ui_refuses_non_loopback(run, tmp_path):
    code, _, err = run("ui", "--host", "0.0.0.0", "--state-dir", str(tmp_path))
    assert code == EXIT_CONFIG and "SSH port forwarding" in err


# ------------------------------------------------------------------ discovery




# ------------------------------------------------------------------ collection policy














def test_findings_commands(run, tmp_path, synthetic):
    s = str(tmp_path / "s")
    code, out, _ = run("analyze", "flow", "--state-dir", s, "--client-id", "C.SYNTH0000000002",
                       "--flow-id", "F.SYNTH0002", "--org-id", ORG, "--json")
    assert code == EXIT_OK and js(out)["investigation"]["collection_status"] == "not_applicable"
    inv = js(out)["investigation"]["investigation_id"]
    code, out, _ = run("findings", "list", inv, "--state-dir", s)
    assert code == EXIT_OK and "FINDING" in out and "not evidence that the target is clean" in out
    assert "No accepted findings" not in out
    code, out, _ = run("findings", "list", inv, "--state-dir", s, "--json")
    findings = js(out)["findings"]
    assert findings and all(f["evidence_ids"] for f in findings)
    code, out, _ = run("findings", "list", inv, "--origin", "deterministic", "--json",
                       "--state-dir", s)
    assert all(f["origin"] == "deterministic_rule" for f in js(out)["findings"])
    fid = findings[0]["finding_id"]
    code, out, _ = run("findings", "show", inv, fid, "--state-dir", s)
    assert code == EXIT_OK and fid in out and findings[0]["evidence_ids"][0] in out
    assert "UNRESOLVED" not in out
    code, out, _ = run("findings", "show", inv, "M-999", "--state-dir", s)
    assert code == EXIT_FAIL
    assert synthetic["launches"]() == 0


def test_analyze_hunt_and_bundle(run, tmp_path, synthetic):
    s = str(tmp_path / "s")
    code, out, _ = run("analyze", "hunt", "--state-dir", s, "--hunt-id", "H.SYNTH0001",
                       "--org-id", ORG, "--no-llm", "--json")
    assert code == EXIT_PARTIAL
    code, out, _ = run("analyze", "bundle", "--state-dir", s, "--input", str(BUNDLE), "--no-llm",
                       "--output", str(tmp_path / "reports"), "--json")
    assert code == EXIT_OK and js(out)["investigation"]["outcome"] == "evidence_only"
    assert synthetic["launches"]() == 0


def test_bundle_analysis_and_reports_without_velociraptor(run, tmp_path):
    s = str(tmp_path / "s")
    code, out, _ = run("analyze", "bundle", "--state-dir", s, "--input", str(BUNDLE), "--json")
    data = js(out)
    # The default model (Ollama) is not reachable here: evidence stays usable, the run is
    # reported as partial, and nothing is fabricated.
    assert code == EXIT_PARTIAL and data["investigation"]["analysis_status"] == "failed"
    inv = data["investigation"]["investigation_id"]
    code, out, _ = run("findings", "list", inv, "--state-dir", s, "--json")
    assert js(out)["status"]["analysis"] == "failed"
    assert all(f["origin"] == "deterministic_rule" for f in js(out)["findings"])
    code, out, _ = run("evidence", "sources", inv, "--state-dir", s)
    assert code == EXIT_OK and "ARTIFACT/SOURCE" in out


