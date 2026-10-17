"""Bounded, resumable retrieval of flow and hunt results into an evidence bundle.

* Flow sources are read with ``source(client_id, flow_id, artifact, start_row, count)``.
* Hunts are read by enumerating contributing flows with ``hunt_flows(hunt_id, start_row)``
  and reading each flow's sources the same way. ``hunt_results()`` is not used because it
  has no paging arguments and flattens provenance.
* Consistency policy: results of non-terminal flows can still grow, so they are not read;
  their coverage says so and the snapshot is marked incomplete.
* Budgets: per-source rows/bytes, and shared total rows/bytes across the run. A budget stop
  is recorded as ``stop_reason``; the source is ``incomplete``, never "complete".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from vr_dfir.errors import DfirError, VelociraptorUnavailable
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.velociraptor.flow_state import (
    FlowState,
    FlowStatus,
    precondition_skipped_names,
    sanitize_text,
)

ProgressFn = Callable[[str, dict], None]


@dataclass
class Budget:
    max_total_rows: int
    max_total_bytes: int
    rows: int = 0
    bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def remaining_rows(self) -> int:
        with self._lock:
            return max(0, self.max_total_rows - self.rows)

    def consume(self, rows: int, nbytes: int) -> None:
        with self._lock:
            self.rows += rows
            self.bytes += nbytes

    def bytes_exhausted(self) -> bool:
        with self._lock:
            return self.bytes >= self.max_total_bytes


@dataclass
class SourceRequest:
    artifact: str
    source: str | None
    collection_ids: list[str] = field(default_factory=list)
    counts_only: bool = False
    defined: bool = True  # False when metadata says the source does not exist


def _collection_status_value(status: FlowStatus | None) -> str:
    if status is None:
        return "unknown"
    return status.state.value


def classify_result(entry_rows: int, status: FlowStatus | None, full_name: str,
                    skipped: set[str], defined: bool) -> tuple[str, str | None]:
    """Result status for one source, preserving uncertainty."""
    if not defined:
        return "unavailable", "The source is not defined in the artifact metadata."
    stat = status.stat_for(*full_name.split("/", 1)) if status and "/" in full_name else (
        status.stat_for(full_name, None) if status else None)
    if stat is not None and stat.failed:
        return "source_failed", stat.error_message or stat.status
    if entry_rows > 0:
        return "rows", None
    if full_name in skipped:
        return "skipped_precondition", "Verified by the client log: skipped due to preconditions."
    if status is not None and status.state in (FlowState.FAILED, FlowState.CANCELLED) and stat is None:
        return "not_collected", status.raw_status or "The collection did not complete."
    if stat is not None:
        return "empty_or_skipped", ("The query reported success with zero rows. Velociraptor "
                                    "does not distinguish 'no matches' from a skipped "
                                    "precondition here.")
    return "unknown", "Zero rows and no per-source status was reported by the server."


def read_flow(service, bundle: EvidenceBundle, *, org_id: str, client_id: str, flow_id: str,
              hostname: str, status: FlowStatus, sources: list[SourceRequest], limits: dict,
              budget: Budget, retrieval: dict, hunt_id: str | None = None,
              stop_event: threading.Event | None = None, progress: ProgressFn | None = None,
              event_time_fields: list[str] | None = None,
              sleep: Callable[[float], None] = time.sleep) -> dict:
    """Read the given sources of one flow. Returns a per-source summary."""
    try:
        logs = [entry["message"] for entry in service.flow_logs(client_id, flow_id, org_id,
                                                                limit=1000)]
    except DfirError:
        logs = []
    skipped = precondition_skipped_names(logs)
    page_size = int(limits.get("page_size", 500))
    max_rows = int(limits.get("max_rows_per_source", 10_000))
    max_bytes = int(limits.get("max_bytes_per_source", 50_000_000))
    retries = int(retrieval.get("read_retries", 2))
    backoff = float(retrieval.get("retry_backoff_seconds", 1.0))
    snapshot = {"flow_state": status.state.value, "raw_state": status.raw_state,
                "total_collected_rows": status.total_collected_rows,
                "complete": status.terminal, "observed_at": None}
    from vr_dfir.evidence.bundle import now_iso
    snapshot["observed_at"] = now_iso()

    def read_one(req: SourceRequest) -> dict:
        entry = bundle.ensure_source(
            org_id=org_id, client_id=client_id, flow_id=flow_id, artifact=req.artifact,
            source=req.source, hostname=hostname, hunt_id=hunt_id,
            collection_ids=req.collection_ids, collected_at=status.start_time or status.create_time,
            counts_only=req.counts_only)
        key = entry.source_key
        full = entry.full_name
        stat = status.stat_for(req.artifact, req.source)
        available = stat.result_rows if stat and stat.result_rows is not None else None
        bundle.set_coverage(key, collection_status=_collection_status_value(status),
                            available_rows=available,
                            available_rows_basis="query_stats.result_rows" if available is not None
                            else "unknown",
                            snapshot=snapshot)
        if not req.defined:
            bundle.set_coverage(key, read_status="not_applicable", result_status="unavailable",
                                error="Source is not defined in artifact metadata.")
            bundle.save()
            return {"source": full, "result_status": "unavailable"}
        if not status.terminal:
            bundle.set_coverage(key, read_status="not_started", result_status="unknown",
                                stop_reason="flow_not_terminal",
                                error=f"Flow is {status.state.value}; results may still change "
                                      "and were not read.")
            bundle.save()
            return {"source": full, "result_status": "unknown", "read_status": "not_started"}
        position = bundle.resume_position(key)
        bundle.set_coverage(key, read_status="in_progress", error=None, stop_reason=None)
        stop_reason = None
        finished = entry.coverage.pagination_finished and position > 0
        error = None
        while not finished:
            if stop_event is not None and stop_event.is_set():
                stop_reason = "interrupted"
                break
            remaining_source = max_rows - position
            if remaining_source <= 0:
                stop_reason = "row_budget"
                break
            if entry.coverage.bytes_persisted >= max_bytes:
                stop_reason = "byte_budget"
                break
            remaining_total = budget.remaining_rows()
            if remaining_total <= 0:
                stop_reason = "total_row_budget"
                break
            if budget.bytes_exhausted():
                stop_reason = "total_byte_budget"
                break
            count = min(page_size, remaining_source, remaining_total)
            attempt = 0
            while True:
                try:
                    result = service.read_source_page(client_id, flow_id, req.artifact,
                                                      req.source, position, count, org_id)
                    break
                except VelociraptorUnavailable as exc:
                    attempt += 1
                    if attempt > retries:
                        error = sanitize_text(exc.message, 300)
                        break
                    sleep(backoff * (2 ** (attempt - 1)))
                except DfirError as exc:
                    error = sanitize_text(exc.message, 300)
                    break
            if error:
                stop_reason = "read_error"
                break
            rows = result.rows
            if not rows and result.log_errors() and position == 0:
                # e.g. "source: result set not found"; recorded, not treated as empty success
                bundle.set_coverage(key, notes=[sanitize_text(m, 200)
                                                for m in result.log_errors()[:3]])
            written, nbytes = bundle.append_rows(key, rows, position,
                                                 event_time_fields=event_time_fields)
            budget.consume(written, nbytes)
            position += written
            bundle.save()
            if progress:
                progress("rows_read", {"source": full, "client_id": client_id,
                                       "flow_id": flow_id, "rows": position})
            if written < count:
                finished = True
        persisted = bundle.source(key).coverage.persisted_rows
        result_status, reason = classify_result(persisted, status, full, skipped, req.defined)
        if error and persisted == 0 and result_status in ("empty_or_skipped", "unknown"):
            # A failed read says nothing about whether the source is empty.
            result_status, reason = "unknown", "The source could not be read."
        if finished:
            read_status = "complete"
        elif stop_reason == "read_error":
            read_status = "read_failed" if persisted == 0 else "incomplete"
        else:
            read_status = "incomplete"
        if result_status == "rows" and not finished:
            reason = None
        bundle.set_coverage(key, read_status=read_status, result_status=result_status,
                            pagination_finished=finished, stop_reason=stop_reason,
                            error=error or (reason if result_status in (
                                "source_failed", "not_collected", "unknown") else None),
                            notes=[n for n in [reason] if n and result_status in (
                                "empty_or_skipped", "skipped_precondition")])
        bundle.save()
        return {"source": full, "result_status": result_status, "read_status": read_status,
                "rows": persisted, "stop_reason": stop_reason}

    workers = max(1, min(int(retrieval.get("max_concurrent_reads", 2)), len(sources) or 1))
    if workers == 1:
        results = [read_one(r) for r in sources]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(read_one, sources))
    return {"client_id": client_id, "flow_id": flow_id, "sources": results,
            "precondition_skips": sorted(skipped)}


def enumerate_hunt_flows(service, *, org_id: str, hunt_id: str, known: list[dict],
                         client_filter: set[str] | None, max_clients: int,
                         page_size: int = 100) -> tuple[list[dict], bool]:
    """Continue enumerating contributing flows from ``len(known)``. Returns
    ``(flows, enumeration_complete)``. Each entry: client_id, flow_id, state."""
    flows = list(known)
    start = len(flows)
    complete = False
    while True:
        page = service.hunt_flows_page(hunt_id, start, page_size, org_id)
        for item in page:
            st: FlowStatus = item["status"]
            flows.append({"client_id": item["client_id"], "flow_id": item["flow_id"],
                          "state": st.state.value, "raw_state": st.raw_state,
                          "status_text": st.raw_status})
        start += len(page)
        if len(page) < page_size:
            complete = True
            break
        if len(flows) >= max_clients * 4:
            break
    selected = [f for f in flows if not client_filter or f["client_id"] in client_filter]
    return selected[:max_clients] if max_clients else selected, complete
