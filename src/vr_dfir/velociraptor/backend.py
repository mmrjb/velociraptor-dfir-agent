"""Low-level Velociraptor query backends.

``GrpcBackend`` talks to a real server through the official ``pyvelociraptor``
stubs. It is created lazily so imports, tests, and profile
validation never open a network connection.

Responses are bounded: a hard cap on returned rows and bytes per query protects
the process from unbounded API responses. Query log lines are captured
(sanitized, bounded) because Velociraptor reports many plugin/function errors
only through the log stream, with no rows.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from vr_dfir.config import describe_file_problem, probe_file
from vr_dfir.errors import ConfigurationError, VelociraptorError, VelociraptorUnavailable
from vr_dfir.velociraptor.flow_state import sanitize_text

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 60
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

# Query-log lines the API emits for every query (progress and statistics), not diagnostics.
_PROGRESS_MARKERS = ("starting query execution", "sending response part", "query stats",
                     "filled cache", "query: running")


def log_line_level(line: str) -> str:
    """``info``, ``warning`` or ``error`` for one query-log line.

    Velociraptor reports plugin/function failures as log lines prefixed with the plugin name
    (``notebook_get: ...``, ``hunts: ...``) or ``ERROR:``; ``INFO:``/``DEBUG:`` lines and the
    API's progress lines are ordinary information. Only log lines are classified: row contents
    are evidence and never diagnostics.
    """
    text = line.strip()
    upper = text[:8].upper()
    if upper.startswith(("INFO:", "DEBUG:")):
        return "info"
    if upper.startswith(("WARN:", "WARNING:")):
        return "warning"
    low = text.lower()
    if any(marker in low for marker in _PROGRESS_MARKERS):
        return "info"
    return "error"


@dataclass
class QueryResult:
    rows: list[dict] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    logs_truncated: bool = False
    bytes_received: int = 0

    def log_errors(self) -> list[str]:
        """Log lines that look like errors (Velociraptor prefixes plugin errors with
        the plugin name, e.g. ``collect_client: ...``)."""
        return [line for line in self.logs if not line.startswith(("INFO:", "DEBUG:"))]

    def server_errors(self) -> list[str]:
        """Server-side diagnostics of this query (see :func:`log_line_level`): log lines that
        are neither ordinary information, progress, nor warnings."""
        return [line for line in self.logs if log_line_level(line) == "error"]


class Backend(Protocol):
    """Executes one fixed query in one org. Implementations must be thread-safe."""

    synthetic: bool

    def query(self, vql: str, *, org_id: str, timeout: float | None = None,
              max_rows: int = 100_000, server_timeout: int | None = None) -> QueryResult: ...


def _org_for_request(org_id: str) -> str:
    # The root org is addressed by leaving org_id empty (the server default).
    return "" if org_id in ("", "root") else org_id


class GrpcBackend:
    """Velociraptor gRPC API backend (``API.Query``)."""

    synthetic = False

    def __init__(self, api_config_path: str, *, timeout_seconds: int = 60,
                 debug_vql: bool = False):
        if not api_config_path:
            raise ConfigurationError(
                "No Velociraptor API client configuration is set.",
                next_action="Set VELOCIRAPTOR_API_CONFIG to the operator-provided api_client.yaml.",
            )
        self.api_config_path = api_config_path
        self.timeout_seconds = timeout_seconds
        self.debug_vql = debug_vql
        self._stub = None
        self._channel = None
        self._lock = threading.Lock()

    def _connect(self):
        with self._lock:
            if self._stub is not None:
                return self._stub
            try:
                import grpc
                import yaml
                from pyvelociraptor import api_pb2_grpc
            except ImportError as exc:  # pragma: no cover - dependency problem
                raise ConfigurationError(
                    f"Velociraptor client libraries are not installed: {exc}",
                    next_action="Install the package with its default dependencies.",
                ) from exc
            path = Path(self.api_config_path)
            state = probe_file(path)
            if state != "file":
                raise ConfigurationError(
                    f"Velociraptor API config {path} {describe_file_problem(state)}.",
                    next_action="Check VELOCIRAPTOR_API_CONFIG and file permissions.",
                )
            try:
                config = yaml.safe_load(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ConfigurationError(
                    f"Velociraptor API config {path} could not be read "
                    f"({exc.strerror or type(exc).__name__}).",
                ) from exc
            except yaml.YAMLError as exc:
                raise ConfigurationError(
                    f"Velociraptor API config could not be read as YAML: {type(exc).__name__}",
                ) from exc
            required = ("ca_certificate", "client_private_key", "client_cert",
                        "api_connection_string")
            missing = [key for key in required if not isinstance(config, dict) or not config.get(key)]
            if missing:
                raise ConfigurationError(
                    f"Velociraptor API config is missing fields: {', '.join(missing)}",
                    next_action="Generate the API client config with 'velociraptor config api_client'.",
                )
            creds = grpc.ssl_channel_credentials(
                root_certificates=config["ca_certificate"].encode("utf-8"),
                private_key=config["client_private_key"].encode("utf-8"),
                certificate_chain=config["client_cert"].encode("utf-8"),
            )
            # The API certificate is issued for the name "VelociraptorServer". Responses are
            # bounded by MAX_RESPONSE_BYTES below, so allow messages up to that size (the
            # grpc default of 4 MiB is too small for some notebook cell listings).
            options = (("grpc.ssl_target_name_override", "VelociraptorServer"),
                       ("grpc.max_receive_message_length", MAX_RESPONSE_BYTES))
            self._channel = grpc.secure_channel(config["api_connection_string"], creds,
                                                options=options)
            self._stub = api_pb2_grpc.APIStub(self._channel)
            return self._stub

    def close(self) -> None:
        with self._lock:
            if self._channel is not None:
                self._channel.close()
            self._channel = None
            self._stub = None

    def query(self, vql: str, *, org_id: str, timeout: float | None = None,
              max_rows: int = 100_000, server_timeout: int | None = None) -> QueryResult:
        """Run one fixed query. ``server_timeout`` (seconds) also makes the server cancel the
        query (``VQLCollectorArgs.timeout``); the client deadline always applies."""
        import grpc
        from pyvelociraptor import api_pb2

        stub = self._connect()
        request = api_pb2.VQLCollectorArgs(
            Query=[api_pb2.VQLRequest(Name="vr-dfir", VQL=vql)],
            max_row=1000,
            max_wait=1,
        )
        if server_timeout:
            request.timeout = int(server_timeout)
        org = _org_for_request(org_id)
        if org:
            request.org_id = org
        if self.debug_vql:
            logger.debug("VQL [%s]: %s", org_id or "root", vql)
        deadline = float(timeout or self.timeout_seconds)
        result = QueryResult()
        try:
            for response in stub.Query(request, timeout=deadline):
                if response.log:
                    if len(result.logs) < MAX_LOG_LINES:
                        result.logs.append(sanitize_text(response.log.strip(), 500))
                    else:
                        result.logs_truncated = True
                if response.Response:
                    result.bytes_received += len(response.Response)
                    if result.bytes_received > MAX_RESPONSE_BYTES:
                        raise VelociraptorError(
                            "Velociraptor response exceeded the per-query byte limit; "
                            "reduce the page size.")
                    batch = json.loads(response.Response)
                    if isinstance(batch, list):
                        result.rows.extend(batch)
                    if len(result.rows) > max_rows:
                        raise VelociraptorError(
                            f"Velociraptor returned more than {max_rows} rows for a bounded query.")
        except grpc.RpcError as exc:
            code = exc.code() if hasattr(exc, "code") else None
            name = getattr(code, "name", str(code))
            detail = sanitize_text(exc.details() if hasattr(exc, "details") else "", 300)
            if name in {"UNAVAILABLE", "DEADLINE_EXCEEDED", "CANCELLED"}:
                raise VelociraptorUnavailable(
                    f"Velociraptor API {name.lower().replace('_', ' ')}: {detail}",
                    details={"grpc_code": name},
                ) from exc
            raise VelociraptorError(
                f"Velociraptor API error ({name}): {detail}", details={"grpc_code": name},
            ) from exc
        return result
