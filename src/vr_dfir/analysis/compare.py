"""Domain adapters for comparing two saved captures of the same host and source.

Each adapter declares a stable entity key, the fields that are compared, and the fields that
are ignored as volatile. These rules are versioned (``POLICY_VERSION``): a rule change produces
a new comparison, never a silent "change on the host". Normalization removes only harmless
representation differences (row order, number-as-text, protocol case, IPv6 spelling,
whitespace in commands, the order of list members and mount options). Raw rows are untouched.

Documented keys
* users: account name. groups: group name. services: unit name.
* listeners: protocol + local address + local port, listening rows only (established
  connections are not stable entities and are not compared).
* cron: owner + file + command (whitespace-normalized); schedule changes are modifications.
* packages: package name + architecture. mounts: mount point.
* ssh_keys: owner + key material. settings: file + setting name. files: canonical path.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from vr_dfir.evidence import presentation as pres

POLICY_VERSION = "compare-2026.10-1"


def _text(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip()
    return v


_INT_RE = re.compile(r"-?[0-9]{1,40}", re.ASCII)


def _num(v: Any) -> Any:
    """Number-as-text and integral floats compare equal to integers; never raises."""
    if isinstance(v, bool) or isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 2**63 else v
    if isinstance(v, str) and _INT_RE.fullmatch(v.strip()):
        return int(v.strip())
    return _text(v)


def _bool(v: Any) -> Any:
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
        return v.strip().lower() in ("true", "yes", "1")
    return v


def _lower(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def _command(v: Any) -> Any:
    return " ".join(v.split()) if isinstance(v, str) else v


def _address(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    addr, _ = pres.split_endpoint(v)
    try:
        return str(ipaddress.ip_address(addr or ""))
    except ValueError:
        return (addr or "").strip().lower()


def _members(v: Any) -> Any:
    if isinstance(v, list):
        return sorted({str(x).strip() for x in v if str(x).strip()})
    if isinstance(v, str):
        return sorted({x.strip() for x in v.split(",") if x.strip()})
    return v


def _options(v: Any) -> Any:
    if isinstance(v, str):
        return sorted({x.strip() for x in v.split(",") if x.strip()})
    return v


NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "uid": _num, "gid": _num, "lport": _num, "size": _num, "pid": _num,
    "proto": _lower, "laddr": _address, "command": _command, "exec": _command,
    "members": _members, "options": _options, "deleted": _bool, "key": _command,
}


@dataclass(frozen=True)
class Adapter:
    id: str
    label: str
    domain: str
    key: tuple[str, ...]
    compare: tuple[str, ...]
    volatile: tuple[str, ...] = ()
    row_filter: Callable[[dict, dict], bool] | None = None
    note: str = ""


def _listening(row: dict, fields: dict) -> bool:
    return pres.is_listening(pres.role_value(row, fields, "state")) or (
        pres.role_value(row, fields, "state") in (pres._MISSING, None, "")
        and str(pres.role_value(row, fields, "proto")).lower().startswith("udp"))


ADAPTERS: dict[str, Adapter] = {a.id: a for a in [
    Adapter("users", "User accounts", "user", ("account",),
            ("uid", "gid", "home", "shell", "description")),
    Adapter("groups", "Groups", "group", ("group",), ("gid", "members")),
    Adapter("services", "Services", "service", ("unit",),
            ("load", "active", "sub", "description", "exec", "user", "start_mode")),
    Adapter("listeners", "Listening endpoints", "network", ("proto", "laddr", "lport"),
            ("pname",), volatile=("pid", "raddr", "rport", "state"), row_filter=_listening,
            note="Only listening sockets are compared; established connections change "
                 "constantly and are not stable entities."),
    Adapter("cron", "Scheduled tasks (cron)", "cron", ("user", "path", "command"),
            ("minute", "hour", "dom", "month", "dow", "event")),
    Adapter("packages", "Installed packages", "package", ("name", "arch"),
            ("version", "release", "status")),
    Adapter("mounts", "Mounts", "mount", ("mountpoint",), ("device", "fstype", "options")),
    Adapter("ssh_keys", "SSH authorized keys", "ssh_key", ("user", "key"),
            ("keytype", "comment", "options", "path")),
    Adapter("settings", "Configuration settings", "config", ("path", "key"), ("value",)),
    Adapter("files", "File metadata", "file", ("path",),
            ("size", "mode", "owner", "group", "hash", "mtime", "ctime"), volatile=("atime",)),
]}


def adapter_for(domain: str) -> Adapter | None:
    d = pres.DOMAINS.get(domain)
    return ADAPTERS.get(d.comparison) if d and d.comparison else None


def normalize(role: str, value: Any) -> Any:
    if value is pres._MISSING:
        return {"$absent": True}
    fn = NORMALIZERS.get(role, _text)
    return fn(value)


@dataclass
class Keyed:
    key: str | None
    key_display: str
    values: dict          # normalized compared values
    raw: dict             # raw compared values (for display)
    ignored_hash: str     # hash of every other field (normalized), to show ignored differences
    reason: str | None = None


def keyed_row(adapter: Adapter, row: Any, fields: dict) -> Keyed | None:
    """Normalized key and compared values of one row; ``None`` if the adapter skips the row."""
    if not isinstance(row, dict):
        return Keyed(None, "", {}, {}, "", "row is not an object")
    if adapter.row_filter and not adapter.row_filter(row, fields):
        return None
    key_parts = []
    for role in adapter.key:
        v = pres.role_value(row, fields, role)
        if v is pres._MISSING or v is None or (isinstance(v, str) and not v.strip()):
            return Keyed(None, "", {}, {}, "", f"missing key field ({role})")
        key_parts.append(normalize(role, v))
    values, raw = {}, {}
    for role in adapter.compare:
        v = pres.role_value(row, fields, role)
        if role in fields:
            values[role] = normalize(role, v)
            raw[role] = None if v is pres._MISSING else v
    used = {fields[r] for r in (*adapter.key, *adapter.compare) if r in fields}
    rest = {k: v for k, v in row.items() if k not in used}
    ignored_hash = hashlib.sha256(json.dumps(rest, sort_keys=True, ensure_ascii=False,
                                             default=str).encode()).hexdigest()[:16]
    # The full normalized key is hashed, so long keys never collide after truncation.
    key = hashlib.sha256(json.dumps(key_parts, ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()[:32]
    display = " · ".join(str(p) for p in key_parts)
    return Keyed(key, display[:500], values, raw, ignored_hash)


def changed_fields(before: dict, after: dict) -> list[str]:
    return [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
