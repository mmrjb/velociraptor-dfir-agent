"""UI API for report selection (pins), baseline changes, capture history, host cohorts and the
selected-evidence report. All operations read saved evidence; pins, comparisons and reports
are local sidecar files. Identifiers are validated and every capture or cohort stays inside one
known organization. No route makes a model request except the explicit ``explain`` action.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from vr_dfir.errors import NotFoundError, ValidationError
from vr_dfir.jsonio import atomic_write_json, atomic_write_text
from vr_dfir.services import captures as caps
from vr_dfir.services import cohort, comparisons
from vr_dfir.services.derived import Index
from vr_dfir.services.selection import SelectionStore, make_pin, pin_id
from vr_dfir.web.views_api import evidence_id, finding_id

CMP_ID_RE = re.compile(r"^cmp-[0-9a-f]{24}$")
PIN_ID_RE = re.compile(r"^pin-[0-9a-f]{16}$")
ITEM_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PinBody(_Body):
    kind: str = Field(pattern="^(evidence|finding|change)$")
    ref: str | None = Field(default=None, max_length=200)
    comparison_id: str | None = Field(default=None, max_length=40)
    item_key: str | None = Field(default=None, max_length=2100)
    note: str | None = Field(default=None, max_length=2000)


class NoteBody(_Body):
    note: str | None = Field(default=None, max_length=2000)


class CompareBody(_Body):
    source_key: str = Field(max_length=600)
    baseline_investigation_id: str = Field(max_length=100)
    baseline_source_key: str = Field(max_length=600)


class ExplainChanges(_Body):
    refresh: bool = False


def _ok(data, status: int = 200):
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def cmp_id(value: str) -> str:
    if not CMP_ID_RE.match(value or ""):
        raise ValidationError("Invalid comparison ID.")
    return value


def register(app: FastAPI, st) -> None:
    store = st.service.store
    st.comparisons = comparisons.ComparisonManager(store, st.settings)

    def ready(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        meta = inv.reload()
        active = st.jobs.is_active(inv_id) or (meta.get("job") or {}).get("state") in (
            "running", "queued")
        return inv, st.indexes.ensure(inv, wait=1.5, job_active=active)

    def capture_of(inv, source_key: str) -> caps.Capture:
        c = caps.find(store, st.settings, inv.id, source_key)
        if c is None:
            raise NotFoundError("This source is not in the investigation's saved evidence.")
        return c

    # ------------------------------------------------------------------ inspector actions

    def inspect_extras(inv, idx: Index, data: dict) -> dict:
        source_key = data["record"]["source_key"]
        actions = {"where_else": {"available": bool(data["entities"])},
                   "pin": {"pinned": False, "pin_id": None}}
        if not data["entities"]:
            actions["where_else"]["reason"] = "This row has no values that are compared across hosts."
        from vr_dfir.analysis.compare import adapter_for
        adapter = adapter_for(data["presentation"]["domain"])
        if adapter is None:
            actions["compare"] = {"available": False, "reason": "No comparison rules for this kind "
                                                                "of source."}
        else:
            current = caps.find(store, st.settings, inv.id, source_key)
            older = caps.baselines(store, st.settings, current) if current else []
            actions["compare"] = ({"available": True, "source_key": source_key,
                                   "baselines": len(older), "adapter": adapter.label}
                                  if older else {"available": False,
                                                 "reason": "No earlier saved capture of this "
                                                           "source for this host."})
        pid = pin_id("evidence", data["record"]["evidence_id"])
        pins = {p["pin_id"]: p for p in SelectionStore(inv.path).list()}
        if pid in pins:
            actions["pin"] = {"pinned": True, "pin_id": pid, "note": pins[pid].get("note")}
        return actions

    st.inspect_extras = inspect_extras

    # ------------------------------------------------------------------ pins

    @app.get("/api/v1/investigations/{inv_id}/selection")
    def list_pins(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        return _ok({"pins": SelectionStore(inv.path).list()})

    @app.post("/api/v1/investigations/{inv_id}/selection")
    def add_pin(inv_id: str, body: PinBody):
        _, inv = st.find_investigation(inv_id)
        if body.kind == "evidence":
            evidence_id(body.ref or "")
        elif body.kind == "finding":
            finding_id(body.ref)
        elif body.comparison_id:
            cmp_id(body.comparison_id)
        pin = make_pin(inv, st.indexes, body.kind, ref=body.ref,
                       comparison_id=body.comparison_id, item_key=body.item_key, note=body.note)
        return _ok(pin, status=201)

    @app.put("/api/v1/investigations/{inv_id}/selection/{pid}")
    def note_pin(inv_id: str, pid: str, body: NoteBody):
        _, inv = st.find_investigation(inv_id)
        if not PIN_ID_RE.match(pid):
            raise ValidationError("Invalid pin ID.")
        return _ok(SelectionStore(inv.path).set_note(pid, body.note))

    @app.delete("/api/v1/investigations/{inv_id}/selection/{pid}")
    def remove_pin(inv_id: str, pid: str):
        _, inv = st.find_investigation(inv_id)
        if not PIN_ID_RE.match(pid):
            raise ValidationError("Invalid pin ID.")
        return _ok({"removed": SelectionStore(inv.path).remove(pid),
                    "note": "Evidence, findings and review decisions are unchanged."})

    # ------------------------------------------------------------------ changes

    @app.get("/api/v1/investigations/{inv_id}/changes/sources")
    def change_sources(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        from vr_dfir.analysis.compare import adapter_for
        out, other = [], []
        for c in caps.catalog(store, st.settings):
            if c.investigation_id != inv.id:
                continue
            adapter = adapter_for(c.domain)
            if adapter is None:
                other.append({"source_key": c.source_key, "full_name": c.full_name,
                              "hostname": c.hostname})
                continue
            older = caps.baselines(store, st.settings, c)
            out.append({"source_key": c.source_key, "full_name": c.full_name,
                        "label": f"{adapter.label} — {c.hostname or c.client_id}",
                        "adapter": adapter.id, "adapter_label": adapter.label,
                        "current": c.to_dict(), "baselines": [b.to_dict() for b in older],
                        "default_baseline": older[0].to_dict() if older else None,
                        "cached": comparisons.cached_for(inv, c.source_key)[:5],
                        "reason": None if older else "No earlier saved capture of this source "
                                                     "for this host."})
        priority = ["users", "groups", "services", "listeners", "cron", "ssh_keys", "settings",
                    "files", "mounts", "packages"]
        out.sort(key=lambda x: (x["default_baseline"] is None,
                                priority.index(x["adapter"]) if x["adapter"] in priority else 99,
                                x["label"]))
        return _ok({"sources": out, "not_comparable": other,
                    "note": "A baseline is an existing saved capture. Comparing never starts a "
                            "collection."})

    @app.post("/api/v1/investigations/{inv_id}/changes")
    def create_change(inv_id: str, body: CompareBody):
        _, inv = st.find_investigation(inv_id)
        current = capture_of(inv, body.source_key)
        _, base_inv = st.find_investigation(body.baseline_investigation_id)
        baseline = capture_of(base_inv, body.baseline_source_key)
        return _ok(st.comparisons.start(current, baseline))

    @app.get("/api/v1/investigations/{inv_id}/changes/{cid}")
    def change_status(inv_id: str, cid: str):
        _, inv = st.find_investigation(inv_id)
        return _ok(st.comparisons.status(inv, cmp_id(cid)))

    @app.post("/api/v1/investigations/{inv_id}/changes/{cid}/cancel")
    def change_cancel(inv_id: str, cid: str):
        st.find_investigation(inv_id)
        return _ok({"cancelled": st.comparisons.cancel(cmp_id(cid))})

    @app.get("/api/v1/investigations/{inv_id}/changes/{cid}/items")
    def change_items(inv_id: str, cid: str, state: str | None = None, unchanged: bool = False,
                     ignored: bool = False, offset: int = 0, limit: int = 50):
        _, inv = st.find_investigation(inv_id)
        comparisons.load(inv, cmp_id(cid))
        return _ok(comparisons.items(inv, cid, state=state or None, show_unchanged=unchanged,
                                     ignored=ignored, offset=offset, limit=limit))

    @app.get("/api/v1/investigations/{inv_id}/changes/{cid}/items/{item_key}")
    def change_item(inv_id: str, cid: str, item_key: str):
        _, inv = st.find_investigation(inv_id)
        if not ITEM_KEY_RE.match(item_key):
            raise ValidationError("Invalid change item key.")
        return _ok(comparisons.item(inv, cmp_id(cid), item_key))

    @app.post("/api/v1/investigations/{inv_id}/changes/{cid}/explain")
    def change_explain(inv_id: str, cid: str, body: ExplainChanges):
        from vr_dfir.services.explanations import explain_changes
        _, inv = st.find_investigation(inv_id)
        return _ok(explain_changes(inv, st.runtime, comparisons.load(inv, cmp_id(cid)),
                                   comparisons.items(inv, cid, limit=40)["items"],
                                   refresh=body.refresh))

    @app.get("/api/v1/investigations/{inv_id}/views/captures")
    def capture_history(inv_id: str, source_key: str):
        _, inv = st.find_investigation(inv_id)
        current = capture_of(inv, source_key)
        hist = caps.history(store, st.settings, current)
        cached = {c["baseline"]["source_key"] + "|" + c["baseline"]["investigation_id"]: c
                  for c in comparisons.cached_for(inv, source_key)}
        points = []
        for c in hist:
            d = c.to_dict()
            d["is_current"] = c.observation == current.observation
            d["complete"] = c.absence_ok()
            points.append(d)
        return _ok({"state": "ready", "current": current.to_dict(), "captures": points,
                    "comparisons": list(cached.values()),
                    "enough_history": len(points) >= 2,
                    "basis": "Capture (observation) time of each saved collection, not event "
                             "time. Appearing in a later capture does not mean it was created "
                             "then.",
                    "counting_unit": "saved rows per capture"})

    # ------------------------------------------------------------------ cohorts

    def members_for(inv, idx: Index):
        orgs = sorted({s["org_id"] for s in idx.sources() if s["org_id"]})
        if len(orgs) != 1:
            return [], 0, 0
        synthetic = idx.meta.get("synthetic") == "true"
        ids = sorted({c.investigation_id for c in caps.catalog(store, st.settings)
                      if c.org_id == orgs[0] and c.synthetic == synthetic
                      and c.investigation_id != inv.id})
        members, pending = [], 0
        for mid in ids[:200]:
            m = store.get(mid)
            active = st.jobs.is_active(mid) or (m.reload().get("job") or {}).get("state") in (
                "running", "queued")
            r = st.indexes.ensure(m, wait=0.3, job_active=active)
            if isinstance(r, Index):
                members.append((m, r))
            elif r["state"] == "building":
                pending += 1
        return members, pending, len(ids)

    @app.get("/api/v1/investigations/{inv_id}/views/hosts")
    def hosts(inv_id: str, scope: str = "investigation", window_days: int = 30,
              offset: int = 0, limit: int = 50):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return _ok(idx)
        if not 1 <= window_days <= 365 or not 1 <= limit <= 200 or offset < 0:
            raise ValidationError("window_days must be 1-365 and limit 1-200.")
        members = []
        if scope == "organization":
            members, pending, total = members_for(inv, idx)
            if pending:
                return _ok({"state": "building", "progress": {
                    "rows_done": total - pending, "rows_total": total, "elapsed_seconds": 0,
                    "unit": "investigations"}})
        return _ok(cohort.hosts_view(inv, idx, scope=scope, members=members,
                                     window_days=window_days, offset=offset, limit=limit))

    @app.get("/api/v1/investigations/{inv_id}/views/where-else/{ev_id}")
    def where_else(inv_id: str, ev_id: str, scope: str = "investigation", window_days: int = 30):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return _ok(idx)
        if not 1 <= window_days <= 365:
            raise ValidationError("window_days must be 1-365.")
        members = []
        if scope == "organization":
            members, pending, total = members_for(inv, idx)
            if pending:
                return _ok({"state": "building", "progress": {
                    "rows_done": total - pending, "rows_total": total, "elapsed_seconds": 0,
                    "unit": "investigations"}})
        return _ok(cohort.where_else(inv, idx, evidence_id(ev_id), scope=scope, members=members,
                                     window_days=window_days))

    # ------------------------------------------------------------------ selected report

    @app.post("/api/v1/investigations/{inv_id}/selected-report")
    def build_selected_report(inv_id: str):
        from vr_dfir.reporting.selected import (
            build_selected,
            render_selected_html,
            render_selected_markdown,
        )
        _, inv = st.find_investigation(inv_id)
        report = build_selected(inv, store)
        atomic_write_json(inv.report_dir / "selected.json", report)
        atomic_write_text(inv.report_dir / "selected.md", render_selected_markdown(report))
        atomic_write_text(inv.report_dir / "selected.html", render_selected_html(report))
        return _ok({"generated_at": report["generated_at"], "items": report["selection"]["items"],
                    "limitations": report["coverage"]["limitations"]})

