"""Source-aware presentation metadata: the renderer registry.

A *mapping* is chosen from the source identity (artifact and source name), never from a
column name or a sample value. Inside a mapped domain, each *role* (for example a process
executable or a listening port) is resolved from a documented list of candidate column names
taken from the Velociraptor artifact definitions (``Linux.Sys.Pslist``, ``Linux.Sys.Users``,
``Linux.Network.Netstat`` ...) and from the baseline sources used with this project. Unknown
sources use the generic renderer, which only distinguishes JSON value types.

Operators can map custom artifacts with ``VR_DFIR_SOURCE_MAPPINGS`` (a JSON file, see
``docs/presentation.md``). Derived values (titles, entity keys, parsed times) are presentation
data: the stored row is never modified, flattened or re-serialized.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vr_dfir.errors import ConfigurationError
from vr_dfir.evidence.timeparse import FORMATS, ParsedTime, parse_time

NORMALIZER_VERSION = "presentation-v1"
MAX_TITLE = 200
MAX_ENTITY_KEY = 1000

# Renderer kinds understood by the frontend registry.
KINDS = ("text", "command", "path", "address", "port", "protocol", "state", "account",
         "number", "hash", "timestamp", "json", "bool", "schedule")


@dataclass(frozen=True)
class Role:
    name: str
    label: str
    candidates: tuple[str, ...]
    kind: str = "text"


@dataclass(frozen=True)
class TimeRule:
    role: str
    fmt: str = "iso8601"
    meaning: str = ""


@dataclass(frozen=True)
class Domain:
    id: str
    label: str
    roles: tuple[Role, ...]
    title: tuple[str, ...]
    columns: tuple[str, ...]
    summary: tuple[str, ...]
    event_time: tuple[TimeRule, ...] = ()
    entities: tuple[str, ...] = ()
    comparison: str | None = None

    def role(self, name: str) -> Role:
        for r in self.roles:
            if r.name == name:
                return r
        raise KeyError(name)


def _r(name, label, candidates, kind="text") -> Role:
    return Role(name, label, tuple(candidates), kind)


DOMAINS: dict[str, Domain] = {d.id: d for d in [
    Domain(
        "process", "Processes",
        roles=(
            _r("name", "Name", ["Name", "ProcessName"]),
            _r("pid", "PID", ["Pid", "ProcessId", "PID"], "number"),
            _r("ppid", "Parent PID", ["Ppid", "PPid", "ParentPid", "ParentProcessId"], "number"),
            _r("user", "User", ["Username", "User", "UserName"], "account"),
            _r("exe", "Executable", ["Exe", "AbsoluteExePath", "ImagePath", "Binary"], "path"),
            _r("cmdline", "Command line", ["CommandLine", "Commandline", "Cmdline"], "command"),
            _r("start", "Started", ["StartTime", "CreateTime"], "timestamp"),
            _r("hash", "SHA-256", ["ExeSHA256", "Hash.SHA256", "SHA256", "Sha256"], "hash"),
            _r("deleted", "Executable deleted", ["IsDeleted", "Deleted", "ExeDeleted"], "bool"),
        ),
        title=("name", "exe"), columns=("name", "user", "pid", "ppid", "exe", "cmdline", "start"),
        summary=("name", "user", "pid", "ppid", "exe", "cmdline", "start", "hash"),
        event_time=(TimeRule("start", "iso8601", "process start time"),),
        entities=("executable_path", "file_hash", "command"),
    ),
    Domain(
        "network", "Network connections",
        roles=(
            _r("proto", "Protocol", ["Type", "Protocol", "Proto"], "protocol"),
            _r("family", "Family", ["Family", "AddressFamily"]),
            _r("state", "State", ["Status", "State", "ConnectionStatus"], "state"),
            _r("laddr", "Local address", ["Laddr.IP", "Laddr", "LocalAddr", "LocalAddress"],
               "address"),
            _r("lport", "Local port", ["Laddr.Port", "Lport", "LocalPort"], "port"),
            _r("raddr", "Remote address", ["Raddr.IP", "Raddr", "RemoteAddr", "RemoteAddress"],
               "address"),
            _r("rport", "Remote port", ["Raddr.Port", "Rport", "RemotePort"], "port"),
            _r("pid", "PID", ["Pid", "ProcessInfo.Pid"], "number"),
            _r("pname", "Process", ["Name", "ProcessName", "ProcessInfo.Name"]),
            _r("direction", "Direction", ["Direction"]),
        ),
        title=("proto", "laddr", "lport", "raddr", "rport", "state"),
        columns=("proto", "state", "laddr", "lport", "raddr", "rport", "pname"),
        summary=("proto", "state", "laddr", "lport", "raddr", "rport", "pname", "pid",
                 "direction"),
        entities=("network_destination",), comparison="listeners",
    ),
    Domain(
        "user", "User accounts",
        roles=(
            _r("account", "Account", ["User", "Username", "UserName", "Name", "Login"], "account"),
            _r("uid", "UID", ["Uid", "UID", "UserId"], "number"),
            _r("gid", "GID", ["Gid", "GID"], "number"),
            _r("home", "Home directory", ["Homedir", "HomeDir", "Home", "Directory"], "path"),
            _r("shell", "Shell", ["Shell"], "path"),
            _r("description", "Description", ["Description", "Gecos", "FullName"]),
        ),
        title=("account",), columns=("account", "uid", "gid", "home", "shell", "description"),
        summary=("account", "uid", "gid", "home", "shell", "description"),
        entities=("privileged_account",), comparison="users",
    ),
    Domain(
        "group", "Groups",
        roles=(
            _r("group", "Group", ["Group", "GroupName", "Name"]),
            _r("gid", "GID", ["GID", "Gid"], "number"),
            _r("members", "Members", ["Members"], "json"),
        ),
        title=("group",), columns=("group", "gid", "members"), summary=("group", "gid", "members"),
        comparison="groups",
    ),
    Domain(
        "service", "Services",
        roles=(
            _r("unit", "Service", ["Unit", "Name", "ServiceName"]),
            _r("active", "Active", ["Active", "ActiveState"], "state"),
            _r("sub", "Sub-state", ["Sub", "SubState"], "state"),
            _r("load", "Load", ["Load", "LoadState"], "state"),
            _r("description", "Description", ["Description", "DisplayName"]),
            _r("exec", "Command", ["ExecStart", "PathName", "Command"], "command"),
            _r("user", "Runs as", ["UserAccount", "StartName", "User"], "account"),
            _r("start_mode", "Start mode", ["StartMode", "UnitFileState", "Enabled"], "state"),
        ),
        title=("unit",), columns=("unit", "active", "sub", "load", "description", "exec"),
        summary=("unit", "active", "sub", "load", "description", "exec", "user", "start_mode"),
        entities=("service",), comparison="services",
    ),
    Domain(
        "cron", "Scheduled tasks (cron)",
        roles=(
            _r("user", "User", ["User", "Owner"], "account"),
            _r("minute", "Minute", ["Minute"]), _r("hour", "Hour", ["Hour"]),
            _r("dom", "Day of month", ["DayOfMonth"]), _r("month", "Month", ["Month"]),
            _r("dow", "Day of week", ["DayOfWeek"]), _r("event", "Event", ["Event"]),
            _r("command", "Command", ["Command"], "command"),
            _r("path", "File", ["Path", "OSPath", "File"], "path"),
        ),
        title=("user", "command"), columns=("user", "schedule", "command", "path"),
        summary=("user", "schedule", "command", "path"),
        entities=("cron_entry",), comparison="cron",
    ),
    Domain(
        "package", "Installed packages",
        roles=(
            _r("name", "Package", ["Package", "Name"]),
            _r("version", "Version", ["Version"]),
            _r("release", "Release", ["Release"]),
            _r("arch", "Architecture", ["Architecture", "Arch"]),
            _r("status", "Status", ["Status"], "state"),
        ),
        title=("name", "version"), columns=("name", "version", "release", "arch", "status"),
        summary=("name", "version", "release", "arch", "status"), comparison="packages",
    ),
    Domain(
        "mount", "Mounts",
        roles=(
            _r("mountpoint", "Mount point", ["Mountpoint", "MountPoint", "Point"], "path"),
            _r("device", "Device", ["Device", "Source"], "path"),
            _r("fstype", "Type", ["Type", "FSType", "FsType"]),
            _r("options", "Options", ["Options", "Opts"]),
        ),
        title=("mountpoint",), columns=("mountpoint", "device", "fstype", "options"),
        summary=("mountpoint", "device", "fstype", "options"), comparison="mounts",
    ),
    Domain(
        "ssh_key", "SSH authorized keys",
        roles=(
            _r("user", "User", ["User", "Owner", "Username"], "account"),
            _r("keytype", "Key type", ["KeyType", "Type"]),
            _r("key", "Key", ["Key", "PublicKey"], "hash"),
            _r("comment", "Comment", ["Comment"]),
            _r("options", "Options", ["Options"]),
            _r("path", "File", ["Path", "OSPath", "File", "FullPath"], "path"),
        ),
        title=("user", "keytype", "comment"), columns=("user", "keytype", "comment", "path"),
        summary=("user", "keytype", "comment", "options", "path", "key"),
        entities=("ssh_key",), comparison="ssh_keys",
    ),
    Domain(
        "config", "Configuration settings",
        roles=(
            _r("key", "Setting", ["Key", "Setting", "Parameter", "Param", "Name"]),
            _r("value", "Value", ["Value"]),
            _r("path", "File", ["Path", "OSPath", "File"], "path"),
        ),
        title=("key",), columns=("path", "key", "value"), summary=("path", "key", "value"),
        comparison="settings",
    ),
    Domain(
        "file", "Files",
        roles=(
            _r("path", "Path", ["OSPath", "FullPath", "Path", "Filename"], "path"),
            _r("size", "Size", ["Size"], "number"),
            _r("mode", "Mode", ["Mode", "Permissions"]),
            _r("owner", "Owner", ["Owner", "OwnerID", "Uid"], "account"),
            _r("group", "Group", ["Group", "GroupID", "Gid"]),
            _r("hash", "SHA-256", ["SHA256", "Sha256", "Hash.SHA256"], "hash"),
            _r("mtime", "Modified", ["Mtime", "MTime", "Modified", "LastWriteTime"], "timestamp"),
            _r("atime", "Accessed", ["Atime", "ATime", "Accessed"], "timestamp"),
            _r("ctime", "Changed", ["Ctime", "CTime", "Changed"], "timestamp"),
            _r("btime", "Created", ["Btime", "BTime", "Born"], "timestamp"),
        ),
        title=("path",), columns=("path", "size", "mode", "owner", "hash", "mtime"),
        summary=("path", "size", "mode", "owner", "group", "hash", "mtime", "ctime"),
        event_time=(TimeRule("mtime", "iso8601", "file modification time"),),
        entities=("file_path", "file_hash"), comparison="files",
    ),
    Domain(
        "system", "Host information",
        roles=(
            _r("hostname", "Hostname", ["Hostname", "Fqdn"]),
            _r("os", "OS", ["OS"]),
            _r("platform", "Platform", ["Platform"]),
            _r("platform_version", "Platform version", ["PlatformVersion"]),
            _r("kernel", "Kernel", ["KernelVersion"]),
            _r("arch", "Architecture", ["Architecture"]),
        ),
        title=("hostname",), columns=("hostname", "os", "platform", "platform_version", "kernel",
                                      "arch"),
        summary=("hostname", "os", "platform", "platform_version", "kernel", "arch"),
    ),
]}

GENERIC = Domain("generic", "Other source", roles=(), title=(), columns=(), summary=())

# (artifact, source) identities of documented Velociraptor artifacts. ``None`` = unnamed source.
_ARTIFACT_RULES: dict[tuple[str, str | None], str] = {
    ("Linux.Sys.Pslist", None): "process", ("Windows.System.Pslist", None): "process",
    ("MacOS.Sys.Pslist", None): "process", ("Generic.System.Pstree", None): "process",
    ("Linux.Network.Netstat", None): "network", ("Linux.Network.NetstatEnriched", None): "network",
    ("Windows.Network.Netstat", None): "network",
    ("Windows.Network.NetstatEnriched", None): "network",
    ("Linux.Sys.Users", None): "user", ("Linux.Sys.Groups", None): "group",
    ("Linux.Sys.Services", None): "service", ("Windows.System.Services", None): "service",
    ("Linux.Sys.Crontab", None): "cron", ("Linux.Sys.Crontab", "CronScripts"): "file",
    ("Linux.Debian.Packages", None): "package", ("Linux.RHEL.Packages", None): "package",
    ("Linux.Mounts", None): "mount", ("Linux.Ssh.AuthorizedKeys", None): "ssh_key",
    ("Linux.Sys.SUID", None): "file", ("Linux.Search.FileFinder", None): "file",
    ("Windows.Search.FileFinder", None): "file",
    ("Generic.Client.Info", "BasicInformation"): "system",
}
# Named sources of composite baseline artifacts (for example the LTH system baseline), by
# exact source name. These names identify what the source collected.
_SOURCE_RULES: dict[str, str] = {
    "processes": "process", "pslist": "process",
    "networkconnections": "network", "netstat": "network",
    "users": "user", "groups": "group", "services": "service",
    "crontab": "cron", "cron": "cron",
    "debianpackages": "package", "rhelpackages": "package", "packages": "package",
    "mounts": "mount", "authorizedkeys": "ssh_key", "sshauthorizedkeys": "ssh_key",
    "sshdconfig": "config", "sshconfig": "config",
    "client_basicinformation": "system", "basicinformation": "system",
    "client_linuxinfo": "system", "linuxinfo": "system",
}

ENTITY_KINDS = {
    "executable_path": "Executable path",
    "file_hash": "File hash",
    "command": "Command line",
    "privileged_account": "Privileged account (UID 0)",
    "service": "Service",
    "cron_entry": "Scheduled task",
    "network_destination": "Network destination",
    "file_path": "File path",
    "ssh_key": "SSH authorized key",
}


@dataclass(frozen=True)
class Mapping:
    domain: Domain
    basis: str
    configured: bool = False
    overrides: dict[str, tuple[str, ...]] = field(default_factory=dict)
    time_override: TimeRule | None = None
    assume_utc: bool = False

    @property
    def id(self) -> str:
        return self.domain.id

    def candidates(self, role: str) -> tuple[str, ...]:
        return self.overrides.get(role) or self.domain.role(role).candidates

    def event_rules(self) -> tuple[TimeRule, ...]:
        return (self.time_override,) if self.time_override else self.domain.event_time

    def describe(self) -> dict:
        return {"id": self.domain.id, "label": self.domain.label, "basis": self.basis,
                "configured": self.configured, "version": NORMALIZER_VERSION}


# ----------------------------------------------------------------------- configured mappings

_CONFIG_CACHE: dict[str, tuple[float, dict]] = {}


def load_configured(path: str | None) -> dict[tuple[str, str | None], dict]:
    """Operator mappings for custom artifacts: ``[{"artifact", "source", "domain", ...}]``."""
    if not path:
        return {}
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError as exc:
        raise ConfigurationError(f"VR_DFIR_SOURCE_MAPPINGS file {path} cannot be read.") from exc
    cached = _CONFIG_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"VR_DFIR_SOURCE_MAPPINGS is not valid JSON: {exc}") from exc
    items = data.get("mappings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ConfigurationError("VR_DFIR_SOURCE_MAPPINGS must contain a 'mappings' list.")
    out: dict[tuple[str, str | None], dict] = {}
    for n, item in enumerate(items[:500]):
        if not isinstance(item, dict) or not isinstance(item.get("artifact"), str):
            raise ConfigurationError(f"Source mapping {n} needs an 'artifact' string.")
        domain = item.get("domain")
        if domain not in DOMAINS:
            raise ConfigurationError(f"Source mapping {n}: unknown domain {domain!r}.")
        roles = item.get("roles") or {}
        if not isinstance(roles, dict):
            raise ConfigurationError(f"Source mapping {n}: 'roles' must be an object.")
        overrides = {}
        for role, fields in roles.items():
            try:
                DOMAINS[domain].role(role)
            except KeyError as exc:
                raise ConfigurationError(f"Source mapping {n}: domain {domain} has no role "
                                         f"{role!r}.") from exc
            names = [fields] if isinstance(fields, str) else fields
            if not isinstance(names, list) or not all(isinstance(x, str) and x for x in names):
                raise ConfigurationError(f"Source mapping {n}: role {role} needs field names.")
            overrides[role] = tuple(names[:10])
        time_rule = None
        if item.get("event_time"):
            et = item["event_time"]
            if not isinstance(et, dict) or et.get("format", "iso8601") not in FORMATS \
                    or not isinstance(et.get("field"), str):
                raise ConfigurationError(f"Source mapping {n}: event_time needs 'field' and a "
                                         f"format in {list(FORMATS)}.")
            overrides["__time__"] = (et["field"],)
            time_rule = TimeRule("__time__", et.get("format", "iso8601"),
                                 str(et.get("meaning") or "configured event time")[:80])
        source = item.get("source")
        out[(item["artifact"], source if isinstance(source, str) and source else None)] = {
            "domain": domain, "overrides": overrides, "time": time_rule,
            "assume_utc": bool((item.get("event_time") or {}).get("assume_utc"))}
    _CONFIG_CACHE[path] = (mtime, out)
    return out


def config_fingerprint(path: str | None) -> str:
    if not path:
        return "none"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "unreadable"


def mapping_for(artifact: str, source: str | None, configured: dict | None = None) -> Mapping:
    item = (configured or {}).get((artifact, source))
    if item:
        return Mapping(DOMAINS[item["domain"]], f"configured mapping for {artifact}"
                       + (f"/{source}" if source else ""), True, dict(item["overrides"]),
                       item["time"], item["assume_utc"])
    rule = _ARTIFACT_RULES.get((artifact, source))
    if rule:
        return Mapping(DOMAINS[rule], f"documented artifact {artifact}"
                       + (f"/{source}" if source else ""))
    if source:
        name = source.strip().lower()
        if name in _SOURCE_RULES:
            return Mapping(DOMAINS[_SOURCE_RULES[name]], f"source name '{source}'")
    return Mapping(GENERIC, "no mapping for this source; generic rendering")


# ----------------------------------------------------------------------- role resolution

_MISSING = object()


def get_field(row: Any, name: str) -> Any:
    """Value of a top-level or one-level nested (``A.B``) field, or ``_MISSING``."""
    if not isinstance(row, dict):
        return _MISSING
    if name in row:
        return row[name]
    if "." in name:
        head, _, tail = name.partition(".")
        nested = row.get(head)
        if isinstance(nested, dict) and tail in nested:
            return nested[tail]
    return _MISSING


def resolve_fields(mapping: Mapping, columns: list[str] | set[str] | None = None,
                   row: dict | None = None) -> dict[str, str]:
    """role -> field name, using the first candidate present in ``columns`` or ``row``."""
    out: dict[str, str] = {}
    present = set(columns or [])
    for role in mapping.domain.roles:
        for cand in mapping.candidates(role.name):
            head = cand.split(".", 1)[0]
            if row is not None:
                if get_field(row, cand) is not _MISSING:
                    out[role.name] = cand
                    break
            elif cand in present or (("." in cand) and head in present):
                out[role.name] = cand
                break
    if mapping.time_override:
        for cand in mapping.candidates("__time__"):
            if row is not None and get_field(row, cand) is not _MISSING:
                out["__time__"] = cand
            elif row is None and cand.split(".", 1)[0] in present:
                out["__time__"] = cand
    return out


def role_value(row: dict, fields: dict[str, str], role: str) -> Any:
    name = fields.get(role)
    if not name:
        return _MISSING
    return get_field(row, name)


def _text(value: Any) -> str:
    if value is _MISSING or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def cron_schedule(row: dict, fields: dict[str, str]) -> str | None:
    event = _text(role_value(row, fields, "event"))
    if event:
        return event
    parts = [_text(role_value(row, fields, r)) for r in ("minute", "hour", "dom", "month", "dow")]
    return " ".join(parts) if any(parts) else None


# ----------------------------------------------------------------------- addresses

def split_endpoint(value: Any) -> tuple[str | None, int | None]:
    """``(address, port)`` from an address field that may carry a port (``[::1]:22``)."""
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if text.startswith("[") and "]" in text:
        addr, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        return addr, int(port) if port.isdigit() else None
    if text.count(":") == 1:
        addr, _, port = text.partition(":")
        if port.isdigit():
            return addr, int(port)
    return text, None


def normalize_address(value: Any) -> str | None:
    addr, _ = split_endpoint(value)
    if not addr:
        return None
    try:
        return str(ipaddress.ip_address(addr))
    except ValueError:
        return addr.lower()


def is_listening(state: Any) -> bool:
    return isinstance(state, str) and state.strip().upper() in ("LISTEN", "LISTENING")


# ----------------------------------------------------------------------- derived values

def _norm_command(value: str) -> str:
    return " ".join(value.split())


def _norm_path(value: str) -> str:
    text = value.strip()
    if text.endswith(" (deleted)"):
        text = text[: -len(" (deleted)")]
    return text


def _bounded_key(full: str) -> str:
    """Entity key of at most ``MAX_ENTITY_KEY`` characters that stays unique for long values.

    Values that fit are used as-is; longer ones keep a readable prefix and the digest of the
    whole normalized value, so two values sharing a long prefix never merge.
    """
    if len(full) <= MAX_ENTITY_KEY:
        return full
    digest = hashlib.sha256(full.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{full[:MAX_ENTITY_KEY - 72]}…sha256:{digest}"


def _shown(full: str) -> str:
    return full if len(full) <= MAX_ENTITY_KEY else full[:MAX_ENTITY_KEY - 1] + "…"


_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")


def entities(mapping: Mapping, row: dict, fields: dict[str, str]) -> list[tuple[str, str, str]]:
    """``(kind, key, display)`` entity keys of one row, for prevalence and Where else seen.

    A canonical entity key identifies *what* is compared across hosts; it is never an
    evidence reference. Values that cannot be keyed reliably are skipped.
    """
    out: list[tuple[str, str, str]] = []
    kinds = mapping.domain.entities
    if not isinstance(row, dict) or not kinds:
        return out

    def val(role):
        v = role_value(row, fields, role)
        return v if v is not _MISSING else None

    if "executable_path" in kinds:
        exe = val("exe")
        if isinstance(exe, str) and exe.strip() and exe.strip() != "-":
            full = _norm_path(exe)
            out.append(("executable_path", _bounded_key(full), _shown(full)))
    if "file_path" in kinds:
        path = val("path")
        if isinstance(path, str) and path.strip():
            full = _norm_path(path)
            out.append(("file_path", _bounded_key(full), _shown(full)))
    if "file_hash" in kinds:
        digest = val("hash")
        if isinstance(digest, str) and _HEX_RE.match(digest.strip()):
            out.append(("file_hash", digest.strip().lower(), digest.strip().lower()))
    if "command" in kinds:
        cmd = val("cmdline")
        if isinstance(cmd, str) and cmd.strip():
            full = _norm_command(cmd)
            out.append(("command", _bounded_key(full), _shown(full)))
    if "privileged_account" in kinds:
        uid = val("uid")
        name = val("account")
        if uid is not None and str(uid).strip() == "0" and isinstance(name, str) and name.strip():
            out.append(("privileged_account", _bounded_key(name.strip()),
                        _shown(name.strip())))
    if "service" in kinds:
        unit = val("unit")
        if isinstance(unit, str) and unit.strip():
            out.append(("service", _bounded_key(unit.strip()), _shown(unit.strip())))
    if "cron_entry" in kinds:
        cmd = val("command")
        if isinstance(cmd, str) and cmd.strip():
            user = _text(val("user")).strip()
            full = _norm_command(cmd)
            out.append(("cron_entry", _bounded_key(f"{user}␟{full}"),
                        _shown(f"{user or '?'}: {full}")))
    if "network_destination" in kinds and not is_listening(val("state")):
        addr = normalize_address(val("raddr"))
        _, inline_port = split_endpoint(val("raddr"))
        port = val("rport")
        port = inline_port if port is None else port
        if addr and port not in (None, "", 0, "0"):
            try:
                ip = ipaddress.ip_address(addr)
                usable = not (ip.is_unspecified or ip.is_loopback)
            except ValueError:
                usable = False
            if usable:
                shown = f"[{addr}]:{port}" if ":" in addr else f"{addr}:{port}"
                out.append(("network_destination", shown, shown))
    if "ssh_key" in kinds:
        key = val("key")
        if isinstance(key, str) and key.strip():
            digest = "sha256:" + hashlib.sha256(key.strip().encode()).hexdigest()[:32]
            comment = _text(val("comment"))
            out.append(("ssh_key", digest, f"{_text(val('keytype'))} {comment}".strip()
                        or digest))
    return out


def entity_fields(mapping: Mapping, fields: dict[str, str]) -> set[str]:
    """Entity kinds this source can carry, given which role fields it has."""
    need = {"executable_path": ("exe",), "file_path": ("path",), "file_hash": ("hash",),
            "command": ("cmdline",), "privileged_account": ("uid", "account"),
            "service": ("unit",), "cron_entry": ("command",),
            "network_destination": ("raddr",), "ssh_key": ("key",)}
    return {k for k in mapping.domain.entities if all(r in fields for r in need[k])}


def title(mapping: Mapping, row: dict, fields: dict[str, str]) -> str:
    if not isinstance(row, dict):
        return _text(row)[:MAX_TITLE]
    d = mapping.domain.id

    def t(role):
        return _text(role_value(row, fields, role)).strip()

    if d == "process":
        name = t("name") or posixpath.basename(t("exe")) or "process"
        pid = t("pid")
        text = f"{name} (PID {pid})" if pid else name
    elif d == "network":
        local = _endpoint(t("laddr"), t("lport"))
        remote = _endpoint(t("raddr"), t("rport"))
        state = t("state")
        proto = t("proto")
        text = " ".join(x for x in (proto, local, ("→ " + remote) if remote and not
                                     is_listening(state) else "", f"({state})" if state else "")
                        if x)
    elif d == "user":
        uid = t("uid")
        text = t("account") + (f" (UID {uid})" if uid else "")
    elif d == "service":
        state = "/".join(x for x in (t("active"), t("sub")) if x)
        text = t("unit") + (f" ({state})" if state else "")
    elif d == "cron":
        text = f"{t('user') or '?'}: {t('command')}"
    elif d == "package":
        text = " ".join(x for x in (t("name"), t("version")) if x)
    elif d == "ssh_key":
        text = " ".join(x for x in (t("user"), t("keytype"), t("comment")) if x)
    elif mapping.domain.title:
        text = " ".join(t(r) for r in mapping.domain.title if t(r))
    else:
        text = ""
        for key, value in row.items():
            if isinstance(value, str) and value.strip():
                text = f"{key}: {value.strip()}"
                break
        if not text and row:
            key = next(iter(row))
            text = f"{key}: {_text(row[key])}"
    return " ".join(text.split())[:MAX_TITLE]


def _endpoint(addr: str, port: str) -> str:
    if not addr:
        return ""
    if port and port not in ("0",) and not addr.endswith(f":{port}"):
        return f"[{addr}]:{port}" if ":" in addr and not addr.startswith("[") else f"{addr}:{port}"
    return addr


def event_time(mapping: Mapping, row: dict, fields: dict[str, str]
               ) -> tuple[ParsedTime, dict | None]:
    """Event time of a row from the explicit mapping, with its parsing basis."""
    assume_utc = mapping.assume_utc
    rules = mapping.event_rules()
    if not rules:
        return ParsedTime("unmapped"), None
    last: tuple[ParsedTime, dict | None] = (ParsedTime("missing"), None)
    for rule in rules:
        name = fields.get(rule.role)
        basis = {"field": name, "format": rule.fmt, "meaning": rule.meaning,
                 "mapping": mapping.describe()["basis"], "version": NORMALIZER_VERSION,
                 "assume_utc": assume_utc}
        if not name:
            last = (ParsedTime("missing", detail="mapped field absent"), basis)
            continue
        parsed = parse_time(get_field(row, name), rule.fmt, assume_utc=assume_utc)
        if parsed.ok:
            return parsed, basis
        last = (parsed, basis)
    return last


def kinds_for(mapping: Mapping, fields: dict[str, str]) -> dict[str, str]:
    """field name -> renderer kind for mapped role fields."""
    out = {}
    for role in mapping.domain.roles:
        name = fields.get(role.name)
        if name:
            out[name] = role.kind
    return out


def column_plan(mapping: Mapping, fields: dict[str, str], all_columns: list[str]) -> dict:
    """Default (5-7) and remaining columns for a source table.

    Mapped role fields come first. ``schedule`` is a display-only virtual column for cron.
    Generic sources show their first six columns in original order.
    """
    default: list[dict] = []
    if mapping.domain.columns:
        for role in mapping.domain.columns:
            if role == "schedule":
                if any(r in fields for r in ("minute", "event")):
                    default.append({"key": "__schedule", "label": "Schedule",
                                    "kind": "schedule", "virtual": True})
                continue
            if role in fields:
                r = mapping.domain.role(role)
                default.append({"key": fields[role], "label": r.label, "kind": r.kind,
                                "role": role})
    if not default:
        default = [{"key": c, "label": c, "kind": "text"} for c in all_columns[:6]]
    shown = {c["key"] for c in default}
    rest = [{"key": c, "label": c, "kind": kinds_for(mapping, fields).get(c, "text")}
            for c in all_columns if c not in shown]
    return {"default": default[:7], "other": default[7:] + rest}
