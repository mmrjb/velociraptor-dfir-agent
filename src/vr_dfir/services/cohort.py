"""Hosts, low-prevalence entities and "Where else was this seen?" within a defined cohort.

Cohort scope
* ``investigation`` (default): the hosts represented in this investigation.
* ``organization``: saved investigations of the same, known organization and data mode, using
  one observation per host and source (the latest capture inside the time window that ends at
  this investigation's newest capture). An unknown organization never joins a cohort.

Denominators
A host is *eligible* for an entity kind (executable path, hash, privileged account, ...) only
when its chosen observation includes a source of that kind that was read completely with rows,
verified, and carries the field. Repeated captures of one host count once. Hosts of another OS
family than the selected host, and hosts with incomplete sources, are listed as excluded with
the reason. Low prevalence is a review signal, never a verdict; with fewer than three
eligible hosts no outlier list is produced.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from vr_dfir.errors import ValidationError
from vr_dfir.evidence import presentation as pres
from vr_dfir.evidence.timeparse import micros_to_iso, parse_iso
from vr_dfir.services.derived import Index
from vr_dfir.services.views import _reviews, source_label, source_state

MIN_COHORT = 3
SCOPES = ("investigation", "organization")
MAX_REFS = 20


@dataclass
class Obs:
    inv_id: str
    idx: Index
    sid: int
    client_id: str
    hostname: str
    full_name: str
    domain: str
    state: str
    collected_ts: int | None
    source: dict


@dataclass
class Host:
    client_id: str
    hostname: str
    os: str = "unknown"
    obs: dict[str, Obs] = field(default_factory=dict)  # full_name -> observation


def _ts(value):
    p = parse_iso(value) if isinstance(value, str) else None
    return p.micros if p and p.ok else None


def build(inv, idx: Index, *, scope: str, members: list[tuple[object, Index]] | None,
          window_days: int = 30) -> dict:
    """Cohort hosts and their chosen observations. ``members`` = other indexed investigations."""
    if scope not in SCOPES:
        raise ValidationError("Unknown cohort scope.")
    org_ids = sorted({s["org_id"] for s in idx.sources() if s["org_id"]})
    mixed = len(org_ids) > 1 or (org_ids and any(not s["org_id"] for s in idx.sources()))
    if mixed or (scope == "organization" and len(org_ids) != 1):
        return {"hosts": {}, "org_id": None, "excluded": [], "window": None,
                "unavailable": ("This investigation's evidence comes from more than one "
                                "organization (or partly from an unknown one), so its hosts "
                                "are not combined into one cohort." if mixed else
                                "The organization of this investigation is unknown, so no "
                                "organization cohort is formed.")}
    pool: list[tuple[object, Index]] = [(inv, idx)]
    if scope == "organization":
        pool += list(members or [])
    own = [s for s in idx.sources()]
    newest = max((t for t in (_ts(s["collected_at"]) for s in own) if t is not None),
                 default=None)
    start = newest - window_days * 86_400_000_000 if newest is not None else None
    hosts: dict[str, Host] = {}
    excluded: list[dict] = []
    for member, midx in pool:
        for s in midx.sources():
            if scope == "organization" and s["org_id"] != org_ids[0]:
                continue
            ts = _ts(s["collected_at"])
            if member is not inv and newest is not None and (ts is None or ts > newest or
                                                             ts < start):
                excluded.append({"client_id": s["client_id"], "hostname": s["hostname"],
                                 "investigation_id": member.id,
                                 "reason": "capture outside the cohort time window"})
                continue
            h = hosts.setdefault(s["client_id"], Host(s["client_id"], s["hostname"]))
            if s.get("host_os"):
                h.os = s["host_os"]
            o = Obs(member.id, midx, s["sid"], s["client_id"], s["hostname"], s["full_name"],
                    s["domain"], source_state(s), ts, s)
            prev = h.obs.get(s["full_name"])
            if prev is None or _better(o, prev):
                h.obs[s["full_name"]] = o
    return {"hosts": hosts, "org_id": org_ids[0] if len(org_ids) == 1 else None,
            "excluded": _dedupe_excluded(excluded),
            "window": {"from": micros_to_iso(start) if start else None,
                       "to": micros_to_iso(newest) if newest else None,
                       "days": window_days} if scope == "organization" else None,
            "unavailable": None}


def _better(new: Obs, old: Obs) -> bool:
    if (new.collected_ts or 0) != (old.collected_ts or 0):
        return (new.collected_ts or 0) > (old.collected_ts or 0)
    return new.state == "complete" and old.state != "complete"


def _dedupe_excluded(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in items:
        key = (e["client_id"], e["reason"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _eligible_kinds(h: Host) -> dict[str, list[Obs]]:
    """entity kind -> complete observations of this host that carry it."""
    out: dict[str, list[Obs]] = defaultdict(list)
    for o in h.obs.values():
        if o.state != "complete":
            continue
        with o.idx.db() as conn:
            for r in conn.execute("SELECT kind FROM entity_fields WHERE sid=?", (o.sid,)):
                out[r["kind"]].append(o)
    return out


def _presence(hosts: dict[str, Host], kinds: set[str] | None = None,
              only: tuple[str, str] | None = None) -> dict[tuple[str, str], dict]:
    """(kind, key) -> {display, hosts: {client_id: [(inv_id, idx, sid, row_index)]}}."""
    out: dict[tuple[str, str], dict] = {}
    for h in hosts.values():
        for o in h.obs.values():
            with o.idx.db() as conn:
                if only:
                    rows = conn.execute("SELECT kind, key, display, row_index FROM entities "
                                        "WHERE sid=? AND kind=? AND key=? LIMIT 50",
                                        (o.sid, only[0], only[1]))
                else:
                    rows = conn.execute("SELECT kind, key, MIN(display) AS display, "
                                        "MIN(row_index) AS row_index FROM entities WHERE sid=? "
                                        "GROUP BY kind, key", (o.sid,))
                for r in rows:
                    if kinds is not None and r["kind"] not in kinds:
                        continue
                    item = out.setdefault((r["kind"], r["key"]), {"display": r["display"],
                                                                  "hosts": {}})
                    item["hosts"].setdefault(h.client_id, []).append(
                        (o.inv_id, o.idx, o.sid, r["row_index"], o.state))
    return out


def _evidence_ref(idx: Index, sid: int, row_index: int) -> dict | None:
    with idx.db() as conn:
        r = conn.execute("SELECT r.evidence_id, r.title, s.full_name, s.hostname, s.client_id "
                         "FROM rows r JOIN sources s ON s.sid=r.sid WHERE r.sid=? AND "
                         "r.row_index=?", (sid, row_index)).fetchone()
    return dict(r) if r else None


def _groups(hosts: dict[str, Host]) -> dict[str, list[Host]]:
    g: dict[str, list[Host]] = defaultdict(list)
    for h in hosts.values():
        g[h.os].append(h)
    return g


def hosts_view(inv, idx: Index, *, scope: str, members=None, window_days: int = 30,
               offset: int = 0, limit: int = 50) -> dict:
    """Host comparison and the low-prevalence entity table for a cohort."""
    c = build(inv, idx, scope=scope, members=members, window_days=window_days)
    hosts: dict[str, Host] = c["hosts"]
    reviews_by_inv: dict[str, dict] = {}
    rows = []
    for h in sorted(hosts.values(), key=lambda x: (x.hostname or x.client_id)):
        open_f = total_f = saved = complete = 0
        seen_findings = set()
        for o in h.obs.values():
            saved += o.source["indexed_rows"]
            complete += o.state == "complete"
        for inv_id, midx in {o.inv_id: o.idx for o in h.obs.values()}.items():
            rv = reviews_by_inv.setdefault(inv_id, _reviews(_inv_of(inv, members, inv_id)))
            with midx.db() as conn:
                for r in conn.execute("SELECT f.finding_id FROM finding_clients fc JOIN findings f "
                                      "ON f.finding_id=fc.finding_id WHERE fc.client_id=?",
                                      (h.client_id,)):
                    fid = (inv_id, r["finding_id"])
                    if fid in seen_findings:
                        continue
                    seen_findings.add(fid)
                    total_f += 1
                    if rv.get(r["finding_id"], "unreviewed") in ("unreviewed", "in_review",
                                                                 "needs_follow_up"):
                        open_f += 1
        rows.append({"client_id": h.client_id, "hostname": h.hostname or h.client_id,
                     "os": h.os, "findings_open": open_f, "findings_total": total_f,
                     "rows": saved, "sources_complete": complete, "sources_total": len(h.obs),
                     "investigations": sorted({o.inv_id for o in h.obs.values()})})
    outliers, groups_info = _outliers(hosts)
    return {"state": "ready", "scope": scope, "org_id": c["org_id"], "window": c["window"],
            "unavailable": c["unavailable"], "hosts": rows, "host_count": len(rows),
            "excluded": c["excluded"], "groups": groups_info,
            "outliers": _with_refs(outliers[offset:offset + limit]),
            "outliers_total": len(outliers),
            "offset": offset, "limit": limit, "min_cohort": MIN_COHORT,
            "counting_unit": "eligible hosts (one observation per host)",
            "note": "Low prevalence means seldom seen in this cohort; it is a reason to review, "
                    "not a sign of compromise."}


def _inv_of(inv, members, inv_id):
    if inv.id == inv_id:
        return inv
    for m, _ in members or []:
        if m.id == inv_id:
            return m
    return inv


def _outliers(hosts: dict[str, Host]) -> tuple[list[dict], list[dict]]:
    out: list[dict] = []
    info = []
    eligible_by_host = {cid: _eligible_kinds(h) for cid, h in hosts.items()}
    for os_name, members in sorted(_groups(hosts).items()):
        group = {h.client_id for h in members}
        kinds: dict[str, set[str]] = defaultdict(set)
        for cid in group:
            for kind in eligible_by_host[cid]:
                kinds[kind].add(cid)
        usable = {k: v for k, v in kinds.items() if len(v) >= MIN_COHORT}
        info.append({"os": os_name, "hosts": len(group),
                     "kinds": [{"kind": k, "label": pres.ENTITY_KINDS.get(k, k),
                                "eligible_hosts": len(v)} for k, v in sorted(kinds.items())],
                     "usable": bool(usable)})
        if not usable:
            continue
        sub = {cid: hosts[cid] for cid in group}
        presence = _presence(sub, set(usable))
        for (kind, key), item in presence.items():
            eligible = usable[kind]
            seen = {cid for cid in item["hosts"] if cid in eligible}
            threshold = max(1, len(eligible) // 5)
            if not seen or len(seen) > threshold or len(seen) >= len(eligible):
                continue
            out.append({"kind": kind, "label": pres.ENTITY_KINDS.get(kind, kind), "key": key,
                        "display": item["display"], "seen": len(seen),
                        "eligible": len(eligible), "os": os_name,
                        "text": f"Seen on {len(seen)} of {len(eligible)} eligible hosts",
                        "hosts": [hosts[cid].hostname or cid for cid in sorted(seen)],
                        "_rows": [item["hosts"][cid][0] for cid in sorted(seen)][:5]})
    out.sort(key=lambda x: (x["seen"] / x["eligible"], x["kind"], x["display"]))
    return out, info


def _with_refs(items: list[dict]) -> list[dict]:
    """Resolve evidence references for one page of outliers (one connection per index)."""
    wanted: dict[int, tuple] = {}
    for item in items:
        for inv_id, idx, sid, ri, _ in item["_rows"]:
            wanted.setdefault(id(idx), (idx, set()))[1].add((sid, ri, inv_id))
    refs: dict[tuple, dict] = {}
    for idx, keys in wanted.values():
        with idx.db() as conn:
            for sid, ri, inv_id in keys:
                r = conn.execute("SELECT r.evidence_id, r.title, s.full_name, s.hostname, "
                                 "s.client_id FROM rows r JOIN sources s ON s.sid=r.sid WHERE "
                                 "r.sid=? AND r.row_index=?", (sid, ri)).fetchone()
                if r:
                    refs[(id(idx), sid, ri)] = {**dict(r), "investigation_id": inv_id}
    out = []
    for item in items:
        rows = item["_rows"]
        clean = {k: v for k, v in item.items() if k != "_rows"}
        clean["refs"] = [refs[(id(idx), sid, ri)] for _, idx, sid, ri, _ in rows
                         if (id(idx), sid, ri) in refs]
        out.append(clean)
    return out


def where_else(inv, idx: Index, evidence_id: str, *, scope: str, members=None,
               window_days: int = 30) -> dict:
    """Prevalence of each entity of one row in the cohort, with the rows that show it."""
    with idx.db() as conn:
        hit = conn.execute("SELECT r.sid, r.row_index, s.client_id, s.hostname FROM rows r "
                           "JOIN sources s ON s.sid=r.sid WHERE r.evidence_id=?",
                           (evidence_id,)).fetchone()
        if hit is None:
            raise ValidationError("This evidence row is not in the investigation.")
        ents = [dict(r) for r in conn.execute("SELECT kind, key, display FROM entities WHERE "
                                              "sid=? AND row_index=?", (hit["sid"],
                                                                        hit["row_index"]))]
    c = build(inv, idx, scope=scope, members=members, window_days=window_days)
    hosts: dict[str, Host] = c["hosts"]
    me = hosts.get(hit["client_id"])
    os_name = me.os if me else "unknown"
    group = {cid: h for cid, h in hosts.items() if h.os == os_name}
    other_os = sorted({(h.hostname or cid) for cid, h in hosts.items() if h.os != os_name})
    eligible_by_host = {cid: _eligible_kinds(h) for cid, h in group.items()}
    results = []
    for e in ents:
        eligible = {cid for cid, kinds in eligible_by_host.items() if e["kind"] in kinds}
        presence = _presence(group, only=(e["kind"], e["key"])).get((e["kind"], e["key"]),
                                                                     {"hosts": {}})
        seen = {cid for cid in presence["hosts"] if cid in eligible}
        also = {cid for cid in presence["hosts"] if cid not in eligible}
        refs = []
        for cid in sorted(presence["hosts"]):
            for inv_id, midx, sid, ri, _ in presence["hosts"][cid][:5]:
                ref = _evidence_ref(midx, sid, ri)
                if ref and ref["evidence_id"] != evidence_id:
                    refs.append({**ref, "investigation_id": inv_id,
                                 "eligible_host": cid in eligible})
        not_eligible = sorted((group[cid].hostname or cid) for cid in group if cid not in
                              eligible)
        results.append({
            "kind": e["kind"], "label": pres.ENTITY_KINDS.get(e["kind"], e["kind"]),
            "key": e["key"], "display": e["display"], "seen": len(seen),
            "eligible": len(eligible),
            "text": (f"Seen on {len(seen)} of {len(eligible)} eligible host"
                     f"{'' if len(eligible) == 1 else 's'}"),
            "also_seen_not_eligible": sorted((group[cid].hostname or cid) for cid in also),
            "not_eligible_hosts": not_eligible, "refs": refs[:MAX_REFS],
            "cohort_too_small": len(eligible) < MIN_COHORT,
        })
    return {"state": "ready", "evidence_id": evidence_id, "scope": scope, "org_id": c["org_id"],
            "window": c["window"], "unavailable": c["unavailable"], "host_os": os_name,
            "other_os_hosts": other_os, "excluded": c["excluded"], "entities": results,
            "counting_unit": "eligible hosts (one observation per host)",
            "note": "Only saved evidence in this scope is searched, with fixed structured "
                    "filters; nothing is queried on the server."}


def source_brief(s: dict) -> str:
    return source_label(s)
