"""Catalog of saved captures across investigations.

A *capture* is one saved source of one flow: (organization, client, flow, artifact/source)
with its capture time and read coverage. The same flow saved in several investigations (for
example a re-analysis copy) is one observation, not repeated history.

Compatibility for comparison and history requires the same non-empty organization, the same
client, the identical artifact/source name and the same data mode (synthetic test data is
never mixed with live data). Nothing here contacts Velociraptor.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from vr_dfir.evidence import presentation as pres
from vr_dfir.evidence.timeparse import parse_iso
from vr_dfir.jsonio import read_json

_CACHE: dict[str, tuple[int, list]] = {}
_LOCK = threading.Lock()


@dataclass
class Capture:
    investigation_id: str
    title: str
    mode: str
    synthetic: bool
    org_id: str
    client_id: str
    hostname: str
    flow_id: str
    hunt_id: str | None
    artifact: str
    source: str | None
    full_name: str
    source_key: str
    collected_at: str | None
    collected_ts: int | None
    read_status: str
    result_status: str
    persisted_rows: int
    stop_reason: str | None
    domain: str
    duplicates: list[str] = field(default_factory=list)

    @property
    def observation(self) -> tuple:
        return (self.org_id, self.client_id, self.flow_id, self.full_name)

    def absence_ok(self) -> bool:
        """Can this capture establish that something is absent? (complete read with rows)"""
        return self.read_status == "complete" and self.result_status == "rows"

    def to_dict(self) -> dict:
        return asdict(self)


def _captures_of(inv_dir: Path, configured: dict) -> list[Capture]:
    manifest_path = inv_dir / "evidence" / "manifest.json"
    meta = read_json(inv_dir / "investigation.json")
    manifest = read_json(manifest_path)
    out = []
    for s in manifest.get("sources") or []:
        cov = s.get("coverage") or {}
        collected = s.get("collected_at")
        parsed = parse_iso(collected) if isinstance(collected, str) else None
        mapping = pres.mapping_for(s.get("artifact", ""), s.get("source"), configured)
        full = f"{s.get('artifact')}/{s.get('source')}" if s.get("source") else s.get("artifact")
        out.append(Capture(
            investigation_id=meta.get("investigation_id"), title=meta.get("title") or "",
            mode=meta.get("mode") or "", synthetic=bool(manifest.get("synthetic")),
            org_id=s.get("org_id") or "", client_id=s.get("client_id") or "",
            hostname=s.get("hostname") or "", flow_id=s.get("flow_id") or "",
            hunt_id=s.get("hunt_id"), artifact=s.get("artifact") or "", source=s.get("source"),
            full_name=full or "", source_key=s.get("source_key") or "",
            collected_at=collected, collected_ts=parsed.micros if parsed and parsed.ok else None,
            read_status=cov.get("read_status") or "unknown",
            result_status=cov.get("result_status") or "unknown",
            persisted_rows=int(cov.get("persisted_rows") or 0),
            stop_reason=cov.get("stop_reason"), domain=mapping.id))
    return out


def catalog(store, settings) -> list[Capture]:
    """Every saved capture in the run store (cached per manifest modification time)."""
    configured = pres.load_configured(getattr(settings, "source_mappings_path", ""))
    mapping_id = pres.config_fingerprint(getattr(settings, "source_mappings_path", ""))
    out: list[Capture] = []
    root = Path(store.root)
    if not root.is_dir():
        return out
    for inv_dir in sorted(root.iterdir()):
        manifest = inv_dir / "evidence" / "manifest.json"
        try:
            mtime = manifest.stat().st_mtime_ns
        except OSError:
            continue
        key = f"{inv_dir}|{mapping_id}"
        with _LOCK:
            cached = _CACHE.get(key)
        if cached and cached[0] == mtime:
            out.extend(cached[1])
            continue
        try:
            caps = _captures_of(inv_dir, configured)
        except (OSError, ValueError, KeyError):
            continue
        with _LOCK:
            _CACHE[key] = (mtime, caps)
        out.extend(caps)
    return out


def _rank(c: Capture) -> tuple:
    return (c.absence_ok(), c.read_status == "complete", c.persisted_rows)


def dedupe(captures: list[Capture]) -> list[Capture]:
    """One entry per observation (same flow and source); the best-read copy represents it."""
    groups: dict[tuple, list[Capture]] = {}
    for c in captures:
        groups.setdefault(c.observation, []).append(c)
    out = []
    for items in groups.values():
        best = max(items, key=_rank)
        best.duplicates = sorted({i.investigation_id for i in items} - {best.investigation_id})
        out.append(best)
    return out


def find(store, settings, investigation_id: str, source_key: str) -> Capture | None:
    for c in catalog(store, settings):
        if c.investigation_id == investigation_id and c.source_key == source_key:
            return c
    return None


def compatibility(current: Capture, other: Capture) -> str | None:
    """Reason ``other`` cannot be compared with ``current``, or ``None`` when compatible."""
    if not current.org_id or not other.org_id:
        return "The organization of a capture is unknown, so its scope cannot be validated."
    if current.org_id != other.org_id:
        return "Different organization."
    if current.client_id != other.client_id:
        return "Different host (client)."
    if current.full_name != other.full_name:
        return "Different artifact or source."
    if current.synthetic != other.synthetic:
        return "Synthetic test data and live data are never compared."
    return None


def history(store, settings, current: Capture) -> list[Capture]:
    """All compatible observations of the same host and source, oldest first (deduplicated)."""
    caps = [c for c in catalog(store, settings) if compatibility(current, c) is None]
    caps = dedupe(caps)
    return sorted(caps, key=lambda c: (c.collected_ts is None, c.collected_ts or 0, c.flow_id))


def baselines(store, settings, current: Capture) -> list[Capture]:
    """Compatible captures strictly older than ``current``, newest first."""
    if current.collected_ts is None:
        return []
    older = [c for c in history(store, settings, current)
             if c.collected_ts is not None and c.collected_ts < current.collected_ts
             and c.observation != current.observation]
    return sorted(older, key=lambda c: -(c.collected_ts or 0))
