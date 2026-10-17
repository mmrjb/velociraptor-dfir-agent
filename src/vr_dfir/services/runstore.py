"""Local run store: one directory per investigation.

Layout::

    <root>/<investigation_id>/
        investigation.json   status, scope, options (atomic writes)
        plan.json            the reviewed plan
        checkpoint.json      launches, flow IDs, cursors (atomic writes)
        evidence/            evidence bundle (manifest + JSONL)
        analysis/            exact model requests, raw outputs, findings (0600)
        report/              report.json, report.md, report.html
        events.jsonl         bounded progress log

Browser/API requests address investigations by ID only; IDs are validated and resolved
inside the root, never as arbitrary paths.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import threading
from collections.abc import Iterator
from pathlib import Path

from vr_dfir import ids
from vr_dfir.errors import DfirError, NotFoundError
from vr_dfir.evidence.bundle import now_iso
from vr_dfir.jsonio import JsonlWriter, atomic_write_json, ensure_dir, iter_jsonl, read_json

MAX_EVENTS_KEPT = 1000
MAX_ERRORS = 50


class InvestigationBusy(DfirError):
    code = "investigation_busy"
    next_action = "Wait for the running job to finish or stop it first."


class Investigation:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.meta: dict = read_json(self.path / "investigation.json")

    @property
    def id(self) -> str:
        return self.meta["investigation_id"]

    @property
    def evidence_dir(self) -> Path:
        return self.path / "evidence"

    @property
    def checkpoint_path(self) -> Path:
        return self.path / "checkpoint.json"

    @property
    def report_dir(self) -> Path:
        return self.path / "report"

    def reload(self) -> dict:
        with self._lock:
            self.meta = read_json(self.path / "investigation.json")
            return self.meta

    def update(self, **fields) -> dict:
        with self._lock:
            self.meta.update(fields)
            self.meta["updated_at"] = now_iso()
            atomic_write_json(self.path / "investigation.json", self.meta)
            return self.meta

    def set_phase(self, phase: str) -> None:
        times = dict(self.meta.get("phase_times") or {})
        times.setdefault(phase, now_iso())
        self.update(phase=phase, phase_times=times)

    def add_error(self, message: str) -> None:
        errors = list(self.meta.get("errors") or [])
        if message not in errors:
            errors.append(message[:600])
        self.update(errors=errors[-MAX_ERRORS:])

    def event(self, kind: str, data: dict | None = None) -> None:
        with self._lock:
            path = self.path / "events.jsonl"
            seq = int(self.meta.get("event_seq", 0)) + 1
            self.meta["event_seq"] = seq
            with JsonlWriter(path) as writer:
                writer.write({"seq": seq, "time": now_iso(), "kind": kind, "data": data or {}})
            if seq % 200 == 0:
                self._compact_events(path)
            atomic_write_json(self.path / "investigation.json", self.meta)

    def _compact_events(self, path: Path) -> None:
        events = [e for _, e in iter_jsonl(path)]
        if len(events) <= MAX_EVENTS_KEPT:
            return
        keep = events[-MAX_EVENTS_KEPT:]
        tmp = path.with_suffix(".jsonl.tmp")
        with JsonlWriter(tmp) as writer:
            writer.write_many(keep)
        tmp.replace(path)

    def events(self, after: int = 0, limit: int = 200) -> list[dict]:
        path = self.path / "events.jsonl"
        if not path.exists():
            return []
        out = [e for _, e in iter_jsonl(path) if e.get("seq", 0) > after]
        return out[:limit]

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        """Process-level exclusive lock so one investigation never runs twice at once."""
        lock_path = self.path / ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InvestigationBusy(f"Investigation {self.id} is already running.") from exc
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def summary(self) -> dict:
        m = self.meta
        keys = ("investigation_id", "title", "mode", "scope", "profile", "created_at",
                "updated_at", "phase", "collection_status", "retrieval_status",
                "analysis_status", "outcome", "counts", "synthetic",
                "flow_ids", "job")
        return {k: m.get(k) for k in keys}


class RunStore:
    def __init__(self, root: Path):
        self.root = ensure_dir(root)

    def create(self, *, mode: str, title: str, scope: dict, profile: dict | None,
               options: dict, synthetic: bool,
               investigation_id: str | None = None) -> Investigation:
        inv_id = investigation_id or ids.new_local_id("inv")
        path = self.root / inv_id
        ensure_dir(path)
        meta = {
            "investigation_id": inv_id,
            "title": title,
            "mode": mode,
            "scope": scope,
            "profile": profile,
            "options": options,
            "synthetic": synthetic,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "phase": "preparing",
            "phase_times": {"preparing": now_iso()},
            "collection_status": "not_applicable" if mode != "new_collection" else "pending",
            "retrieval_status": "not_started" if mode != "bundle" else "not_applicable",
            "analysis_status": "not_started",
            "outcome": "running",
            "counts": {},
            "errors": [],
            "flow_ids": [],
            "event_seq": 0,
            "job": {"state": "created"},
        }
        atomic_write_json(path / "investigation.json", meta)
        return Investigation(path)

    def path_for(self, investigation_id: str) -> Path:
        inv = ids.local_id(investigation_id, "investigation ID")
        path = (self.root / inv).resolve()
        if path.parent != self.root.resolve():
            raise NotFoundError("Investigation path escapes the run store.")
        return path

    def get(self, investigation_id: str) -> Investigation:
        path = self.path_for(investigation_id)
        if not (path / "investigation.json").is_file():
            raise NotFoundError(f"Investigation {investigation_id} was not found.")
        return Investigation(path)

    def list(self, *, search: str | None = None, status: str | None = None,
             limit: int = 200) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        needle = (search or "").strip().lower()
        for path in sorted(self.root.iterdir(), reverse=True):
            meta_path = path / "investigation.json"
            if not meta_path.is_file():
                continue
            try:
                inv = Investigation(path)
            except (OSError, ValueError):
                continue
            s = inv.summary()
            if status and status not in (s.get("outcome"), s.get("phase")):
                continue
            if needle:
                hay = " ".join(str(v) for v in (
                    s.get("investigation_id"), s.get("title"), s.get("mode"),
                    (s.get("scope") or {}).get("client_id"), (s.get("scope") or {}).get("hostname"),
                    (s.get("scope") or {}).get("org_id"), (s.get("scope") or {}).get("flow_id"),
                    (s.get("scope") or {}).get("hunt_id"),
                    (s.get("profile") or {}).get("name"))).lower()
                if needle not in hay:
                    continue
            out.append(s)
            if len(out) >= limit:
                break
        return out
