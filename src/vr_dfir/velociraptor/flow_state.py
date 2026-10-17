"""Normalization adapter for Velociraptor collection (flow) metadata.

Verified against Velociraptor v0.77.3 source (``flows/proto/artifact_collector.proto``,
``vql/server/flows/flows.go``, ``json/protobuf.go``, ``services/launcher/flows.go``):

* VQL ``flows(client_id, flow_id)`` and ``get_flow()`` return the **flat**
  ``ArtifactCollectorContext`` (plus ``AvailableDownloads``). Enum values are emitted as
  their names and zero values are included.
* The GUI/gRPC ``FlowDetails`` message wraps the same context as ``{"context": {...}}``.
  protojson omits zero values there, so a missing ``state`` means ``UNSET``.
* ``hunt_flows()`` rows carry the flat context under ``Flow``.
* A cancelled flow has ``state: ERROR`` and ``status`` starting with ``Cancelled by``.

Anything else is reported as ``unrecognized`` with a key-only diagnostic. An absent
state is never reported as running.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FlowState(str, Enum):
    SCHEDULED = "scheduled"        # RUNNING: scheduled/active; client may not have picked it up
    QUEUED = "queued"              # WAITING: delivered, waiting for a client concurrency slot
    IN_PROGRESS = "in_progress"    # IN_PROGRESS: executing on the client
    UNRESPONSIVE = "unresponsive"  # UNRESPONSIVE: client stopped reporting; not terminal
    COMPLETED = "completed"        # FINISHED
    FAILED = "failed"              # ERROR
    CANCELLED = "cancelled"        # ERROR with "Cancelled by ..."
    NOT_FOUND = "not_found"        # no flow row returned
    UNKNOWN = "unknown"            # UNSET, unrecognized value, or unrecognized shape


TERMINAL_STATES = frozenset({FlowState.COMPLETED, FlowState.FAILED, FlowState.CANCELLED})
ACTIVE_STATES = frozenset({
    FlowState.SCHEDULED, FlowState.QUEUED, FlowState.IN_PROGRESS, FlowState.UNRESPONSIVE,
})

# ArtifactCollectorContext.State enum numbers (for numeric encodings).
_ENUM_NUMBERS = {0: "UNSET", 1: "RUNNING", 2: "FINISHED", 3: "ERROR", 4: "IN_PROGRESS",
                 5: "UNRESPONSIVE", 6: "WAITING"}

_STATE_MAP = {
    "RUNNING": FlowState.SCHEDULED,
    "WAITING": FlowState.QUEUED,
    "IN_PROGRESS": FlowState.IN_PROGRESS,
    "UNRESPONSIVE": FlowState.UNRESPONSIVE,
    "FINISHED": FlowState.COMPLETED,
    "ERROR": FlowState.FAILED,
}

CANCELLED_PREFIX = "Cancelled by"
MAX_STATUS_CHARS = 600


def epoch_to_iso(value: Any) -> str | None:
    """Convert a Velociraptor epoch (seconds, ms, us or ns) to ISO-8601 UTC."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1e17:
        number /= 1e9
    elif number > 1e14:
        number /= 1e6
    elif number > 1e11:
        number /= 1e3
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def sanitize_text(value: Any, limit: int = MAX_STATUS_CHARS) -> str:
    text = "" if value is None else str(value)
    if "PRIVATE KEY" in text or "BEGIN CERTIFICATE" in text:
        return "[redacted: key or certificate material]"
    text = text.replace("\x00", "")
    if len(text) > limit:
        return text[:limit] + f"... [truncated {len(text) - limit} chars]"
    return text


@dataclass
class QueryStat:
    """Per-query status reported by the client (``VeloStatus``)."""

    name: str
    status: str
    error_message: str = ""
    result_rows: int | None = None
    names_with_response: list[str] = field(default_factory=list)
    duration: int | None = None

    @property
    def failed(self) -> bool:
        return self.status in {"GENERIC_ERROR", "UNKNOWN_FLOW"}


@dataclass
class FlowStatus:
    client_id: str
    flow_id: str
    state: FlowState
    raw_state: str | None
    response_shape: str
    raw_status: str = ""
    diagnostic: str = ""
    create_time: str | None = None
    start_time: str | None = None
    active_time: str | None = None
    artifacts: list[str] = field(default_factory=list)
    artifacts_with_results: list[str] = field(default_factory=list)
    total_collected_rows: int | None = None
    total_uploaded_files: int | None = None
    total_uploaded_bytes: int | None = None
    creator: str | None = None
    query_stats: list[QueryStat] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def stat_for(self, artifact: str, source: str | None) -> QueryStat | None:
        """Find the per-query status for an artifact source.

        ``Artifact`` naming in ``VeloStatus`` is matched against ``Artifact/Source`` first and
        the bare artifact name second (single-source artifacts).
        """
        full = f"{artifact}/{source}" if source else artifact
        for stat in self.query_stats:
            if stat.name == full:
                return stat
        if not source:
            return None
        for stat in self.query_stats:
            if full in stat.names_with_response:
                return stat
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        data["terminal"] = self.terminal
        return data


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _detect_shape(response: Any) -> tuple[str, dict | None]:
    """Return ``(shape, context_dict)`` for a supported response form."""
    if response is None:
        return "empty", None
    if isinstance(response, list):
        if not response:
            return "empty", None
        if len(response) > 1:
            # flows(client_id, flow_id) yields at most one row; more is unexpected.
            return "unrecognized", None
        response = response[0]
    if not isinstance(response, dict):
        return "unrecognized", None
    if "context" in response and isinstance(response["context"], dict):
        return "nested_context", response["context"]
    if "Flow" in response:
        flow = response["Flow"]
        if flow is None:
            return "empty", None
        if isinstance(flow, dict):
            return "hunt_flow_row", flow
        return "unrecognized", None
    if "session_id" in response or "state" in response:
        return "flat_context", response
    return "unrecognized", None


def normalize_flow(response: Any, *, client_id: str, flow_id: str,
                   logs: list[str] | None = None) -> FlowStatus:
    """Normalize a flow metadata response into :class:`FlowStatus`.

    ``logs`` are the (already sanitized) query log lines returned with the response;
    they are used only for diagnostics (e.g. permission errors that produce no rows).
    """
    shape, ctx = _detect_shape(response)
    if shape == "empty":
        hint = ""
        joined = " ".join(logs or [])
        if "Permission" in joined or "permission" in joined:
            hint = " The query log reports a permission problem."
        return FlowStatus(
            client_id=client_id, flow_id=flow_id, state=FlowState.NOT_FOUND, raw_state=None,
            response_shape=shape,
            diagnostic=(
                "No flow metadata row was returned. The flow does not exist in this org/client "
                "scope, or the API account lacks READ_RESULTS." + hint
            ),
        )
    if shape == "unrecognized" or ctx is None:
        sample = response
        kind = type(response).__name__
        if isinstance(response, list) and response:
            sample = response[0]
            kind = f"list of {len(response)} {type(sample).__name__}"
        keys = sorted(str(k) for k in sample.keys())[:20] if isinstance(sample, dict) else []
        return FlowStatus(
            client_id=client_id, flow_id=flow_id, state=FlowState.UNKNOWN, raw_state=None,
            response_shape="unrecognized",
            diagnostic=(
                f"Unrecognized flow metadata response ({kind}; top-level keys: {keys}). "
                "Supported forms: flat flow context, {'context': {...}}, {'Flow': {...}}."
            ),
        )

    # Scope check: a context for another client/flow is never accepted silently.
    ctx_client = ctx.get("client_id")
    ctx_flow = ctx.get("session_id")
    if (ctx_client and ctx_client != client_id) or (ctx_flow and ctx_flow != flow_id):
        return FlowStatus(
            client_id=client_id, flow_id=flow_id, state=FlowState.UNKNOWN, raw_state=None,
            response_shape=shape,
            diagnostic=(
                f"Flow metadata scope mismatch: requested {client_id}/{flow_id}, "
                f"received {ctx_client}/{ctx_flow}."
            ),
        )

    raw_state_value = ctx.get("state")
    diagnostic = ""
    if raw_state_value is None:
        raw_state = "UNSET" if shape == "nested_context" else None
        diagnostic = (
            "The flow context has no 'state' field"
            + (" (protojson omits UNSET)." if shape == "nested_context" else ".")
        )
    elif isinstance(raw_state_value, bool):
        raw_state = None
        diagnostic = f"Unrecognized state value type: {type(raw_state_value).__name__}."
    elif isinstance(raw_state_value, int):
        raw_state = _ENUM_NUMBERS.get(raw_state_value, str(raw_state_value))
    else:
        raw_state = str(raw_state_value).strip().upper()

    raw_status = sanitize_text(ctx.get("status"))
    state = _STATE_MAP.get(raw_state or "", FlowState.UNKNOWN)
    if state == FlowState.FAILED and raw_status.startswith(CANCELLED_PREFIX):
        state = FlowState.CANCELLED
    if state == FlowState.UNKNOWN and not diagnostic:
        diagnostic = f"Unrecognized or unset flow state {raw_state!r}; not treated as running."

    request = ctx.get("request") if isinstance(ctx.get("request"), dict) else {}
    stats = []
    for item in ctx.get("query_stats") or []:
        if not isinstance(item, dict):
            continue
        status_value = item.get("status", "OK")
        if isinstance(status_value, int):
            status_value = {0: "OK", 4: "PROGRESS", 10: "GENERIC_ERROR", 11: "UNKNOWN_FLOW"}.get(
                status_value, str(status_value))
        stats.append(QueryStat(
            name=str(item.get("Artifact") or ""),
            status=str(status_value or "OK"),
            error_message=sanitize_text(item.get("error_message"), 400),
            result_rows=_to_int(item.get("result_rows")),
            names_with_response=[str(n) for n in item.get("names_with_response") or []],
            duration=_to_int(item.get("duration")),
        ))

    return FlowStatus(
        client_id=client_id,
        flow_id=flow_id,
        state=state,
        raw_state=raw_state,
        response_shape=shape,
        raw_status=raw_status,
        diagnostic=diagnostic,
        create_time=epoch_to_iso(ctx.get("create_time")),
        start_time=epoch_to_iso(ctx.get("start_time")),
        active_time=epoch_to_iso(ctx.get("active_time")),
        artifacts=[str(a) for a in (request.get("artifacts") or [])],
        artifacts_with_results=[str(a) for a in ctx.get("artifacts_with_results") or []],
        total_collected_rows=_to_int(ctx.get("total_collected_rows")),
        total_uploaded_files=_to_int(ctx.get("total_uploaded_files")),
        total_uploaded_bytes=_to_int(ctx.get("total_uploaded_bytes")),
        creator=request.get("creator"),
        query_stats=stats,
    )


PRECONDITION_SKIP_MESSAGE = "Skipping query due to preconditions"


def precondition_skipped_names(log_messages: list[str]) -> set[str]:
    """Names reported by the verified client log ``INFO:<name>: Skipping query due to
    preconditions`` (``actions/vql.go``)."""
    names = set()
    for message in log_messages:
        if PRECONDITION_SKIP_MESSAGE not in message:
            continue
        head = message.split(PRECONDITION_SKIP_MESSAGE, 1)[0].strip().rstrip(":").strip()
        if head.startswith("INFO:"):
            head = head[len("INFO:"):]
        if head:
            names.add(head.strip())
    return names
