"""Deterministic review signals.

A signal says "an analyst should look at this row", never "this host is malicious".
Public addresses, temporary paths, UID 0 accounts, deleted executables, and keyword hits
are review signals that often have legitimate explanations. Keyword matching uses whole
tokens, so ``tor`` does not match ``monitor`` or ``editor``.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

DEFAULT_ALIASES: dict[str, list[str]] = {
    "exe": ["Exe", "Path", "AbsoluteExePath", "Binary", "ImagePath", "OSPath", "FullPath"],
    "command_line": ["CommandLine", "Commandline", "Command", "Arguments", "Cmdline"],
    "process_name": ["Name", "ProcessName"],
    "username": ["Username", "User", "UserName", "UserAccount"],
    "uid": ["Uid", "UID"],
    "homedir": ["Homedir", "HomeDir", "Home", "Directory"],
    "conn_status": ["Status", "ConnectionStatus"],
    "remote_address": ["Raddr", "RemoteAddr", "RemoteAddress", "Raddr.IP", "DestinationIP"],
    "remote_port": ["Rport", "RemotePort", "Raddr.Port", "DestinationPort"],
    "deleted": ["IsDeleted", "Deleted", "ExeDeleted"],
    "mode": ["Mode", "Permissions"],
    "file_hash": ["SHA256", "Sha256", "ExeSHA256", "Hash.SHA256"],
}

TEMP_DIRS = ("/tmp", "/var/tmp", "/dev/shm")
WINDOWS_TEMP_RE = re.compile(r"\\(windows\\temp|appdata\\local\\temp)\\", re.I)
SUSPICIOUS_TOKENS = frozenset({
    "mimikatz", "ncat", "netcat", "socat", "xmrig", "minerd", "tor", "chisel", "frpc",
    "meterpreter", "cobaltstrike", "lazagne", "psexec",
})
DOWNLOAD_TOOLS = re.compile(r"(?<![\w.-])(curl|wget|fetch)(?![\w.-])", re.I)
PIPE_TO_SHELL = re.compile(r"\|\s*(sudo\s+)?(ba|z|k|da)?sh\b", re.I)
STANDARD_SETUID_DIRS = ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/", "/usr/lib/",
                        "/usr/libexec/")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class SignalRule:
    id: str
    title: str
    severity: str
    rationale: str
    benign: tuple[str, ...]
    next_steps: tuple[str, ...]


RULES: dict[str, SignalRule] = {r.id: r for r in [
    SignalRule("exec_from_temp", "Process executable in a temporary directory", "medium",
               "The executable path is under a world-writable temporary directory.",
               ("Installer or update helper unpacked to /tmp",
                "Administrator test binary or lab exercise"),
               ("Hash the binary and compare with known software inventory",
                "Check the parent process and the account that started it")),
    SignalRule("exec_hidden_dir", "Process executable in a hidden directory", "medium",
               "The executable path contains a dot-directory component.",
               ("Per-user tool caches (for example ~/.cache, ~/.local)",),
               ("Identify the package or user that owns the directory",)),
    SignalRule("deleted_executable", "Running process whose executable was deleted", "medium",
               "The process image is marked deleted on disk.",
               ("Binary replaced during a package upgrade while the old process kept running",),
               ("Check package update history and restart times",
                "Collect the process memory or /proc/<pid>/exe if still available")),
    SignalRule("kernel_thread_mimic", "Process name resembles a kernel thread but has an executable",
               "medium",
               "Kernel worker threads have no user-space executable path.",
               ("Software that deliberately uses a similar name",),
               ("Compare with the process list of a known-good host",)),
    SignalRule("external_established", "Established connection to a non-local address", "low",
               "The remote address is not loopback, link-local, or RFC 1918/CGNAT private space. "
               "RFC 5737 documentation ranges are treated as non-local for synthetic data.",
               ("Normal client traffic of a public-facing service",
                "Software updates, monitoring, or backup services"),
               ("Identify the remote owner and whether this service should talk to it",)),
    SignalRule("cron_download_exec", "Scheduled task downloads and executes content", "medium",
               "The scheduled command pipes a download tool into a shell.",
               ("Configuration-management bootstrap or vendor update script",),
               ("Retrieve the referenced script and review its content",
                "Check when the cron file was created and by whom")),
    SignalRule("extra_uid0", "Additional account with UID 0", "medium",
               "A non-root account has UID 0 and therefore root privileges.",
               ("Deliberate break-glass or legacy administrator account",),
               ("Confirm with the system owner whether this account is approved",
                "Review authentication logs for this account")),
    SignalRule("home_in_temp", "Account home directory in a temporary location", "low",
               "The account's home directory is under a temporary directory.",
               ("Short-lived service or test account",),
               ("Check when the account was created",)),
    SignalRule("suspicious_token", "Command or path contains a tool name worth reviewing", "low",
               "A whole token matches a list of dual-use tool names.",
               ("Legitimate administrative or security-testing use",),
               ("Confirm whether the tool is approved on this host",)),
    SignalRule("setuid_nonstandard", "Set-UID file outside standard system directories", "medium",
               "The file mode has the set-UID bit and the path is outside standard binary "
               "directories.",
               ("Vendor software that intentionally ships a set-UID helper",),
               ("Identify the owning package and whether set-UID is required",)),
]}


@dataclass
class RowSignals:
    rule_ids: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        weights = {"medium": 3, "low": 1, "high": 5, "informational": 0}
        return sum(weights.get(RULES[r].severity, 1) for r in self.rule_ids)


def _get(row: dict, names: Iterable[str]):
    for name in names:
        if "." in name:
            head, _, tail = name.partition(".")
            nested = row.get(head)
            if isinstance(nested, dict) and tail in nested:
                return nested[tail]
            continue
        if name in row:
            return row[name]
    return None


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def _path_under(path: str, dirs: tuple[str, ...]) -> bool:
    p = path.strip()
    return any(p == d or p.startswith(d + "/") for d in dirs)


def is_non_local_address(value) -> bool:
    try:
        addr = ipaddress.ip_address(str(value).strip().strip("[]"))
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    if isinstance(addr, ipaddress.IPv4Address):
        for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"):
            if addr in ipaddress.ip_network(net):
                return False
        return True
    return not addr.is_private or addr in ipaddress.ip_network("2001:db8::/32")


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "yes", "y", "1"}


def evaluate_row(row: dict, aliases: dict[str, list[str]] | None = None) -> RowSignals:
    if not isinstance(row, dict):
        return RowSignals()
    al = {**DEFAULT_ALIASES, **(aliases or {})}
    hits: list[str] = []
    exe = _get(row, al["exe"])
    exe_s = str(exe) if isinstance(exe, str) else ""
    cmd = _get(row, al["command_line"])
    cmd_s = str(cmd) if isinstance(cmd, str) else ""
    name = _get(row, al["process_name"])
    name_s = str(name) if isinstance(name, str) else ""

    if exe_s:
        if _path_under(exe_s, TEMP_DIRS) or WINDOWS_TEMP_RE.search(exe_s):
            hits.append("exec_from_temp")
        parts = [p for p in exe_s.replace("\\", "/").split("/")[:-1] if p]
        if any(p.startswith(".") and p not in (".", "..") for p in parts):
            hits.append("exec_hidden_dir")
        if exe_s.endswith(" (deleted)"):
            hits.append("deleted_executable")
    deleted = _get(row, al["deleted"])
    if deleted is not None and _truthy(deleted) and "deleted_executable" not in hits and exe_s:
        hits.append("deleted_executable")
    if name_s.lower().startswith(("kworker", "kthreadd", "ksoftirqd")) and exe_s:
        hits.append("kernel_thread_mimic")

    status = _get(row, al["conn_status"])
    raddr = _get(row, al["remote_address"])
    if status is not None and str(status).upper().startswith("ESTAB") and raddr is not None \
            and is_non_local_address(raddr):
        hits.append("external_established")

    if cmd_s and DOWNLOAD_TOOLS.search(cmd_s) and PIPE_TO_SHELL.search(cmd_s):
        hits.append("cron_download_exec")

    uid = _get(row, al["uid"])
    user = _get(row, al["username"])
    if uid is not None and str(uid).strip() == "0" and user is not None \
            and str(user).strip() not in ("root", ""):
        hits.append("extra_uid0")
    home = _get(row, al["homedir"])
    if isinstance(home, str) and _path_under(home, TEMP_DIRS) and user is not None:
        hits.append("home_in_temp")

    text_tokens = _tokens(" ".join(x for x in (exe_s, cmd_s, name_s) if x))
    if text_tokens & SUSPICIOUS_TOKENS:
        hits.append("suspicious_token")

    mode = _get(row, al["mode"])
    if isinstance(mode, str) and len(mode) >= 4 and mode[3] in ("s", "S") and exe_s \
            and not exe_s.startswith(STANDARD_SETUID_DIRS):
        hits.append("setuid_nonstandard")

    seen = set()
    ordered = [h for h in hits if not (h in seen or seen.add(h))]
    return RowSignals(ordered)
