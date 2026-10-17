"""Execution context: the Velociraptor service, the collection allowlist, and the model provider.

Live adapters are created lazily, so constructing a runtime never opens a network connection.
Tests inject their own service, provider, and allowlist through the constructor; production
code always uses the configured Velociraptor API and model settings.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from vr_dfir.analysis.providers import Provider, build_provider
from vr_dfir.config import Settings
from vr_dfir.profiles.allowlist import Allowlist, load_allowlist
from vr_dfir.velociraptor.service import VelociraptorService


@dataclass
class Runtime:
    settings: Settings
    _service: VelociraptorService | None = None
    provider_override: Callable[[bool], Provider] | None = None
    allowlist_override: Allowlist | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def service(self) -> VelociraptorService:
        with self._lock:
            if self._service is None:
                from vr_dfir.velociraptor.backend import GrpcBackend
                backend = GrpcBackend(self.settings.api_config_path,
                                      timeout_seconds=self.settings.grpc_timeout_seconds,
                                      debug_vql=self.settings.debug_vql)
                self._service = VelociraptorService(backend,
                                                    default_org_id=self.settings.default_org_id)
            return self._service

    @property
    def synthetic(self) -> bool:
        """Provenance label: True only when an injected (test) backend serves synthetic data."""
        return bool(self._service is not None and self._service.synthetic)

    @property
    def default_org_id(self) -> str:
        if self._service is not None:
            return self._service.default_org_id
        return self.settings.default_org_id or "root"

    @property
    def live_configured(self) -> bool:
        return self._service is not None or bool(self.settings.api_config_path)

    def allowlist(self) -> Allowlist:
        if self.allowlist_override is not None:
            return self.allowlist_override
        return load_allowlist(self.settings.allowlist_path)

    def provider(self, *, no_llm: bool = False) -> Provider:
        if self.provider_override is not None:
            return self.provider_override(no_llm)
        return build_provider(self.settings.model, no_llm=no_llm)


def live_runtime(settings: Settings) -> Runtime:
    return Runtime(settings=settings)
