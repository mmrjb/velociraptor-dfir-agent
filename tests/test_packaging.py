"""The wheel must ship the built UI, profiles, and schema, and nothing from node_modules."""

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
