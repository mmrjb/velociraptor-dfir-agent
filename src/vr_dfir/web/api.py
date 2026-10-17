"""Private local UI API (``/api/v1``) over the shared services.

This is a UI adapter, not an MCP-over-HTTP endpoint. The browser can only call the
operations below; it cannot submit VQL, tool names, filesystem paths, or credentials.
Allowlists, scope validation, budgets, and evidence restrictions are enforced here and in
the shared services, never only in the frontend.
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import tarfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from vr_dfir import ids
from vr_dfir.config import Settings
from vr_dfir.errors import (
    ConfigurationError,
    DfirError,
    NotFoundError,
    ScopeError,
    StaleRequestError,
    ValidationError,
)
from vr_dfir.evidence.bundle import EvidenceBundle, now_iso
from vr_dfir.jsonio import atomic_write_json, ensure_dir, read_json
from vr_dfir.profiles.store import ProfileStore
from vr_dfir.services.investigations import InvestigationService
from vr_dfir.services.runstore import InvestigationBusy, RunStore
from vr_dfir.services.runtime import Runtime, live_runtime
from vr_dfir.version import __version__
from vr_dfir.web.assets import static_dir
from vr_dfir.web.jobs import JobManager, TooManyJobs, recover_interrupted
from vr_dfir.web.security import (
    CSRF_HEADER,
    SAFE_METHODS,
    SECURITY_HEADERS,
    SESSION_COOKIE,
    SessionManager,
    host_allowed,
    origin_matches,
)

logger = logging.getLogger(__name__)
# Bundled UI fonts; some hosts' mimetypes tables lack these (responses use nosniff).
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
PLAN_TOKEN_TTL = 1800
MAX_JSON_BODY = 1024 * 1024
STATUS_CODES = {
    "validation_error": 422, "not_found": 404, "scope_error": 409, "ambiguous_client": 409,
    "policy_refused": 403, "stale_plan": 409, "configuration_error": 503,
    "velociraptor_unavailable": 503, "velociraptor_error": 502, "launch_outcome_unknown": 502,
    "unrecognized_response": 502, "model_unavailable": 503, "model_error": 502,
    "investigation_busy": 409, "too_many_jobs": 429,
}


# ----------------------------------------------------------------------- request models

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanRequest(_Body):
    mode: str = Field(pattern="^(existing_flow|existing_hunt)$")
    org_id: str | None = None
    client_id: str | None = None
    hostname: str | None = None
    expected_client_id: str | None = None
    flow_id: str | None = None
    hunt_id: str | None = None
    client_filter: list[str] | None = None
    artifact_filter: str | None = None
    selected_sources: list[str] | None = None
    profile_id: str | None = None
    no_llm: bool = False
    analysis_overrides: dict[str, Any] | None = None
    limit_overrides: dict[str, Any] | None = None
    max_clients: int = Field(default=200, ge=1, le=5000)


class StartRequest(_Body):
    plan_token: str
    title: str | None = Field(default=None, max_length=200)


class RetryRequest(_Body):
    no_llm: bool | None = None






class PreferencesBody(_Body):
    selected_org: str | None = None
    page_size: int = Field(default=50, ge=10, le=200)
    default_no_llm: bool = False
    analysis_budget: dict[str, Any] | None = None


# ----------------------------------------------------------------------- application


class AppState:
    def __init__(self, settings: Settings, runtime: Runtime | None = None):
        self.settings = settings
        state = Path(settings.state_dir)
        ensure_dir(state)
        self.profiles = ProfileStore(state, settings.profile_dirs)
        self.runtime: Runtime = runtime or live_runtime(settings)
        self.service = InvestigationService(self.runtime, RunStore(state / "investigations"),
                                            self.profiles)
        self.jobs = JobManager()
        self.sessions = SessionManager()
        self.plan_tokens: dict[str, dict] = {}
        self.idempotency_path = state / "ui-idempotency.json"
        self.preferences_path = state / "ui-preferences.json"
        self.status_cache: dict = {"velociraptor": None, "model": None, "tested_at": None}
        self._lock = threading.Lock()
        self.recovered = recover_interrupted(self.service.store)
    def live(self) -> InvestigationService:
        """The service for operations that need the Velociraptor API."""
        if not self.runtime.live_configured:
            raise ConfigurationError(
                "No Velociraptor API configuration is set on the server.",
                next_action="Set VELOCIRAPTOR_API_CONFIG in the server environment "
                            "(see CONFIGURATION.md). Saved and imported evidence remain available.")
        return self.service

    def find_investigation(self, inv_id: str):
        ids.local_id(inv_id, "investigation ID")
        return self.service, self.service.store.get(inv_id)

    # idempotency -------------------------------------------------------------------
    def idempotency(self) -> dict:
        try:
            return read_json(self.idempotency_path)
        except (OSError, ValueError):
            return {}

    def remember(self, key: str, inv_id: str) -> None:
        with self._lock:
            data = self.idempotency()
            data[key] = {"investigation_id": inv_id, "at": now_iso()}
            if len(data) > 5000:
                data = dict(list(data.items())[-2000:])
            atomic_write_json(self.idempotency_path, data)

    def preferences(self) -> dict:
        try:
            return read_json(self.preferences_path)
        except (OSError, ValueError):
            return {"selected_org": None, "page_size": 50, "default_no_llm": False,
                    "analysis_budget": None}


def _error_response(exc: DfirError, correlation: str | None = None) -> JSONResponse:
    body = {"ok": False, "error": exc.to_dict()}
    if correlation:
        body["error"]["correlation_id"] = correlation
    return JSONResponse(body, status_code=STATUS_CODES.get(exc.code, 400))


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def create_app(settings: Settings, *, runtime: Runtime | None = None) -> FastAPI:
    app = FastAPI(title="Velociraptor DFIR Agent UI API", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None)
    st = AppState(settings, runtime)
    app.state.vr = st

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if not host_allowed(host):
            return PlainTextResponse("Host not allowed. Use 127.0.0.1 or an SSH tunnel.",
                                     status_code=421)
        path = request.url.path
        if path.startswith("/api/") and request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and not origin_matches(origin, host):
                return JSONResponse({"ok": False, "error": {"code": "origin_rejected",
                                                            "message": "Cross-origin request "
                                                                       "rejected."}},
                                    status_code=403)
            sid = request.cookies.get(SESSION_COOKIE)
            if not st.sessions.check_csrf(sid, request.headers.get(CSRF_HEADER)):
                return JSONResponse({"ok": False, "error": {
                    "code": "csrf_failed", "message": "Session expired or missing. Reload the "
                                                      "page.", "next_action": "Reload the page."}},
                    status_code=403)
            length = request.headers.get("content-length")
            limit = settings.import_max_bytes if path.endswith("/imports") else \
                8 * 1024 * 1024 if path.endswith("/notebook-sources") else MAX_JSON_BODY
            if length and length.isdigit() and int(length) > limit:
                return JSONResponse({"ok": False, "error": {
                    "code": "too_large", "message": f"Request larger than {limit} bytes."}},
                    status_code=413)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DfirError)
    async def dfir_error(request: Request, exc: DfirError):
        return _error_response(exc, request.headers.get("x-request-id"))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        corr = f"err-{int(time.time())}-{id(exc) % 100000}"
        logger.exception("UI API error %s", corr)
        return JSONResponse({"ok": False, "error": {
            "code": "internal_error", "message": "Unexpected server error.",
            "correlation_id": corr, "details": {"type": type(exc).__name__}}}, status_code=500)

    # ------------------------------------------------------------------- session/status

    @app.get("/api/v1/session")
    def session(request: Request):
        sid = request.cookies.get(SESSION_COOKIE)
        resp = _ok(None)
        if not st.sessions.valid(sid):
            sid = st.sessions.new_session()
        body = {"csrf_token": st.sessions.csrf_for(sid), "version": __version__,
                "recovered_jobs": st.recovered}
        resp = _ok(body)
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", path="/",
                        secure=False, max_age=st.sessions.ttl)
        return resp

    def _status() -> dict:
        public = settings.public_view()
        try:
            allow = st.runtime.allowlist().to_dict()
        except DfirError as exc:
            allow = {"configured": False, "error": exc.message}
        configured = st.runtime.live_configured
        return {
            "version": __version__,
            "velociraptor": {
                "configured": configured,
                "default_org": st.runtime.default_org_id,
                "last_test": st.status_cache.get("velociraptor"),
                "synthetic": st.runtime.synthetic,
            },
            "model": {
                "provider": public["model"]["provider"],
                "model": public["model"]["model"],
                "hosted": public["model"]["hosted"],
                "configured": public["model"]["provider"] == "none" or bool(public["model"]["model"]),
                "last_test": st.status_cache.get("model"),
                "budgets": {k: public["model"][k] for k in ("max_input_chars", "max_requests",
                                                            "max_output_tokens", "num_ctx",
                                                            "timeout_seconds")},
            },
            "allowlist": allow,
            "edition": "AI public preview",
            "backend_access": "read_only",
            "tested_at": st.status_cache.get("tested_at"),
            "setup_hint": (None if configured else
                           "Live actions need VELOCIRAPTOR_API_CONFIG in the server environment "
                           "(see CONFIGURATION.md). Saved and imported evidence remain available."),
        }

    @app.get("/api/v1/status")
    def status():
        return _ok(_status())

    @app.post("/api/v1/status/test")
    def test_connection():
        """Bounded probes using the doctor services. Never collects."""
        result = {"velociraptor": None, "model": None}
        if st.runtime.live_configured:
            try:
                info = st.runtime.service.probe(timeout=10)
                result["velociraptor"] = {"reachable": True, "org_id": info["org_id"],
                                          "latency_seconds": info["latency_seconds"]}
            except DfirError as exc:
                result["velociraptor"] = {"reachable": False, "error": exc.message}
        else:
            result["velociraptor"] = {"reachable": False, "error": "Not configured."}
        try:
            info = st.runtime.provider().probe()
            result["model"] = {k: v for k, v in info.items() if k != "error"} | (
                {"error": info["error"]} if "error" in info else {})
        except DfirError as exc:
            result["model"] = {"reachable": False, "error": exc.message}
        st.status_cache.update(result, tested_at=now_iso())
        return _ok({**_status(), "test": result})

    @app.get("/api/v1/settings")
    def get_settings():
        return _ok({"preferences": st.preferences(),
                    "server": settings.public_view()})

    @app.put("/api/v1/settings/preferences")
    def put_prefs(body: PreferencesBody):
        if body.selected_org:
            ids.org_id(body.selected_org)
        if body.analysis_budget:
            from vr_dfir.profiles.models import AnalysisSettings
            AnalysisSettings.model_validate(body.analysis_budget)
        atomic_write_json(st.preferences_path, body.model_dump())
        return _ok(body.model_dump())

    # ------------------------------------------------------------------- metadata

    def _vr():
        return st.live().runtime.service

    @app.get("/api/v1/orgs")
    def orgs():
        return _ok(_vr().list_orgs())

    @app.get("/api/v1/clients")
    def clients(org_id: str | None = None, q: str = "", limit: int = 25):
        svc = _vr()
        found = svc.search_clients(q, org_id, limit=max(1, min(limit, 100)))
        return _ok({"org_id": svc.resolve_org(org_id), "clients": [c.to_dict() for c in found],
                    "search": "plain text (hostname, FQDN, client ID)"})



    @app.get("/api/v1/flows")
    def flows(client_id: str, org_id: str | None = None, limit: int = 30):
        svc = _vr()
        return _ok(svc.recent_flows(ids.client_id(client_id), org_id,
                                    limit=max(1, min(limit, 100))))

    @app.get("/api/v1/hunts")
    def hunts(org_id: str | None = None, limit: int = 30):
        return _ok(_vr().list_hunts(org_id, limit=max(1, min(limit, 100))))

    # ------------------------------------------------------------------- profiles











    # ------------------------------------------------------------------- plans/investigations

    def _plan(svc: InvestigationService, req: PlanRequest):
        if req.mode == "existing_flow":
            if not req.client_id or not req.flow_id:
                raise ValidationError("Existing Collection needs a client and a flow ID.")
            return svc.plan_existing_flow(org_id=req.org_id, client_id=req.client_id,
                                          flow_id=req.flow_id, profile_ref=req.profile_id,
                                          selected_sources=req.selected_sources)
        if not req.hunt_id:
            raise ValidationError("Existing Hunt needs a hunt ID.")
        return svc.plan_existing_hunt(org_id=req.org_id, hunt_id=req.hunt_id,
                                      client_filter=req.client_filter,
                                      artifact_filter=req.artifact_filter,
                                      selected_sources=req.selected_sources,
                                      profile_ref=req.profile_id, max_clients=req.max_clients)

    @app.post("/api/v1/plans")
    def create_plan(req: PlanRequest):
        svc = st.live()
        if req.analysis_overrides:
            from vr_dfir.profiles.models import AnalysisSettings
            AnalysisSettings.model_validate(req.analysis_overrides)
        plan = _plan(svc, req)
        fingerprint = plan.fingerprint()
        token_id = ids.new_local_id("pln")
        payload = f"{token_id}|{fingerprint}"
        token = f"{token_id}.{st.sessions.sign(payload)}"
        with st._lock:
            st.plan_tokens[token_id] = {"request": req.model_dump(), "fingerprint": fingerprint,
                                        "issued": time.time()}
            if len(st.plan_tokens) > 500:
                oldest = sorted(st.plan_tokens.items(), key=lambda kv: kv[1]["issued"])[:250]
                for k, _ in oldest:
                    st.plan_tokens.pop(k, None)
        return _ok({"plan": plan.to_dict(), "plan_token": token if plan.ok else None,
                    "primary_action": "Analyze Existing Evidence"})

    def _start_job(svc: InvestigationService, inv_id: str, kind: str = "execute",
                   no_llm: bool | None = None) -> bool:
        def run(stop):
            if kind == "execute":
                return svc.execute(inv_id, stop_event=stop)
            if kind == "resume":
                return svc.resume(inv_id, stop_event=stop)
            return svc.retry_analysis(inv_id, no_llm=no_llm)
        inv = svc.store.get(inv_id)
        previous_job = inv.reload().get("job")
        inv.update(job={"state": "queued", "kind": kind, "queued_at": now_iso()})
        try:
            return st.jobs.submit(inv_id, run)
        except TooManyJobs as exc:
            # Never leave a job that will not run marked as queued or running.
            fields: dict = {"job": {**(previous_job or {}), "state": "not_started", "kind": kind,
                                    "error": exc.message}}
            if kind == "execute":
                fields.update(outcome="failed", phase="stopped")
                if inv.meta.get("mode") == "new_collection":
                    fields["collection_status"] = "not_started"
                inv.add_error("Not started: too many investigations were running. Nothing was "
                              "collected; review the plan again to start a new run.")
            inv.update(**fields)
            raise

    @app.post("/api/v1/investigations")
    def start(req: StartRequest, request: Request):
        idem = request.headers.get("idempotency-key", "")
        if idem and (len(idem) > 100 or not idem.replace("-", "").isalnum()):
            raise ValidationError("Invalid Idempotency-Key.")
        token_id, _, sig = req.plan_token.partition(".")
        known = st.idempotency()
        for key in ([f"idem:{idem}"] if idem else []) + [f"plan:{token_id}"]:
            if key in known:
                inv_id = known[key]["investigation_id"]
                return _ok({"investigation_id": inv_id, "duplicate": True})
        with st._lock:
            entry = st.plan_tokens.get(token_id)
        if entry is None or not sig or sig != st.sessions.sign(f"{token_id}|{entry['fingerprint']}"):
            raise StaleRequestError("The plan is unknown or expired. Review the plan again.")
        if time.time() - entry["issued"] > PLAN_TOKEN_TTL:
            raise StaleRequestError("The plan expired. Review the plan again.")
        svc = st.live()
        preq = PlanRequest(**entry["request"])
        plan = _plan(svc, preq)
        if plan.fingerprint() != entry["fingerprint"]:
            raise StaleRequestError("Target, org, profile, or parameters changed since the plan "
                                    "was reviewed.")
        inv = svc.create(plan, title=req.title, no_llm=preq.no_llm,
                         analysis_overrides=preq.analysis_overrides,
                         expected_fingerprint=entry["fingerprint"])
        st.remember(f"plan:{token_id}", inv.id)
        if idem:
            st.remember(f"idem:{idem}", inv.id)
        with st._lock:
            st.plan_tokens.pop(token_id, None)
        _start_job(svc, inv.id)
        return _ok({"investigation_id": inv.id, "duplicate": False}, status=201)

    @app.get("/api/v1/investigations")
    def list_investigations(q: str = "", status: str = "", limit: int = 200):
        items = []
        for svc in [st.service]:
            items += svc.store.list(search=q or None, status=status or None,
                                    limit=max(1, min(limit, 500)))
        items.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        for it in items:
            it["job_active"] = st.jobs.is_active(it["investigation_id"])
        return _ok(items[:limit])

    @app.get("/api/v1/investigations/{inv_id}")
    def get_investigation(inv_id: str):
        svc, inv = st.find_investigation(inv_id)
        meta = inv.reload()
        plan = read_json(inv.path / "plan.json") if (inv.path / "plan.json").is_file() else None
        cp = read_json(inv.checkpoint_path) if inv.checkpoint_path.is_file() else {}
        collections = [{"artifact": c["artifact"], "collection_ids": c["collection_ids"],
                        "launch_state": c["launch"]["state"], "flow_id": c["launch"]["flow_id"],
                        "flow_state": (c.get("flow_status") or {}).get("state"),
                        "raw_state": (c.get("flow_status") or {}).get("raw_state"),
                        "wait": c.get("wait")} for c in cp.get("collections", [])]
        actions = _actions(meta, st.jobs.is_active(inv_id), inv)
        return _ok({"investigation": meta, "plan": plan, "collections": collections,
                    "existing": {k: v for k, v in (cp.get("existing") or {}).items()
                                 if k in ("hunt_flows", "enumeration_complete", "flow_status",
                                          "sources")},
                    "job_active": st.jobs.is_active(inv_id), "actions": actions})

    @app.get("/api/v1/investigations/{inv_id}/events")
    def events(inv_id: str, after: int = 0, limit: int = 100):
        _, inv = st.find_investigation(inv_id)
        return _ok({"events": inv.events(after=max(0, after), limit=max(1, min(limit, 500))),
                    "job_active": st.jobs.is_active(inv_id),
                    "phase": inv.reload().get("phase")})

    @app.post("/api/v1/investigations/{inv_id}/resume")
    def resume(inv_id: str):
        svc, inv = st.find_investigation(inv_id)
        if st.jobs.is_active(inv_id):
            return _ok({"investigation_id": inv_id, "already_running": True})
        _start_job(svc, inv_id, "resume")
        return _ok({"investigation_id": inv_id, "started": True})

    @app.post("/api/v1/investigations/{inv_id}/retry-analysis")
    def retry(inv_id: str, body: RetryRequest):
        svc, inv = st.find_investigation(inv_id)
        if st.jobs.is_active(inv_id):
            return _ok({"investigation_id": inv_id, "already_running": True})
        _start_job(svc, inv_id, "retry", no_llm=body.no_llm)
        return _ok({"investigation_id": inv_id, "started": True})

    @app.post("/api/v1/investigations/{inv_id}/stop")
    def stop(inv_id: str):
        st.find_investigation(inv_id)
        stopped = st.jobs.stop(inv_id)
        return _ok({"stop_requested": stopped,
                    "note": "Local processing will stop and save its state. Endpoint "
                            "collections may continue on the server."})

    def _bundle(inv) -> EvidenceBundle:
        if not (inv.evidence_dir / "manifest.json").is_file():
            raise NotFoundError("No evidence has been saved for this investigation yet.")
        return EvidenceBundle.open(inv.evidence_dir)

    @app.get("/api/v1/investigations/{inv_id}/findings")
    def findings(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        path = inv.report_dir / "report.json"
        if not path.is_file():
            return _ok({"findings": [], "rejected_findings": [], "coverage": None,
                        "report_ready": False})
        report = read_json(path)
        return _ok({"findings": report["findings"],
                    "rejected_findings": report["rejected_findings"],
                    "coverage": report["coverage"], "summary": report["summary_statements"],
                    "analysis": report["analysis"], "provider": report["provider"],
                    "status": report["status"], "synthetic": report["synthetic"],
                    "citation_note": report["citation_validation_note"], "report_ready": True})

    @app.get("/api/v1/investigations/{inv_id}/sources")
    def sources(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        return _ok(_bundle(inv).summary())

    @app.get("/api/v1/investigations/{inv_id}/evidence")
    def evidence_page(inv_id: str, source_key: str, offset: int = 0, limit: int = 50,
                      q: str = ""):
        _, inv = st.find_investigation(inv_id)
        bundle = _bundle(inv)
        entry = bundle.source(source_key)
        page = bundle.read_page(source_key, offset=max(0, offset), limit=max(1, min(limit, 200)),
                                search=q or None)
        page["source"] = {"source_key": entry.source_key, "full_name": entry.full_name,
                          "client_id": entry.client_id, "hostname": entry.hostname,
                          "flow_id": entry.flow_id, "org_id": entry.org_id,
                          "coverage": entry.coverage.__dict__}
        return _ok(page)

    @app.get("/api/v1/investigations/{inv_id}/evidence/{evidence_id}")
    def evidence_row(inv_id: str, evidence_id: str):
        _, inv = st.find_investigation(inv_id)
        return _ok(_bundle(inv).get_record(evidence_id))

    @app.get("/api/v1/reports")
    def reports(q: str = ""):
        items = []
        for svc in [st.service]:
            for s in svc.store.list(search=q or None):
                inv = svc.store.get(s["investigation_id"])
                if (inv.report_dir / "report.json").is_file():
                    items.append({**s, "report_updated": (inv.report_dir / "report.json").stat().st_mtime})
        items.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return _ok(items)

    @app.get("/api/v1/investigations/{inv_id}/report")
    def report(inv_id: str, format: str = "json", download: bool = False):
        _, inv = st.find_investigation(inv_id)
        names = {"json": ("report.json", "application/json"),
                 "md": ("report.md", "text/markdown; charset=utf-8"),
                 "html": ("report.html", "text/html; charset=utf-8")}
        if format not in names:
            raise ValidationError("format must be json, md, or html")
        fname, media = names[format]
        path = inv.report_dir / fname
        if not path.is_file():
            raise NotFoundError("The report has not been generated yet.")
        # The UI previews the HTML report in a sandboxed same-origin iframe; nothing else may
        # frame it and it can never run scripts.
        headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                              "img-src 'none'; script-src 'none'; "
                                              "frame-ancestors 'self'; sandbox",
                   "X-Frame-Options": "SAMEORIGIN"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{inv.id}-{fname}"'
        return FileResponse(path, media_type=media, headers=headers)

    # ------------------------------------------------------------------- bundles/imports

    @app.get("/api/v1/bundles")
    def bundles():
        items = []
        for svc in [st.service]:
            for s in svc.store.list():
                inv = svc.store.get(s["investigation_id"])
                if (inv.evidence_dir / "manifest.json").is_file():
                    items.append({"investigation_id": s["investigation_id"], "title": s["title"],
                                  "synthetic": s.get("synthetic"), "scope": s.get("scope"),
                                  "rows": (s.get("counts") or {}).get("rows_persisted")})
        return _ok(items)

    @app.post("/api/v1/bundles/{inv_id}/analyze")
    def analyze_saved_bundle(inv_id: str, body: RetryRequest):
        src_svc, inv = st.find_investigation(inv_id)
        new = src_svc.analyze_bundle(inv.evidence_dir, no_llm=bool(body.no_llm),
                                     title=f"Re-analysis of {inv.id}")
        _start_job(src_svc, new.id)
        return _ok({"investigation_id": new.id}, status=201)

    @app.post("/api/v1/imports")
    async def import_bundle(request: Request):
        """Import a versioned evidence bundle archive (.tar.gz or .zip) as raw request body."""
        limit = settings.import_max_bytes
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise ValidationError(f"Upload exceeds the {limit}-byte import limit.")
            chunks.append(chunk)
        data = b"".join(chunks)
        no_llm = request.query_params.get("no_llm", "true") != "false"
        import_root = ensure_dir(Path(settings.state_dir) / "imports")
        target = import_root / ids.new_local_id("imp")
        safe_extract(data, target, max_total=limit * 10)
        manifest_dirs = [p.parent for p in target.rglob("manifest.json")]
        if len(manifest_dirs) != 1:
            raise ValidationError("The archive must contain exactly one evidence bundle "
                                  "(one manifest.json).")
        bundle_dir = manifest_dirs[0]
        synthetic = bool(read_json(bundle_dir / "manifest.json").get("synthetic"))
        svc = st.service
        inv = svc.analyze_bundle(bundle_dir, no_llm=no_llm,
                                 title=request.query_params.get("title") or "Imported evidence")
        _start_job(svc, inv.id)
        return _ok({"investigation_id": inv.id, "synthetic": synthetic}, status=201)

    from vr_dfir.web import review_api
    review_api.register(app, st)

    # ------------------------------------------------------------------- static frontend

    static = static_dir()
    index = static / "index.html" if static else None

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise NotFoundError("Unknown API endpoint.")
        if static is None or index is None or not index.is_file():
            return HTMLResponse(
                "<!doctype html><title>Velociraptor DFIR Agent</title><p>The UI assets are "
                "not built. Install the release wheel, or run <code>npm run build</code> in "
                "<code>frontend/</code>.</p>", status_code=503)
        candidate = (static / full_path).resolve()
        if full_path and candidate.is_file() and static.resolve() in candidate.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
                if full_path.startswith("assets/") else {}
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _actions(meta: dict, active: bool, inv) -> dict:
    has_evidence = (inv.evidence_dir / "manifest.json").is_file()
    retrieval = meta.get("retrieval_status")
    analysis = meta.get("analysis_status")
    outcome = meta.get("outcome")
    return {
        "resume_reading": (not active and has_evidence and retrieval in (
            "incomplete", "failed", "in_progress", "not_started")) or (
            not active and outcome == "interrupted"),
        "retry_analysis": not active and has_evidence and analysis in (
            "failed", "completed_with_issues", "not_requested", "completed", "running",
            "not_started", "no_model_requests"),
        "view_evidence": has_evidence,
        "export_report": (inv.report_dir / "report.json").is_file(),
        "stop_local_processing": active,
    }


def safe_extract(data: bytes, target: Path, *, max_total: int, max_files: int = 2000) -> None:
    """Extract a .tar.gz or .zip archive safely into ``target``.

    Rejects absolute paths, ``..`` components, symlinks/hardlinks/devices, unsupported entry
    types, too many files, and excessive expansion.
    """
    ensure_dir(target)
    total = 0
    count = 0

    def check_name(name: str) -> PurePosixPath:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or name.startswith(("/", "\\")) or "\\" in name:
            raise ValidationError(f"Unsafe path in archive: {name[:80]!r}")
        if not p.parts:
            raise ValidationError("Empty path in archive.")
        return p

    if data[:2] == b"\x1f\x8b":
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        except tarfile.TarError as exc:
            raise ValidationError(f"Not a valid .tar.gz archive: {exc}") from exc
        with tf:
            for member in tf.getmembers():
                p = check_name(member.name)
                count += 1
                if count > max_files:
                    raise ValidationError("Archive has too many entries.")
                if member.isdir():
                    ensure_dir(target / p)
                    continue
                if not member.isreg():
                    raise ValidationError(f"Unsupported archive entry (link/device): {member.name[:80]!r}")
                total += member.size
                if total > max_total:
                    raise ValidationError("Archive expands beyond the allowed size.")
                fh = tf.extractfile(member)
                if fh is None:
                    raise ValidationError("Unreadable archive entry.")
                out = target / p
                ensure_dir(out.parent)
                with open(out, "wb") as dst:
                    remaining = member.size
                    while remaining > 0:
                        chunk = fh.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        dst.write(chunk)
                        remaining -= len(chunk)
                out.chmod(0o600)
        return
    if data[:4] == b"PK\x03\x04":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValidationError(f"Not a valid .zip archive: {exc}") from exc
        with zf:
            for info in zf.infolist():
                p = check_name(info.filename)
                count += 1
                if count > max_files:
                    raise ValidationError("Archive has too many entries.")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValidationError("Symlinks are not allowed in imports.")
                if info.is_dir():
                    ensure_dir(target / p)
                    continue
                total += info.file_size
                if total > max_total:
                    raise ValidationError("Archive expands beyond the allowed size.")
                out = target / p
                ensure_dir(out.parent)
                with zf.open(info) as src, open(out, "wb") as dst:
                    written = 0
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size:
                            raise ValidationError("Archive entry is larger than declared.")
                        dst.write(chunk)
                out.chmod(0o600)
        return
    raise ValidationError("Unsupported upload: send a .tar.gz or .zip evidence bundle.")


__all__ = ["create_app", "safe_extract", "ScopeError", "InvestigationBusy", "TooManyJobs",
           "json"]
