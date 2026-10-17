"""Serve the UI over the synthetic test runtime for the browser journeys (tests only).

Usage::

    PYTHONPATH=tests VR_DFIR_STATE_DIR=/path/to/state python tests/support/e2e_server.py --port 8766

It uses the production UI server and API with an injected synthetic Velociraptor server and
fake model; it never connects to a real server. ``VR_DFIR_E2E_MODEL_MODE_FILE`` may name a
file whose content (``ok``, ``outage``, ...) selects the fake model behaviour per request.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from support.fake_model import FakeProvider
from support.runtime import fake_runtime
from vr_dfir.analysis.providers import DisabledProvider
from vr_dfir.config import load_settings
from vr_dfir.web.server import serve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    settings = load_settings(load_dotenv=False)
    runtime = fake_runtime(settings, state_path=Path(settings.state_dir) / "fake_server_state.json")
    mode_file = os.environ.get("VR_DFIR_E2E_MODEL_MODE_FILE")

    def provider(no_llm: bool):
        if no_llm:
            return DisabledProvider()
        mode = "ok"
        if mode_file and Path(mode_file).is_file():
            mode = Path(mode_file).read_text(encoding="utf-8").strip() or "ok"
        return FakeProvider(mode)

    runtime.provider_override = provider
    return serve(settings, host="127.0.0.1", port=args.port, runtime=runtime)


if __name__ == "__main__":
    raise SystemExit(main())
