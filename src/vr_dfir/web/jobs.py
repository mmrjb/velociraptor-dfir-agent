"""Bounded background job runner for UI investigations.

* At most ``max_workers`` jobs run at once; at most ``max_queued`` wait.
* Job state is persisted in each investigation (``job`` field). A server restart marks
  unfinished jobs as interrupted/recoverable and never starts another collection.
* Stop requests stop local work and waiting only; endpoint collections may continue.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from vr_dfir.errors import DfirError
from vr_dfir.evidence.bundle import now_iso

logger = logging.getLogger(__name__)


class TooManyJobs(DfirError):
    code = "too_many_jobs"
    next_action = "Wait for a running investigation to finish."


class JobManager:
    def __init__(self, max_workers: int = 2, max_queued: int = 8):
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="vrdfir-job")
        self.max_queued = max_queued
        self._futures: dict[str, Future] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def active(self) -> list[str]:
        with self._lock:
            return [k for k, f in self._futures.items() if not f.done()]

    def is_active(self, inv_id: str) -> bool:
        with self._lock:
            f = self._futures.get(inv_id)
            return f is not None and not f.done()

    def submit(self, inv_id: str, fn: Callable[[threading.Event], object]) -> bool:
        """Start a job unless one is already active for this investigation.

        Returns False when a job for the same investigation is already running (idempotent).
        """
        with self._lock:
            existing = self._futures.get(inv_id)
            if existing is not None and not existing.done():
                return False
            if len([f for f in self._futures.values() if not f.done()]) >= self.max_queued:
                raise TooManyJobs("Too many investigations are running or queued.")
            stop = threading.Event()
            self._stops[inv_id] = stop

            def run():
                try:
                    return fn(stop)
                except DfirError as exc:
                    logger.warning("job %s: %s", inv_id, exc.message)
                except Exception:  # noqa: BLE001 - recorded by the service
                    logger.exception("job %s failed", inv_id)
                return None
            self._futures[inv_id] = self.pool.submit(run)
            return True

    def stop(self, inv_id: str) -> bool:
        with self._lock:
            stop = self._stops.get(inv_id)
            f = self._futures.get(inv_id)
        if stop is None or f is None or f.done():
            return False
        stop.set()
        return True

    def shutdown(self) -> None:
        for stop in list(self._stops.values()):
            stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


def recover_interrupted(store) -> list[str]:
    """Mark investigations left running by a previous server process as interrupted."""
    recovered = []
    for summary in store.list(limit=10_000):
        if summary.get("outcome") != "running":
            continue
        job = summary.get("job") or {}
        if job.get("state") in ("running", "queued", "created") or summary.get("phase") in (
                "collecting", "reading", "analyzing"):
            inv = store.get(summary["investigation_id"])
            never_started = job.get("state") == "created" and summary.get("phase") == "preparing"
            fields = {"outcome": "interrupted",
                      "job": {"state": "interrupted", "interrupted_at": now_iso()}}
            if never_started and summary.get("mode") == "new_collection":
                # Resume never launches; record that the planned collection did not start.
                fields["collection_status"] = "not_started"
            inv.update(**fields)
            inv.add_error("The UI server stopped while this investigation was running. Nothing "
                          "was restarted automatically. Resume when ready; the endpoint "
                          "collection (if any) may have continued.")
            inv.event("interrupted", {"reason": "server restart"})
            recovered.append(inv.id)
    return recovered
