"""Configuration loader: inaccessible implicit fallbacks and unreadable explicit files.

Regression for the 0.2.0 defect: running ``vr-dfir`` as the service account from a
root-private working directory raised ``PermissionError`` while probing the implicit
``./.env`` even though a valid ``--env-file`` was given.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from vr_dfir import config
from vr_dfir.cli import main
from vr_dfir.errors import ConfigurationError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("VELOCIRAPTOR_", "VR_DFIR_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "SKIPPED_IMPLICIT", [])
    monkeypatch.setattr(config, "_ENV_LOADED", False)


def _deny(monkeypatch, denied: Path):
    """Make stat() of ``denied`` fail as it does for a non-root user in a 0700 directory."""
    real_stat = pathlib.Path.stat

    def fake_stat(self, *args, **kwargs):
        if Path(os.path.abspath(self)) == denied:
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)


def test_inaccessible_implicit_env_is_skipped_with_valid_explicit_file(tmp_path, monkeypatch,
                                                                       capsys):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / ".env").write_text("VR_DFIR_UI_PORT=1\n")
    env_file = tmp_path / "vr-dfir.env"
    env_file.write_text(f"VR_DFIR_STATE_DIR={tmp_path / 'state'}\n"
                        "VELOCIRAPTOR_MODEL_PROVIDER=none\n")
    monkeypatch.chdir(staging)
    _deny(monkeypatch, staging / ".env")

    code = main(["investigations", "list", "--env-file", str(env_file), "--json"])

    out = capsys.readouterr()
    assert code == 0, out.err
    assert "Traceback" not in out.err
    assert json.loads(out.out) == {"investigations": []}
    assert os.environ["VR_DFIR_STATE_DIR"] == str(tmp_path / "state")
    assert "VR_DFIR_UI_PORT" not in os.environ  # nothing read from the skipped fallback
    assert config.SKIPPED_IMPLICIT and config.SKIPPED_IMPLICIT[0][0].endswith(".env")


def test_unreadable_explicit_env_file_is_a_configuration_error(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "private.env"
    env_file.write_text("VR_DFIR_STATE_DIR=/nowhere\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "probe_file",
                        lambda p: "unreadable" if Path(p) == env_file else "missing")

    code = main(["investigations", "list", "--env-file", str(env_file), "--json"])

    out = capsys.readouterr()
    assert code == 4
    body = json.loads(out.out)
    assert body["ok"] is False and body["error"]["code"] == "configuration_error"
    assert "not readable" in body["error"]["message"]
    assert "Traceback" not in out.err and "not readable" in out.err


def test_missing_explicit_env_file_is_a_configuration_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main(["investigations", "list", "--env-file", str(tmp_path / "missing.env")])
    err = capsys.readouterr().err
    assert code == 4
    assert "does not exist" in err and "Traceback" not in err


def test_explicit_file_keeps_environment_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / "a.env"
    env_file.write_text("VR_DFIR_UI_PORT=9001\nVR_DFIR_STATE_DIR=/from-file\n")
    (tmp_path / ".env").write_text("VR_DFIR_UI_PORT=9002\nVR_DFIR_GRPC_TIMEOUT_SECONDS=33\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VR_DFIR_STATE_DIR", "/from-environment")
    monkeypatch.setenv("VELOCIRAPTOR_ENV_FILE", str(env_file))
    config.load_environment(force=True)
    settings = config.load_settings(load_dotenv=False)
    assert str(settings.state_dir) == "/from-environment"  # process environment wins
    assert settings.ui_port == 9001                          # explicit file beats ./.env
    assert settings.grpc_timeout_seconds == 33               # ./.env still fills the rest


def test_load_env_file_explicit_raises_but_implicit_does_not(tmp_path, monkeypatch):
    target = tmp_path / "x.env"
    target.write_text("A=1\n")
    _deny(monkeypatch, target)
    assert config.load_env_file(target, {}) == []
    with pytest.raises(ConfigurationError) as info:
        config.load_env_file(target, {}, explicit=True)
    assert "cannot access" in info.value.message


def test_inaccessible_implicit_api_config_is_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api_client.yaml").write_text("x: 1\n")
    _deny(monkeypatch, tmp_path / "api_client.yaml")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path, source = config.resolve_api_config({})
    assert (path, source) == ("", "unset")
    assert any(p.endswith("api_client.yaml") for p, _ in config.SKIPPED_IMPLICIT)


def test_probe_file_states(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    assert config.probe_file(f) == "file"
    assert config.probe_file(tmp_path / "missing") == "missing"
    assert config.probe_file(tmp_path) == "not_file"
    assert config.probe_file(f / "below-a-file") == "missing"


def _unprivileged_python() -> list[str] | None:
    """A command prefix that runs Python as ``nobody`` with this checkout importable."""
    if os.geteuid() != 0 or not shutil.which("runuser"):
        return None
    for python in (sys.executable, "/usr/bin/python3"):
        cmd = ["runuser", "-u", "nobody", "--", "env", f"PYTHONPATH={ROOT / 'src'}", python]
        probe = subprocess.run(cmd + ["-c", "import vr_dfir.cli"], capture_output=True,
                               cwd="/", timeout=60)
        if probe.returncode == 0:
            return cmd
    return None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux permission semantics")
def test_service_account_in_root_private_directory_real_permissions():
    """End to end with real permissions: needs root (to create the directory) and runuser."""
    cmd = _unprivileged_python()
    if cmd is None:
        pytest.skip("needs root, runuser, and a checkout readable by 'nobody'")
    base = Path(tempfile.mkdtemp(prefix="vrdfir-cfg-", dir="/tmp"))
    try:
        base.chmod(0o755)
        private = base / "staging"
        private.mkdir(mode=0o700)
        (private / ".env").write_text("VR_DFIR_UI_PORT=1\n")
        state = base / "state"
        state.mkdir(mode=0o777)
        state.chmod(0o777)
        good = base / "vr-dfir.env"
        good.write_text(f"VR_DFIR_STATE_DIR={state}\nVELOCIRAPTOR_MODEL_PROVIDER=none\n")
        good.chmod(0o644)
        secret = base / "secret.env"
        secret.write_text("VR_DFIR_STATE_DIR=/x\n")
        secret.chmod(0o600)
        script = ("import sys; from vr_dfir.cli import main; "
                  "raise SystemExit(main(sys.argv[1:]))")

        ok = subprocess.run(cmd + ["-c", script, "investigations", "list", "--env-file",
                                   str(good)], cwd=private, capture_output=True, text=True,
                            timeout=60)
        assert ok.returncode == 0, ok.stderr
        assert "Traceback" not in ok.stderr and "PermissionError" not in ok.stderr

        denied = subprocess.run(cmd + ["-c", script, "investigations", "list", "--env-file",
                                       str(secret)], cwd=private, capture_output=True,
                                text=True, timeout=60)
        assert denied.returncode == 4
        assert "not readable by this account" in denied.stderr
        assert "Traceback" not in denied.stderr
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600  # permissions untouched
    finally:
        shutil.rmtree(base, ignore_errors=True)
