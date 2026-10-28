"""UI API for the summary-first investigation views (read-only over saved evidence).

Every route takes validated identifiers and fixed, structured filters; there is no generic
query, path or VQL parameter. View routes answer ``{"state": "ready", ...}`` when the derived
index is fresh, or a status (``building`` with progress, ``evidence_changing``,
``no_evidence``, ``failed``) the browser can show and poll. No route makes a model request.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from vr_dfir import ids
from vr_dfir.errors import ValidationError
from vr_dfir.services import views
from vr_dfir.services.derived import Index, IndexManager

EVIDENCE_ID_RE = re.compile(r"^ev-[0-9a-f]{8,40}$")
FINDING_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
SOURCE_KEY_MAX = 600


def _ok(data):
    return JSONResponse({"ok": True, "data": data})


def evidence_id(value: str) -> str:
    if not isinstance(value, str) or not EVIDENCE_ID_RE.match(value):
        raise ValidationError("Invalid evidence ID.")
    return value


def finding_id(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not FINDING_ID_RE.match(value):
        raise ValidationError("Invalid finding ID.")
    return value


def source_keys(value: str | None) -> list[str]:
    if not value:
        return []
    keys = [k for k in value.split("|") if k]
    if any(len(k) > SOURCE_KEY_MAX for k in keys):
        raise ValidationError("Invalid source key.")
    return keys


def register(app: FastAPI, st) -> None:
    st.indexes = IndexManager(st.settings)

    def ready(inv_id: str, wait: float = 1.5) -> tuple[object, Index | dict]:
        _, inv = st.find_investigation(inv_id)
        meta = inv.reload()
        active = st.jobs.is_active(inv_id) or (meta.get("job") or {}).get("state") in (
            "running", "queued")
        return inv, st.indexes.ensure(inv, wait=wait, job_active=active)

    def not_ready(result: dict) -> JSONResponse:
        return _ok(result)

    @app.get("/api/v1/investigations/{inv_id}/views/status")
    def view_status(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        return _ok(st.indexes.status(inv))

    @app.post("/api/v1/investigations/{inv_id}/views/index/cancel")
    def cancel_index(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        return _ok({"cancelled": st.indexes.cancel(inv),
                    "note": "Saved evidence, reviews and notes are unchanged."})

    @app.post("/api/v1/investigations/{inv_id}/views/index/rebuild")
    def rebuild_index(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        meta = inv.reload()
        active = st.jobs.is_active(inv_id) or (meta.get("job") or {}).get("state") in (
            "running", "queued")
        result = st.indexes.ensure(inv, job_active=active, retry=True)
        return _ok({"state": "ready"} if isinstance(result, Index) else result)

    @app.get("/api/v1/investigations/{inv_id}/views/overview")
    def view_overview(inv_id: str):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return not_ready(idx)
        return _ok(views.overview(inv, idx, stale_days=st.settings.stale_after_days))

    @app.get("/api/v1/investigations/{inv_id}/views/sources")
    def view_sources(inv_id: str):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return not_ready(idx)
        return _ok({"state": "ready",
                    **views.sources_list(idx, stale_days=st.settings.stale_after_days)})

    @app.get("/api/v1/investigations/{inv_id}/views/rows")
    def view_rows(inv_id: str, source_key: str | None = None, client_id: str | None = None,
                  finding: str | None = None, severity: str | None = None,
                  review: str | None = None, origin: str | None = None, cited: bool = False,
                  from_us: int | None = None, to_us: int | None = None,
                  untimed: str | None = None, entity_kind: str | None = None,
                  entity_key: str | None = None, q: str | None = None, order: str = "source",
                  offset: int = 0, limit: int = 50):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return not_ready(idx)
        query = views.RowQuery(
            source_keys=source_keys(source_key),
            client_id=ids.client_id(client_id) if client_id else None,
            finding_id=finding_id(finding), severity=severity or None, review=review or None,
            origin=origin or None, cited=cited, time_from=from_us, time_to=to_us,
            untimed=untimed or None, entity_kind=entity_kind or None,
            entity_key=entity_key or None, q=q or None, order=order)
        return _ok({"state": "ready", **views.query_rows(inv, idx, query, offset=offset,
                                                         limit=limit)})

    @app.get("/api/v1/investigations/{inv_id}/views/inspect/{ev_id}")
    def view_inspect(inv_id: str, ev_id: str):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return not_ready(idx)
        data = views.inspect(inv, idx, evidence_id(ev_id), settings=st.settings)
        extra = getattr(st, "inspect_extras", None)
        if extra:
            data["actions"] = extra(inv, idx, data)
        return _ok({"state": "ready", **data})

    @app.get("/api/v1/investigations/{inv_id}/views/timeline")
    def view_timeline(inv_id: str, source_key: str | None = None,
                      client_id: str | None = None, from_us: int | None = None,
                      to_us: int | None = None, buckets: int = views.EVENT_BUCKETS):
        inv, idx = ready(inv_id)
        if not isinstance(idx, Index):
            return not_ready(idx)
        if not 4 <= buckets <= 120:
            raise ValidationError("buckets must be between 4 and 120.")
        return _ok({"state": "ready", **views.timeline(
            inv, idx, source_keys=source_keys(source_key),
            client_id=ids.client_id(client_id) if client_id else None,
            buckets=buckets, time_from=from_us, time_to=to_us)})
