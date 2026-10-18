"""Verify an extracted preview in a new offline venv, without any server or model.

python scripts/verify_preview.py RELEASE_DIRECTORY NEW_WORK_DIRECTORY
The work directory must not exist. Nothing is installed system-wide.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path


async def mcp_check(executable, env, cwd):
    process = await asyncio.create_subprocess_exec(str(executable), cwd=cwd, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "preview-verification", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        for message in messages:
            process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
            assert line, "MCP closed before tools/list"
            data = json.loads(line)
            assert data["jsonrpc"] == "2.0", data
            if data.get("id") == 2:
                names = {tool["name"] for tool in data["result"]["tools"]}
                assert len(names) == 16
                assert not names & {"start_profile", "collect_artifact", "run_vql"}
                break
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    release, work = args.release.resolve(), args.work.resolve()
    work.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(
        ("VELOCIRAPTOR_", "VR_DFIR_", "OLLAMA_", "AZURE_OPENAI"))
        and k not in ("PYTHONPATH", "ENABLE_DANGEROUS_TOOLS")}
    install = work / "installed"
    subprocess.run(["bash", str(release / "install-preview.sh"), str(install)],
                   check=True, env=env, cwd=work, timeout=60)
    env["VELOCIRAPTOR_ENV_FILE"] = str(install / "preview.env")
    vr = install / "venv/bin/vr-dfir"

    def cli(*arguments, code=0):
        result = subprocess.run([str(vr), *arguments], env=env, cwd=work,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == code, result.stdout + result.stderr
        return result.stdout

    assert "0.4.0a2" in cli("--version")
    assert json.loads(cli("doctor", "--json"))["result"] in ("ok", "warn")
    for removed in ("collect", "triage", "notebooks", "profiles", "demo"):
        cli(removed, code=2)
    cli("clients", "search", "web", code=4)
    source = release / "source/velociraptor-dfir-agent-src.tar.gz"
    with tarfile.open(source) as archive:
        members = [m for m in archive.getmembers()
                   if m.name.startswith("velociraptor-dfir-agent/tests/fixtures/bundle/")]
        archive.extractall(work, members=members, filter="data")
    bundle = work / "velociraptor-dfir-agent/tests/fixtures/bundle"
    result = json.loads(cli("analyze", "bundle", "--input", str(bundle), "--no-llm", "--json", "-q"))
    inv = result["investigation"]["investigation_id"]
    assert result["investigation"]["outcome"] == "evidence_only"
    findings = json.loads(cli("findings", "list", inv, "--json"))
    assert findings["findings"]
    cli("evidence", "sources", inv)
    cli("report", "--id", inv, "--format", "html", "--output", str(work / "report.html"))
    asyncio.run(mcp_check(install / "venv/bin/vr-dfir-mcp", env, work))
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    with (work / "ui.log").open("wb") as log:
        server = subprocess.Popen([str(vr), "ui", "--port", str(port)],
                                  env=env, cwd=work, stdout=log, stderr=log)
        try:
            base = f"http://127.0.0.1:{port}"

            def get(path):
                # Both URL parts are fixed loopback/test paths, never external input.
                with urllib.request.urlopen(base + path, timeout=3) as response:  # noqa: S310
                    return response.read()

            deadline = time.monotonic() + 15
            while True:
                try:
                    status = json.loads(get("/api/v1/status"))["data"]
                    break
                except urllib.error.URLError:
                    if time.monotonic() > deadline or server.poll() is not None:
                        raise
                    time.sleep(0.1)
            assert status["backend_access"] == "read_only" and status["version"] == "0.4.0a2"
            assert status["model"]["provider"] == "none"
            html = get("/").decode()
            asset = re.search(r'src="(/assets/[^\"]+\.js)"', html).group(1)
            assert len(get(asset)) > 1000
            get("/investigations/" + inv)
            assert json.loads(get("/api/v1/investigations"))["data"]
            try:
                get("/api/v1/notebooks")
                raise AssertionError("Removed notebook API is still available")
            except urllib.error.HTTPError as error:
                assert error.code == 404
        finally:
            server.terminate()
            server.wait(timeout=15)
    print(f"PASS: offline install, CLI, evidence-only bundle ({len(findings['findings'])} findings), "
          "report, 16-tool MCP stdio, built UI and read-only API")


if __name__ == "__main__":
    main()
