"""MCP adapter over the same read-only evidence and investigation services.

Stdout carries JSON-RPC only. No raw-query, collection or response tools are registered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

from vr_dfir import ids
from vr_dfir.config import load_environment, load_settings
from vr_dfir.errors import AmbiguousClientError, DfirError, NotFoundError, ValidationError

API_VERSION = "vr-dfir.mcp/1"
MAX_PAGE = 1000
MAX_LEGACY_ROWS = 10000
logger = logging.getLogger("vr_dfir.mcp")


def ok(data, **meta) -> str:
    payload = {"ok": True, "data": data}
    if meta:
        payload["meta"] = {"api_version": API_VERSION, **meta}
    return json.dumps(payload, default=str, ensure_ascii=False)


def fail(message: str, **meta) -> str:
    payload = {"ok": False, "error": message}
    if meta:
        payload["meta"] = {"api_version": API_VERSION, **meta}
    return json.dumps(payload, ensure_ascii=False)


def _err(exc: Exception) -> str:
    if isinstance(exc, DfirError):
        extra = {"code": exc.code}
        if exc.next_action:
            extra["next_action"] = exc.next_action
        if isinstance(exc.details, dict) and exc.details.get("candidates"):
            extra["candidates"] = exc.details["candidates"]
        return fail(exc.message, **extra)
    logger.exception("tool error")
    return fail(f"Unexpected error: {type(exc).__name__}")


class ToolContext:
    """Lazily-created shared services for tool calls (thread-safe)."""

    def __init__(self, settings=None, runtime=None):
        load_environment()
        self.settings = settings or load_settings()
        self._runtime = runtime
        self._svc = None
        self._lock = threading.RLock()  # investigations -> runtime re-enters
        self.jobs: dict[str, threading.Thread] = {}

    @property
    def runtime(self):
        with self._lock:
            if self._runtime is None:
                from vr_dfir.services.runtime import live_runtime
                self._runtime = live_runtime(self.settings)
            return self._runtime

    @property
    def vr(self):
        return self.runtime.service

    @property
    def investigations(self):
        with self._lock:
            if self._svc is None:
                from vr_dfir.profiles.store import ProfileStore
                from vr_dfir.services.investigations import InvestigationService
                from vr_dfir.services.runstore import RunStore
                state = Path(self.settings.state_dir)
                root = state / "investigations"
                self._svc = InvestigationService(self.runtime, RunStore(root),
                                                 ProfileStore(state, self.settings.profile_dirs))
            return self._svc

    def run_background(self, inv_id: str, fn) -> None:
        def target():
            try:
                fn()
            except Exception:  # noqa: BLE001 - recorded in the investigation
                logger.exception("background investigation %s failed", inv_id)
        t = threading.Thread(target=target, name=f"inv-{inv_id}", daemon=True)
        self.jobs[inv_id] = t
        t.start()


# ------------------------------------------------------------------------ legacy tools


def t_list_orgs(ctx: ToolContext) -> str:
    try:
        return ok(ctx.vr.list_orgs())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_client_info(ctx: ToolContext, hostname: str, org_id: str = "",
                  search_all_orgs: bool = False) -> str:
    """Resolve a hostname. Ambiguous matches are returned as candidates, never guessed."""
    try:
        if search_all_orgs and not org_id:
            candidates = []
            for org in ctx.vr.list_orgs():
                oid = org.get("OrgId") or "root"
                try:
                    candidates.append(ctx.vr.resolve_client(org_id=oid, hostname=hostname).to_dict())
                except AmbiguousClientError as exc:
                    candidates.extend(exc.details.get("candidates", []))
                except NotFoundError:
                    continue
            if not candidates:
                return fail(f"Client not found: {hostname}")
            if len(candidates) > 1:
                return fail(f"Hostname {hostname!r} matches {len(candidates)} clients across "
                            "orgs; choose an explicit org_id and client ID.",
                            code="ambiguous_client", candidates=candidates)
            return ok(candidates[0])
        client = ctx.vr.resolve_client(org_id=org_id or None, hostname=hostname)
        return ok(client.to_dict())
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_list_clients(ctx: ToolContext, search: str = ".", os_filter: str = ".", limit: int = 100,
                   org_id: str = "") -> str:
    try:
        lim = max(1, min(int(limit), 500))
        text = "" if search in ("", ".", ".*") else search
        clients = ctx.vr.search_clients(text, org_id or None, limit=lim)
        if os_filter not in ("", ".", ".*"):
            rx = re.compile(os_filter, re.I)
            clients = [c for c in clients if rx.search(c.os_type or "")]
        return ok([c.to_dict() for c in clients],
                  search_semantics="plain-text substring match on hostname/FQDN/client ID")
    except re.error as exc:
        return fail(f"Invalid os_filter regex: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)




def t_get_collection_results(ctx: ToolContext, client_id: str, flow_id: str, artifact: str,
                             org_id: str = "", fields: str = "*", max_retries: int = 10,
                             retry_delay: int = 30) -> str:
    """Wait for the *whole flow* to reach a terminal state (not one source's log line),
    then return up to 10000 rows of ``artifact`` (``Artifact`` or ``Artifact/Source``)."""
    try:
        cid, fid = ids.client_id(client_id), ids.flow_id(flow_id)
        art, _, src = artifact.partition("/")
        ids.artifact_name(art)
        wanted = None if fields.strip() in ("", "*") else [
            ids.field_name(f.strip()) for f in fields.split(",") if f.strip()]
        attempts = max(1, min(int(max_retries), 120))
        delay = max(0, min(int(retry_delay), 300))
        status = None
        for attempt in range(attempts):
            status = ctx.vr.flow_status(cid, fid, org_id or None)
            if status.terminal or status.state.value in ("not_found",) or \
                    status.response_shape == "unrecognized":
                break
            if attempt < attempts - 1:
                time.sleep(delay)
        if status is None or not status.terminal:
            return fail(f"Flow {fid} is not finished (state: "
                        f"{status.state.value if status else 'unknown'}). "
                        + (status.diagnostic if status else ""),
                        flow_status=status.to_dict() if status else None)
        rows = []
        start = 0
        while len(rows) < MAX_LEGACY_ROWS:
            count = min(MAX_PAGE, MAX_LEGACY_ROWS - len(rows))
            page = ctx.vr.read_source_page(cid, fid, art, src or None, start, count,
                                           org_id or None).rows
            rows.extend(page)
            start += len(page)
            if len(page) < count:
                break
        if wanted:
            rows = [{k: r.get(k) for k in wanted} for r in rows]
        return ok(rows, flow_state=status.state.value, rows=len(rows),
                  truncated=len(rows) >= MAX_LEGACY_ROWS)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_get_hunt_results(ctx: ToolContext, hunt_id: str, artifact: str, org_id: str = "",
                       fields: str = "*", limit: int = 500) -> str:
    """Bounded hunt results with per-row provenance (ClientId, FlowId added)."""
    try:
        lim = max(1, min(int(limit), MAX_LEGACY_ROWS))
        art, _, src = artifact.partition("/")
        ids.artifact_name(art)
        wanted = None if fields.strip() in ("", "*") else [
            ids.field_name(f.strip()) for f in fields.split(",") if f.strip()]
        rows: list[dict] = []
        start = 0
        flows_seen = 0
        incomplete = []
        while len(rows) < lim:
            page = ctx.vr.hunt_flows_page(hunt_id, start, 100, org_id or None)
            for item in page:
                flows_seen += 1
                if not item["status"].terminal:
                    incomplete.append(item["flow_id"])
                    continue
                pos = 0
                while len(rows) < lim:
                    count = min(MAX_PAGE, lim - len(rows))
                    got = ctx.vr.read_source_page(item["client_id"], item["flow_id"], art,
                                                  src or None, pos, count, org_id or None).rows
                    for r in got:
                        row = {k: r.get(k) for k in wanted} if wanted else dict(r)
                        row["ClientId"] = item["client_id"]
                        row["FlowId"] = item["flow_id"]
                        rows.append(row)
                    pos += len(got)
                    if len(got) < count:
                        break
                if len(rows) >= lim:
                    break
            start += len(page)
            if len(page) < 100:
                break
        return ok(rows, rows=len(rows), flows_seen=flows_seen,
                  flows_not_terminal=incomplete[:50], limited=len(rows) >= lim)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ------------------------------------------------------------------------ new tools






def t_flow_status(ctx: ToolContext, client_id: str, flow_id: str, org_id: str = "") -> str:
    from vr_dfir.services.investigations import flow_status_snapshot
    try:
        return ok(flow_status_snapshot(ctx.vr, org_id or None, client_id, flow_id),
                  org_id=ctx.vr.resolve_org(org_id or None))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_list_result_sources(ctx: ToolContext, client_id: str, flow_id: str, org_id: str = "") -> str:
    try:
        plan = ctx.investigations.plan_existing_flow(org_id=org_id or None, client_id=client_id,
                                                     flow_id=flow_id)
        d = plan.to_dict()
        return ok({"flow_status": d["existing"]["flow_status"],
                   "sources": d["collections"][0]["sources"], "warnings": d["warnings"],
                   "errors": d["errors"]}, org_id=d["org_id"])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_get_result_page(ctx: ToolContext, client_id: str, flow_id: str, artifact: str,
                      source: str = "", start_row: int = 0, count: int = 100,
                      org_id: str = "") -> str:
    try:
        cnt = max(1, min(int(count), MAX_PAGE))
        start = max(0, int(start_row))
        result = ctx.vr.read_source_page(client_id, flow_id, artifact, source or None, start, cnt,
                                         org_id or None)
        return ok({"rows": result.rows, "start_row": start, "returned": len(result.rows),
                   "next_start_row": start + len(result.rows),
                   "end_of_results": len(result.rows) < cnt},
                  org_id=ctx.vr.resolve_org(org_id or None),
                  cursor_scope={"client_id": client_id, "flow_id": flow_id,
                                "artifact": artifact, "source": source or None},
                  log_errors=result.log_errors()[:5])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)










def t_analyze_existing_flow(ctx: ToolContext, client_id: str, flow_id: str, org_id: str = "",
                            profile: str = "", no_llm: bool = False) -> str:
    try:
        svc = ctx.investigations
        plan = svc.plan_existing_flow(org_id=org_id or None, client_id=client_id,
                                      flow_id=flow_id, profile_ref=profile or None)
        inv = svc.create(plan, no_llm=bool(no_llm))
        ctx.run_background(inv.id, lambda: svc.execute(inv.id))
        return ok({"investigation_id": inv.id, "launches_planned": 0},
                  note="Existing evidence only; nothing is collected.")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_analyze_existing_hunt(ctx: ToolContext, hunt_id: str, org_id: str = "",
                            client_ids: list[str] | None = None, artifact: str = "",
                            profile: str = "", max_clients: int = 200, no_llm: bool = False) -> str:
    try:
        svc = ctx.investigations
        plan = svc.plan_existing_hunt(org_id=org_id or None, hunt_id=hunt_id,
                                      client_filter=client_ids or None,
                                      artifact_filter=artifact or None,
                                      profile_ref=profile or None, max_clients=int(max_clients))
        inv = svc.create(plan, no_llm=bool(no_llm))
        ctx.run_background(inv.id, lambda: svc.execute(inv.id))
        return ok({"investigation_id": inv.id, "launches_planned": 0},
                  note="Existing hunt only; no hunt is created or restarted.")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_get_investigation(ctx: ToolContext, investigation_id: str) -> str:
    try:
        inv = ctx.investigations.store.get(investigation_id)
        return ok(inv.summary(), errors=(inv.meta.get("errors") or [])[-10:])
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_list_investigations(ctx: ToolContext, search: str = "", limit: int = 50) -> str:
    try:
        return ok(ctx.investigations.store.list(search=search or None,
                                                limit=max(1, min(int(limit), 200))))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_resume_investigation(ctx: ToolContext, investigation_id: str) -> str:
    try:
        svc = ctx.investigations
        svc.store.get(investigation_id)
        ctx.run_background(investigation_id, lambda: svc.resume(investigation_id))
        return ok({"investigation_id": investigation_id}, note="Resuming without recollection.")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_retry_analysis(ctx: ToolContext, investigation_id: str, no_llm: bool = False) -> str:
    try:
        svc = ctx.investigations
        svc.store.get(investigation_id)
        ctx.run_background(investigation_id,
                           lambda: svc.retry_analysis(investigation_id,
                                                      no_llm=True if no_llm else None))
        return ok({"investigation_id": investigation_id}, note="Uses saved evidence only.")
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_get_report(ctx: ToolContext, investigation_id: str, format: str = "json",
                 max_chars: int = 200000) -> str:
    try:
        inv = ctx.investigations.store.get(investigation_id)
        fmt = {"json": "report.json", "md": "report.md", "markdown": "report.md"}.get(format)
        if fmt is None:
            raise ValidationError("format must be json or md")
        path = inv.report_dir / fmt
        if not path.is_file():
            raise NotFoundError("The report has not been generated yet.")
        text = path.read_text(encoding="utf-8")
        limit = max(1000, min(int(max_chars), 2_000_000))
        return ok(json.loads(text) if fmt.endswith(".json") and len(text) <= limit else
                  text[:limit], truncated=len(text) > limit)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_get_evidence(ctx: ToolContext, investigation_id: str, evidence_id: str) -> str:
    from vr_dfir.evidence.bundle import EvidenceBundle
    try:
        inv = ctx.investigations.store.get(investigation_id)
        bundle = EvidenceBundle.open(inv.evidence_dir)
        return ok(bundle.get_record(evidence_id))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _indexed(ctx: ToolContext, investigation_id: str):
    from vr_dfir.services.derived import IndexManager
    inv = ctx.investigations.store.get(investigation_id)
    return inv, IndexManager(ctx.settings).require(inv, timeout=120)


def t_get_overview(ctx: ToolContext, investigation_id: str) -> str:
    from vr_dfir.services import views
    try:
        inv, idx = _indexed(ctx, investigation_id)
        return ok(views.overview(inv, idx, stale_days=ctx.settings.stale_after_days))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_compare_captures(ctx: ToolContext, investigation_id: str, source_key: str,
                       baseline_investigation_id: str = "", baseline_source_key: str = "",
                       include_unchanged: bool = False, limit: int = 100) -> str:
    from vr_dfir.services import captures, comparisons
    try:
        store = ctx.investigations.store
        inv = store.get(investigation_id)
        current = captures.find(store, ctx.settings, inv.id, source_key)
        if current is None:
            return fail("This source is not in the investigation's saved evidence.")
        if baseline_investigation_id:
            baseline = captures.find(store, ctx.settings, baseline_investigation_id,
                                     baseline_source_key or source_key)
        else:
            older = captures.baselines(store, ctx.settings, current)
            baseline = older[0] if older else None
        if baseline is None:
            return fail("No earlier compatible saved capture of this source exists.")
        cid = comparisons.run(store, ctx.settings, current, baseline)
        page = comparisons.items(inv, cid, show_unchanged=include_unchanged,
                                 limit=max(1, min(int(limit), 200)))
        return ok({**comparisons.load(inv, cid), **page})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def t_where_else_seen(ctx: ToolContext, investigation_id: str, evidence_id: str) -> str:
    from vr_dfir.services import cohort
    try:
        inv, idx = _indexed(ctx, investigation_id)
        return ok(cohort.where_else(inv, idx, evidence_id, scope="investigation"))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# ------------------------------------------------------------------------ registration

LEGACY_TOOLS = ["list_orgs", "client_info", "list_clients", "get_collection_results", "get_hunt_results_tool"]
NEW_TOOLS = ["flow_status", "list_result_sources", "get_result_page", "analyze_existing_flow", "analyze_existing_hunt", "get_investigation", "list_investigations", "resume_investigation", "retry_analysis", "get_report", "get_evidence", "get_overview", "compare_captures", "where_else_seen"]


def _server_class():
    try:
        from mcp.server.mcpserver import MCPServer
        return MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP
        return FastMCP


def build_server(ctx: ToolContext | None = None):
    logging.getLogger("mcp").setLevel(logging.WARNING)
    Server = _server_class()
    server = Server("velociraptor-dfir-agent")
    c = ctx or ToolContext()

    def run(fn, *args, **kwargs):
        return asyncio.to_thread(fn, c, *args, **kwargs)

    @server.tool()
    async def list_orgs() -> str:
        """List available Velociraptor orgs for multi-tenant deployments."""
        return await run(t_list_orgs)

    @server.tool()
    async def client_info(hostname: str, org_id: str = "", search_all_orgs: bool = False) -> str:
        """Resolve a hostname/FQDN to one client in one org. Ambiguous matches (including
        across orgs with search_all_orgs) are returned as candidates, never auto-selected."""
        return await run(t_client_info, hostname, org_id, search_all_orgs)

    @server.tool()
    async def list_clients(search: str = ".", os_filter: str = ".", limit: int = 100,
                           org_id: str = "") -> str:
        """List clients. search is a plain-text substring; os_filter is a regex on OS type."""
        return await run(t_list_clients, search, os_filter, limit, org_id)




    @server.tool()
    async def get_collection_results(client_id: str, flow_id: str, artifact: str,
                                     org_id: str = "", fields: str = "*", max_retries: int = 10,
                                     retry_delay: int = 30) -> str:
        """Wait for the flow to finish (normalized flow state) and return its rows."""
        return await run(t_get_collection_results, client_id, flow_id, artifact, org_id, fields,
                         max_retries, retry_delay)

    @server.tool()
    async def get_hunt_results_tool(hunt_id: str, artifact: str, org_id: str = "",
                                    fields: str = "*", limit: int = 500) -> str:
        """Bounded hunt rows with ClientId/FlowId provenance (read via contributing flows)."""
        return await run(t_get_hunt_results, hunt_id, artifact, org_id, fields, limit)



    @server.tool()
    async def flow_status(client_id: str, flow_id: str, org_id: str = "") -> str:
        """Normalized flow status (raw server state preserved). Read-only."""
        return await run(t_flow_status, client_id, flow_id, org_id)

    @server.tool()
    async def list_result_sources(client_id: str, flow_id: str, org_id: str = "") -> str:
        """Sources of an existing flow and whether results were reported. Read-only."""
        return await run(t_list_result_sources, client_id, flow_id, org_id)

    @server.tool()
    async def get_result_page(client_id: str, flow_id: str, artifact: str, source: str = "",
                              start_row: int = 0, count: int = 100, org_id: str = "") -> str:
        """One bounded page (max 1000 rows) of a flow source via source(start_row, count)."""
        return await run(t_get_result_page, client_id, flow_id, artifact, source, start_row,
                         count, org_id)





    @server.tool()
    async def analyze_existing_flow(client_id: str, flow_id: str, org_id: str = "",
                                    profile: str = "", no_llm: bool = False) -> str:
        """Analyze a previously collected flow. Never collects."""
        return await run(t_analyze_existing_flow, client_id, flow_id, org_id, profile, no_llm)

    @server.tool()
    async def analyze_existing_hunt(hunt_id: str, org_id: str = "",
                                    client_ids: list[str] | None = None, artifact: str = "",
                                    profile: str = "", max_clients: int = 200,
                                    no_llm: bool = False) -> str:
        """Analyze an existing hunt within budgets. Never creates or restarts hunts."""
        return await run(t_analyze_existing_hunt, hunt_id, org_id, client_ids, artifact, profile,
                         max_clients, no_llm)

    @server.tool()
    async def get_investigation(investigation_id: str) -> str:
        """Status of an investigation (collection, retrieval, analysis kept separate)."""
        return await run(t_get_investigation, investigation_id)

    @server.tool()
    async def list_investigations(search: str = "", limit: int = 50) -> str:
        """List local investigations."""
        return await run(t_list_investigations, search, limit)

    @server.tool()
    async def resume_investigation(investigation_id: str) -> str:
        """Resume reading/analysis from the checkpoint. Never recollects."""
        return await run(t_resume_investigation, investigation_id)

    @server.tool()
    async def retry_analysis(investigation_id: str, no_llm: bool = False) -> str:
        """Re-run analysis on saved evidence. Never recollects."""
        return await run(t_retry_analysis, investigation_id, no_llm)

    @server.tool()
    async def get_report(investigation_id: str, format: str = "json",
                         max_chars: int = 200000) -> str:
        """Canonical report (json) or Markdown (md)."""
        return await run(t_get_report, investigation_id, format, max_chars)

    @server.tool()
    async def get_evidence(investigation_id: str, evidence_id: str) -> str:
        """Resolve an evidence ID to its exact persisted row with provenance."""
        return await run(t_get_evidence, investigation_id, evidence_id)

    @server.tool()
    async def get_overview(investigation_id: str) -> str:
        """Summary of a saved investigation: needs-review findings, represented hosts, source
        coverage, chart aggregates over all saved rows, and the next suggested step."""
        return await run(t_get_overview, investigation_id)

    @server.tool()
    async def compare_captures(investigation_id: str, source_key: str,
                               baseline_investigation_id: str = "", baseline_source_key: str = "",
                               include_unchanged: bool = False, limit: int = 100) -> str:
        """What changed between a saved source and an earlier saved capture of the same host
        and source (default: the latest earlier one). Reads saved evidence only."""
        return await run(t_compare_captures, investigation_id, source_key,
                         baseline_investigation_id, baseline_source_key, include_unchanged, limit)

    @server.tool()
    async def where_else_seen(investigation_id: str, evidence_id: str) -> str:
        """Where else the values of one saved row were seen among the investigation's hosts,
        with eligible-host denominators."""
        return await run(t_where_else_seen, investigation_id, evidence_id)

    return server


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    del argv  # the MCP server takes its configuration from the environment only
    try:
        server = build_server()
    except DfirError as exc:
        # stdout is reserved for the protocol; report configuration problems on stderr.
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.next_action:
            print(f"Next: {exc.next_action}", file=sys.stderr)
        return 4
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
