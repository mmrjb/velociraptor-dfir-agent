"""Versioned evidence bundle: ``manifest.json`` plus one streaming JSONL file per source.

Raw rows are stored exactly as returned by Velociraptor (JSON types preserved: ``false``,
``0``, ``""``, ``null`` and absent keys stay distinct; Unicode is not escaped). Analytical
views for the model are produced elsewhere and never written back into raw records.

Evidence IDs
    ``ev-`` + the first 20 hex digits of
    ``sha256("v1|<org>|<client>|<flow>|<artifact>|<source>|<row_index>")``.
    ``row_index`` is the zero-based position in the server result set of that
    org/client/flow/artifact/source, i.e. the ``start_row`` that returns the row. A content
    hash alone is never used because identical rows can appear at several positions.

Cursors
    The JSONL file is the source of truth for progress. Records are appended in row order
    and carry ``row_index``; on resume the next ``start_row`` is derived from the file, so a
    crash between appending and updating the manifest cannot lose or duplicate rows.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vr_dfir import ids
from vr_dfir.errors import NotFoundError, ScopeError, ValidationError
from vr_dfir.jsonio import (
    JsonlWriter,
    atomic_write_json,
    canonical_json,
    ensure_dir,
    iter_jsonl,
    read_json,
    sha256_json,
    truncate_jsonl_to_complete_lines,
)

FORMAT = "vr-dfir-evidence-bundle"
FORMAT_VERSION = 1
ID_SCHEME = "ev-sha256(v1|org|client|flow|artifact|source|row_index)[:20]"

# Result status per source (what the server-side result set means).
RESULT_STATUSES = (
    "rows",                  # rows were returned
    "empty_or_skipped",      # query reported OK with zero rows; cannot distinguish skip
    "skipped_precondition",  # verified by the client log message
    "source_failed",         # the client reported an error for this source
    "not_collected",         # the collection failed or never produced this source
    "unavailable",           # the source/artifact is not defined
    "unknown",               # zero rows and no per-source status from the server
)
# Read status per source (what this application retrieved).
READ_STATUSES = ("not_started", "in_progress", "complete", "incomplete", "read_failed",
                 "not_applicable")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def evidence_id(org_id: str, client_id: str, flow_id: str, artifact: str,
                source: str | None, row_index: int) -> str:
    key = f"v1|{org_id}|{client_id}|{flow_id}|{artifact}|{source or ''}|{int(row_index)}"
    return "ev-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def row_hash(row: Any) -> str:
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def source_key(org_id: str, client_id: str, flow_id: str, artifact: str,
               source: str | None) -> str:
    return f"{org_id}/{client_id}/{flow_id}/{artifact}/{source or ''}"


@dataclass
class Coverage:
    collection_status: str = "unknown"
    read_status: str = "not_started"
    result_status: str = "unknown"
    available_rows: int | None = None
    available_rows_basis: str = "unknown"
    retrieved_rows: int = 0
    persisted_rows: int = 0
    bytes_persisted: int = 0
    stop_reason: str | None = None
    pagination_finished: bool = False
    pages_read: int = 0
    error: str | None = None
    snapshot: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class SourceEntry:
    source_key: str
    org_id: str
    client_id: str
    flow_id: str
    artifact: str
    source: str | None
    file: str
    hostname: str = ""
    hunt_id: str | None = None
    collection_ids: list[str] = field(default_factory=list)
    collected_at: str | None = None
    counts_only: bool = False
    coverage: Coverage = field(default_factory=Coverage)

    @property
    def full_name(self) -> str:
        return f"{self.artifact}/{self.source}" if self.source else self.artifact

    def to_dict(self) -> dict:
        data = asdict(self)
        data["full_name"] = self.full_name
        return data

    @classmethod
    def from_dict(cls, data: dict) -> SourceEntry:
        data = dict(data)
        data.pop("full_name", None)
        cov = Coverage(**data.pop("coverage", {}))
        return cls(**data, coverage=cov)


class EvidenceBundle:
    """Read/write access to one bundle directory. Thread-safe for appends per source."""

    def __init__(self, path: Path | str, manifest: dict):
        self.path = Path(path)
        self.manifest = manifest
        self._lock = threading.RLock()
        self._sources: dict[str, SourceEntry] = {
            s["source_key"]: SourceEntry.from_dict(s) for s in manifest.get("sources", [])
        }
        self._index: dict[str, tuple[str, int]] | None = None

    # --- lifecycle --------------------------------------------------------------------------

    @classmethod
    def create(cls, path: Path | str, *, investigation_id: str, scope: dict,
               synthetic: bool = False) -> EvidenceBundle:
        p = Path(path)
        if (p / "manifest.json").exists():
            raise ValidationError(f"An evidence bundle already exists at {p}.")
        ensure_dir(p / "sources")
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "bundle_id": ids.new_local_id("bnd"),
            "investigation_id": investigation_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "synthetic": bool(synthetic),
            "row_index_base": 0,
            "evidence_id_scheme": ID_SCHEME,
            "scope": scope,
            "sources": [],
            "legacy_aliases": {},
        }
        bundle = cls(p, manifest)
        bundle.save()
        return bundle

    @classmethod
    def open(cls, path: Path | str) -> EvidenceBundle:
        p = Path(path)
        manifest_path = p / "manifest.json"
        if not manifest_path.is_file():
            raise NotFoundError(f"No evidence bundle manifest at {p}.")
        manifest = read_json(manifest_path)
        validate_manifest(manifest)
        return cls(p, manifest)

    def save(self) -> None:
        with self._lock:
            self.manifest["sources"] = [s.to_dict() for s in self._sources.values()]
            self.manifest["updated_at"] = now_iso()
            atomic_write_json(self.path / "manifest.json", self.manifest)

    # --- sources ----------------------------------------------------------------------------

    @property
    def synthetic(self) -> bool:
        return bool(self.manifest.get("synthetic"))

    @property
    def bundle_id(self) -> str:
        return self.manifest["bundle_id"]

    def sources(self) -> list[SourceEntry]:
        return list(self._sources.values())

    def source(self, key: str) -> SourceEntry:
        try:
            return self._sources[key]
        except KeyError as exc:
            raise NotFoundError(f"Source {key!r} is not in this evidence bundle.") from exc

    def ensure_source(self, *, org_id: str, client_id: str, flow_id: str, artifact: str,
                      source: str | None, hostname: str = "", hunt_id: str | None = None,
                      collection_ids: list[str] | None = None, collected_at: str | None = None,
                      counts_only: bool = False) -> SourceEntry:
        key = source_key(org_id, client_id, flow_id, artifact, source)
        with self._lock:
            existing = self._sources.get(key)
            if existing is not None:
                for cid in collection_ids or []:
                    if cid not in existing.collection_ids:
                        existing.collection_ids.append(cid)
                return existing
            fname = "sources/" + ids.safe_filename(
                f"{client_id}_{flow_id}_{artifact}_{source or 'default'}", max_length=120) + ".jsonl"
            entry = SourceEntry(
                source_key=key, org_id=org_id, client_id=client_id, flow_id=flow_id,
                artifact=artifact, source=source, file=fname, hostname=hostname, hunt_id=hunt_id,
                collection_ids=list(collection_ids or []), collected_at=collected_at,
                counts_only=counts_only,
            )
            self._sources[key] = entry
            (self.path / fname).touch(mode=0o600, exist_ok=True)
            return entry

    def resume_position(self, key: str) -> int:
        """Next ``start_row`` for a source, derived from the persisted JSONL file."""
        entry = self.source(key)
        file_path = self.path / entry.file
        truncate_jsonl_to_complete_lines(file_path)
        last_index = -1
        count = 0
        nbytes = 0
        for _, record in iter_jsonl(file_path):
            idx = int(record["row_index"])
            if idx != last_index + 1:
                raise ValidationError(
                    f"Evidence file {entry.file} has a gap or duplicate at row {idx}; refusing "
                    "to resume to avoid losing or duplicating rows.")
            last_index = idx
            count += 1
            nbytes += len(canonical_json(record["row"]).encode("utf-8"))
        with self._lock:
            entry.coverage.persisted_rows = count
            entry.coverage.retrieved_rows = max(entry.coverage.retrieved_rows, count)
            entry.coverage.bytes_persisted = nbytes
        return last_index + 1

    def append_rows(self, key: str, rows: list[dict], start_row: int, *,
                    event_time_fields: list[str] | None = None) -> tuple[int, int]:
        """Append one page. ``start_row`` must equal the current resume position.

        Returns ``(rows_written, bytes_written)``.
        """
        entry = self.source(key)
        with self._lock:
            expected = entry.coverage.persisted_rows
            if start_row != expected:
                raise ScopeError(
                    f"Page for {entry.full_name} starts at row {start_row}, but the bundle "
                    f"has {expected} rows; refusing to write a gap or duplicate.")
            retrieved_at = now_iso()
            nbytes = 0
            with JsonlWriter(self.path / entry.file) as writer:
                for offset, row in enumerate(rows):
                    idx = start_row + offset
                    etime, efield = _event_time(row, event_time_fields or [])
                    record = {
                        "evidence_id": evidence_id(entry.org_id, entry.client_id, entry.flow_id,
                                                   entry.artifact, entry.source, idx),
                        "row_index": idx,
                        "raw_sha256": row_hash(row),
                        "retrieved_at": retrieved_at,
                        "event_time": etime,
                        "event_time_field": efield,
                        "row": row,
                    }
                    nbytes += len(canonical_json(row).encode("utf-8"))
                    writer.write(record)
            entry.coverage.persisted_rows += len(rows)
            entry.coverage.retrieved_rows += len(rows)
            entry.coverage.bytes_persisted += nbytes
            entry.coverage.pages_read += 1
            self._index = None
            return len(rows), nbytes

    def set_coverage(self, key: str, **updates) -> None:
        entry = self.source(key)
        with self._lock:
            for name, value in updates.items():
                if not hasattr(entry.coverage, name):
                    raise AttributeError(name)
                setattr(entry.coverage, name, value)

    # --- reading ----------------------------------------------------------------------------

    def iter_records(self, key: str) -> Iterator[tuple[int, dict]]:
        entry = self.source(key)
        file_path = self.path / entry.file
        if not file_path.exists():
            return iter(())
        return iter_jsonl(file_path)

    def read_page(self, key: str, *, offset: int = 0, limit: int = 50,
                  search: str | None = None, max_scan: int = 1_000_000) -> dict:
        """Backend pagination over persisted rows. ``search`` is a case-insensitive plain
        text match over the raw JSON of each row across the *entire persisted source*."""
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500.")
        needle = (search or "").strip().lower()
        matched = 0
        out = []
        scanned = 0
        for _, record in self.iter_records(key):
            scanned += 1
            if scanned > max_scan:
                break
            if needle and needle not in json.dumps(record["row"], ensure_ascii=False).lower():
                continue
            if matched >= offset and len(out) < limit:
                out.append(record)
            matched += 1
        return {"records": out, "total_matched": matched, "offset": offset, "limit": limit,
                "search_scope": "entire persisted source" if needle else None,
                "scan_truncated": scanned > max_scan}

    def _build_index(self) -> dict[str, tuple[str, int]]:
        index: dict[str, tuple[str, int]] = {}
        for entry in self._sources.values():
            for line_no, record in self.iter_records(entry.source_key):
                eid = record["evidence_id"]
                if eid in index:
                    raise ValidationError(f"Duplicate evidence ID {eid} in bundle.")
                index[eid] = (entry.source_key, line_no)
        return index

    def evidence_ids(self) -> set[str]:
        with self._lock:
            if self._index is None:
                self._index = self._build_index()
            return set(self._index)

    def get_record(self, eid: str) -> dict:
        """Resolve an evidence ID to its exact persisted row with full provenance."""
        if not isinstance(eid, str) or not eid.startswith("ev-"):
            raise ValidationError(f"Invalid evidence ID {str(eid)[:40]!r}.")
        with self._lock:
            if self._index is None:
                self._index = self._build_index()
            loc = self._index.get(eid)
            if loc is None:
                alias = self.manifest.get("legacy_aliases", {}).get(eid)
                loc = self._index.get(alias) if alias else None
            if loc is None:
                raise NotFoundError(f"Evidence ID {eid} does not exist in this bundle.")
        key, line_no = loc
        entry = self.source(key)
        for ln, record in self.iter_records(key):
            if ln == line_no:
                return self.provenance(entry, record, line_no)
        raise NotFoundError(f"Evidence ID {eid} could not be read back from {entry.file}.")

    def provenance(self, entry: SourceEntry, record: dict, line_no: int | None = None) -> dict:
        return {
            "evidence_id": record["evidence_id"],
            "investigation_id": self.manifest.get("investigation_id"),
            "bundle_id": self.bundle_id,
            "org_id": entry.org_id,
            "client_id": entry.client_id,
            "hostname": entry.hostname,
            "flow_id": entry.flow_id,
            "hunt_id": entry.hunt_id,
            "artifact": entry.artifact,
            "source": entry.source,
            "full_name": entry.full_name,
            "source_key": entry.source_key,
            "collected_at": entry.collected_at,
            "retrieved_at": record.get("retrieved_at"),
            "event_time": record.get("event_time"),
            "event_time_field": record.get("event_time_field"),
            "row_index": record["row_index"],
            "row_index_base": 0,
            "raw_sha256": record["raw_sha256"],
            "file": entry.file,
            "line": line_no,
            "synthetic": self.synthetic,
            "row": record["row"],
        }

    def verify(self, *, check_hashes: bool = True) -> list[str]:
        """Integrity problems (empty list when the bundle is consistent)."""
        problems = []
        seen = set()
        for entry in self._sources.values():
            count = 0
            expected_idx = 0
            for _, record in self.iter_records(entry.source_key):
                if record.get("row_index") != expected_idx:
                    problems.append(f"{entry.file}: row_index {record.get('row_index')} "
                                    f"expected {expected_idx}")
                    break
                eid = evidence_id(entry.org_id, entry.client_id, entry.flow_id, entry.artifact,
                                  entry.source, expected_idx)
                if record.get("evidence_id") != eid:
                    problems.append(f"{entry.file}: evidence ID mismatch at row {expected_idx}")
                if eid in seen:
                    problems.append(f"duplicate evidence ID {eid}")
                seen.add(eid)
                if check_hashes and record.get("raw_sha256") != row_hash(record.get("row")):
                    problems.append(f"{entry.file}: raw hash mismatch at row {expected_idx}")
                expected_idx += 1
                count += 1
            if count != entry.coverage.persisted_rows:
                problems.append(f"{entry.file}: {count} rows on disk, manifest says "
                                f"{entry.coverage.persisted_rows}")
        return problems

    def summary(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "investigation_id": self.manifest.get("investigation_id"),
            "synthetic": self.synthetic,
            "scope": self.manifest.get("scope"),
            "sources": [
                {"source_key": s.source_key, "full_name": s.full_name, "client_id": s.client_id,
                 "hostname": s.hostname, "flow_id": s.flow_id, "hunt_id": s.hunt_id,
                 "counts_only": s.counts_only, "coverage": asdict(s.coverage)}
                for s in self._sources.values()
            ],
        }

    def fingerprint(self) -> str:
        return sha256_json({k: v for k, v in self.manifest.items() if k != "updated_at"})


def _event_time(row: dict, fields: list[str]) -> tuple[str | None, str | None]:
    if not isinstance(row, dict):
        return None, None
    for name in fields:
        value = row.get(name)
        if value in (None, "", 0):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            from vr_dfir.velociraptor.flow_state import epoch_to_iso
            iso = epoch_to_iso(value)
            if iso:
                return iso, name
            continue
        if isinstance(value, str) and len(value) <= 64:
            return value, name
    return None, None


_MANIFEST_REQUIRED = ("format", "format_version", "bundle_id", "sources", "scope")


def validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise ValidationError("Evidence manifest must be a JSON object.")
    missing = [k for k in _MANIFEST_REQUIRED if k not in manifest]
    if missing:
        raise ValidationError(f"Evidence manifest is missing fields: {missing}")
    if manifest["format"] != FORMAT:
        raise ValidationError(f"Unsupported evidence format {manifest['format']!r}.")
    if manifest["format_version"] != FORMAT_VERSION:
        raise ValidationError(
            f"Unsupported evidence format_version {manifest['format_version']!r}; this release "
            f"reads version {FORMAT_VERSION}.")
    if manifest.get("row_index_base", 0) != 0:
        raise ValidationError(
            "This bundle uses one-based row indexes. Migrate it with "
            "'vr-dfir evidence migrate-index-base' first; references are not shifted silently.")
    ids.local_id(manifest["bundle_id"], "bundle ID")
    if not isinstance(manifest["sources"], list):
        raise ValidationError("Evidence manifest 'sources' must be a list.")
    for s in manifest["sources"]:
        if not isinstance(s, dict):
            raise ValidationError("Evidence manifest source entries must be objects.")
        f = str(s.get("file", ""))
        if not f.startswith("sources/") or ".." in f or "/" in f[len("sources/"):] \
                or not f.endswith(".jsonl"):
            raise ValidationError(f"Unsafe or unsupported source file path {f!r}.")
        for k in ("org_id", "client_id", "flow_id", "artifact", "source_key"):
            if k not in s:
                raise ValidationError(f"Evidence manifest source entry is missing {k!r}.")
        ids.artifact_name(s["artifact"])


def migrate_one_based(path: Path | str) -> dict:
    """Convert a bundle that recorded one-based row indexes to zero-based indexes.

    Each legacy evidence ID is kept as an explicit alias to the new ID of the *same* row,
    so old references resolve to the exact row they named. Nothing is shifted silently.
    """
    p = Path(path)
    manifest = read_json(p / "manifest.json")
    if manifest.get("row_index_base", 0) != 1:
        return {"migrated": False, "reason": "bundle already uses zero-based row indexes"}
    aliases = dict(manifest.get("legacy_aliases") or {})
    for s in manifest.get("sources", []):
        file_path = p / s["file"]
        records = [r for _, r in iter_jsonl(file_path)]
        new_records = []
        for record in records:
            old_id = record["evidence_id"]
            idx = int(record["row_index"]) - 1
            if idx < 0:
                raise ValidationError(f"{s['file']}: row_index 0 in a one-based bundle.")
            new_id = evidence_id(s["org_id"], s["client_id"], s["flow_id"], s["artifact"],
                                 s.get("source"), idx)
            aliases[old_id] = new_id
            new_records.append({**record, "row_index": idx, "evidence_id": new_id,
                                "legacy_row_index": idx + 1, "legacy_evidence_id": old_id})
        tmp = file_path.with_suffix(".jsonl.migrating")
        with JsonlWriter(tmp) as writer:
            writer.write_many(new_records)
        tmp.replace(file_path)
    manifest["row_index_base"] = 0
    manifest["legacy_aliases"] = aliases
    manifest["migrated_from_row_index_base"] = 1
    atomic_write_json(p / "manifest.json", manifest)
    return {"migrated": True, "aliases": len(aliases)}


def coverage_dict(entry: SourceEntry) -> dict:
    return asdict(entry.coverage)


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


__all__ = [
    "Coverage", "EvidenceBundle", "SourceEntry", "evidence_id", "row_hash", "source_key",
    "validate_manifest", "migrate_one_based", "now_iso", "sha256_json", "json_size",
]
