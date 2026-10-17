"""Run the MCP stdio server over the synthetic test runtime (tests only)."""

from __future__ import annotations

import logging
import sys

from support.runtime import fake_runtime
from vr_dfir import mcp_server
from vr_dfir.config import load_settings


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    settings = load_settings(load_dotenv=False)
    mcp_server.build_server(mcp_server.ToolContext(settings, fake_runtime(settings))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
