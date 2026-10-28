"""Summary-first investigation views over the derived index.

Every count here is computed over the investigation's complete saved dataset (the derived
index), never over a rendered page. Each aggregate states its counting unit, its scope and
what is incomplete. Findings are counted by finding ID; evidence rows, hosts and sources are
separate units. Review states are joined from the analyst review store at query time, so a
review never requires re-indexing and is never altered by a view.

These functions are shared by the UI API, the CLI and MCP. They only read saved data.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from vr_dfir.analysis.networks import classify
from vr_dfir.analysis.signals import rule_spans
from vr_dfir.errors import NotFoundError, ValidationError
from vr_dfir.evidence import presentation as pres
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.evidence.exact import protect, raw_text
from vr_dfir.evidence.timeparse import UNTIMED_LABELS, micros_to_iso
from vr_dfir.jsonio import read_json
from vr_dfir.services.derived import Index
from vr_dfir.services.reviews import REVIEW_STATES, load_reviews

SEVERITY_ORDER = ("high", "medium", "low", "informational")
SEVERITY_LABELS = {"high": "High", "medium": "Medium", "low": "Low",
                   "informational": "Informational"}
OPEN_REVIEW = ("unreviewed", "in_review", "needs_follow_up")
REVIEW_LABELS = {**REVIEW_STATES, "unreviewed": "Not reviewed"}
SUCCESS_RESULTS = ("rows", "empty_or_skipped", "skipped_precondition")
MAX_PAGE = 200
MAX_SCAN = 250_000
EVENT_BUCKETS = 60
_WIDTHS = [(1, "1 second"), (5, "5 seconds"), (15, "15 seconds"), (60, "1 minute"),
           (300, "5 minutes"), (900, "15 minutes"), (3600, "1 hour"), (3 * 3600, "3 hours"),
           (6 * 3600, "6 hours"), (12 * 3600, "12 hours"), (86400, "1 day"),
           (7 * 86400, "7 days"), (30 * 86400, "30 days"), (365 * 86400, "365 days")]


# ----------------------------------------------------------------------- shared helpers

def _reviews(inv) -> dict[str, str]:
    return {fid: (r or {}).get("state", "unreviewed") for fid, r in load_reviews(inv.path).items()}


def _findings(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM findings ORDER BY position")]


def _parse_ts(value: str | None) -> int | None:
    if not value:
        return None
    from vr_dfir.evidence.timeparse import parse_iso
    parsed = parse_iso(str(value))
    return parsed.micros if parsed.ok else None


def source_state(s: dict) -> str:
    """One plain coverage state per source (distinct for missing, empty, failed, partial)."""
    if not s["verified"]:
        return "unverified"
    read, result = s["read_status"], s["result_status"]
    if read == "complete" and result == "rows":
        return "complete"
    if read == "complete" and result == "empty_or_skipped":
        return "empty"
    if result == "skipped_precondition":
        return "skipped"
    if result in ("source_failed", "not_collected", "unavailable") or read == "read_failed":
        return "failed"
    if read in ("incomplete", "in_progress"):
        return "incomplete"
    if read == "not_started":
        return "not_read"
    if read == "not_applicable":
        return "not_applicable"
    return "unknown"


SOURCE_STATE_LABELS = {
    "complete": "Read completely", "empty": "Read, no rows (empty or skipped)",
    "skipped": "Skipped by a precondition", "failed": "Failed", "incomplete": "Partially read",
    "not_read": "Not read", "unverified": "Saved rows do not match the manifest",
    "not_applicable": "Not applicable", "unknown": "Unknown",
}


def source_label(s: dict) -> str:
    host = s.get("hostname") or s.get("client_id")
    name = (s.get("mapping") or {}).get("label") if s.get("domain") != "generic" else None
    return f"{name or s['full_name']} — {host}"


def _source_brief(s: dict, now: datetime, stale_days: int) -> dict:
    collected = _parse_ts(s.get("collected_at"))
    age = None if collected is None else round((now.timestamp() * 1e6 - collected) / 86400e6, 1)
    return {"source_key": s["source_key"], "label": source_label(s), "full_name": s["full_name"],
            "domain": s["domain"], "client_id": s["client_id"], "hostname": s["hostname"],
            "org_id": s["org_id"], "flow_id": s["flow_id"], "hunt_id": s["hunt_id"],
            "rows": s["indexed_rows"], "state": source_state(s),
            "state_label": SOURCE_STATE_LABELS[source_state(s)],
            "read_status": s["read_status"], "result_status": s["result_status"],
            "stop_reason": s["stop_reason"], "collected_at": s["collected_at"],
            "age_days": age, "stale": age is not None and age > stale_days,
            "timed_rows": s["timed_rows"], "untimed_rows": s["untimed_rows"],
            "counts_only": bool(s["counts_only"])}


# ----------------------------------------------------------------------- overview

def overview(inv, idx: Index, *, stale_days: int = 7, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    meta = inv.reload()
    reviews = _reviews(inv)
    sources = idx.sources()
    briefs = [_source_brief(s, now, stale_days) for s in sources]
    findings_ready = idx.meta.get("findings_ready") == "true"
    with idx.db() as conn:
        findings = _findings(conn)
        clients = defaultdict(set)
        for r in conn.execute("SELECT finding_id, client_id FROM finding_clients"):
            clients[r["finding_id"]].add(r["client_id"])
    for f in findings:
        f["review_state"] = reviews.get(f["finding_id"], "unreviewed")
    open_f = [f for f in findings if f["review_state"] in OPEN_REVIEW]
    hosts = {}
    for s in sources:
        hosts.setdefault(s["client_id"], s["hostname"] or s["client_id"])
    states = Counter(b["state"] for b in briefs)
    read_ok = states["complete"] + states["empty"] + states["skipped"]
    orgs = sorted({s["org_id"] for s in sources if s["org_id"]})
    metrics = [
        {"id": "needs_review", "label": "Needs review", "unit": "findings",
         "value": len(open_f) if findings_ready else None,
         "total": len(findings) if findings_ready else None,
         "detail": ("Findings not yet reviewed, in review or needing follow-up (each finding "
                    "counted once)" if findings_ready else
                    "Findings are not available until analysis has produced a report"),
         "target": {"view": "evidence", "filters": {"review": "open"}}},
        {"id": "hosts", "label": "Represented hosts", "unit": "hosts", "value": len(hosts),
         "detail": "Hosts with saved evidence in this investigation; not a fleet inventory",
         "target": {"view": "hosts"}},
        {"id": "sources", "label": "Sources read", "unit": "sources", "value": read_ok,
         "total": len(sources),
         "detail": "Read completely (including empty or precondition-skipped results). Failed, "
                   "partial, unread or unverified sources are not counted",
         "breakdown": {k: states[k] for k in SOURCE_STATE_LABELS if states[k]},
         "target": {"view": "coverage"}},
    ]
    charts = [_severity_chart(findings, findings_ready)]
    second = _second_chart(conn_index=idx, findings=findings, hosts=hosts, clients=clients,
                           briefs=briefs, findings_ready=findings_ready)
    if second:
        charts.append(second)
    attention = sorted(open_f, key=lambda f: (
        SEVERITY_ORDER.index(f["severity"]) if f["severity"] in SEVERITY_ORDER else 9,
        -int(f["evidence_count"] or 0), f["position"]))[:5]
    captured = [b["collected_at"] for b in briefs if b["collected_at"]]
    coverage = {
        "sources_total": len(sources), "states": {k: states[k] for k in SOURCE_STATE_LABELS
                                                  if states[k]},
        "state_labels": SOURCE_STATE_LABELS,
        "captured_from": min(captured) if captured else None,
        "captured_to": max(captured) if captured else None,
        "stale_sources": sum(1 for b in briefs if b["stale"]), "stale_after_days": stale_days,
        "rows": sum(b["rows"] for b in briefs), "sources": briefs,
        "retrieval_status": meta.get("retrieval_status"),
    }
    return {
        "state": "ready",
        "scope": {"investigation_id": inv.id, "title": meta.get("title"), "mode": meta.get("mode"),
                  "org_ids": orgs, "hosts": [{"client_id": k, "hostname": v}
                                             for k, v in sorted(hosts.items())],
                  "synthetic": bool(meta.get("synthetic")),
                  "note": "Counts cover this investigation's saved evidence only."},
        "status": {k: meta.get(k) for k in ("outcome", "phase", "retrieval_status",
                                            "analysis_status", "collection_status")},
        "findings_ready": findings_ready,
        "metrics": metrics, "charts": charts,
        "attention": [{"finding_id": f["finding_id"], "title": f["title"],
                       "severity": f["severity"], "origin": f["origin"],
                       "review_state": f["review_state"],
                       "review_label": REVIEW_LABELS.get(f["review_state"], f["review_state"]),
                       "evidence_count": f["evidence_count"],
                       "hosts": sorted(hosts.get(c, c) for c in clients.get(f["finding_id"], ()))}
                      for f in attention],
        "coverage": coverage,
        "next_action": _next_action(meta, findings_ready, open_f, states, findings),
        "index": {"built_at": idx.meta.get("built_at"),
                  "normalizer_version": idx.meta.get("normalizer_version")},
    }


def _severity_chart(findings: list[dict], ready: bool) -> dict:
    sev = Counter(f["severity"] for f in findings)
    rev = Counter(f["review_state"] for f in findings)
    return {
        "id": "severity", "kind": "donut", "title": "Findings by severity",
        "unit": "unique findings", "total": len(findings) if ready else None,
        "state": "ready" if ready else "not_ready",
        "note": None if ready else "Findings appear after analysis has produced a report.",
        "segments": [{"key": k, "label": SEVERITY_LABELS[k], "count": sev[k],
                      "filter": {"severity": k}} for k in SEVERITY_ORDER if sev[k]],
        "alternate": {"title": "Findings by review state", "unit": "unique findings",
                      "segments": [{"key": k, "label": REVIEW_LABELS[k], "count": rev[k],
                                    "filter": {"review": k}} for k in REVIEW_STATES if rev[k]]},
    }


def _second_chart(*, conn_index: Index, findings, hosts, clients, briefs, findings_ready):
    if len(hosts) >= 2:
        per_host = Counter()
        open_host = Counter()
        for f in findings:
            for c in clients.get(f["finding_id"], ()):
                per_host[c] += 1
                if f["review_state"] in OPEN_REVIEW:
                    open_host[c] += 1
        rows = Counter()
        for b in briefs:
            rows[b["client_id"]] += b["rows"]
        bars = sorted(hosts, key=lambda c: (-open_host[c], -per_host[c], hosts[c]))
        return {"id": "hosts", "kind": "bars", "title": "Findings needing review by host",
                "unit": "unique findings per host",
                "note": "A finding that cites several hosts counts once for each host.",
                "state": "ready" if findings_ready else "not_ready",
                "bars": [{"key": c, "label": hosts[c], "count": open_host[c],
                          "secondary": f"{per_host[c]} finding(s) · {rows[c]} saved rows",
                          "filter": {"client_id": c, "review": "open"}} for c in bars[:12]],
                "more": max(0, len(bars) - 12), "link": {"view": "hosts"}}
    timed = sum(b["timed_rows"] for b in briefs)
    if timed:
        tl = timeline(conn_index.inv, conn_index, buckets=24)
        return {"id": "events", "kind": "histogram", "title": "Events over time",
                "unit": "evidence rows with a mapped event time", "state": "ready",
                "buckets": tl["buckets"], "bucket": tl["bucket"], "complete": tl["complete"],
                "untimed": tl["untimed"]["total"], "note": tl["note"],
                "link": {"view": "timeline"}}
    bars = sorted(briefs, key=lambda b: -b["rows"])[:8]
    return {"id": "sources", "kind": "bars", "title": "Saved rows by source",
            "unit": "evidence rows", "state": "ready",
            "bars": [{"key": b["source_key"], "label": b["label"], "count": b["rows"],
                      "secondary": b["state_label"], "filter": {"source_key": b["source_key"]}}
                     for b in bars], "more": max(0, len(briefs) - 8)}


def _next_action(meta, ready, open_f, states, findings) -> dict:
    if meta.get("outcome") == "running" or (meta.get("job") or {}).get("state") in (
            "running", "queued"):
        return {"label": "Evidence is still being processed", "target": None}
    if not ready:
        return {"label": "Browse the saved source rows", "target": {"view": "evidence",
                                                                     "mode": "rows"}}
    if open_f:
        return {"label": f"Review {len(open_f)} finding(s) that need attention",
                "target": {"view": "evidence", "filters": {"review": "open"}}}
    if states["incomplete"] or states["not_read"]:
        return {"label": "Some sources were not read completely; resume reading",
                "target": {"view": "run"}}
    if findings:
        return {"label": "All findings have a review decision; build the report",
                "target": {"view": "report"}}
    return {"label": "No accepted findings; browse the source rows (not a clean result)",
            "target": {"view": "evidence", "mode": "rows"}}


# ----------------------------------------------------------------------- row queries

@dataclass
class RowQuery:
    source_keys: list[str] = field(default_factory=list)
    client_id: str | None = None
    finding_id: str | None = None
    severity: str | None = None
    review: str | None = None
    origin: str | None = None
    cited: bool = False
    time_from: int | None = None
    time_to: int | None = None
    untimed: str | None = None
    entity_kind: str | None = None
    entity_key: str | None = None
    q: str | None = None
    order: str = "source"

    def describe(self) -> list[dict]:
        chips = []
        for name in ("client_id", "finding_id", "severity", "review", "origin", "entity_kind",
                     "untimed"):
            value = getattr(self, name)
            if value:
                chips.append({"key": name, "value": value})
        if self.source_keys:
            chips.append({"key": "source_key", "value": ",".join(self.source_keys)})
        if self.time_from is not None or self.time_to is not None:
            chips.append({"key": "time", "value": f"{micros_to_iso(self.time_from) if self.time_from is not None else '…'}"
                                                  f" – {micros_to_iso(self.time_to) if self.time_to is not None else '…'}"})
        if self.cited:
            chips.append({"key": "cited", "value": "cited by a finding"})
        if self.entity_key:
            chips.append({"key": "entity_key", "value": self.entity_key})
        if self.q:
            chips.append({"key": "q", "value": self.q})
        return chips


def validate_query(q: RowQuery) -> RowQuery:
    if q.severity and q.severity not in SEVERITY_ORDER:
        raise ValidationError("Unknown severity filter.")
    if q.review and q.review != "open" and q.review not in REVIEW_STATES:
        raise ValidationError("Unknown review filter.")
    if q.origin and q.origin not in ("deterministic_rule", "model_proposal", "analyst_input"):
        raise ValidationError("Unknown origin filter.")
    if q.untimed and q.untimed != "any" and q.untimed not in UNTIMED_LABELS:
        raise ValidationError("Unknown untimed-row filter.")
    if q.entity_kind and q.entity_kind not in pres.ENTITY_KINDS:
        raise ValidationError("Unknown entity kind.")
    if bool(q.entity_kind) != bool(q.entity_key):
        raise ValidationError("An entity filter needs both a kind and a key.")
    if q.entity_key and len(q.entity_key) > pres.MAX_ENTITY_KEY + 50:
        raise ValidationError("Entity key is too long.")
    if q.q and len(q.q) > 200:
        raise ValidationError("Search text is limited to 200 characters.")
    if q.order not in ("source", "time"):
        raise ValidationError("Unknown order.")
    if len(q.source_keys) > 50:
        raise ValidationError("Too many sources in one query.")
    for value in (q.time_from, q.time_to):
        if value is not None and not (0 <= value < 4_102_444_800_000_000):
            raise ValidationError("Time filter out of range.")
    return q


def _finding_ids(conn, inv, q: RowQuery) -> list[str] | None:
    if not (q.finding_id or q.severity or q.review or q.origin):
        return None
    reviews = _reviews(inv)
    ids = []
    for f in _findings(conn):
        state = reviews.get(f["finding_id"], "unreviewed")
        if q.finding_id and f["finding_id"] != q.finding_id:
            continue
        if q.severity and f["severity"] != q.severity:
            continue
        if q.origin and f["origin"] != q.origin:
            continue
        if q.review == "open" and state not in OPEN_REVIEW:
            continue
        if q.review and q.review != "open" and state != q.review:
            continue
        ids.append(f["finding_id"])
    return ids


def _where(conn, inv, q: RowQuery, sources: dict[str, dict]) -> tuple[str, list]:
    clauses, params = [], []
    if q.source_keys:
        sids = [sources[k]["sid"] for k in q.source_keys if k in sources]
        if len(sids) != len(q.source_keys):
            raise NotFoundError("A requested source is not in this investigation.")
        clauses.append(f"r.sid IN ({','.join('?' * len(sids))})")
        params += sids
    if q.client_id:
        clauses.append("s.client_id = ?")
        params.append(q.client_id)
    fids = _finding_ids(conn, inv, q)
    if fids is not None:
        if not fids:
            return "0", []
        clauses.append("EXISTS (SELECT 1 FROM finding_rows fr WHERE fr.sid=r.sid AND "
                       f"fr.row_index=r.row_index AND fr.finding_id IN ({','.join('?' * len(fids))}))")
        params += fids
    elif q.cited:
        clauses.append("EXISTS (SELECT 1 FROM finding_rows fr WHERE fr.sid=r.sid AND "
                       "fr.row_index=r.row_index)")
    if q.time_from is not None:
        clauses.append("r.event_ts >= ?")
        params.append(q.time_from)
    if q.time_to is not None:
        clauses.append("r.event_ts < ?")
        params.append(q.time_to)
    if q.untimed:
        clauses.append("r.event_ts IS NULL")
        if q.untimed != "any":
            clauses.append("r.time_status = ?")
            params.append(q.untimed)
    if q.entity_kind:
        clauses.append("EXISTS (SELECT 1 FROM entities e WHERE e.sid=r.sid AND "
                       "e.row_index=r.row_index AND e.kind=? AND e.key=?)")
        params += [q.entity_kind, q.entity_key]
    return (" AND ".join(clauses) or "1"), params


def query_rows(inv, idx: Index, q: RowQuery, *, offset: int = 0, limit: int = 50) -> dict:
    """One page of saved rows matching structured filters, with an exact total."""
    validate_query(q)
    if not (1 <= limit <= MAX_PAGE) or offset < 0:
        raise ValidationError(f"limit must be 1-{MAX_PAGE} and offset non-negative.")
    sources = {s["source_key"]: s for s in idx.sources()}
    by_sid = {s["sid"]: s for s in sources.values()}
    order = "r.event_ts, r.sid, r.row_index" if q.order == "time" else "r.sid, r.row_index"
    with idx.db() as conn:
        where, params = _where(conn, inv, q, sources)
        base = f"FROM rows r JOIN sources s ON s.sid = r.sid WHERE {where}"
        if q.order == "time" and not q.untimed:
            base += " AND r.event_ts IS NOT NULL"
        needle = (q.q or "").strip().lower()
        scan_truncated = False
        if not needle:
            total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
            items = [tuple(r) for r in conn.execute(
                f"SELECT r.sid, r.row_index, r.offset, r.length {base} ORDER BY {order} "
                "LIMIT ? OFFSET ?", [*params, limit, offset])]
        else:
            total, items, scan_truncated = _search(idx, conn, base, params, order, needle,
                                                   offset, limit, by_sid)
        records = idx.read_records(items, {sid: s["file"] for sid, s in by_sid.items()})
        meta = {}
        cited = defaultdict(list)
        if items:
            values = ",".join("(?, ?)" for _ in items)
            pairs = [v for sid, ri, _, _ in items for v in (sid, ri)]
            for r in conn.execute(
                    "SELECT sid, row_index, event_ts, time_status, title FROM rows WHERE "
                    f"(sid, row_index) IN (VALUES {values})", pairs):
                meta[(r["sid"], r["row_index"])] = dict(r)
            for r in conn.execute(
                    "SELECT sid, row_index, finding_id FROM finding_rows WHERE "
                    f"(sid, row_index) IN (VALUES {values})", pairs):
                cited[(r["sid"], r["row_index"])].append(r["finding_id"])
    out = []
    for sid, ri, _, _ in items:
        rec = records[(sid, ri)]
        m = meta[(sid, ri)]
        s = by_sid[sid]
        out.append({"evidence_id": rec["evidence_id"], "row_index": ri,
                    "source_key": s["source_key"], "full_name": s["full_name"],
                    "client_id": s["client_id"], "hostname": s["hostname"],
                    "title": m["title"], "time_status": m["time_status"],
                    "event_time": micros_to_iso(m["event_ts"]) if m["event_ts"] is not None
                    else None, "cited_by": sorted(set(cited.get((sid, ri), []))),
                    "row": protect(rec.get("row"))})
    single = sources[q.source_keys[0]] if len(q.source_keys) == 1 else None
    return {"records": out, "total": total, "offset": offset, "limit": limit,
            "filters": q.describe(), "scan_truncated": scan_truncated,
            "scope": ("one source" if single else "all saved sources of this investigation"),
            "source": _table_source(single) if single else None,
            "counting_unit": "evidence rows"}


def _table_source(s: dict) -> dict:
    return {"source_key": s["source_key"], "full_name": s["full_name"], "label": source_label(s),
            "domain": s["domain"], "mapping": s["mapping"], "columns": s["columns"],
            "fields": s["fields"], "client_id": s["client_id"], "hostname": s["hostname"],
            "flow_id": s["flow_id"], "org_id": s["org_id"], "state": source_state(s),
            "state_label": SOURCE_STATE_LABELS[source_state(s)], "rows": s["indexed_rows"],
            "read_status": s["read_status"], "result_status": s["result_status"],
            "stop_reason": s["stop_reason"], "available_rows": s["available_rows"]}


def _search(idx, conn, base, params, order, needle, offset, limit, by_sid):
    """Plain-text match over the raw rows in scope (bounded scan, exact within the bound)."""
    total = 0
    items = []
    scanned = 0
    handles = {}
    try:
        for r in conn.execute(f"SELECT r.sid, r.row_index, r.offset, r.length {base} "
                              f"ORDER BY {order}", params):
            scanned += 1
            if scanned > MAX_SCAN:
                return total, items, True
            sid = r["sid"]
            handle = handles.get(sid)
            if handle is None:
                handle = handles[sid] = open(Path(idx.inv.evidence_dir) / by_sid[sid]["file"],
                                             "rb")
            handle.seek(r["offset"])
            line = handle.read(r["length"]).decode("utf-8", "replace")
            if needle not in line.lower():
                continue
            row = json.loads(line).get("row")
            if needle not in json.dumps(row, ensure_ascii=False).lower():
                continue
            if total >= offset and len(items) < limit:
                items.append((sid, r["row_index"], r["offset"], r["length"]))
            total += 1
    finally:
        for handle in handles.values():
            handle.close()
    return total, items, False


def sources_list(idx: Index, *, stale_days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    with idx.db() as conn:
        cited = {r[0]: r[1] for r in conn.execute(
            "SELECT sid, COUNT(DISTINCT row_index) FROM finding_rows WHERE sid IS NOT NULL "
            "GROUP BY sid")}
    return {"sources": [_source_brief(s, now, stale_days) | {"mapping": s["mapping"],
                                                            "cited_rows": cited.get(s["sid"], 0)}
                        for s in idx.sources()],
            "state_labels": SOURCE_STATE_LABELS}


# ----------------------------------------------------------------------- inspector

def _locate(conn, idx: Index, inv, evidence_id: str) -> tuple:
    hit = conn.execute("SELECT sid, row_index, offset, length FROM rows WHERE evidence_id=?",
                       (evidence_id,)).fetchone()
    if hit is None:
        aliases = (EvidenceBundle.open(inv.evidence_dir).manifest.get("legacy_aliases") or {})
        alias = aliases.get(evidence_id)
        if alias:
            hit = conn.execute("SELECT sid, row_index, offset, length FROM rows WHERE "
                               "evidence_id=?", (alias,)).fetchone()
    if hit is None:
        raise NotFoundError(f"Evidence ID {evidence_id} does not exist in this investigation. "
                            "No other row is shown in its place.")
    return tuple(hit)


def _field_aliases(inv) -> dict | None:
    plan = Path(inv.path) / "plan.json"
    if plan.is_file():
        try:
            return (read_json(plan).get("analysis") or {}).get("field_aliases") or None
        except (OSError, ValueError):
            return None
    return None


def inspect(inv, idx: Index, evidence_id: str, *, settings=None) -> dict:
    """Everything the single evidence inspector shows for one exact saved row."""
    if not isinstance(evidence_id, str) or not evidence_id.startswith("ev-") or \
            len(evidence_id) > 40:
        raise ValidationError("Invalid evidence ID.")
    reviews = _reviews(inv)
    with idx.db() as conn:
        sid, ri, offset, length = _locate(conn, idx, inv, evidence_id)
        s = _source_dict_by_sid(conn, sid)
        record = idx.read_records([(sid, ri, offset, length)], {sid: s["file"]})[(sid, ri)]
        findings = [dict(r) for r in conn.execute(
            "SELECT f.* FROM finding_rows fr JOIN findings f ON f.finding_id = fr.finding_id "
            "WHERE fr.sid=? AND fr.row_index=? ORDER BY f.position", (sid, ri))]
        ents = [dict(r) for r in conn.execute(
            "SELECT kind, key, display FROM entities WHERE sid=? AND row_index=?", (sid, ri))]
        rowmeta = dict(conn.execute("SELECT event_ts, time_status, title FROM rows WHERE sid=? "
                                    "AND row_index=?", (sid, ri)).fetchone())
        relations = _relations(conn, s, sid, ri)
        peer_eids = _peer_evidence(conn, findings, sid, ri)
    row = record.get("row")
    configured = pres.load_configured(getattr(settings, "source_mappings_path", ""))
    mapping = pres.mapping_for(s["artifact"], s["source"], configured)
    fields = pres.resolve_fields(mapping, row=row if isinstance(row, dict) else {})
    kinds = pres.kinds_for(mapping, fields)
    summary = []
    for role in mapping.domain.summary:
        if role == "schedule":
            sched = pres.cron_schedule(row, fields) if isinstance(row, dict) else None
            if sched:
                summary.append({"field": None, "role": "schedule", "label": "Schedule",
                                "kind": "schedule", "value": sched, "virtual": True})
            continue
        name = fields.get(role)
        if name:
            r = mapping.domain.role(role)
            summary.append({"field": name, "role": role, "label": r.label, "kind": r.kind})
    if not summary and isinstance(row, dict):
        summary = [{"field": k, "role": None, "label": k, "kind": "text"}
                   for k in list(row)[:6]]
    parsed, basis = pres.event_time(mapping, row, fields) if isinstance(row, dict) else (
        pres.ParsedTime("unmapped"), None)
    internal = getattr(settings, "internal_networks", "") if settings else ""
    addresses = []
    for role in ("laddr", "raddr"):
        name = fields.get(role)
        if not name:
            continue
        value = pres.get_field(row, name)
        info = classify(pres.split_endpoint(value)[0] if isinstance(value, str) else value,
                        internal)
        if info:
            port_role = "lport" if role == "laddr" else "rport"
            port = pres.role_value(row, fields, port_role)
            addresses.append({"field": name, "role": role, **info,
                              "port": None if port is pres._MISSING else port,
                              "port_field": fields.get(port_role)})
    rule_ids = [f["rule_id"] for f in findings if f["origin"] == "deterministic_rule"
                and f["rule_id"]]
    highlights = rule_spans(row, rule_ids, _field_aliases(inv)) if rule_ids else []
    provenance = {
        "evidence_id": record["evidence_id"], "investigation_id": inv.id,
        "org_id": s["org_id"], "client_id": s["client_id"], "hostname": s["hostname"],
        "flow_id": s["flow_id"], "hunt_id": s["hunt_id"], "artifact": s["artifact"],
        "source": s["source"], "full_name": s["full_name"], "source_key": s["source_key"],
        "collected_at": s["collected_at"], "retrieved_at": record.get("retrieved_at"),
        "row_index": ri, "row_index_base": 0, "raw_sha256": record.get("raw_sha256"),
        "file": s["file"], "line_offset_bytes": offset,
        "recorded_event_time": record.get("event_time"),
        "recorded_event_time_field": record.get("event_time_field"),
        "recorded_event_time_note": "Stored at retrieval from the profile's time-field list; "
                                    "numeric values there were converted by magnitude, so the "
                                    "timeline does not use it.",
    }
    return {
        "record": provenance, "row": protect(row), "raw_text": raw_text(row),
        "title": rowmeta["title"],
        "source": _table_source(s),
        "presentation": {"mapping": mapping.describe(), "domain": mapping.id,
                         "summary": summary, "kinds": kinds, "addresses": addresses,
                         "event_time": {"status": parsed.status,
                                        "utc": micros_to_iso(parsed.micros) if parsed.ok
                                        else None,
                                        "reason": UNTIMED_LABELS.get(parsed.status),
                                        "detail": parsed.detail, "basis": basis}},
        "highlights": highlights,
        "findings": [{"finding_id": f["finding_id"], "title": f["title"],
                      "severity": f["severity"], "confidence": f["confidence"],
                      "origin": f["origin"], "rule_id": f["rule_id"],
                      "review_state": reviews.get(f["finding_id"], "unreviewed"),
                      "review_label": REVIEW_LABELS.get(reviews.get(f["finding_id"],
                                                                    "unreviewed"))}
                     for f in findings],
        "entities": [{**e, "label": pres.ENTITY_KINDS.get(e["kind"], e["kind"])} for e in ents],
        "relations": relations, "related_by_finding": peer_eids,
    }


def _source_dict_by_sid(conn, sid: int) -> dict:
    from vr_dfir.services.derived import _source_dict
    return _source_dict(conn.execute("SELECT * FROM sources WHERE sid=?", (sid,)).fetchone())


def _row_ref(conn, sid: int, ri: int) -> dict:
    r = conn.execute("SELECT r.evidence_id, r.title, r.event_ts, s.full_name FROM rows r "
                     "JOIN sources s ON s.sid=r.sid WHERE r.sid=? AND r.row_index=?",
                     (sid, ri)).fetchone()
    return {"evidence_id": r["evidence_id"], "title": r["title"], "full_name": r["full_name"],
            "event_time": micros_to_iso(r["event_ts"]) if r["event_ts"] is not None else None}


def _relations(conn, s: dict, sid: int, ri: int) -> dict:
    """Observed process relations inside the same capture, with explicit identity checks.

    A parent is shown only when exactly one row in the same capture has the parent PID and
    both start times exist with the parent starting first. PID reuse (several candidates),
    a missing parent, or missing start times are reported as such, never as a lineage.
    """
    out = {"parent": None, "parent_status": None, "children": [], "children_status": None,
           "connections": [], "process": None, "process_status": None}
    me = conn.execute("SELECT pid, ppid, start_ts FROM procs WHERE sid=? AND row_index=?",
                      (sid, ri)).fetchone()
    if s["domain"] == "process" and me is not None:
        if me["ppid"] is None:
            out["parent_status"] = "No parent PID in this row."
        else:
            cands = conn.execute("SELECT row_index, start_ts FROM procs WHERE sid=? AND pid=? "
                                 "AND row_index != ?", (sid, me["ppid"], ri)).fetchall()
            if not cands:
                out["parent_status"] = (f"No row with PID {me['ppid']} was saved in this "
                                        "capture; the parent is not shown.")
            elif len(cands) > 1:
                out["parent_status"] = (f"{len(cands)} rows have PID {me['ppid']} (PID reuse); "
                                        "the parent cannot be identified.")
            elif me["start_ts"] is None or cands[0]["start_ts"] is None:
                out["parent_status"] = ("A row with the parent PID exists, but start times "
                                        "are unavailable to confirm it is the same process.")
            elif cands[0]["start_ts"] > me["start_ts"]:
                out["parent_status"] = ("The process with the parent PID started after this "
                                        "one, so it is a different process (PID reuse).")
            else:
                out["parent"] = _row_ref(conn, sid, cands[0]["row_index"])
                out["parent_status"] = "Same capture, unique PID, parent started first."
        dup = conn.execute("SELECT COUNT(*) FROM procs WHERE sid=? AND pid=?",
                           (sid, me["pid"])).fetchone()[0]
        if dup > 1:
            out["children_status"] = (f"{dup} rows share PID {me['pid']}; children are not "
                                      "attributed.")
        else:
            kids = conn.execute("SELECT row_index, start_ts FROM procs WHERE sid=? AND ppid=? "
                                "ORDER BY start_ts LIMIT 21", (sid, me["pid"])).fetchall()
            ok = [k for k in kids if me["start_ts"] is not None and k["start_ts"] is not None
                  and k["start_ts"] >= me["start_ts"]]
            out["children"] = [_row_ref(conn, sid, k["row_index"]) for k in ok[:20]]
            unverified = len(kids) - len(ok)
            if unverified:
                out["children_status"] = (f"{unverified} row(s) name this PID as parent but "
                                          "lack start times that confirm it.")
        out["connections"] = [_row_ref(conn, r["sid"], r["row_index"]) for r in conn.execute(
            "SELECT p.sid, p.row_index FROM procs p JOIN sources x ON x.sid=p.sid "
            "WHERE x.domain='network' AND x.client_id=? AND x.flow_id=? AND p.pid=? LIMIT 20",
            (s["client_id"], s["flow_id"], me["pid"]))] if dup == 1 else []
    if s["domain"] == "network" and me is not None:
        cands = conn.execute(
            "SELECT p.sid, p.row_index FROM procs p JOIN sources x ON x.sid=p.sid "
            "WHERE x.domain='process' AND x.client_id=? AND x.flow_id=? AND p.pid=?",
            (s["client_id"], s["flow_id"], me["pid"])).fetchall()
        if len(cands) == 1:
            out["process"] = _row_ref(conn, cands[0]["sid"], cands[0]["row_index"])
            out["process_status"] = "Process with this PID in the same capture."
        elif len(cands) > 1:
            out["process_status"] = f"{len(cands)} process rows share PID {me['pid']}."
        else:
            out["process_status"] = "No process row with this PID was saved in this capture."
    return out


def _peer_evidence(conn, findings, sid, ri) -> list[dict]:
    if not findings:
        return []
    ids = [f["finding_id"] for f in findings]
    rows = conn.execute(
        "SELECT DISTINCT sid, row_index FROM finding_rows WHERE finding_id IN "
        f"({','.join('?' * len(ids))}) AND sid IS NOT NULL AND NOT (sid=? AND row_index=?) "
        "LIMIT 20", [*ids, sid, ri]).fetchall()
    return [_row_ref(conn, r["sid"], r["row_index"]) for r in rows]


# ----------------------------------------------------------------------- timeline

def _width(span_us: int, buckets: int) -> tuple[int, str]:
    for seconds, label in _WIDTHS:
        if span_us / (seconds * 1e6) <= buckets:
            return seconds * 1_000_000, label
    return _WIDTHS[-1][0] * 1_000_000, _WIDTHS[-1][1]


def timeline(inv, idx: Index, *, source_keys: list[str] | None = None,
             client_id: str | None = None, buckets: int = EVENT_BUCKETS,
             time_from: int | None = None, time_to: int | None = None) -> dict:
    """Event-count histogram over mapped event times, with untimed rows counted separately.

    Empty buckets inside the range are measured zeros only when every source with mapped
    event times was read completely and verified; otherwise they are marked as possible gaps.
    """
    sources = {s["source_key"]: s for s in idx.sources()}
    q = RowQuery(source_keys=list(source_keys or []), client_id=client_id)
    with idx.db() as conn:
        where, params = _where(conn, inv, q, sources)
        base = f"FROM rows r JOIN sources s ON s.sid=r.sid WHERE {where}"
        bounds = conn.execute(f"SELECT MIN(r.event_ts), MAX(r.event_ts), COUNT(r.event_ts) "
                              f"{base}", params).fetchone()
        untimed = {r[0]: r[1] for r in conn.execute(
            f"SELECT r.time_status, COUNT(*) {base} AND r.event_ts IS NULL GROUP BY "
            "r.time_status", params)}
        lo, hi, timed = bounds
        if time_from is not None:
            lo = max(lo, time_from) if lo is not None else None
        if time_to is not None:
            hi = min(hi, time_to - 1) if hi is not None else None
        rows = []
        width, label = 1_000_000, "1 second"
        if timed and lo is not None and hi is not None and hi >= lo:
            width, label = _width(max(1, hi - lo + 1), buckets)
            start = lo - (lo % width)
            counts = {r[0]: r[1] for r in conn.execute(
                f"SELECT (r.event_ts - ?) / ? AS b, COUNT(*) {base} AND r.event_ts >= ? AND "
                "r.event_ts <= ? GROUP BY b", [start, width, *params, lo, hi])}
            n = (hi - start) // width + 1
            for i in range(int(n)):
                # Edge buckets are clipped to the requested range, so a bucket never claims
                # an interval whose events were excluded by the filter.
                b_from = start + i * width
                b_to = b_from + width
                if time_from is not None:
                    b_from = max(b_from, time_from)
                if time_to is not None:
                    b_to = min(b_to, time_to)
                rows.append({"start": micros_to_iso(b_from), "end": micros_to_iso(b_to),
                             "from_us": b_from, "to_us": b_to, "count": counts.get(i, 0),
                             "partial_bucket": (b_to - b_from) != width})
    # Every in-scope source whose mapping declares an event time counts for completeness,
    # including sources that failed or saved no timed rows.
    in_scope = [s for s in sources.values()
                if (not source_keys or s["source_key"] in source_keys)
                and (not client_id or s["client_id"] == client_id)]
    time_mapped = [s for s in in_scope if (s.get("time") or {}).get("rules")]
    incomplete = [s["full_name"] + " on " + (s["hostname"] or s["client_id"])
                  for s in time_mapped if source_state(s) != "complete"]
    complete = not incomplete
    for b in rows:
        b["measured"] = b["count"] > 0 or complete
    mappings = []
    for s in in_scope:
        t = s.get("time") or {}
        mappings.append({"source_key": s["source_key"], "label": source_label(s),
                         "rules": t.get("rules") or [], "statuses": t.get("statuses") or {},
                         "basis": t.get("basis")})
    total_untimed = sum(untimed.values())
    return {
        "mode": "events", "basis": "Mapped source event time, shown in UTC",
        "counting_unit": "evidence rows",
        "range": {"from": micros_to_iso(lo) if lo is not None and timed else None,
                  "to": micros_to_iso(hi) if hi is not None and timed else None},
        "bucket": {"width_us": width, "label": label}, "buckets": rows,
        "timed_rows": timed or 0, "complete": complete, "incomplete_sources": incomplete,
        "untimed": {"total": total_untimed,
                    "by_reason": [{"status": k, "count": v, "label": UNTIMED_LABELS.get(k, k)}
                                  for k, v in sorted(untimed.items(), key=lambda kv: -kv[1])]},
        "mappings": mappings,
        "note": (None if complete else
                 "Some sources with event times were not read completely; an empty interval "
                 "may be a collection gap rather than a measured zero."),
    }
