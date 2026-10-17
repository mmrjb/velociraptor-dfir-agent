"""Synthetic runtime for tests and browser journeys (never shipped in the package)."""

from __future__ import annotations

from pathlib import Path

from support.fake_model import FakeProvider
from support.scenarios import ORG, build_fake_server
from vr_dfir.analysis.providers import DisabledProvider
from vr_dfir.profiles.allowlist import Allowlist
from vr_dfir.services.runtime import Runtime
from vr_dfir.velociraptor.service import VelociraptorService

TEST_ALLOWLIST = Allowlist(
    frozenset({"LTH.SystemBaseline", "Custom.Example", "Linux.Sys.Pslist"}),
    "synthetic test allowlist", True,
)


def fake_runtime(settings, *, fake_mode: str = "ok", state_path: Path | None = None,
                 allowlist: Allowlist | None = TEST_ALLOWLIST) -> Runtime:
    """Runtime backed by the synthetic server and the fake model.

    ``runtime.fake_mode`` can be changed by a test (for example "outage" then "ok").
    ``state_path`` persists synthetic launches so a restarted test server still knows them.
    """
    server = build_fake_server(state_path=state_path)
    rt = Runtime(settings=settings, _service=VelociraptorService(server, default_org_id=ORG),
                 allowlist_override=allowlist)
    rt.fake_server = server
    rt.fake_mode = fake_mode

    def provider(no_llm: bool):
        return DisabledProvider() if no_llm else FakeProvider(rt.fake_mode)
    rt.provider_override = provider
    return rt
