"""Derived presentation index of one investigation (``<inv>/derived/index-v1.sqlite``).

The evidence bundle (``manifest.json`` + JSONL) stays the only source of truth. This index is
disposable metadata built by streaming the saved rows once with bounded memory:

* row locations (byte offset/length), so table pages and exact rows are read directly;
* mapped presentation data per row: title, event time with its parsing status, entity keys
  and process identifiers;
* per-source verification: rows on disk versus the manifest, sequence and content hashes;
* findings from the generated report and the exact rows they cite.

Its identity (``fingerprint``) covers the manifest, every evidence file's size and mtime, the
report, the normalizer version and the operator mapping configuration. A changed input makes
the index stale; it is rebuilt into a temporary file and atomically replaced. Builds run in a
background thread with progress, and can be cancelled without touching evidence or reviews.
SQLite is used through the standard library as an embedded file; there is no server.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from vr_dfir.errors import DfirError, NotFoundError
from vr_dfir.evidence import presentation as pres
from vr_dfir.evidence.bundle import EvidenceBundle, now_iso, row_hash
from vr_dfir.evidence.timeparse import parse_time
from vr_dfir.jsonio import ensure_dir, read_json, sha256_json

INDEX_VERSION = 1
INDEX_FILE = f"index-v{INDEX_VERSION}.sqlite"
BATCH = 2000
MAX_COLUMNS = 300
SAMPLE_ROWS = 200

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sources(
  sid INTEGER PRIMARY KEY, source_key TEXT UNIQUE, org_id TEXT, client_id TEXT, hostname TEXT,
  flow_id TEXT, hunt_id TEXT, artifact TEXT, source TEXT, full_name TEXT, file TEXT,
  collected_at TEXT, counts_only INTEGER, read_status TEXT, result_status TEXT,
  stop_reason TEXT, available_rows INTEGER, persisted_rows INTEGER, indexed_rows INTEGER,
  sequence_ok INTEGER, hash_mismatches INTEGER, domain TEXT, mapping_json TEXT,
  fields_json TEXT, columns_json TEXT, time_json TEXT, time_min INTEGER, time_max INTEGER,
  timed_rows INTEGER, untimed_rows INTEGER, host_os TEXT);
CREATE TABLE rows(
  sid INTEGER, row_index INTEGER, evidence_id TEXT, offset INTEGER, length INTEGER,
  event_ts INTEGER, time_status TEXT, title TEXT, PRIMARY KEY(sid, row_index));
CREATE TABLE entities(sid INTEGER, row_index INTEGER, kind TEXT, key TEXT, display TEXT);
CREATE TABLE entity_fields(sid INTEGER, kind TEXT, PRIMARY KEY(sid, kind));
CREATE TABLE procs(sid INTEGER, row_index INTEGER, pid TEXT, ppid TEXT, start_ts INTEGER);
CREATE TABLE findings(
  finding_id TEXT PRIMARY KEY, title TEXT, severity TEXT, confidence TEXT, origin TEXT,
  rule_id TEXT, evidence_count INTEGER, position INTEGER);
CREATE TABLE finding_rows(finding_id TEXT, evidence_id TEXT, sid INTEGER, row_index INTEGER);
CREATE TABLE finding_clients(finding_id TEXT, client_id TEXT);
"""
POST_INDEXES = """
CREATE INDEX rows_eid ON rows(evidence_id);
CREATE INDEX rows_time ON rows(event_ts);
CREATE INDEX ent_kind_key ON entities(kind, key);
CREATE INDEX ent_row ON entities(sid, row_index);
CREATE INDEX procs_pid ON procs(sid, pid);
CREATE INDEX fr_row ON finding_rows(sid, row_index);
CREATE INDEX fr_finding ON finding_rows(finding_id);
"""


class IndexNotReady(DfirError):
    code = "index_not_ready"


class BuildCancelled(DfirError):
    code = "index_build_cancelled"


def derived_dir(inv) -> Path:
    return Path(inv.path) / "derived"


def index_path(inv) -> Path:
    return derived_dir(inv) / INDEX_FILE


def fingerprint(inv, settings) -> str | None:
    """Identity of every input of the index, or ``None`` when there is no saved evidence."""
    manifest_path = inv.evidence_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = read_json(manifest_path)
    files = []
    for s in manifest.get("sources") or []:
        try:
            st = (inv.evidence_dir / str(s.get("file", ""))).stat()
            files.append([s.get("file"), st.st_size, st.st_mtime_ns])
        except OSError:
            files.append([s.get("file"), None, None])
    report = inv.report_dir / "report.json"
    try:
        rst = report.stat()
        report_id = [rst.st_size, rst.st_mtime_ns]
    except OSError:
        report_id = None
    return sha256_json({
        "index_version": INDEX_VERSION, "normalizer": pres.NORMALIZER_VERSION,
        "manifest": {k: v for k, v in manifest.items() if k != "updated_at"},
        "files": files, "report": report_id,
        "mappings": pres.config_fingerprint(getattr(settings, "source_mappings_path", "")),
    })


# ----------------------------------------------------------------------- reading

def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False,
                           timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class Index:
    """Read-only handle on a fresh index."""

    path: Path
    inv: object
    meta: dict

    @contextlib.contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def sources(self) -> list[dict]:
        with self.db() as conn:
            return [_source_dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY sid")]

    def read_records(self, items: list[tuple[int, int, int, int]],
                     files: dict[int, str]) -> dict[tuple[int, int], dict]:
        """Exact saved records for ``(sid, row_index, offset, length)`` items."""
        out: dict[tuple[int, int], dict] = {}
        by_sid: dict[int, list] = {}
        for item in items:
            by_sid.setdefault(item[0], []).append(item)
        for sid, group in by_sid.items():
            path = Path(self.inv.evidence_dir) / files[sid]
            with open(path, "rb") as handle:
                for _, row_index, offset, length in sorted(group, key=lambda x: x[2]):
                    handle.seek(offset)
                    data = handle.read(length)
                    record = json.loads(data)
                    if record.get("row_index") != row_index:
                        raise NotFoundError("A saved row no longer matches the index; reload "
                                            "after the index is rebuilt.")
                    out[(sid, row_index)] = record
        return out


def _source_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for key in ("mapping_json", "fields_json", "columns_json", "time_json"):
        d[key[:-5]] = json.loads(d.pop(key) or "null")
    d["verified"] = bool(d["sequence_ok"]) and d["hash_mismatches"] == 0 and \
        d["indexed_rows"] == d["persisted_rows"]
    return d


# ----------------------------------------------------------------------- building

@dataclass
class BuildState:
    fingerprint: str
    stop: threading.Event = field(default_factory=threading.Event)
    rows_done: int = 0
    rows_total: int = 0
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    error: str | None = None
    cancelled: bool = False
    thread: threading.Thread | None = None

    def progress(self) -> dict:
        return {"rows_done": self.rows_done, "rows_total": self.rows_total,
                "elapsed_seconds": round(time.time() - self.started_at, 1)}


def build(inv, settings, *, state: BuildState | None = None,
          progress: Callable[[int, int], None] | None = None) -> Path:
    """Build the index into a temporary file and atomically install it."""
    bundle = EvidenceBundle.open(inv.evidence_dir)
    fp = fingerprint(inv, settings)
    out_dir = ensure_dir(derived_dir(inv))
    final = out_dir / INDEX_FILE
    lock_fd = os.open(out_dir / ".build.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if final.is_file() and _stored_fingerprint(final) == fp:
            return final  # another builder finished it while we waited
        tmp = out_dir / f".{INDEX_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        try:
            _build_into(tmp, inv, bundle, settings, fp, state, progress)
            os.chmod(tmp, 0o600)
            os.replace(tmp, final)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            for leftover in out_dir.glob(f".{INDEX_FILE}.*.tmp"):
                with contextlib.suppress(OSError):
                    if time.time() - leftover.stat().st_mtime > 3600:
                        leftover.unlink()
        return final
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _stored_fingerprint(path: Path) -> str | None:
    try:
        conn = connect(path)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _build_into(tmp: Path, inv, bundle: EvidenceBundle, settings, fp: str,
                state: BuildState | None, progress) -> None:
    configured = pres.load_configured(getattr(settings, "source_mappings_path", ""))
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        entries = bundle.sources()
        total = sum(e.coverage.persisted_rows for e in entries)
        done = 0
        if state:
            state.rows_total = total
        for sid, entry in enumerate(entries, start=1):
            _check_stop(state)
            done = _index_source(conn, inv, sid, entry, configured, state, progress, done, total)
        _check_stop(state)
        _index_findings(conn, inv, bundle.manifest.get("legacy_aliases") or {})
        conn.executescript(POST_INDEXES)
        org_ids = sorted({e.org_id for e in entries if e.org_id})
        meta = {"fingerprint": fp, "index_version": str(INDEX_VERSION),
                "normalizer_version": pres.NORMALIZER_VERSION, "built_at": now_iso(),
                "bundle_id": bundle.bundle_id, "investigation_id": inv.id,
                "org_ids": json.dumps(org_ids), "synthetic": json.dumps(bundle.synthetic),
                "rows": str(done)}
        conn.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
        conn.commit()
        _check_stop(state)
    finally:
        conn.close()


def _check_stop(state: BuildState | None) -> None:
    if state is not None and state.stop.is_set():
        raise BuildCancelled("The index build was cancelled.")


def _index_source(conn, inv, sid, entry, configured, state, progress, done, total) -> int:
    mapping = pres.mapping_for(entry.artifact, entry.source, configured)
    path = Path(inv.evidence_dir) / entry.file
    columns: list[str] = []
    seen_cols: set[str] = set()
    resolution: Counter = Counter()
    kinds_seen: set[str] = set()
    time_counts: Counter = Counter()
    time_basis: dict | None = None
    tmin = tmax = None
    host_os = _artifact_os(entry.artifact)
    rows_batch, ent_batch, proc_batch = [], [], []
    expected = 0
    sequence_ok = True
    mismatches = 0
    sampled = 0
    if path.is_file():
        with open(path, "rb") as handle:
            offset = 0
            for line in handle:
                length = len(line)
                if not line.endswith(b"\n"):
                    break  # partial trailing line from an interrupted write: not saved data
                stripped = line.strip()
                if not stripped:
                    offset += length
                    continue
                record = json.loads(stripped)
                row_index = record.get("row_index")
                if row_index != expected:
                    sequence_ok = False
                expected += 1
                row = record.get("row")
                if record.get("raw_sha256") != row_hash(row):
                    mismatches += 1
                if isinstance(row, dict) and len(seen_cols) < MAX_COLUMNS:
                    for key in row:
                        if key not in seen_cols and len(seen_cols) < MAX_COLUMNS:
                            seen_cols.add(key)
                            columns.append(key)
                fields = pres.resolve_fields(mapping, row=row if isinstance(row, dict) else {})
                if mapping.id == "system" and expected == 1 and isinstance(row, dict):
                    os_value = pres.role_value(row, fields, "os")
                    if isinstance(os_value, str) and os_value.strip():
                        host_os = os_value.strip().lower()
                if sampled < SAMPLE_ROWS:
                    resolution[json.dumps(fields, sort_keys=True)] += 1
                    sampled += 1
                parsed, basis = pres.event_time(mapping, row, fields) if isinstance(row, dict) \
                    else (pres.ParsedTime("unmapped"), None)
                if basis and time_basis is None:
                    time_basis = basis
                time_counts[parsed.status] += 1
                ts = parsed.micros if parsed.ok else None
                if ts is not None:
                    tmin = ts if tmin is None else min(tmin, ts)
                    tmax = ts if tmax is None else max(tmax, ts)
                rows_batch.append((sid, row_index, record.get("evidence_id"), offset, length, ts,
                                   parsed.status, pres.title(mapping, row, fields)))
                if isinstance(row, dict):
                    kinds_seen |= pres.entity_fields(mapping, fields)
                    for kind, key, display in pres.entities(mapping, row, fields):
                        ent_batch.append((sid, row_index, kind, key, display[:300]))
                    if mapping.id in ("process", "network"):
                        pid = pres.role_value(row, fields, "pid")
                        if pid is not pres._MISSING and pid is not None and pid != "":
                            ppid = pres.role_value(row, fields, "ppid") \
                                if mapping.id == "process" else None
                            start = None
                            if mapping.id == "process":
                                # Identity checks need the start time even when an operator
                                # mapping chose another field as the event time.
                                start = ts if not mapping.time_override else _start_time(
                                    row, fields)
                            proc_batch.append((sid, row_index, str(pid),
                                               None if ppid in (pres._MISSING, None, "")
                                               else str(ppid), start))
                offset += length
                done += 1
                if len(rows_batch) >= BATCH:
                    _flush(conn, rows_batch, ent_batch, proc_batch)
                    if state:
                        state.rows_done = done
                        if state.stop.is_set():
                            raise BuildCancelled("The index build was cancelled.")
                    if progress:
                        progress(done, total)
    _flush(conn, rows_batch, ent_batch, proc_batch)
    best = json.loads(resolution.most_common(1)[0][0]) if resolution else \
        pres.resolve_fields(mapping, columns=columns)
    conn.executemany("INSERT OR IGNORE INTO entity_fields VALUES (?, ?)",
                     [(sid, k) for k in sorted(kinds_seen)])
    cov = entry.coverage
    timed = time_counts.get("ok", 0)
    conn.execute(
        "INSERT INTO sources VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, entry.source_key, entry.org_id, entry.client_id, entry.hostname, entry.flow_id,
         entry.hunt_id, entry.artifact, entry.source, entry.full_name, entry.file,
         entry.collected_at, int(entry.counts_only), cov.read_status, cov.result_status,
         cov.stop_reason, cov.available_rows, cov.persisted_rows, expected, int(sequence_ok),
         mismatches, mapping.id, json.dumps(mapping.describe()), json.dumps(best),
         json.dumps(pres.column_plan(mapping, best, columns)),
         json.dumps({"basis": time_basis, "statuses": dict(time_counts),
                     "rules": [{"role": r.role, "format": r.fmt, "meaning": r.meaning}
                               for r in mapping.event_rules()]}),
         tmin, tmax, timed, expected - timed, host_os))
    if state:
        state.rows_done = done
    return done


def _start_time(row: dict, fields: dict) -> int | None:
    name = fields.get("start")
    if not name:
        return None
    parsed = parse_time(pres.get_field(row, name), "iso8601")
    return parsed.micros if parsed.ok else None


def _artifact_os(artifact: str) -> str | None:
    """OS family implied by a documented artifact namespace (``Linux.*``, ``Windows.*``)."""
    head = (artifact or "").split(".", 1)[0].lower()
    return {"linux": "linux", "windows": "windows", "macos": "darwin"}.get(head)


def _flush(conn, rows_batch, ent_batch, proc_batch) -> None:
    if rows_batch:
        conn.executemany("INSERT OR REPLACE INTO rows VALUES (?,?,?,?,?,?,?,?)", rows_batch)
        rows_batch.clear()
    if ent_batch:
        conn.executemany("INSERT INTO entities VALUES (?,?,?,?,?)", ent_batch)
        ent_batch.clear()
    if proc_batch:
        conn.executemany("INSERT INTO procs VALUES (?,?,?,?,?)", proc_batch)
        proc_batch.clear()


def _index_findings(conn, inv, aliases: dict) -> None:
    path = inv.report_dir / "report.json"
    if not path.is_file():
        conn.execute("INSERT INTO meta VALUES ('findings_ready', 'false')")
        return
    report = read_json(path)
    conn.execute("INSERT INTO meta VALUES ('findings_ready', 'true')")
    conn.execute("INSERT INTO meta VALUES ('report_generated_at', ?)",
                 (str(report.get("generated_at") or ""),))
    conn.execute("CREATE TEMP TABLE eids AS SELECT evidence_id, sid, row_index FROM rows")
    conn.execute("CREATE INDEX temp.eids_i ON eids(evidence_id)")
    for pos, f in enumerate(report.get("findings") or []):
        fid = f.get("finding_id")
        if not isinstance(fid, str):
            continue
        eids = [e for e in dict.fromkeys(f.get("evidence_ids") or []) if isinstance(e, str)]
        conn.execute("INSERT OR IGNORE INTO findings VALUES (?,?,?,?,?,?,?,?)",
                     (fid, f.get("title"), f.get("severity"), f.get("confidence"),
                      f.get("origin"), f.get("rule_id"), len(eids), pos))
        for eid in eids:
            hit = conn.execute("SELECT sid, row_index FROM eids WHERE evidence_id=?",
                               (eid,)).fetchone()
            if hit is None and isinstance(aliases.get(eid), str):
                # Findings saved before an evidence-ID migration cite the legacy ID.
                hit = conn.execute("SELECT sid, row_index FROM eids WHERE evidence_id=?",
                                   (aliases[eid],)).fetchone()
            conn.execute("INSERT INTO finding_rows VALUES (?,?,?,?)",
                         (fid, eid, hit[0] if hit else None, hit[1] if hit else None))
        for cid in dict.fromkeys(f.get("client_ids") or []):
            conn.execute("INSERT INTO finding_clients VALUES (?, ?)", (fid, cid))


# ----------------------------------------------------------------------- manager

def _ended(state: BuildState) -> dict:
    return {"state": "cancelled" if state.cancelled else "failed", "error": state.error}


class IndexManager:
    """Builds indexes in the background (bounded) and hands out fresh read handles."""

    def __init__(self, settings, max_concurrent: int = 2):
        self.settings = settings
        self._builds: dict[str, BuildState] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(max_concurrent)

    def current(self, inv) -> Index | None:
        fp = fingerprint(inv, self.settings)
        path = index_path(inv)
        if fp is None or not path.is_file():
            return None
        stored = _stored_fingerprint(path)
        if stored != fp:
            return None
        with contextlib.closing(connect(path)) as conn:
            meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM meta")}
        return Index(path, inv, meta)

    def status(self, inv) -> dict:
        with self._lock:
            state = self._builds.get(inv.id)
        if state and not state.finished:
            return {"state": "building", "progress": state.progress()}
        if state and state.error:
            return _ended(state)
        return {"state": "ready" if self.current(inv) else "stale"}

    def ensure(self, inv, *, wait: float = 0.0, job_active: bool = False,
               retry: bool = False) -> Index | dict:
        """A fresh index, or a status dict while it is being built.

        While a retrieval/analysis job is changing the evidence no index is built; the
        status says so instead of indexing a moving target. A build that was cancelled or
        failed for the same evidence version is not restarted by polling; only an explicit
        ``retry`` (or changed evidence) starts a new one.
        """
        fp = fingerprint(inv, self.settings)
        if fp is None:
            return {"state": "no_evidence"}
        idx = self.current(inv)
        if idx is not None:
            return idx
        if job_active:
            return {"state": "evidence_changing"}
        with self._lock:
            state = self._builds.get(inv.id)
            if state and not state.finished and state.fingerprint != fp:
                state.stop.set()  # superseded by newer evidence
                state = None
            if state and state.finished and state.error and state.fingerprint == fp \
                    and not retry:
                return _ended(state)
            if state is None or state.finished:
                state = BuildState(fp)
                self._builds[inv.id] = state
                state.thread = threading.Thread(target=self._run, args=(inv, state),
                                                name=f"vrdfir-index-{inv.id}", daemon=True)
                state.thread.start()
        if wait > 0 and state.thread is not None:
            state.thread.join(timeout=wait)
        if state.finished and not state.error:
            idx = self.current(inv)
            if idx is not None:
                return idx
        if state.finished and state.error:
            return _ended(state)
        return {"state": "building", "progress": state.progress()}

    def _run(self, inv, state: BuildState) -> None:
        with self._slots:
            try:
                if state.stop.is_set():
                    raise BuildCancelled("The index build was cancelled.")
                build(inv, self.settings, state=state)
            except BuildCancelled as exc:
                state.error = exc.message
                state.cancelled = True
            except DfirError as exc:
                state.error = exc.message
            except Exception as exc:  # noqa: BLE001 - recorded and shown; never left "building"
                state.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                state.finished = True

    def cancel(self, inv) -> bool:
        with self._lock:
            state = self._builds.get(inv.id)
        if state is None or state.finished:
            return False
        state.stop.set()
        return True

    def require(self, inv, *, timeout: float = 600.0) -> Index:
        """Synchronous variant for the CLI, MCP and tests."""
        deadline = time.time() + timeout
        first = True
        while True:
            result = self.ensure(inv, wait=min(5.0, max(0.1, deadline - time.time())),
                                 retry=first)
            first = False
            if isinstance(result, Index):
                return result
            if result["state"] in ("no_evidence", "failed", "cancelled"):
                raise IndexNotReady(result.get("error") or "No saved evidence to index.")
            if result["state"] == "evidence_changing" or time.time() > deadline:
                raise IndexNotReady("The evidence index is not available yet.")
