"""Centralised VQL literal handling and fixed query templates.

User input never becomes VQL syntax. Every dynamic value is rendered through
:func:`literal`, and every identifier is validated in :mod:`vr_dfir.ids` before
it is placed in a template. There is no API in this module that accepts a raw
query fragment.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence

from vr_dfir import ids
from vr_dfir.errors import ValidationError

Scalar = str | int | float | bool | None


def literal(value: Scalar | Sequence[Scalar]) -> str:
    """Render a Python value as a VQL literal.

    Strings use single quotes with backslash and quote escaping. Newlines and
    other control characters are escaped so a value cannot terminate the
    statement.
    """
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(literal(item) for item in value) + "]"
    if value is None:
        raise ValidationError("NULL values are not sent to Velociraptor; omit the parameter.")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Non-finite numbers cannot be sent to Velociraptor.")
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace("\x00", "")
        )
        return f"'{escaped}'"
    raise ValidationError(f"Unsupported VQL literal type: {type(value).__name__}")


def bounded_int(value: int, *, minimum: int = 0, maximum: int = 1_000_000, name: str = "value") -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer.") from exc
    if number < minimum or number > maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}.")
    return number


def env_dict(parameters: Mapping[str, str]) -> str:
    """Render artifact parameters as ``dict(Name='value', ...)``.

    Values must already be strings (see ``profiles.parameters.serialize``):
    Velociraptor passes artifact parameters to the client as strings.
    """
    items = []
    for key in sorted(parameters):
        ids.parameter_name(key)
        value = parameters[key]
        if not isinstance(value, str):
            raise ValidationError(f"Parameter {key} must be serialized to a string first.")
        items.append(f"{key}={literal(value)}")
    return "dict(" + ", ".join(items) + ")"


# --- Fixed query templates ---------------------------------------------------------------
# Each template returns a complete query. Only validated identifiers and literals are
# interpolated.


def q_artifact_catalog() -> str:
    """All artifact definitions; filtering happens locally for deterministic regex semantics."""
    return (
        "SELECT name, description, type, author, precondition, parameters, sources, "
        "required_permissions, implied_permissions, resources, tools, metadata, "
        "built_in, compiled_in, is_alias, aliases "
        "FROM artifact_definitions()"
    )


def q_artifact_exact(names: Sequence[str]) -> str:
    validated = [ids.artifact_name(n) for n in names]
    return (
        "SELECT name, description, type, author, precondition, parameters, sources, "
        "required_permissions, implied_permissions, resources, tools, metadata, "
        "built_in, compiled_in, is_alias, aliases "
        f"FROM artifact_definitions(names={literal(validated)})"
    )


CLIENT_COLUMNS = (
    "client_id, os_info.hostname AS Hostname, os_info.fqdn AS Fqdn, "
    "os_info.system AS OSType, os_info.release AS OSRelease, "
    "os_info.machine AS Machine, last_seen_at AS LastSeenAt, "
    "first_seen_at AS FirstSeenAt, last_ip AS LastIP, labels AS Labels, "
    "agent_information.version AS AgentVersion"
)


def q_client_by_id(client_id: str) -> str:
    cid = ids.client_id(client_id)
    return f"SELECT {CLIENT_COLUMNS} FROM clients(client_id={literal(cid)})"


def q_clients_search(text: str, limit: int) -> str:
    """Case-insensitive *substring* search on hostname/FQDN/client ID.

    The search text is escaped into a regular expression literal locally so it is a
    plain-text search for the user, not a regex.
    """
    lim = bounded_int(limit, minimum=1, maximum=500, name="limit")
    if len(text) > 200:
        raise ValidationError("Search text is too long.")
    pattern = _regex_escape(text.strip()) if text.strip() else "."
    lit = literal(pattern)
    return (
        f"SELECT {CLIENT_COLUMNS} FROM clients() "
        f"WHERE os_info.hostname =~ {lit} OR os_info.fqdn =~ {lit} OR client_id =~ {lit} "
        f"ORDER BY LastSeenAt DESC LIMIT {lim}"
    )


def q_clients_exact_host(hostname: str, limit: int = 20) -> str:
    lim = bounded_int(limit, minimum=1, maximum=100, name="limit")
    if not hostname or len(hostname) > 255:
        raise ValidationError("Hostname must be 1-255 characters.")
    pattern = literal("^" + _regex_escape(hostname.strip()) + "$")
    return (
        f"SELECT {CLIENT_COLUMNS} FROM clients() "
        f"WHERE os_info.hostname =~ {pattern} OR os_info.fqdn =~ {pattern} "
        f"ORDER BY LastSeenAt DESC LIMIT {lim}"
    )


def q_flow_context(client_id: str, flow_id: str) -> str:
    """Flow metadata via ``flows()`` (READ_RESULTS), verified in Velociraptor v0.77.3."""
    return (
        f"SELECT * FROM flows(client_id={literal(ids.client_id(client_id))}, "
        f"flow_id={literal(ids.flow_id(flow_id))})"
    )


def q_recent_flows(client_id: str, limit: int = 50) -> str:
    lim = bounded_int(limit, minimum=1, maximum=500, name="limit")
    return (
        "SELECT session_id, create_time, start_time, active_time, state, status, "
        "request.artifacts AS artifacts, request.creator AS creator, "
        "total_collected_rows, artifacts_with_results "
        f"FROM flows(client_id={literal(ids.client_id(client_id))}, summary=TRUE) "
        f"ORDER BY create_time DESC LIMIT {lim}"
    )


def q_flow_logs(client_id: str, flow_id: str, limit: int = 200) -> str:
    lim = bounded_int(limit, minimum=1, maximum=5000, name="limit")
    return (
        f"SELECT * FROM flow_logs(client_id={literal(ids.client_id(client_id))}, "
        f"flow_id={literal(ids.flow_id(flow_id))}) LIMIT {lim}"
    )


def full_source_name(artifact: str, source: str | None) -> str:
    art = ids.artifact_name(artifact)
    if source:
        return f"{art}/{ids.source_name(source)}"
    return art


def q_source_page(client_id: str, flow_id: str, artifact: str, source: str | None,
                  start_row: int, count: int) -> str:
    """One page of a flow result set. ``start_row``/``count`` are documented and implemented
    by ``source()`` for flow artifacts (Velociraptor v0.77.3)."""
    start = bounded_int(start_row, minimum=0, maximum=10**12, name="start_row")
    cnt = bounded_int(count, minimum=1, maximum=100_000, name="count")
    return (
        f"SELECT * FROM source(client_id={literal(ids.client_id(client_id))}, "
        f"flow_id={literal(ids.flow_id(flow_id))}, "
        f"artifact={literal(full_source_name(artifact, source))}, "
        f"start_row={start}, count={cnt})"
    )


def q_hunt_info(hunt_id: str) -> str:
    return f"SELECT * FROM hunts(hunt_id={literal(ids.hunt_id(hunt_id))})"


def q_hunts_list(limit: int = 50) -> str:
    """Recent hunts. ``hunts()`` takes no ``summary`` argument here: Velociraptor 0.76.5 rejects
    it and the query then returns no rows (only a log line), so the listing is projected
    instead."""
    lim = bounded_int(limit, minimum=1, maximum=500, name="limit")
    return (
        "SELECT hunt_id, hunt_description, create_time, start_time, state, artifacts, "
        "stats, creator FROM hunts() "
        f"ORDER BY create_time DESC LIMIT {lim}"
    )


def q_hunt_flows_page(hunt_id: str, start_row: int, count: int) -> str:
    """Contributing flows of a hunt. ``start_row`` is implemented by ``hunt_flows``; the
    declared ``limit`` argument is not applied in v0.77.3, so a VQL ``LIMIT`` bounds the page."""
    start = bounded_int(start_row, minimum=0, maximum=10**9, name="start_row")
    cnt = bounded_int(count, minimum=1, maximum=5000, name="count")
    return (
        f"SELECT HuntId, ClientId, FlowId, Flow FROM hunt_flows("
        f"hunt_id={literal(ids.hunt_id(hunt_id))}, start_row={start}) LIMIT {cnt}"
    )




# --- Notebooks (read-only; v0.77.3 source, shapes confirmed on a v0.76.5 server) ------------
# notebook_get() needs READ_RESULTS only and never creates a notebook or recalculates a cell.
# Its cells contain rendered output with table placeholders but no rows; rows are read with
# source() in notebook mode with an explicit cell version (an empty version could make the
# server rewrite a legacy notebook record). Bulky fields are projected away so a response
# stays well below the gRPC message limit.
#
# notebook_get() returns a protobuf object. On v0.76.5 its truthiness is FALSE even for an
# existing notebook (``WHERE NB`` drops the row), so the metadata row is selected by comparing
# the returned notebook ID with the validated requested ID. ``foreach`` over the raw
# ``NB.cell_metadata`` repeated field yields one row per cell with every projected field empty;
# serializing the field to JSON and iterating parse_json_array() exposes the cell fields.

NOTEBOOK_CELL_FIELDS = ("cell_id, type, current_version, available_versions, calculating, "
                        "error, timestamp, duration, version, notebook_id, input, output, "
                        "messages, more_messages")










def q_orgs() -> str:
    return "SELECT OrgId, Name FROM orgs()"


def q_probe() -> str:
    """Cheap read-only probe: plugin/function versions used by this application."""
    return (
        "SELECT version(plugin='source') AS source, version(plugin='flows') AS flows, "
        "version(plugin='hunt_flows') AS hunt_flows, "
        "version(plugin='artifact_definitions') AS artifact_definitions, "
        "version(plugin='hunt_results') AS hunt_results, version(plugin='hunts') AS hunts, "
        "version(function='client_info') AS client_info FROM scope()"
    )




def _regex_escape(text: str) -> str:
    """Escape text for a Go RE2 regular expression (plain-text matching)."""
    special = set("\\.^$|?*+()[]{}")
    return "".join("\\" + ch if ch in special else ch for ch in text)


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
