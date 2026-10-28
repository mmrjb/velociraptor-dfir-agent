"""Report selection: pinned evidence rows, findings and changes, with analyst notes.

Stored per investigation as ``selection.json`` (current state) plus ``selection.jsonl``
(append-only history). Every read-modify-write holds an exclusive file lock, so the UI, CLI
and MCP never lose each other's pins. If the snapshot is unreadable it is rebuilt from the
history log; nothing is silently emptied.

A pin stores a stable reference and a snapshot built by the server from saved data (never
from the browser): evidence ID with its content hash and position, a finding with the exact
evidence IDs it cited, or a change with both observations and the comparison policy.
Unpinning removes the pin only; evidence, findings and review decisions are untouched.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path

from vr_dfir.config import current_user
from vr_dfir.errors import NotFoundError, ValidationError
from vr_dfir.evidence.bundle import now_iso
from vr_dfir.jsonio import JsonlWriter, atomic_write_json, iter_jsonl, read_json

SELECTION_VERSION = 1
MAX_PINS = 500
MAX_NOTE = 2000
KINDS = ("evidence", "finding", "change")


def pin_id(kind: str, ref: str) -> str:
    return "pin-" + hashlib.sha256(f"{kind}|{ref}".encode()).hexdigest()[:16]


class SelectionStore:
    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.path = self.dir / "selection.json"
        self.log = self.dir / "selection.jsonl"
        self.lock_path = self.dir / ".selection.lock"

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _replay(self) -> dict[str, dict]:
        pins: dict[str, dict] = {}
        if not self.log.is_file():
            return pins
        try:
            entries = [e for _, e in iter_jsonl(self.log)]
        except (OSError, ValueError):
            entries = []
            with open(self.log, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        continue
        for e in entries:
            op, pid = e.get("op"), e.get("pin_id")
            if op == "add" and isinstance(e.get("pin"), dict):
                pins[pid] = e["pin"]
            elif op == "note" and pid in pins:
                pins[pid] = {**pins[pid], "note": e.get("note"), "updated_at": e.get("at")}
            elif op == "remove":
                pins.pop(pid, None)
        return pins

    def _load(self) -> tuple[dict[str, dict], bool]:
        if not self.path.is_file():
            return self._replay(), False
        try:
            data = read_json(self.path)
            if not isinstance(data, dict) or not isinstance(data.get("pins"), list):
                raise ValueError("unexpected selection format")
            return {p["pin_id"]: p for p in data["pins"]}, False
        except (OSError, ValueError, KeyError, TypeError):
            return self._replay(), True

    def _save(self, pins: dict[str, dict]) -> None:
        atomic_write_json(self.path, {"selection_version": SELECTION_VERSION,
                                      "updated_at": now_iso(), "pins": list(pins.values())})

    def _append(self, entry: dict) -> None:
        with JsonlWriter(self.log) as log:
            log.write({"at": now_iso(), "by": current_user(), **entry})

    def list(self) -> list[dict]:
        with self._locked():
            pins, recovered = self._load()
            if recovered:
                self._save(pins)
        return sorted(pins.values(), key=lambda p: p.get("created_at") or "")

    def add(self, kind: str, ref: str, *, title: str, snapshot: dict,
            note: str | None = None) -> dict:
        if kind not in KINDS:
            raise ValidationError(f"Pin kind must be one of {', '.join(KINDS)}.")
        note = _note(note)
        pid = pin_id(kind, ref)
        with self._locked():
            pins, _ = self._load()
            if pid in pins:
                return pins[pid]
            if len(pins) >= MAX_PINS:
                raise ValidationError(f"The report selection is limited to {MAX_PINS} pins.")
            now = now_iso()
            pin = {"pin_id": pid, "kind": kind, "ref": ref, "title": title[:300], "note": note,
                   "created_at": now, "updated_at": now, "snapshot": snapshot}
            pins[pid] = pin
            self._append({"op": "add", "pin_id": pid, "pin": pin})
            self._save(pins)
            return pin

    def set_note(self, pid: str, note: str | None) -> dict:
        note = _note(note)
        with self._locked():
            pins, _ = self._load()
            if pid not in pins:
                raise NotFoundError("This pin is not in the report selection.")
            pins[pid] = {**pins[pid], "note": note, "updated_at": now_iso()}
            self._append({"op": "note", "pin_id": pid, "note": note})
            self._save(pins)
            return pins[pid]

    def remove(self, pid: str) -> bool:
        with self._locked():
            pins, _ = self._load()
            if pid not in pins:
                return False
            pins.pop(pid)
            self._append({"op": "remove", "pin_id": pid})
            self._save(pins)
            return True


def _note(note: str | None) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str):
        raise ValidationError("A note must be text.")
    if len(note) > MAX_NOTE:
        raise ValidationError(f"A note is limited to {MAX_NOTE} characters.")
    return note.strip() or None


# ----------------------------------------------------------------------- snapshots

def _evidence_snapshot(inv, indexes, eid: str) -> tuple[str, dict]:
    idx = indexes.require(inv, timeout=60)
    with idx.db() as conn:
        r = conn.execute("SELECT r.row_index, r.offset AS row_offset, r.length AS row_length, "
                         "r.title, s.* FROM rows r JOIN sources s ON s.sid=r.sid "
                         "WHERE r.evidence_id=?", (eid,)).fetchone()
    if r is None:
        raise NotFoundError("This evidence row is not in the investigation.")
    key = (r["sid"], r["row_index"])
    rec = idx.read_records([(*key, r["row_offset"], r["row_length"])], {r["sid"]: r["file"]})[key]
    snap = {"evidence_id": eid, "source_key": r["source_key"], "org_id": r["org_id"],
            "client_id": r["client_id"], "hostname": r["hostname"], "flow_id": r["flow_id"],
            "full_name": r["full_name"], "row_index": r["row_index"],
            "raw_sha256": rec.get("raw_sha256"), "collected_at": r["collected_at"]}
    return f"{r['title']} — {r['hostname'] or r['client_id']}", snap


def _finding_snapshot(inv, fid: str) -> tuple[str, dict]:
    path = inv.report_dir / "report.json"
    if not path.is_file():
        raise NotFoundError("The report has not been generated yet.")
    report = read_json(path)
    f = next((x for x in report.get("findings") or [] if x.get("finding_id") == fid), None)
    if f is None:
        raise NotFoundError("This finding is not in the investigation's report.")
    return f"{fid}: {f.get('title')}", {
        "finding_id": fid, "severity": f.get("severity"), "origin": f.get("origin"),
        "evidence_ids": list(dict.fromkeys(f.get("evidence_ids") or [])),
        "report_generated_at": report.get("generated_at")}


def _change_snapshot(inv, cid: str, key: str) -> tuple[str, dict]:
    from vr_dfir.services import comparisons
    loaded = comparisons.load(inv, cid)
    it = comparisons.item(inv, cid, key)
    d = loaded["definition"]
    fields = ("investigation_id", "source_key", "flow_id", "collected_at", "full_name")
    snap = {"comparison_id": cid, "item_key": key, "state": it["state"],
            "key_display": it["key_display"], "changed": it["changed"], "reason": it["reason"],
            "before_values": it["before_values"], "after_values": it["after_values"],
            "before_eids": it["before_eids"], "after_eids": it["after_eids"],
            "policy_version": d["policy_version"], "adapter_label": d["adapter_label"],
            "baseline": {k: d["baseline"].get(k) for k in fields},
            "current": {k: d["current"].get(k) for k in fields}}
    return f"{it['state_label']}: {it['key_display']} ({d['adapter_label']})", snap


def make_pin(inv, indexes, kind: str, *, ref: str | None = None,
             comparison_id: str | None = None, item_key: str | None = None,
             note: str | None = None) -> dict:
    """Pin one item after resolving it in saved data (shared by the UI, CLI and MCP)."""
    if kind == "evidence":
        if not ref:
            raise ValidationError("An evidence pin needs the evidence ID.")
        title, snap = _evidence_snapshot(inv, indexes, ref)
    elif kind == "finding":
        if not ref:
            raise ValidationError("A finding pin needs the finding ID.")
        title, snap = _finding_snapshot(inv, ref)
    elif kind == "change":
        if not comparison_id or item_key is None:
            raise ValidationError("A change pin needs the comparison and the item.")
        title, snap = _change_snapshot(inv, comparison_id, item_key)
        ref = f"{comparison_id}|{item_key}"
    else:
        raise ValidationError(f"Pin kind must be one of {', '.join(KINDS)}.")
    return SelectionStore(inv.path).add(kind, ref, title=title, snapshot=snap, note=note)
