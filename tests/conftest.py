from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

# Tests never read the developer's dotenv or API configuration.
for key in list(os.environ):
    if key.startswith(("VELOCIRAPTOR_", "VR_DFIR_", "OLLAMA_", "AZURE_OPENAI")) or key == \
            "ENABLE_DANGEROUS_TOOLS":
        os.environ.pop(key)


def load_fixture(name: str):
    return json.loads((FIXTURES / "velociraptor" / name).read_text())["response"]


@pytest.fixture
def settings(tmp_path):
    from vr_dfir.config import load_settings
    return load_settings({"VR_DFIR_STATE_DIR": str(tmp_path / "state")}, load_dotenv=False)


@pytest.fixture
def fake_server():
    from support.scenarios import build_fake_server
    return build_fake_server()


@pytest.fixture
def vr(fake_server):
    from support.scenarios import ORG
    from vr_dfir.velociraptor.service import VelociraptorService
    return VelociraptorService(fake_server, default_org_id=ORG)


@pytest.fixture
def synth(tmp_path, monkeypatch):
    """Investigation service over the synthetic test server and fake model (fast retries)."""
    from support.synthetic import scenario_service
    svc = scenario_service(tmp_path / "synth")
    import vr_dfir.evidence.retrieval as retrieval
    monkeypatch.setattr(retrieval.time, "sleep", lambda s: None)
    return svc


@pytest.fixture
def no_sleep(monkeypatch):
    import vr_dfir.evidence.retrieval as retrieval
    monkeypatch.setattr(retrieval.time, "sleep", lambda s: None)



@pytest.fixture(autouse=True)
def analysis_profile_fixtures(monkeypatch):
    import vr_dfir.profiles.store as store
    monkeypatch.setattr(store, "bundled_profile_dir", lambda: FIXTURES / "analysis_profiles")
