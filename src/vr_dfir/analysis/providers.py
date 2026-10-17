"""Bounded Ollama completions, or evidence-only analysis. Models receive no tools."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from vr_dfir.config import ModelSettings
from vr_dfir.errors import ConfigurationError, ModelError, ModelUnavailable


@dataclass
class ModelResponse:
    text: str
    provider: str
    model: str
    duration_seconds: float
    server_metadata: dict = field(default_factory=dict)


class Provider(Protocol):
    name: str
    model: str
    is_fake: bool
    is_hosted: bool

    def options(self) -> dict: ...

    def complete(self, messages: list[dict], *, max_output_tokens: int) -> ModelResponse: ...

    def probe(self) -> dict: ...


class OllamaProvider:
    name = "ollama"
    is_fake = False
    is_hosted = False

    def __init__(self, settings: ModelSettings, transport: httpx.BaseTransport | None = None):
        if not settings.ollama_model:
            raise ConfigurationError(
                "No Ollama model is configured.",
                next_action="Set OLLAMA_MODEL (for example qwen2.5:1.5b) to a model already "
                            "available on the Ollama server; models are never downloaded "
                            "automatically.")
        self.settings = settings
        self.model = settings.ollama_model
        self.host = settings.ollama_host.rstrip("/")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
                            transport=self._transport, trust_env=False)

    def options(self) -> dict:
        return {"temperature": self.settings.temperature, "num_ctx": self.settings.num_ctx,
                "format": "json", "stream": False, "timeout_seconds": self.settings.timeout_seconds}

    def complete(self, messages: list[dict], *, max_output_tokens: int) -> ModelResponse:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.settings.temperature,
                        "num_ctx": self.settings.num_ctx,
                        "num_predict": int(max_output_tokens)},
        }
        started = time.monotonic()
        try:
            with self._client(self.settings.timeout_seconds) as client:
                response = client.post(f"{self.host}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise ModelUnavailable(
                f"The Ollama request timed out after {self.settings.timeout_seconds:.0f}s.") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"Ollama at {self.host} is not reachable: "
                                   f"{type(exc).__name__}.") from exc
        if response.status_code == 404:
            raise ModelUnavailable(
                f"Ollama reports model {self.model!r} is not available (HTTP 404). Pull it on "
                "the Ollama server explicitly; this tool never downloads models.")
        if response.status_code >= 400:
            raise ModelError(f"Ollama returned HTTP {response.status_code}: "
                             f"{response.text[:300]}")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ModelError("Ollama returned a non-JSON response.") from exc
        content = ((data.get("message") or {}).get("content")) or ""
        meta = {k: data.get(k) for k in ("done", "done_reason", "prompt_eval_count", "eval_count",
                                         "total_duration") if k in data}
        return ModelResponse(content, self.name, self.model, round(time.monotonic() - started, 3),
                             meta)

    def probe(self) -> dict:
        try:
            with self._client(min(10.0, self.settings.timeout_seconds)) as client:
                response = client.get(f"{self.host}/api/tags")
            response.raise_for_status()
            names = [m.get("name") for m in response.json().get("models", [])]
        except (httpx.HTTPError, json.JSONDecodeError, AttributeError) as exc:
            return {"reachable": False, "error": f"{type(exc).__name__}", "endpoint": self.host}
        return {"reachable": True, "endpoint": self.host, "model": self.model,
                "model_available": self.model in names or f"{self.model}:latest" in names,
                "models_on_server": len(names)}




class DisabledProvider:
    name = "none"
    model = ""
    is_fake = False
    is_hosted = False

    def options(self) -> dict:
        return {}

    def complete(self, messages, *, max_output_tokens):  # pragma: no cover - never called
        raise ModelError("No model provider is selected.")

    def probe(self) -> dict:
        return {"reachable": None, "note": "Model analysis is disabled."}


def build_provider(settings: ModelSettings, *, no_llm: bool = False) -> Provider:
    if no_llm:
        return DisabledProvider()
    if settings.provider == "none":
        return DisabledProvider()
    if settings.provider == "ollama":
        return OllamaProvider(settings)
    raise ConfigurationError(
        f"Unsupported model provider {settings.provider!r}. Use ollama or none.")
