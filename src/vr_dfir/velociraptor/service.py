"""Typed Velociraptor operations built on fixed query templates.

This is the only module that turns application intent into VQL. Every method uses a
template from :mod:`vr_dfir.vql`; there is no raw-VQL entry point. Read operations
need ``READ_RESULTS``; only :meth:`VelociraptorService.launch_collection` needs
``COLLECT_CLIENT``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field

from vr_dfir import ids, vql
from vr_dfir.errors import (
    AmbiguousClientError,
    NotFoundError,
    ScopeError,
    VelociraptorError,
)
from vr_dfir.velociraptor.artifacts import ArtifactMetadata, parse_artifact
from vr_dfir.velociraptor.backend import Backend, QueryResult
from vr_dfir.velociraptor.flow_state import FlowStatus, epoch_to_iso, normalize_flow, sanitize_text

CATALOG_TTL_SECONDS = 300
logger = logging.getLogger(__name__)


@dataclass
class ClientInfo:
    client_id: str
    org_id: str
    hostname: str = ""
    fqdn: str = ""
    os_type: str = ""
    os_release: str = ""
    machine: str = ""
    last_seen: str | None = None
    first_seen: str | None = None
    last_ip: str = ""
    labels: list[str] = field(default_factory=list)
    agent_version: str = ""
    synthetic: bool = False

    @property
    def platform(self) -> str:
        value = (self.os_type or "").lower()
        if value.startswith("darwin") or value == "macos":
            return "macos"
        return value

    def to_dict(self) -> dict:
        data = asdict(self)
        data["platform"] = self.platform
        return data




def _client_from_row(row: dict, org_id: str, synthetic: bool) -> ClientInfo:
    return ClientInfo(
        client_id=str(row.get("client_id") or ""),
        org_id=org_id,
        hostname=str(row.get("Hostname") or ""),
        fqdn=str(row.get("Fqdn") or ""),
        os_type=str(row.get("OSType") or ""),
        os_release=str(row.get("OSRelease") or ""),
        machine=str(row.get("Machine") or ""),
        last_seen=epoch_to_iso(row.get("LastSeenAt")),
        first_seen=epoch_to_iso(row.get("FirstSeenAt")),
        last_ip=str(row.get("LastIP") or ""),
        labels=[str(x) for x in (row.get("Labels") or [])],
        agent_version=str(row.get("AgentVersion") or ""),
        synthetic=synthetic,
    )


class VelociraptorService:
    def __init__(self, backend: Backend, *, default_org_id: str = ""):
        self.backend = backend
        self.default_org_id = ids.org_id(default_org_id)
        self._catalog_cache: dict[str, tuple[float, list[ArtifactMetadata]]] = {}
        self._lock = threading.Lock()

    @property
    def synthetic(self) -> bool:
        return bool(getattr(self.backend, "synthetic", False))

    def resolve_org(self, org_id: str | None) -> str:
        """Explicit org if given, else the configured default. Never searches all orgs."""
        if org_id is None or not str(org_id).strip():
            return self.default_org_id
        return ids.org_id(org_id)

    def _q(self, query: str, org_id: str, *, timeout: float | None = None,
           max_rows: int = 100_000, server_timeout: int | None = None) -> QueryResult:
        if server_timeout:
            return self.backend.query(query, org_id=org_id, timeout=timeout, max_rows=max_rows,
                                      server_timeout=server_timeout)
        return self.backend.query(query, org_id=org_id, timeout=timeout, max_rows=max_rows)

    @staticmethod
    def raise_server_errors(result: QueryResult, what: str) -> None:
        """A query that returned no rows but logged server-side errors failed; it is not an
        empty or not-found result. INFO/progress lines are ignored."""
        errors = [sanitize_text(line, 300) for line in result.server_errors()]
        if not result.rows and errors:
            raise VelociraptorError(
                f"Velociraptor reported an error while {what}: " + "; ".join(errors[:3]),
                details={"server_diagnostics": errors[:10]},
                next_action="Check the server version and the API account's permissions; the "
                            "diagnostics are the server's own query log lines.")

    # --- server / orgs ----------------------------------------------------------------------

    def probe(self, org_id: str | None = None, timeout: float = 15) -> dict:
        org = self.resolve_org(org_id)
        started = time.monotonic()
        result = self._q(vql.q_probe(), org, timeout=timeout, max_rows=5)
        elapsed = round(time.monotonic() - started, 3)
        info = result.rows[0] if result.rows else {}
        return {
            "reachable": True,
            "org_id": org,
            "latency_seconds": elapsed,
            "plugin_versions": {k: v for k, v in info.items() if not str(k).startswith("_")},
            "server_version": "not reported by the query API",
            "log_errors": result.log_errors()[:5],
            "synthetic": self.synthetic,
        }

    def list_orgs(self) -> list[dict]:
        result = self._q(vql.q_orgs(), "root", max_rows=10_000)
        return [{"OrgId": r.get("OrgId"), "Name": r.get("Name")} for r in result.rows]

    # --- artifacts --------------------------------------------------------------------------

    def artifact_catalog(self, org_id: str | None = None, *, refresh: bool = False
                         ) -> list[ArtifactMetadata]:
        org = self.resolve_org(org_id)
        now = time.monotonic()
        with self._lock:
            cached = self._catalog_cache.get(org)
            if cached and not refresh and now - cached[0] < CATALOG_TTL_SECONDS:
                return cached[1]
        result = self._q(vql.q_artifact_catalog(), org, max_rows=50_000)
        catalog = []
        for row in result.rows:
            try:
                catalog.append(parse_artifact(row, synthetic=self.synthetic))
            except Exception as exc:  # noqa: BLE001 - skip malformed definitions, keep the rest
                logger.warning("Skipping malformed artifact definition: %s", type(exc).__name__)
        with self._lock:
            self._catalog_cache[org] = (now, catalog)
        return catalog

    def get_artifacts(self, names: list[str], org_id: str | None = None
                      ) -> dict[str, ArtifactMetadata]:
        """Exact-name lookup (not a regex). Missing names are absent from the result."""
        org = self.resolve_org(org_id)
        if not names:
            return {}
        result = self._q(vql.q_artifact_exact(sorted(set(names))), org, max_rows=1000)
        found = {}
        for row in result.rows:
            meta = parse_artifact(row, synthetic=self.synthetic)
            found[meta.name] = meta
        return {n: found[n] for n in names if n in found}

    # --- clients ----------------------------------------------------------------------------

    def get_client(self, client_id: str, org_id: str | None = None) -> ClientInfo:
        org = self.resolve_org(org_id)
        cid = ids.client_id(client_id)
        result = self._q(vql.q_client_by_id(cid), org, max_rows=5)
        rows = [r for r in result.rows if r.get("client_id") == cid]
        if not rows:
            raise NotFoundError(
                f"Client {cid} was not found in org {org}.",
                next_action="Check the client ID and the selected organization.",
            )
        return _client_from_row(rows[0], org, self.synthetic)

    def search_clients(self, text: str, org_id: str | None = None, limit: int = 25
                       ) -> list[ClientInfo]:
        org = self.resolve_org(org_id)
        result = self._q(vql.q_clients_search(text or "", limit), org, max_rows=limit + 5)
        return [_client_from_row(r, org, self.synthetic) for r in result.rows][:limit]

    def resolve_client(self, *, org_id: str | None, client_id: str | None = None,
                       hostname: str | None = None, expected_client_id: str | None = None
                       ) -> ClientInfo:
        """Resolve one client in one org. Ambiguous hostnames are rejected, never guessed."""
        org = self.resolve_org(org_id)
        if client_id:
            client = self.get_client(client_id, org)
        elif hostname:
            result = self._q(vql.q_clients_exact_host(hostname), org, max_rows=50)
            matches = [_client_from_row(r, org, self.synthetic) for r in result.rows]
            if not matches:
                raise NotFoundError(
                    f"No client with hostname or FQDN {hostname!r} in org {org}.",
                    next_action="Search clients by text and select an explicit client ID.",
                )
            if len(matches) > 1:
                raise AmbiguousClientError(
                    f"Hostname {hostname!r} matches {len(matches)} clients in org {org}.",
                    details={"candidates": [m.to_dict() for m in matches]},
                )
            client = matches[0]
        else:
            raise ScopeError("Provide a client ID or a hostname.")
        if expected_client_id and client.client_id != ids.client_id(expected_client_id):
            raise ScopeError(
                f"Resolved client {client.client_id} does not match the expected client ID "
                f"{expected_client_id}.",
                next_action="Check the target; the expected-client guard stopped the action.",
            )
        return client

    # --- flows ------------------------------------------------------------------------------

    def flow_status(self, client_id: str, flow_id: str, org_id: str | None = None,
                    timeout: float | None = None) -> FlowStatus:
        org = self.resolve_org(org_id)
        cid, fid = ids.client_id(client_id), ids.flow_id(flow_id)
        result = self._q(vql.q_flow_context(cid, fid), org, timeout=timeout, max_rows=5)
        return normalize_flow(result.rows, client_id=cid, flow_id=fid, logs=result.logs)

    def flow_logs(self, client_id: str, flow_id: str, org_id: str | None = None,
                  limit: int = 200) -> list[dict]:
        org = self.resolve_org(org_id)
        result = self._q(vql.q_flow_logs(client_id, flow_id, limit), org, max_rows=limit + 5)
        out = []
        for row in result.rows[:limit]:
            out.append({
                "time": epoch_to_iso(row.get("_ts") or row.get("Timestamp") or row.get("client_time")),
                "level": str(row.get("level") or row.get("Level") or ""),
                "message": sanitize_text(row.get("message") or row.get("Message"), 500),
            })
        return out

    def recent_flows(self, client_id: str, org_id: str | None = None, limit: int = 50
                     ) -> list[dict]:
        org = self.resolve_org(org_id)
        result = self._q(vql.q_recent_flows(client_id, limit), org, max_rows=limit + 5)
        out = []
        for row in result.rows[:limit]:
            status = normalize_flow(
                {**row, "client_id": ids.client_id(client_id)},
                client_id=ids.client_id(client_id), flow_id=str(row.get("session_id") or ""),
            ) if row.get("session_id") else None
            out.append({
                "flow_id": row.get("session_id"),
                "artifacts": row.get("artifacts") or [],
                "created": epoch_to_iso(row.get("create_time")),
                "state": status.state.value if status else "unknown",
                "raw_state": row.get("state"),
                "creator": row.get("creator"),
                "total_collected_rows": row.get("total_collected_rows"),
                "artifacts_with_results": row.get("artifacts_with_results") or [],
            })
        return out

    def read_source_page(self, client_id: str, flow_id: str, artifact: str,
                         source: str | None, start_row: int, count: int,
                         org_id: str | None = None) -> QueryResult:
        org = self.resolve_org(org_id)
        query = vql.q_source_page(client_id, flow_id, artifact, source, start_row, count)
        return self._q(query, org, max_rows=count)

    # --- hunts ------------------------------------------------------------------------------

    def hunt_info(self, hunt_id: str, org_id: str | None = None) -> dict:
        org = self.resolve_org(org_id)
        hid = ids.hunt_id(hunt_id)
        result = self._q(vql.q_hunt_info(hid), org, max_rows=5)
        rows = [r for r in result.rows if r.get("hunt_id") == hid]
        if not rows:
            # A logged query error is reported as such, not as a missing hunt.
            if not any("not found" in e.lower() for e in result.server_errors()):
                self.raise_server_errors(result, f"reading hunt {hid}")
            raise NotFoundError(f"Hunt {hid} was not found in org {org}.")
        row = rows[0]
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
        request = row.get("start_request") if isinstance(row.get("start_request"), dict) else {}
        return {
            "hunt_id": hid,
            "org_id": org,
            "description": sanitize_text(row.get("hunt_description"), 500),
            "state": str(row.get("state") or "UNSET"),
            "created": epoch_to_iso(row.get("create_time")),
            "started": epoch_to_iso(row.get("start_time")),
            "creator": row.get("creator"),
            "artifacts": [str(a) for a in (row.get("artifacts") or request.get("artifacts") or [])],
            "artifact_sources": [str(a) for a in (row.get("artifact_sources") or [])],
            "stats": {
                "total_clients_scheduled": stats.get("total_clients_scheduled"),
                "total_clients_with_results": stats.get("total_clients_with_results"),
                "total_clients_with_errors": stats.get("total_clients_with_errors"),
                "total_finished_clients": stats.get("total_finished_clients"),
            },
        }

    def list_hunts(self, org_id: str | None = None, limit: int = 50) -> list[dict]:
        org = self.resolve_org(org_id)
        result = self._q(vql.q_hunts_list(limit), org, max_rows=limit + 5)
        self.raise_server_errors(result, "listing hunts")
        return [{
            "hunt_id": r.get("hunt_id"),
            "description": sanitize_text(r.get("hunt_description"), 200),
            "created": epoch_to_iso(r.get("create_time")),
            "state": r.get("state"),
            "artifacts": r.get("artifacts") or [],
            "creator": r.get("creator"),
        } for r in result.rows[:limit]]

    def hunt_flows_page(self, hunt_id: str, start_row: int, count: int,
                        org_id: str | None = None) -> list[dict]:
        org = self.resolve_org(org_id)
        result = self._q(vql.q_hunt_flows_page(hunt_id, start_row, count), org, max_rows=count)
        out = []
        for row in result.rows:
            cid = str(row.get("ClientId") or "")
            fid = str(row.get("FlowId") or "")
            if not cid or not fid:
                continue
            status = normalize_flow(row, client_id=cid, flow_id=fid)
            out.append({"client_id": cid, "flow_id": fid, "status": status})
        return out

    # --- collection launch (COLLECT_CLIENT) -------------------------------------------------

