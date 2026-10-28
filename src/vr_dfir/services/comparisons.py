"""Baseline comparison of two compatible saved captures ("What changed?").

Both captures are streamed from their saved JSONL files into a temporary SQLite table (bounded
memory), verified (rows on disk versus the manifest, row sequence and content hashes), keyed
by the domain adapter and classified group by group:

* Added: present only in the current capture, and the baseline was read completely with rows.
* Removed: present only in the baseline, and the current capture was read completely with rows
  and verified. A failed, partial, unverified, skipped or empty current capture never produces
  Removed; those entities are Unknown with the reason.
* Modified: present in both with different compared values (or a different number of
  occurrences). Unchanged: equal compared values (other fields are ignored, but can be shown).
* Unknown: absence not established, ambiguous duplicate keys, or rows without key fields.

A role present in only one capture's rows is a schema difference: it is excluded from the
comparison and reported, rather than turning every entity into a modification.

Results are stored in the current investigation (``derived/comparisons-v1.sqlite``), separate
from the evidence, keyed by a comparison ID that covers both captures' saved files and the
policy version. Creating a comparison never contacts Velociraptor.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from vr_dfir.analysis.compare import POLICY_VERSION, adapter_for, changed_fields, keyed_row
from vr_dfir.errors import DfirError, NotFoundError, ValidationError
from vr_dfir.evidence import presentation as pres
from vr_dfir.evidence.bundle import EvidenceBundle, now_iso, row_hash
from vr_dfir.evidence.exact import protect
from vr_dfir.jsonio import ensure_dir, sha256_json
from vr_dfir.services.captures import Capture, compatibility

STATES = ("added", "removed", "modified", "unchanged", "unknown")
STATE_LABELS = {"added": "Added", "removed": "Removed", "modified": "Modified",
                "unchanged": "Unchanged", "unknown": "Unknown"}
DB_FILE = "comparisons-v1.sqlite"
SCHEMA = """
CREATE TABLE IF NOT EXISTS comparisons(cmp_id TEXT PRIMARY KEY, created_at TEXT,
  definition TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS items(cmp_id TEXT, item_key TEXT, position INTEGER, state TEXT,
  key_display TEXT, changed TEXT, before_eids TEXT, after_eids TEXT, before_values TEXT,
  after_values TEXT, ignored_diff INTEGER, reason TEXT, PRIMARY KEY(cmp_id, item_key));
CREATE INDEX IF NOT EXISTS items_state ON items(cmp_id, state, position);
"""


class ComparisonCancelled(DfirError):
    code = "comparison_cancelled"


def db_path(inv) -> Path:
    return Path(inv.path) / "derived" / DB_FILE


def _connect(path: Path) -> sqlite3.Connection:
    ensure_dir(path.parent)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return conn


def _file_id(inv_dir: Path, capture: Capture) -> list:
    bundle = EvidenceBundle.open(inv_dir / "evidence")
    entry = bundle.source(capture.source_key)
    try:
        st = (inv_dir / "evidence" / entry.file).stat()
        return [entry.file, st.st_size, st.st_mtime_ns, entry.coverage.persisted_rows,
                entry.coverage.read_status, entry.coverage.result_status]
    except OSError:
        return [entry.file, None, None]


def comparison_id(store, current: Capture, baseline: Capture, settings=None) -> str:
    cur_dir = store.path_for(current.investigation_id)
    base_dir = store.path_for(baseline.investigation_id)
    return "cmp-" + sha256_json({
        "policy": POLICY_VERSION, "normalizer": pres.NORMALIZER_VERSION,
        "mappings": pres.config_fingerprint(getattr(settings, "source_mappings_path", "")),
        "current": [current.investigation_id, current.source_key, _file_id(cur_dir, current)],
        "baseline": [baseline.investigation_id, baseline.source_key, _file_id(base_dir, baseline)],
    })[:24]


@dataclass
class RunState:
    cmp_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    rows_done: int = 0
    rows_total: int = 0
    started: float = field(default_factory=time.time)
    finished: bool = False
    error: str | None = None
    thread: threading.Thread | None = None

    def progress(self) -> dict:
        return {"rows_done": self.rows_done, "rows_total": self.rows_total,
                "elapsed_seconds": round(time.time() - self.started, 1)}


def _stream(inv_dir: Path, capture: Capture, side: str, conn, adapter, configured,
            state: RunState | None) -> dict:
    _check_stop(state)
    bundle = EvidenceBundle.open(inv_dir / "evidence")
    entry = bundle.source(capture.source_key)
    mapping = pres.mapping_for(entry.artifact, entry.source, configured)
    path = inv_dir / "evidence" / entry.file
    stats = {"rows": 0, "sequence_ok": True, "hash_mismatches": 0, "keyed": 0, "skipped": 0,
             "unkeyed": 0, "unkeyed_reasons": Counter(), "roles": Counter()}
    batch = []
    if path.is_file():
        with open(path, "rb") as handle:
            for line in handle:
                if not line.endswith(b"\n"):
                    break
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("row_index") != stats["rows"]:
                    stats["sequence_ok"] = False
                row = record.get("row")
                if record.get("raw_sha256") != row_hash(row):
                    stats["hash_mismatches"] += 1
                stats["rows"] += 1
                fields = pres.resolve_fields(mapping, row=row if isinstance(row, dict) else {})
                k = keyed_row(adapter, row, fields)
                if k is None:
                    stats["skipped"] += 1
                elif k.key is None:
                    stats["unkeyed"] += 1
                    stats["unkeyed_reasons"][k.reason] += 1
                else:
                    stats["keyed"] += 1
                    for role in k.values:
                        stats["roles"][role] += 1
                    batch.append((side, k.key, k.key_display, json.dumps(k.values, sort_keys=True,
                                                                      default=str),
                                  json.dumps(k.raw, ensure_ascii=False, default=str),
                                  k.ignored_hash, record.get("evidence_id"),
                                  record.get("raw_sha256")))
                if len(batch) >= 2000:
                    conn.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?,?)", batch)
                    batch.clear()
                    if state:
                        state.rows_done += 2000
                    _check_stop(state)
    if batch:
        conn.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?,?)", batch)
    stats["verified"] = stats["sequence_ok"] and stats["hash_mismatches"] == 0 and \
        stats["rows"] == entry.coverage.persisted_rows
    stats["manifest_rows"] = entry.coverage.persisted_rows
    stats["unkeyed_reasons"] = dict(stats["unkeyed_reasons"])
    stats["roles"] = dict(stats["roles"])
    return stats


def _check_stop(state: RunState | None) -> None:
    if state is not None and state.stop.is_set():
        raise ComparisonCancelled("The comparison was cancelled.")


def _side_ok(c: Capture, stats: dict) -> tuple[bool, str | None]:
    if not stats["verified"]:
        return False, (f"saved rows do not match the manifest ({stats['rows']} on disk, "
                       f"{stats['manifest_rows']} recorded) or failed hash checks")
    if c.read_status != "complete":
        return False, f"read status is {c.read_status.replace('_', ' ')}"
    if c.result_status != "rows":
        return False, {"empty_or_skipped": "zero rows, and empty cannot be told from skipped",
                       "skipped_precondition": "skipped by a precondition",
                       "source_failed": "the source failed on the client",
                       "not_collected": "the source was not collected",
                       "unavailable": "the source was unavailable"}.get(
            c.result_status, f"result status is {c.result_status}")
    return True, None


def validate_pair(current: Capture, baseline: Capture):
    """The adapter for a valid (current, older baseline) pair, or a ValidationError."""
    reason = compatibility(current, baseline)
    if reason:
        raise ValidationError(f"These captures cannot be compared: {reason}")
    if current.observation == baseline.observation:
        raise ValidationError("A capture cannot be compared with itself.")
    if current.collected_ts is None or baseline.collected_ts is None:
        raise ValidationError("Both captures need a known capture time to be compared.")
    if baseline.collected_ts >= current.collected_ts:
        raise ValidationError("The baseline must be an older capture than the current one.")
    adapter = adapter_for(current.domain)
    if adapter is None:
        raise ValidationError("This kind of source has no comparison rules.")
    return adapter


def run(store, settings, current: Capture, baseline: Capture, *,
        state: RunState | None = None) -> str:
    """Compute and persist the comparison; returns its ID (idempotent per inputs)."""
    adapter = validate_pair(current, baseline)
    cmp_id = comparison_id(store, current, baseline, settings)
    cur_inv = store.get(current.investigation_id)
    path = db_path(cur_inv)
    with contextlib.closing(_connect(path)) as conn:
        if conn.execute("SELECT 1 FROM comparisons WHERE cmp_id=?", (cmp_id,)).fetchone():
            return cmp_id
    configured = pres.load_configured(getattr(settings, "source_mappings_path", ""))
    if state:
        state.rows_total = current.persisted_rows + baseline.persisted_rows
    tmp = sqlite3.connect(":memory:" if (current.persisted_rows + baseline.persisted_rows) < 20000
                          else str(path.parent / f".cmp-{os.getpid()}-{threading.get_ident()}.tmp"))
    try:
        tmp.execute("PRAGMA journal_mode=OFF")
        tmp.execute("CREATE TABLE t(side TEXT, key TEXT, disp TEXT, norm TEXT, raw TEXT, "
                    "ign TEXT, eid TEXT, sha TEXT)")
        b_stats = _stream(store.path_for(baseline.investigation_id), baseline, "b", tmp, adapter,
                          configured, state)
        c_stats = _stream(store.path_for(current.investigation_id), current, "c", tmp, adapter,
                          configured, state)
        tmp.execute("CREATE INDEX t_key ON t(key, side)")
        b_ok, b_why = _side_ok(baseline, b_stats)
        c_ok, c_why = _side_ok(current, c_stats)
        # Rows that cannot be keyed could be the "missing" entity: no absence is established.
        if c_ok and c_stats["unkeyed"]:
            c_ok, c_why = False, (f"{c_stats['unkeyed']} row(s) lack the key fields and "
                                  "cannot be matched")
        if b_ok and b_stats["unkeyed"]:
            b_ok, b_why = False, (f"{b_stats['unkeyed']} row(s) lack the key fields and "
                                  "cannot be matched")
        roles = [r for r in adapter.compare
                 if b_stats["roles"].get(r) and c_stats["roles"].get(r)]
        schema_only = sorted(set(b_stats["roles"]) ^ set(c_stats["roles"])) \
            if b_stats["keyed"] and c_stats["keyed"] else []
        incomparable = None
        if (b_stats["keyed"] == 0 and b_stats["rows"] > 0) or \
                (c_stats["keyed"] == 0 and c_stats["rows"] > 0):
            incomparable = ("The rows do not carry the fields this comparison needs as a key "
                            f"({', '.join(adapter.key)}); the schemas are not compatible.")
        items = [] if incomparable else _classify(tmp, roles, b_ok, b_why, c_ok, c_why)
        _check_stop(state)
        counts = Counter(i["state"] for i in items)
        ignored_only = sum(1 for i in items if i["state"] == "unchanged" and i["ignored_diff"])
        unknown_reasons = Counter(i["reason"] for i in items if i["state"] == "unknown")
        if c_stats["unkeyed"] or b_stats["unkeyed"]:
            unknown_reasons["rows without key fields could not be matched"] += \
                c_stats["unkeyed"] + b_stats["unkeyed"]
        limitations = []
        if not c_ok:
            limitations.append(f"Current capture: {c_why}. Absence cannot be established, so "
                               "nothing is reported as Removed.")
        if not b_ok:
            limitations.append(f"Baseline capture: {b_why}. New entries cannot be told from "
                               "missing baseline data, so nothing is reported as Added.")
        if schema_only:
            limitations.append("Fields present in only one capture are not compared: "
                               + ", ".join(schema_only) + ".")
        if adapter.note:
            limitations.append(adapter.note)
        definition = {
            "comparison_id": cmp_id, "policy_version": POLICY_VERSION,
            "normalizer_version": pres.NORMALIZER_VERSION, "adapter": adapter.id,
            "adapter_label": adapter.label, "key_roles": list(adapter.key),
            "compared_roles": roles, "volatile_roles": list(adapter.volatile),
            "current": current.to_dict(), "baseline": baseline.to_dict(),
        }
        summary = {"state": "incomparable" if incomparable else "complete",
                   "incomparable_reason": incomparable,
                   "counts": {s: counts.get(s, 0) for s in STATES},
                   "unchanged_with_ignored_differences": ignored_only,
                   "unknown_reasons": [{"reason": r, "count": n}
                                       for r, n in unknown_reasons.most_common()],
                   "limitations": limitations,
                   "current_side": {**_public(c_stats), "absence_established": c_ok},
                   "baseline_side": {**_public(b_stats), "absence_established": b_ok}}
        with contextlib.closing(_connect(path)) as conn:
            conn.execute("INSERT OR REPLACE INTO comparisons VALUES (?,?,?,?)",
                         (cmp_id, now_iso(), json.dumps(definition), json.dumps(summary)))
            conn.execute("DELETE FROM items WHERE cmp_id=?", (cmp_id,))
            conn.executemany(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(cmp_id, i["item_key"], n, i["state"], i["key_display"], json.dumps(i["changed"]),
                  json.dumps(i["before_eids"]), json.dumps(i["after_eids"]),
                  json.dumps(i["before_values"], ensure_ascii=False, default=str),
                  json.dumps(i["after_values"], ensure_ascii=False, default=str),
                  int(i["ignored_diff"]), i["reason"]) for n, i in enumerate(items)])
            conn.commit()
        return cmp_id
    finally:
        tmp.close()
        for leftover in path.parent.glob(f".cmp-{os.getpid()}-{threading.get_ident()}.tmp"):
            with contextlib.suppress(OSError):
                leftover.unlink()


def _public(stats: dict) -> dict:
    return {k: stats[k] for k in ("rows", "manifest_rows", "keyed", "skipped", "unkeyed",
                                  "verified", "hash_mismatches", "unkeyed_reasons")}


def _classify(tmp, roles, b_ok, b_why, c_ok, c_why) -> list[dict]:
    out = []
    group: list = []
    current_key = None

    def flush(rows):
        if not rows:
            return
        sides = {"b": [r for r in rows if r[0] == "b"], "c": [r for r in rows if r[0] == "c"]}

        def proj(r):
            norm = json.loads(r[3])
            return json.dumps({k: norm.get(k, {"$absent": True}) for k in roles}, sort_keys=True)
        distinct = {s: {proj(r) for r in v} for s, v in sides.items()}
        item = {"item_key": rows[0][1], "key_display": rows[0][2],
                "before_eids": [r[6] for r in sides["b"]][:20],
                "after_eids": [r[6] for r in sides["c"]][:20], "changed": [],
                "before_values": json.loads(sides["b"][0][4]) if sides["b"] else None,
                "after_values": json.loads(sides["c"][0][4]) if sides["c"] else None,
                "ignored_diff": False, "reason": None}
        if len(distinct["b"]) > 1 or len(distinct["c"]) > 1:
            item.update(state="unknown", reason="the same key appears with different values "
                                                "(ambiguous duplicate entries)")
        elif sides["b"] and sides["c"]:
            before = json.loads(next(iter(distinct["b"])))
            after = json.loads(next(iter(distinct["c"])))
            changed = changed_fields(before, after)
            if changed:
                item.update(state="modified", changed=changed)
            elif len(sides["b"]) != len(sides["c"]):
                item.update(state="modified", changed=["occurrences"],
                            reason=f"occurrences {len(sides['b'])} → {len(sides['c'])}")
            else:
                item.update(state="unchanged",
                            ignored_diff={r[5] for r in sides["b"]} != {r[5] for r in sides["c"]})
        elif sides["c"]:
            if b_ok:
                item.update(state="added")
            else:
                item.update(state="unknown",
                            reason=f"not in the baseline, which cannot establish absence: {b_why}")
        elif c_ok:
            item.update(state="removed")
        else:
            item.update(state="unknown", reason="not in the current capture, which cannot "
                                                f"establish absence: {c_why}")
        out.append(item)

    for r in tmp.execute("SELECT side, key, disp, norm, raw, ign, eid, sha FROM t "
                         "ORDER BY key, side, eid"):
        if r[1] != current_key:
            flush(group)
            group = []
            current_key = r[1]
        group.append(r)
    flush(group)
    order = {"modified": 0, "added": 1, "removed": 2, "unknown": 3, "unchanged": 4}
    out.sort(key=lambda i: (order[i["state"]], i["key_display"]))
    return out


def load(inv, cmp_id: str) -> dict:
    path = db_path(inv)
    if not path.is_file():
        raise NotFoundError("This comparison has not been created.")
    with contextlib.closing(_connect(path)) as conn:
        row = conn.execute("SELECT * FROM comparisons WHERE cmp_id=?", (cmp_id,)).fetchone()
    if row is None:
        raise NotFoundError("This comparison has not been created.")
    return {"comparison_id": cmp_id, "created_at": row["created_at"],
            "definition": json.loads(row["definition"]), "summary": json.loads(row["summary"])}


def _item(r) -> dict:
    return {"item_key": r["item_key"], "state": r["state"], "state_label": STATE_LABELS[r["state"]],
            "key_display": r["key_display"], "changed": json.loads(r["changed"]),
            "before_eids": json.loads(r["before_eids"]), "after_eids": json.loads(r["after_eids"]),
            "before_values": protect(json.loads(r["before_values"])),
            "after_values": protect(json.loads(r["after_values"])),
            "ignored_diff": bool(r["ignored_diff"]), "reason": r["reason"]}


def items(inv, cmp_id: str, *, state: str | None = None, show_unchanged: bool = False,
          ignored: bool = False, offset: int = 0, limit: int = 50) -> dict:
    """A page of comparison items. Unchanged items appear only when asked for."""
    if state and state not in STATES:
        raise ValidationError("Unknown change state.")
    if not (1 <= limit <= 200) or offset < 0:
        raise ValidationError("limit must be 1-200 and offset non-negative.")
    clauses, params = ["cmp_id=?"], [cmp_id]
    if state:
        clauses.append("state=?")
        params.append(state)
    elif not show_unchanged:
        clauses.append("(state != 'unchanged'" + (" OR ignored_diff = 1)" if ignored else ")"))
    if state == "unchanged" and ignored:
        clauses.append("ignored_diff = 1")
    where = " AND ".join(clauses)
    with contextlib.closing(_connect(db_path(inv))) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM items WHERE {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM items WHERE {where} ORDER BY position LIMIT ? "
                            "OFFSET ?", [*params, limit, offset]).fetchall()
    return {"items": [_item(r) for r in rows], "total": total, "offset": offset, "limit": limit}


def item(inv, cmp_id: str, item_key: str) -> dict:
    with contextlib.closing(_connect(db_path(inv))) as conn:
        r = conn.execute("SELECT * FROM items WHERE cmp_id=? AND item_key=?",
                         (cmp_id, item_key)).fetchone()
    if r is None:
        raise NotFoundError("This change is not part of the comparison.")
    return _item(r)


def cached_for(inv, current_key: str) -> list[dict]:
    """Comparisons already computed for one current source of this investigation."""
    path = db_path(inv)
    if not path.is_file():
        return []
    with contextlib.closing(_connect(path)) as conn:
        out = []
        for r in conn.execute("SELECT cmp_id, created_at, definition, summary FROM comparisons "
                              "ORDER BY created_at DESC"):
            d = json.loads(r["definition"])
            if d["current"]["source_key"] == current_key:
                out.append({"comparison_id": r["cmp_id"], "created_at": r["created_at"],
                            "baseline": d["baseline"], "summary": json.loads(r["summary"]),
                            "policy_version": d["policy_version"]})
        return out


class ComparisonManager:
    """Runs comparisons in the background with progress and cancellation."""

    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(2)

    def start(self, current: Capture, baseline: Capture, *, wait: float = 1.5) -> dict:
        validate_pair(current, baseline)
        cmp_id = comparison_id(self.store, current, baseline, self.settings)
        inv = self.store.get(current.investigation_id)
        try:
            return {"state": "ready", **load(inv, cmp_id)}
        except NotFoundError:
            pass
        with self._lock:
            st = self._runs.get(cmp_id)
            if st is None or (st.finished and st.error):
                st = RunState(cmp_id)
                self._runs[cmp_id] = st
                st.thread = threading.Thread(target=self._run, args=(current, baseline, st),
                                             daemon=True, name=f"vrdfir-cmp-{cmp_id}")
                st.thread.start()
        if wait and st.thread:
            st.thread.join(timeout=wait)
        if st.finished and not st.error:
            return {"state": "ready", **load(inv, cmp_id)}
        if st.finished:
            return {"state": "failed", "comparison_id": cmp_id, "error": st.error}
        return {"state": "building", "comparison_id": cmp_id, "progress": st.progress()}

    def status(self, inv, cmp_id: str) -> dict:
        try:
            return {"state": "ready", **load(inv, cmp_id)}
        except NotFoundError:
            pass
        with self._lock:
            st = self._runs.get(cmp_id)
        if st is None:
            raise NotFoundError("This comparison has not been created.")
        if st.finished and st.error:
            return {"state": "failed", "comparison_id": cmp_id, "error": st.error}
        return {"state": "building", "comparison_id": cmp_id, "progress": st.progress()}

    def cancel(self, cmp_id: str) -> bool:
        with self._lock:
            st = self._runs.get(cmp_id)
        if st is None or st.finished:
            return False
        st.stop.set()
        return True

    def _run(self, current, baseline, st: RunState) -> None:
        with self._slots:
            try:
                run(self.store, self.settings, current, baseline, state=st)
            except DfirError as exc:
                st.error = exc.message
            except Exception as exc:  # noqa: BLE001 - recorded and shown; never left "building"
                st.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                st.finished = True
