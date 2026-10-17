"""In-memory synthetic Velociraptor server for tests (never shipped in the package).

The fake implements the low-level :class:`~vr_dfir.velociraptor.backend.Backend`
interface and understands *only* this project's fixed query templates. Responses
use the verified Velociraptor v0.77.3 row shapes (flat flow context from
``flows()``, ``{HuntId, ClientId, FlowId, Flow}`` from ``hunt_flows()``,
``{flow_id, request}`` from ``collect_client()``). Any other query raises, so a
template change cannot silently bypass the fake.

All data is synthetic. Every launch is recorded in :attr:`FakeServer.launches` so
tests can prove that analysis paths never start collections.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vr_dfir.errors import VelociraptorError, VelociraptorUnavailable
from vr_dfir.velociraptor.backend import QueryResult

# --- tiny parser for this project's own VQL templates -------------------------------------

_TOKEN_RE = re.compile(r"""\s*(?:
    (?P<str>'(?:\\.|[^'\\])*')
  | (?P<num>-?\d+(?:\.\d+)?)
  | (?P<bool>TRUE|FALSE)
  | (?P<ident>`[^`]+`|[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<punct>[()\[\],=])
)""", re.VERBOSE)


def _unescape(literal: str) -> str:
    body = literal[1:-1]
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise VelociraptorError(f"Fake server cannot parse arguments near {text[pos:pos+30]!r}")
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    return tokens


def _parse_value(tokens: list, i: int) -> tuple[Any, int]:
    kind, val = tokens[i]
    if kind == "str":
        return _unescape(val), i + 1
    if kind == "num":
        return (float(val) if "." in val else int(val)), i + 1
    if kind == "bool":
        return val == "TRUE", i + 1
    if kind == "punct" and val == "[":
        items = []
        i += 1
        while tokens[i] != ("punct", "]"):
            item, i = _parse_value(tokens, i)
            items.append(item)
            if tokens[i] == ("punct", ","):
                i += 1
        return items, i + 1
    if kind == "ident" and val == "dict" and tokens[i + 1] == ("punct", "("):
        args, i = _parse_args(tokens, i + 2)
        return args, i
    raise VelociraptorError(f"Fake server: unexpected token {val!r}")


def _parse_args(tokens: list, i: int) -> tuple[dict, int]:
    """Parse ``key=value, ...)`` starting after the opening parenthesis."""
    args: dict[str, Any] = {}
    while tokens[i] != ("punct", ")"):
        kind, key = tokens[i]
        if kind != "ident" or tokens[i + 1] != ("punct", "="):
            raise VelociraptorError(f"Fake server: expected key=value, got {key!r}")
        value, i = _parse_value(tokens, i + 2)
        args[key.strip("`")] = value
        if tokens[i] == ("punct", ","):
            i += 1
    return args, i + 1


def _call_args(query: str, name: str) -> dict:
    marker = f"{name}("
    start = query.index(marker) + len(marker)
    depth = 1
    pos = start
    in_str = False
    while pos < len(query) and depth:
        ch = query[pos]
        if in_str:
            if ch == "\\":
                pos += 2
                continue
            if ch == "'":
                in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        pos += 1
    inner = query[start:pos - 1]
    if not inner.strip():
        return {}
    args, _ = _parse_args(_tokenize(inner) + [("punct", ")")], 0)
    return args


def _limit(query: str) -> int | None:
    m = re.search(r"\bLIMIT (\d+)\s*$", query)
    return int(m.group(1)) if m else None


def _where_pattern(query: str) -> str | None:
    m = re.search(r"os_info\.hostname =~ ('(?:\\.|[^'\\])*')", query)
    return _unescape(m.group(1)) if m else None


# --- server state -------------------------------------------------------------------------


@dataclass
class FakeFlow:
    client_id: str
    flow_id: str
    artifacts: list[str]
    results: dict[str, list[dict]] = field(default_factory=dict)
    final_state: str = "FINISHED"
    final_status: str = ""
    query_stats: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
    progression: list[str] = field(default_factory=list)
    create_time: int = 1767225600_000000
    creator: str = "synthetic-analyst"
    hunt_id: str | None = None
    # (full source name) -> number of initial reads that fail at or after this row
    read_failures: dict[str, dict] = field(default_factory=dict)
    polls: int = 0

    def current_state(self) -> tuple[str, str]:
        if self.polls < len(self.progression):
            return self.progression[self.polls], ""
        return self.final_state, self.final_status

    def context(self, advance: bool = True) -> dict:
        state, status = self.current_state()
        if advance:
            self.polls += 1
        finished = state in {"FINISHED", "ERROR"}
        with_results = [name for name, rows in self.results.items() if rows] if finished else []
        total = sum(len(r) for r in self.results.values()) if finished else 0
        return {
            "client_id": self.client_id,
            "session_id": self.flow_id,
            "request": {
                "creator": self.creator,
                "client_id": self.client_id,
                "flow_id": "",
                "artifacts": list(self.artifacts),
                "timeout": 600,
            },
            "backtrace": "",
            "create_time": self.create_time,
            "start_time": self.create_time + 2_000_000,
            "active_time": self.create_time + 30_000_000,
            "total_uploaded_files": 0,
            "total_uploaded_bytes": 0,
            "total_collected_rows": total,
            "total_logs": len(self.logs),
            "state": state,
            "status": status,
            "artifacts_with_results": with_results,
            "query_stats": copy.deepcopy(self.query_stats) if finished else [],
            "AvailableDownloads": {"files": []},
        }


class FakeServer:
    """Synthetic multi-org Velociraptor server."""

    synthetic = True

    def __init__(self, data: dict, *, launch_templates: dict | None = None,
                 state_path: Path | None = None):
        self._lock = threading.RLock()
        self.state_path = Path(state_path) if state_path else None
        self.orgs: list[dict] = copy.deepcopy(data.get("orgs", [{"OrgId": "root", "Name": "root"}]))
        self.artifacts: dict[str, list[dict]] = copy.deepcopy(data.get("artifacts", {}))
        self.clients: dict[str, list[dict]] = copy.deepcopy(data.get("clients", {}))
        self.flows: dict[str, dict[tuple[str, str], FakeFlow]] = {}
        for org, flows in data.get("flows", {}).items():
            for f in flows:
                flow = f if isinstance(f, FakeFlow) else FakeFlow(**copy.deepcopy(f))
                self.flows.setdefault(org, {})[(flow.client_id, flow.flow_id)] = flow
        self.hunts: dict[str, dict[str, dict]] = copy.deepcopy(data.get("hunts", {}))
        self.launch_templates = launch_templates or {}
        self.launches: list[dict] = []
        self.queries: list[tuple[str, str]] = []
        self.launch_behavior = "ok"  # ok | unavailable_after_submit | refused
        self.unavailable = False
        self._next_flow = 9000
        self.server_timeouts: list[int | None] = []
        # Compatibility: hunts(summary=TRUE) is rejected by Velociraptor 0.76.5.
        self.v0765 = False
        self.extra_logs: list[str] = []  # appended to every query's logs (INFO noise etc.)
        self.fail_templates: dict[str, list[str]] = {}  # substring of a query -> error logs

    # Backend protocol
    def query(self, vql: str, *, org_id: str, timeout: float | None = None,
              max_rows: int = 100_000, server_timeout: int | None = None) -> QueryResult:
        with self._lock:
            org = org_id or "root"
            self.queries.append((org, vql))
            self.server_timeouts.append(server_timeout)
            if self.unavailable:
                raise VelociraptorUnavailable("Synthetic server is configured as unavailable.")
            if org not in {o["OrgId"] for o in self.orgs}:
                return QueryResult(rows=[], logs=[f"query: Org {org} not found"])
            for marker, logs in self.fail_templates.items():
                if marker in vql:
                    return QueryResult(rows=[], logs=list(self.extra_logs) + list(logs))
            rows, logs = self._dispatch(vql, org)
            logs = list(self.extra_logs) + list(logs)
            if len(rows) > max_rows:
                raise VelociraptorError("Fake server: too many rows for a bounded query.")
            return QueryResult(rows=copy.deepcopy(rows), logs=list(logs))

    def _dispatch(self, q: str, org: str) -> tuple[list[dict], list[str]]:
        if "collect_client(" in q:
            return self._collect(q, org)
        if "FROM artifact_definitions(" in q:
            args = _call_args(q, "artifact_definitions")
            defs = self.artifacts.get(org, [])
            if "names" in args:
                wanted = set(args["names"])
                return [d for d in defs if d["name"] in wanted], []
            return list(defs), []
        if "FROM clients(" in q:
            args = _call_args(q, "clients")
            rows = [self._client_row(c) for c in self.clients.get(org, [])]
            if "client_id" in args:
                return [r for r in rows if r["client_id"] == args["client_id"]], []
            pattern = _where_pattern(q)
            if pattern:
                rx = re.compile(pattern, re.I)
                rows = [r for r in rows if rx.search(r.get("Hostname") or "")
                        or rx.search(r.get("Fqdn") or "") or rx.search(r["client_id"])]
            rows.sort(key=lambda r: r.get("LastSeenAt") or 0, reverse=True)
            lim = _limit(q)
            return rows[:lim] if lim else rows, []
        if "FROM flows(" in q:
            args = _call_args(q, "flows")
            flows = self.flows.get(org, {})
            cid = args["client_id"]
            if "flow_id" in args:
                flow = flows.get((cid, args["flow_id"]))
                return ([flow.context()] if flow else []), []
            rows = []
            for (fc, _), flow in flows.items():
                if fc != cid:
                    continue
                ctx = flow.context(advance=False)
                rows.append({
                    "session_id": ctx["session_id"], "create_time": ctx["create_time"],
                    "start_time": ctx["start_time"], "active_time": ctx["active_time"],
                    "state": ctx["state"], "status": ctx["status"],
                    "artifacts": ctx["request"]["artifacts"], "creator": ctx["request"]["creator"],
                    "total_collected_rows": ctx["total_collected_rows"],
                    "artifacts_with_results": ctx["artifacts_with_results"],
                })
            rows.sort(key=lambda r: r["create_time"], reverse=True)
            lim = _limit(q)
            return rows[:lim] if lim else rows, []
        if "FROM flow_logs(" in q:
            args = _call_args(q, "flow_logs")
            flow = self.flows.get(org, {}).get((args["client_id"], args["flow_id"]))
            if flow is None:
                return [], ["flow_logs: not found"]
            lim = _limit(q) or len(flow.logs)
            return list(flow.logs[:lim]), []
        if "FROM source(" in q:
            return self._source(q, org)
        if "FROM hunt_flows(" in q:
            args = _call_args(q, "hunt_flows")
            hunt = self.hunts.get(org, {}).get(args["hunt_id"])
            if hunt is None:
                return [], ["hunt_flows: hunt not found"]
            members = [
                flow for (cid, fid), flow in sorted(self.flows.get(org, {}).items())
                if flow.hunt_id == args["hunt_id"]
            ]
            start = int(args.get("start_row", 0))
            lim = _limit(q) or len(members)
            out = []
            for flow in members[start:start + lim]:
                out.append({"HuntId": args["hunt_id"], "ClientId": flow.client_id,
                            "FlowId": flow.flow_id, "Flow": flow.context(advance=False)})
            return out, []
        if "FROM hunts(" in q:
            args = _call_args(q, "hunts")
            if self.v0765 and "summary" in args:
                return [], ["hunts: Unexpected arg summary while parsing args"]
            hunts = self.hunts.get(org, {})
            if "hunt_id" in args:
                h = hunts.get(args["hunt_id"])
                return ([copy.deepcopy(h)] if h else []), []
            rows = sorted(hunts.values(), key=lambda h: h.get("create_time", 0), reverse=True)
            lim = _limit(q)
            return rows[:lim] if lim else rows, []
        if "FROM orgs()" in q:
            return [{"OrgId": o["OrgId"], "Name": o["Name"]} for o in self.orgs], []
        if "version(plugin='source')" in q:
            return [{"source": 2, "flows": 3, "hunt_flows": 2, "artifact_definitions": 1,
                     "collect_client": 3}], []
        raise VelociraptorError("Fake server does not support this query template.")

    def _client_row(self, c: dict) -> dict:
        return {
            "client_id": c["client_id"], "Hostname": c.get("hostname"), "Fqdn": c.get("fqdn"),
            "OSType": c.get("os"), "OSRelease": c.get("release"), "Machine": c.get("machine"),
            "LastSeenAt": c.get("last_seen", 0), "FirstSeenAt": c.get("first_seen", 0),
            "LastIP": c.get("last_ip", ""), "Labels": c.get("labels", []),
            "AgentVersion": c.get("agent_version", "0.77.3-synthetic"),
        }

    def _source(self, q: str, org: str) -> tuple[list[dict], list[str]]:
        args = _call_args(q, "source")
        flow = self.flows.get(org, {}).get((args.get("client_id"), args.get("flow_id")))
        name = args.get("artifact", "")
        if flow is None:
            return [], [f"source: flow {args.get('flow_id')} not found"]
        state, _ = flow.current_state()
        rows = flow.results.get(name)
        if rows is None:
            return [], [f"source: result set {name} not found"]
        start = int(args.get("start_row", 0))
        count = int(args.get("count", len(rows)))
        failure = flow.read_failures.get(name)
        if failure and failure.get("remaining", 0) > 0 and start >= failure["at_row"]:
            failure["remaining"] -= 1
            raise VelociraptorUnavailable("Synthetic transient read failure (injected).")
        return list(rows[start:start + count]), []

    def _collect(self, q: str, org: str) -> tuple[list[dict], list[str]]:
        args = _call_args(q, "collect_client")
        client_id = args["client_id"]
        artifacts = args["artifacts"]
        record = {"org_id": org, "client_id": client_id, "artifacts": list(artifacts),
                  "env": args.get("env", {}), "timeout": args.get("timeout"),
                  "max_rows": args.get("max_rows"), "max_bytes": args.get("max_bytes"),
                  "urgent": args.get("urgent", False)}
        if self.launch_behavior == "refused":
            self.launches.append({**record, "outcome": "refused"})
            return [{"Collection": None}], ["collect_client: synthetic refusal (permission denied)"]
        known = {c["client_id"] for c in self.clients.get(org, [])}
        if client_id not in known:
            return [{"Collection": None}], [f"collect_client: client {client_id} not known"]
        defs = {d["name"] for d in self.artifacts.get(org, [])}
        missing = [a for a in artifacts if a not in defs]
        if missing:
            return [{"Collection": None}], [f"collect_client: Unknown artifact {missing[0]}"]
        self._next_flow += 1
        flow_id = f"F.SYNTHL{self._next_flow:05d}"
        template = self.launch_templates.get((client_id, artifacts[0])) or \
            self.launch_templates.get(("*", artifacts[0])) or {}
        flow = FakeFlow(
            client_id=client_id, flow_id=flow_id, artifacts=list(artifacts),
            results=copy.deepcopy(template.get("results", {})),
            final_state=template.get("final_state", "FINISHED"),
            final_status=template.get("final_status", ""),
            query_stats=copy.deepcopy(template.get("query_stats", [])),
            logs=copy.deepcopy(template.get("logs", [])),
            progression=list(template.get("progression", ["RUNNING", "IN_PROGRESS"])),
            read_failures=copy.deepcopy(template.get("read_failures", {})),
            create_time=int(time.time() * 1_000_000),
        )
        self.flows.setdefault(org, {})[(client_id, flow_id)] = flow
        self.launches.append({**record, "flow_id": flow_id, "outcome": "scheduled"})
        self._persist()
        if self.launch_behavior == "unavailable_after_submit":
            raise VelociraptorUnavailable("Synthetic connection drop after submission.")
        return [{"Collection": {"flow_id": flow_id, "request": {
            "creator": "synthetic-api", "client_id": client_id, "artifacts": list(artifacts),
            "timeout": args.get("timeout", 600), "max_rows": args.get("max_rows"),
            "urgent": args.get("urgent", False)}}}], []


    # -- persistence for restart tests -----------------------------------------------------

    def _persist(self) -> None:
        if self.state_path is not None:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"launches": self.launches}))

    def restore_launches(self) -> None:
        """Recreate earlier synthetic launches (in their final state) after a restart."""
        if self.state_path is None or not self.state_path.is_file():
            return
        data = json.loads(self.state_path.read_text())
        for launch in data.get("launches", []):
            if launch.get("outcome") != "scheduled":
                continue
            art = launch["artifacts"][0]
            tmpl = self.launch_templates.get((launch["client_id"], art)) or \
                self.launch_templates.get(("*", art)) or {}
            flow = FakeFlow(client_id=launch["client_id"], flow_id=launch["flow_id"],
                            artifacts=launch["artifacts"],
                            results=copy.deepcopy(tmpl.get("results", {})),
                            final_state=tmpl.get("final_state", "FINISHED"),
                            final_status=tmpl.get("final_status", ""),
                            query_stats=copy.deepcopy(tmpl.get("query_stats", [])),
                            logs=copy.deepcopy(tmpl.get("logs", [])))
            self.flows.setdefault(launch.get("org_id", "root"), {})[
                (flow.client_id, flow.flow_id)] = flow
            self.launches.append(launch)
            if "SYNTHL" in flow.flow_id:
                self._next_flow = max(self._next_flow, int(flow.flow_id.rsplit("L", 1)[-1]))
