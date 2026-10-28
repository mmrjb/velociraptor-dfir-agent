"""Installation preflight: listener parsing, port conflicts, paths, gateway scan, safe output."""

import io
import os
import runpy
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PF = runpy.run_path(str(ROOT / "scripts" / "preflight.py"), run_name="preflight")


def report():
    return PF["Report"](out=io.StringIO())


def statuses(rep, check):
    return [status for status, name, _ in rep.items if name == check]


def test_proc_net_listeners_ipv4_ipv6_and_mapped():
    v4 = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  "
          "timeout inode\n"
          "   0: 0100007F:223E 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1002 "
          "0 4242 1 0\n"
          "   1: 0A0200C0:223E 00000000:0000 0A 00000000:00000000 00:00000000 00000000    33 "
          "0 4243 1 0\n"
          "   2: 0100007F:9C40 0100007F:223E 01 00000000:00000000 00:00000000 00000000  1002 "
          "0 4244 1 0\n")
    found = PF["parse_proc_net"](v4, False)
    assert [(item["addr"], item["port"], item["uid"]) for item in found] == [
        ("127.0.0.1", 8766, 1002), ("192.0.2.10", 8766, 33)]  # established row ignored
    v6 = ("  sl  local_address remote_address st ...\n"
          "   0: 00000000000000000000000000000000:223D 00000000000000000000000000000000:0000 0A "
          "00000000:00000000 00:00000000 00000000 0 0 5000 1 0\n"
          "   1: 0000000000000000FFFF00000100007F:223E 00000000000000000000000000000000:0000 0A "
          "00000000:00000000 00:00000000 00000000 0 0 5001 1 0\n"
          "   2: 00000000000000000000000001000000:223E 00000000000000000000000000000000:0000 0A "
          "00000000:00000000 00:00000000 00000000 0 0 5002 1 0\n")
    assert [(i["addr"], i["port"]) for i in PF["parse_proc_net"](v6, True)] == [
        ("::", 8765), ("127.0.0.1", 8766), ("::1", 8766)]


def _listener(addr="127.0.0.1"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((addr, 0))
    sock.listen(1)
    return sock


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_free_port_passes_and_held_port_fails_with_owner():
    rep = report()
    port = _free_port()
    PF["check_port"](rep, [], port, "preview", "fresh", "vr-dfir-ai-preview")
    assert statuses(rep, "port.preview") == ["PASS"]
    with _listener() as held:
        busy = held.getsockname()[1]
        rep = report()
        PF["check_port"](rep, PF["socket_listeners"](), busy, "staging", "fresh",
                         "vr-dfir-ai-preview")
    assert statuses(rep, "port.staging") == ["FAIL"]
    assert f"127.0.0.1:{busy}" in rep.items[-1][2] and "pid" in rep.items[-1][2]


def test_existing_preview_may_hold_its_port_only_in_upgrade_mode():
    with _listener() as held:
        port = held.getsockname()[1]
        owner = [{"addr": "127.0.0.1", "port": port, "uid": os.getuid(), "inode": 1,
                  "pids": [os.getpid()], "units": ["vr-dfir-ai-preview.service"],
                  "comm": ["vr-dfir"]}]
        for mode, expected in (("upgrade", "PASS"), ("fresh", "FAIL")):
            rep = report()
            PF["check_port"](rep, owner, port, "preview", mode, "vr-dfir-ai-preview")
            assert statuses(rep, "port.preview") == [expected], mode
        rep = report()
        PF["check_port"](rep, owner, port, "staging", "upgrade", "vr-dfir-ai-preview")
        assert statuses(rep, "port.staging") == ["FAIL"] and "--staging-port" in rep.items[0][2]
        wildcard = [dict(owner[0], addr="0.0.0.0", units=["nginx.service"])]
        rep = report()
        PF["check_port"](rep, wildcard, port, "preview", "upgrade", "vr-dfir-ai-preview")
        assert statuses(rep, "port.preview") == ["FAIL"] and "wildcard" in rep.items[0][2]


def test_gateway_on_a_specific_address_does_not_conflict():
    try:
        gateway = _listener("127.0.0.2")
    except OSError:
        pytest.skip("127.0.0.2 is not available on this host")
    with gateway:
        port = gateway.getsockname()[1]
        rep = report()
        PF["check_port"](rep, PF["socket_listeners"](), port, "preview", "fresh",
                         "vr-dfir-ai-preview")
    assert statuses(rep, "port.preview") == ["INFO", "PASS"]
    assert "specific address" in rep.items[0][2]


def test_gateway_scan_flags_wildcard_and_missing_origin(tmp_path):
    good = tmp_path / "good.conf"
    good.write_text('map x $y {\n    "https://192.0.2.10:8766|8766" 1;\n}\n'
                    "server {\n    listen 192.0.2.10:8766 ssl;\n"
                    "    # listen 192.0.2.10:8765 ssl;\n}\n")
    rep = report()
    PF["scan_gateway"](rep, 8766, files=[str(good)])
    assert statuses(rep, "gateway") == ["INFO"] and "good.conf:5" in rep.items[0][2]
    bad = tmp_path / "bad.conf"
    bad.write_text("server {\n    listen 8766 ssl;\n    listen 192.0.2.10:8766 ssl;\n}\n")
    rep = report()
    PF["scan_gateway"](rep, 8766, files=[str(bad)])
    assert statuses(rep, "gateway") == ["WARN", "INFO", "WARN"]
    assert "wildcard" in rep.items[0][2] and "Origin map" in rep.items[2][2]


def test_mount_lookup_prefers_the_longest_mount_and_decodes_spaces(tmp_path):
    mounts = ("/dev/root / xfs rw,relatime 0 0\n"
              "tmpfs /tmp tmpfs rw,nosuid,nodev,noexec 0 0\n"
              "/dev/sdb1 /srv/with\\040space ext4 rw 0 0\n")
    assert PF["mount_for"]("/tmp/x/y", mounts) == ("/tmp", ["rw", "nosuid", "nodev", "noexec"])
    assert PF["mount_for"]("/opt/new/dir", mounts)[0] == "/"
    assert PF["mount_for"]("/tmpfoo", mounts)[0] == "/"


def test_permission_model_for_another_account(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (shared / "file").write_text("x")
    (shared / "file").chmod(0o644)
    other = os.getuid() + 12345
    assert PF["may_access"](str(shared), other, set(), "rx")
    assert not PF["may_access"](str(shared), other, set(), "w")
    assert not PF["may_access"](str(private), other, set(), "x")
    assert PF["may_access"](str(shared / "file"), other, set(), "r")
    assert PF["traversable"]("/usr/bin/env", other, set()) is None
    os.chmod(tmp_path, 0o700)
    try:
        blocked = PF["traversable"](str(shared), other, set())
        # The first ancestor (from /) that the account cannot enter; pytest's base may be first.
        assert blocked is not None and str(tmp_path).startswith(blocked)
    finally:
        os.chmod(tmp_path, 0o755)


def test_environment_reader_returns_only_allow_listed_keys(tmp_path):
    env = tmp_path / "preview.env"
    env.write_text("VR_DFIR_STATE_DIR=/opt/p/state\nSECRET_TOKEN=do-not-print\n"
                   "# VELOCIRAPTOR_ORG_ID=commented\nOLLAMA_MODEL='llama3'\n")
    assert PF["read_env_keys"](str(env)) == {"VR_DFIR_STATE_DIR": "/opt/p/state",
                                             "OLLAMA_MODEL": "llama3"}


def test_output_is_inert_single_line_data():
    assert PF["clean"]("a\nb\x1b[31m") == "a\\x0ab\\x1b[31m"
    assert PF["clean"]("x" * 300).endswith("...") and len(PF["clean"]("x" * 300)) == 203
    info = PF["parse_os_release"]('ID=ubuntu\nVERSION_ID="24.04"\n# c\nPRETTY_NAME=\'U\'\n')
    assert info == {"ID": "ubuntu", "VERSION_ID": "24.04", "PRETTY_NAME": "U"}


def test_exec_start_options_from_systemctl_show():
    value = ("{ path=/opt/p/venv/bin/vr-dfir ; argv[]=/opt/p/venv/bin/vr-dfir ui --env-file "
             "/opt/p/preview.env --host 127.0.0.1 --port 8766 ; ignore_errors=no ; }")
    assert PF["exec_option"](value, "--env-file") == "/opt/p/preview.env"
    assert PF["exec_option"](value, "--port") == "8766"
    assert PF["exec_option"](value, "--state-dir") is None


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_RELEASE_DIR"),
                    reason="set PREFLIGHT_RELEASE_DIR to an extracted release on an exec filesystem")
def test_install_phase_on_a_built_release():
    release = Path(os.environ["PREFLIGHT_RELEASE_DIR"]).resolve()
    dest = release.parent / f"preflight-test-{uuid.uuid4().hex}"
    result = subprocess.run([sys.executable, "-I", str(release / "preflight.py"), "--phase",
                             "install", "--dest", str(dest)], capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS] release.deps" in result.stdout and "[PASS] release.files" in result.stdout
    assert not dest.exists()


def test_preview_must_not_share_state_or_directory_with_the_internal_ui(tmp_path, monkeypatch):
    env = tmp_path / "internal.env"
    env.write_text(f"VR_DFIR_STATE_DIR={tmp_path / 'state'}\n")
    (tmp_path / "state").mkdir()
    props = {"LoadState": "loaded", "WorkingDirectory": str(tmp_path),
             "ExecStart": f"argv[]=/x/vr-dfir ui --env-file {env} --port 8765 ;"}
    # run_path returns a copy of the module globals; patch the ones the function uses.
    monkeypatch.setitem(PF["check_internal_overlap"].__globals__, "systemd_show",
                        lambda unit: props if unit == "velociraptor-dfir-agent" else {})
    rep = report()
    PF["check_internal_overlap"](rep, str(tmp_path / "state"), str(tmp_path / "new"))
    assert [(s, c) for s, c, _ in rep.items] == [("FAIL", "existing.state"), ("FAIL", "dest")]
    rep = report()
    PF["check_internal_overlap"](rep, str(tmp_path / "other"), "/opt/vr-dfir-ai-preview/x")
    assert rep.items == []
