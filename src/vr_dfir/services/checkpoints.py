"""Durable local checkpoints and scope guards for existing-evidence retrieval."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from vr_dfir.errors import (
    ScopeError,
    ValidationError,
)
from vr_dfir.jsonio import atomic_write_json, read_json, sha256_json

CHECKPOINT_VERSION = 1
LAUNCH_STATES = ("pending", "intent_recorded", "acknowledged", "outcome_unknown", "refused",
                 "skipped", "reconciled", "reconcile_failed")

ProgressFn = Callable[[str, dict], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")




class Checkpoint:
    """Durable record of a run's scope, launches, and progress (``checkpoint.json``)."""

    def __init__(self, path: Path, data: dict):
        self.path = Path(path)
        self.data = data
        self._lock = threading.RLock()

    @classmethod
    def create(cls, path: Path, *, investigation_id: str, mode: str, org_id: str,
               target: dict, profile: dict | None, plan_fingerprint: str,
               collections: list[dict] | None = None, existing: dict | None = None,
               synthetic: bool = False) -> Checkpoint:
        data = {
            "checkpoint_version": CHECKPOINT_VERSION,
            "investigation_id": investigation_id,
            "mode": mode,
            "org_id": org_id,
            "target": target,
            "profile": profile,
            "plan_fingerprint": plan_fingerprint,
            "collections": collections or [],
            "existing": existing or {},
            "synthetic": synthetic,
            "created_at": _now(),
            "updated_at": _now(),
        }
        cp = cls(path, data)
        cp.save()
        return cp

    @classmethod
    def load(cls, path: Path) -> Checkpoint:
        p = Path(path)
        if p.is_dir():
            p = p / "checkpoint.json"
        data = read_json(p)
        if data.get("checkpoint_version") != CHECKPOINT_VERSION:
            raise ValidationError(f"Unsupported checkpoint version in {p}.")
        for key in ("investigation_id", "mode", "org_id", "target"):
            if key not in data:
                raise ValidationError(f"Checkpoint {p} is missing {key!r}.")
        return cls(p, data)

    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = _now()
            atomic_write_json(self.path, self.data)

    def update_collection(self, index: int, **launch_fields) -> None:
        with self._lock:
            self.data["collections"][index]["launch"].update(launch_fields)
            self.save()

    def scope_fingerprint(self) -> str:
        d = self.data
        return sha256_json({"org": d["org_id"], "client": d["target"].get("client_id"),
                            "mode": d["mode"], "profile": (d.get("profile") or {}).get("fingerprint")})

    def check_scope(self, *, org_id: str | None = None, client_id: str | None = None,
                    profile_fingerprint: str | None = None) -> None:
        d = self.data
        if org_id and org_id != d["org_id"]:
            raise ScopeError(f"Checkpoint org {d['org_id']} does not match requested org {org_id}.")
        if client_id and client_id != d["target"].get("client_id"):
            raise ScopeError(
                f"Checkpoint client {d['target'].get('client_id')} does not match {client_id}.")
        prof = (d.get("profile") or {}).get("fingerprint")
        if profile_fingerprint and prof and profile_fingerprint != prof:
            raise ScopeError(
                "The profile changed since this run started (fingerprint mismatch). Resume uses "
                "the original plan; start a new investigation to use the edited profile.")










