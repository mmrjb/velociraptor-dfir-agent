"""The wheel must ship the built UI, profiles, and schema, and nothing from node_modules."""

import runpy
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "vr_dfir" / "web" / "static" / "index.html"


@pytest.mark.skipif(not STATIC.is_file(), reason="frontend not built (run the frontend build)")
def test_wheel_contents(tmp_path):
    result = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation",
                             "--outdir", str(tmp_path), str(ROOT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    wheel = next(tmp_path.glob("velociraptor_dfir_agent-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert "vr_dfir/web/static/index.html" in names
    assert any(n.startswith("vr_dfir/web/static/assets/") and n.endswith(".js") for n in names)
    assert any(n.startswith("vr_dfir/web/static/assets/") and n.endswith(".css") for n in names)
    assert not any("/data/profiles/" in name for name in names)
    assert not any(name.startswith(("vr_dfir/triage/", "vr_dfir/notebooks/", "vr_dfir/collection/")) for name in names)
    assert "vr_dfir/data/schemas/profile.schema.json" in names
    assert "vr_dfir/data/THIRD_PARTY_NOTICES.md" in names
    assert "vr_dfir/data/NOTICE" in names
    assert "vr_dfir/reporting/font_notices.txt" in names
    assert not any("node_modules" in n or n.endswith((".env", ".pem")) for n in names)
    assert not any("api_client" in n for n in names)
    # Synthetic fixtures and the fake server/model live in tests/ only.
    assert not any(n.startswith(("tests/", "examples/")) or "/demo" in n or "fake" in n
                   for n in names), [n for n in names if "fake" in n or "demo" in n]
    entry = [n for n in names if n.endswith("entry_points.txt")]
    text = zipfile.ZipFile(wheel).read(entry[0]).decode()
    assert "vr-dfir = vr_dfir.cli:main" in text and "vr-dfir-mcp" in text


def test_no_demo_mode_in_production_code():
    """Production code has no demo command, endpoint, runtime, or synthetic fallback."""
    src = ROOT / "src" / "vr_dfir"
    text = "\n".join(p.read_text(encoding="utf-8") for p in src.rglob("*.py"))
    for needle in ("--demo", "/api/v1/demo", "demo_runtime", "demo_only", "FakeServer",
                   "FakeProvider", "DEMO_ALLOWLIST", "build_fake_server"):
        assert needle not in text, needle
    assert not (src / "demo").exists() and not (src / "velociraptor" / "fake.py").exists()
    assert not (ROOT / "examples").exists()


def _release_builder():
    return runpy.run_path(str(ROOT / "scripts" / "build_preview.py"))


def test_release_lock_pins_every_entry_by_hash():
    build = _release_builder()
    text = build["lock_text"]([("pip", "26.2.1", "a" * 64),
                               ("velociraptor-dfir-agent[mcp,ui]", "0.5.0a1", "b" * 64),
                               ("Anyio", "4.15.1", "c" * 64)])
    entries = [line for line in text.splitlines() if line and not line.startswith(("#", " "))]
    assert entries == ["Anyio==4.15.1 \\", "pip==26.2.1 \\",
                       "velociraptor-dfir-agent[mcp,ui]==0.5.0a1 \\"]
    assert text.count("--hash=sha256:") == 3 and "--hash=sha256:" + "b" * 64 in text
    with pytest.raises(ValueError):
        build["lock_text"]([("x", "1", "not-a-digest")])


def test_release_resolution_accepts_only_local_wheels(tmp_path):
    build = _release_builder()
    wheel = tmp_path / "pkg-1.0-py3-none-any.whl"
    local = {"install": [{"download_info": {"url": wheel.as_uri()},
                          "metadata": {"name": "pkg", "version": "1.0"}}]}
    assert build["resolved_wheels"](local) == [("pkg", "1.0", wheel)]
    for url in ("https://example.invalid/pkg-1.0-py3-none-any.whl",
                (tmp_path / "pkg-1.0.tar.gz").as_uri()):
        remote = {"install": [{"download_info": {"url": url},
                               "metadata": {"name": "pkg", "version": "1.0"}}]}
        with pytest.raises(SystemExit):
            build["resolved_wheels"](remote)


def test_installer_uses_only_the_hash_pinned_offline_set():
    text = (ROOT / "scripts" / "install-preview.sh").read_text()
    for flag in ("--without-pip", "--no-index", "--require-hashes", "--only-binary=:all:",
                 "--isolated", "--no-cache-dir", "requirements-cp312.txt",
                 'preflight.py" --phase install'):
        assert flag in text, flag
    assert "--index-url" not in text and "--extra-index-url" not in text
