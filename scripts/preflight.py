"""Read-only installation preflight for the Velociraptor DFIR AI preview offline bundle.

Run it on the target server from the extracted release, before installing:

    sudo bash preflight.sh [--mode auto|fresh|upgrade] [--dest DIR] [--account NAME]
                           [--port 8766] [--staging-port 18766] [--unit vr-dfir-ai-preview]

It checks the operating system, architecture, glibc, Python and its required modules, the
release files and the offline dependency set (resolved, hash-checked, with this interpreter),
the release and destination paths, and loopback port conflicts. It also reports the existing
preview installation, the HTTPS gateway configuration and neighbouring services.

It changes nothing: it reads files, /proc and service metadata, briefly test-binds loopback
ports, requests loopback status URLs, and creates a throwaway virtualenv in a temporary
directory. Configuration and endpoint text is printed as inert data, never executed.
Run with sudo to see listener owners and to read the existing preview's environment file.

install-preview.sh runs the install-time subset (--phase install) as the installing account.
The script runs on Python 3.8+ so that it can report an unsupported interpreter.
Exit status: 0 ready (warnings allowed), 1 at least one failure, 2 usage error.
"""

import argparse
import glob
import hashlib
import http.client
import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

UNIT = "vr-dfir-ai-preview"
INTERNAL_UNIT = "velociraptor-dfir-agent"
ACCOUNT = "vrdfir"
INTERNAL_PORT = 8765
NEED_PYTHON = (3, 12)
MIN_GLIBC = (2, 17)
MIN_FREE = 300 * 1024 * 1024
ENV_KEYS = ("VR_DFIR_STATE_DIR", "VR_DFIR_UI_HOST", "VR_DFIR_UI_PORT", "VELOCIRAPTOR_API_CONFIG",
            "VELOCIRAPTOR_ORG_ID", "VELOCIRAPTOR_MODEL_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL")
NEIGHBOUR_PORTS = ((INTERNAL_PORT, "internal analyst UI"), (8089, "Velociraptor GUI"), (11434, "Ollama"))
NEIGHBOUR_UNITS = (INTERNAL_UNIT, "velociraptor_server", "velociraptor", "ollama", "nginx")
WILDCARDS = ("0.0.0.0", "::")  # noqa: S104 - recognized in listings, never bound
NGINX_FILES = ("/etc/nginx/nginx.conf", "/etc/nginx/conf.d/*.conf", "/etc/nginx/sites-enabled/*")
STATE_WALK_LIMIT = 2_000_000


class Report:
    def __init__(self, out=sys.stdout):
        self.items = []
        self.out = out

    def add(self, status, check, message):
        self.items.append((status, check, message))
        print(f"[{status}] {check:<17} {message}", file=self.out, flush=True)

    def count(self, status):
        return sum(1 for item in self.items if item[0] == status)


def clean(value, limit=200):
    """Render configuration or endpoint text as inert, single-line data."""
    text = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" if ord(ch) < 256
                   else f"\\u{ord(ch):04x}" for ch in str(value))
    return text if len(text) <= limit else text[:limit] + "..."


def human(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(size)


def run(args, timeout=120, env=None):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def read_text(path, limit=1_000_000):
    with open(path, encoding="utf-8", errors="replace") as stream:
        return stream.read(limit)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        block = stream.read(1024 * 1024)
        while block:
            digest.update(block)
            block = stream.read(1024 * 1024)
    return digest.hexdigest()


# ------------------------------------------------------------------ platform and Python


def parse_os_release(text):
    data = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def glibc_version():
    try:
        value = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (ValueError, OSError, AttributeError):
        value = ""
    match = re.match(r"glibc (\d+)\.(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def check_platform(report):
    try:
        info = parse_os_release(read_text("/etc/os-release"))
    except OSError:
        info = {}
    name = clean(info.get("PRETTY_NAME") or "unknown operating system")
    if info.get("ID") == "ubuntu" and info.get("VERSION_ID") == "24.04":
        report.add("PASS", "os", name)
    else:
        report.add("WARN", "os", f"{name}; this package was prepared for Ubuntu 24.04. Other "
                   "glibc-based Linux systems with CPython 3.12 may work but are not the target")
    system, machine = platform.system(), platform.machine()
    if system == "Linux" and machine == "x86_64":
        report.add("PASS", "arch", "Linux x86_64")
    else:
        report.add("FAIL", "arch", f"{clean(system)} {clean(machine)}; the bundled wheels are "
                   "for Linux x86_64 only")
    libc = glibc_version()
    if libc is None:
        report.add("FAIL", "glibc", "glibc not detected; the bundled manylinux wheels need glibc")
    elif libc < MIN_GLIBC:
        report.add("FAIL", "glibc", f"{libc[0]}.{libc[1]}; the bundled wheels need >= 2.17")
    else:
        report.add("PASS", "glibc", f"{libc[0]}.{libc[1]} (bundled wheels need >= 2.17)")


def check_python(report):
    version = platform.python_version()
    impl = platform.python_implementation()
    ok = impl == "CPython" and sys.version_info[:2] == NEED_PYTHON and sys.maxsize > 2**32
    if ok:
        report.add("PASS", "python", f"CPython {version} at {sys.executable}")
    else:
        report.add("FAIL", "python", f"{impl} {version} at {clean(sys.executable)}; the bundle "
                   "needs 64-bit CPython 3.12 (Ubuntu 24.04: /usr/bin/python3.12); choose one with "
                   "PYTHON=/path/to/python3.12 bash preflight.sh")
    found, missing = [], []
    for module in ("venv", "sqlite3", "ssl", "zlib", "zipfile", "hashlib", "json"):
        try:
            imported = __import__(module)
        except ImportError:
            missing.append(module)
            continue
        if module == "sqlite3":
            found.append(f"sqlite3 {imported.sqlite_version}")
        elif module == "ssl":
            found.append(f"ssl ({imported.OPENSSL_VERSION})")
        else:
            found.append(module)
    if missing:
        report.add("FAIL", "python.modules", f"missing: {', '.join(missing)}; found: "
                   f"{', '.join(found)}")
    else:
        report.add("PASS", "python.modules", ", ".join(found))
    try:
        __import__("ensurepip")
        report.add("INFO", "python.ensurepip", "available (not needed; the bundled pip is used)")
    except ImportError:
        report.add("INFO", "python.ensurepip", "not installed (python3.12-venv); not needed, "
                   "the bundled pip is used")
    return ok


# ------------------------------------------------------------------ release and dependencies


def load_release(release, report):
    try:
        info = json.loads(read_text(os.path.join(release, "RELEASE.json")))
        name, version = info["name"], info["version"]
    except (OSError, ValueError, KeyError, TypeError):
        report.add("FAIL", "release", f"{clean(release)} is not an extracted preview release "
                   "(RELEASE.json missing or invalid)")
        return None
    report.add("INFO", "release", f"{clean(name)} (version {clean(version)}, source "
               f"{clean(info.get('source_commit', 'unknown'))[:12]})")
    return info


def verify_sums(release):
    """Return (checked, problems) for every file listed in SHA256SUMS."""
    problems, checked, listed = [], 0, set()
    try:
        lines = read_text(os.path.join(release, "SHA256SUMS")).splitlines()
    except OSError:
        return 0, ["SHA256SUMS is missing"]
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            problems.append(f"malformed line: {clean(line, 80)}")
            continue
        digest, rel = match.groups()
        if rel.startswith("/") or ".." in rel.split("/"):
            problems.append(f"unsafe path: {clean(rel, 80)}")
            continue
        listed.add(rel)
        path = os.path.join(release, rel)
        try:
            actual = sha256_file(path)
        except OSError:
            problems.append(f"missing or unreadable: {clean(rel, 80)}")
            continue
        checked += 1
        if actual != digest:
            problems.append(f"checksum mismatch: {clean(rel, 80)}")
    for root, _dirs, files in os.walk(release):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), release).replace(os.sep, "/")
            if rel != "SHA256SUMS" and rel not in listed:
                problems.append(f"not in SHA256SUMS: {clean(rel, 80)}")
    return checked, problems


def check_release(release, info, python_ok, report):
    checked, problems = verify_sums(release)
    if problems:
        shown = "; ".join(problems[:5]) + (f"; and {len(problems) - 5} more" if
                                           len(problems) > 5 else "")
        report.add("FAIL", "release.files", f"{len(problems)} problem(s): {shown}. Transfer "
                   "and extract the archive again")
        return
    report.add("PASS", "release.files", f"{checked} files match SHA256SUMS")
    if not python_ok:
        report.add("INFO", "release.deps", "not checked: needs CPython 3.12")
        return
    wheelhouse = os.path.join(release, info.get("wheelhouse", "wheelhouse/cp312"))
    lock = os.path.join(release, info.get("lock", "requirements-cp312.txt"))
    pips = glob.glob(os.path.join(wheelhouse, "pip-*-py3-none-any.whl"))
    if len(pips) != 1 or not os.path.isfile(lock):
        report.add("FAIL", "release.deps", "bundled pip wheel or dependency lock is missing")
        return
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PIP_", "PYTHON"))}
    env["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="vrdfir-preflight-") as tmp:
        venv = os.path.join(tmp, "venv")
        made = run([sys.executable, "-I", "-m", "venv", "--without-pip", venv], env=env)
        if made.returncode != 0:
            tail = clean((made.stderr or made.stdout).strip().splitlines()[-1:] or ["no output"])
            report.add("FAIL", "venv", f"python3.12 -m venv --without-pip failed: {tail}")
            return
        report.add("PASS", "venv", "a virtualenv can be created without ensurepip")
        report_path = os.path.join(tmp, "report.json")
        resolved = run([os.path.join(venv, "bin", "python"), "-I", os.path.join(pips[0], "pip"),
                        "install", "--dry-run", "--isolated", "--disable-pip-version-check",
                        "--no-input", "--no-cache-dir", "--no-index", "--only-binary=:all:",
                        "--require-hashes", "--find-links", wheelhouse, "--find-links",
                        os.path.join(release, "dist"), "-r", lock, "--quiet",
                        "--report", report_path], env=env, timeout=300)
        if resolved.returncode != 0:
            lines = [line for line in (resolved.stderr or resolved.stdout).splitlines()
                     if line.strip()]
            report.add("FAIL", "release.deps", "the locked dependency set does not install "
                       f"offline with this interpreter: {clean(' | '.join(lines[-3:]), 400)}")
            return
        try:
            count = len(json.loads(read_text(report_path, 50_000_000))["install"])
        except (OSError, ValueError, KeyError):
            count = "all"
        report.add("PASS", "release.deps", f"{count} locked packages resolve offline for this "
                   "interpreter, hash-checked (pip --no-index --require-hashes)")


# ------------------------------------------------------------------ paths and accounts


def nearest_existing(path):
    path = os.path.abspath(path)
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _unescape_mount(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def mount_for(path, mounts_text):
    """(mount point, options) of the filesystem holding path, from /proc/self/mounts text."""
    real = os.path.realpath(nearest_existing(path))
    best = ("", [])
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        point = _unescape_mount(parts[1])
        inside = point == "/" or real == point or real.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best[0]):
            best = (point, parts[3].split(","))
    return best


def account_ids(name):
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return None
    return entry.pw_uid, set(os.getgrouplist(name, entry.pw_gid))


def may_access(path, uid, gids, want):
    """POSIX mode-bit check of r/w/x for uid and gids (ACLs are not evaluated)."""
    st = os.stat(path)
    if uid == 0:
        return "x" not in want or os.path.isdir(path) or bool(st.st_mode & 0o111)
    if st.st_uid == uid:
        bits = (st.st_mode >> 6) & 7
    elif st.st_gid in gids:
        bits = (st.st_mode >> 3) & 7
    else:
        bits = st.st_mode & 7
    need = (4 if "r" in want else 0) | (2 if "w" in want else 0) | (1 if "x" in want else 0)
    return bits & need == need


def traversable(path, uid, gids):
    """The first ancestor directory of path that uid cannot traverse, or None."""
    current = os.path.abspath(path)
    chain = []
    while True:
        chain.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for directory in reversed(chain[1:]):
        if not may_access(directory, uid, gids, "x"):
            return directory
    return None


def check_paths(report, release, dest, account, phase, extra_space=0):
    if not os.path.isabs(dest) or "\n" in dest or "\r" in dest:
        report.add("FAIL", "dest", "the destination must be an absolute path without line breaks")
        return
    if os.path.lexists(dest):
        report.add("FAIL", "dest", f"{clean(dest)} already exists; the installer never "
                   "overwrites an installation. Inspect it or choose a new directory")
    else:
        report.add("PASS", "dest", f"{clean(dest)} does not exist yet")
    parent = os.path.dirname(dest)
    base = nearest_existing(parent)
    try:
        mounts = read_text("/proc/self/mounts")
    except OSError:
        mounts = ""
    point, options = mount_for(base, mounts)
    if "noexec" in options:
        report.add("FAIL", "dest.mount", f"{clean(point)} is mounted noexec; the virtualenv's "
                   "compiled modules cannot load there. Choose a destination on another "
                   "filesystem")
    elif point:
        report.add("PASS", "dest.mount", f"{clean(point)} allows execution")
    free = shutil.disk_usage(base).free
    need = MIN_FREE + extra_space
    status = "PASS" if free >= need else "FAIL"
    report.add(status, "dest.space", f"{human(free)} free at {clean(base)} (needs about "
               f"{human(need)})")
    if phase == "install":
        if not os.access(base, os.W_OK | os.X_OK):
            report.add("FAIL", "dest.parent", f"{clean(base)} is not writable by this account "
                       f"({clean(pwd.getpwuid(os.getuid()).pw_name)}), so {clean(dest)} cannot be "
                       "created")
        return
    ids = account_ids(account)
    if ids is None:
        report.add("INFO", "dest.parent", f"account {clean(account)} does not exist yet; "
                   f"create it and {clean(parent)} as in INSTALL.md")
        return
    uid, gids = ids
    if not os.path.isdir(parent):
        report.add("INFO", "dest.parent", f"{clean(parent)} does not exist yet; create it for "
                   f"{clean(account)}: sudo install -d -m 0750 -o {clean(account)} -g "
                   f"{clean(account)} {clean(parent)}")
    elif may_access(parent, uid, gids, "wx") and traversable(parent, uid, gids) is None:
        report.add("PASS", "dest.parent", f"{clean(parent)} is writable by {clean(account)}")
    else:
        report.add("FAIL", "dest.parent", f"{clean(parent)} is not writable by "
                   f"{clean(account)}: sudo install -d -m 0750 -o {clean(account)} -g "
                   f"{clean(account)} {clean(parent)}")
    blocked = traversable(release, uid, gids)
    unreadable = []
    for root, _dirs, files in os.walk(release):
        if not may_access(root, uid, gids, "rx"):
            unreadable.append(root)
        unreadable += [os.path.join(root, f) for f in files
                       if not may_access(os.path.join(root, f), uid, gids, "r")]
    if blocked or unreadable:
        where = blocked or unreadable[0]
        report.add("FAIL", "release.access", f"{clean(account)} cannot read the release at "
                   f"{clean(where)}; extract it into a directory such as "
                   "/opt/vr-dfir-ai-preview-releases (mode 0755)")
    else:
        report.add("PASS", "release.access", f"{clean(account)} can read the release directory")


# ------------------------------------------------------------------ services and ports


def systemd_show(unit):
    if not shutil.which("systemctl"):
        return None
    result = run(["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,User,"
                  "WorkingDirectory,ExecStart,FragmentPath,DropInPaths"], timeout=20)
    props = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props if props.get("LoadState") == "loaded" else {}


def exec_option(exec_start, option):
    match = re.search(r"(?:^|\s)" + re.escape(option) + r"[ =]([^\s;]+)", exec_start or "")
    return match.group(1) if match else None


def read_env_keys(path):
    """Allow-listed keys only; other lines (which may hold secrets) are never read out."""
    values = {}
    for line in read_text(path).splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() in ENV_KEYS:
            values[key.strip()] = value.strip().strip("'\"")
    return values


def parse_proc_net(text, ipv6):
    listeners = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != "0A":
            continue
        addr_hex, port_hex = fields[1].split(":")
        raw = bytes.fromhex(addr_hex)
        if ipv6:
            raw = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
            addr = socket.inet_ntop(socket.AF_INET6, raw)
            if addr.startswith("::ffff:") and "." in addr:
                addr = addr[7:]
        else:
            addr = socket.inet_ntop(socket.AF_INET, raw[::-1])
        listeners.append({"addr": addr, "port": int(port_hex, 16), "uid": int(fields[7]),
                          "inode": int(fields[9]), "pids": [], "units": [], "comm": []})
    return listeners


def socket_listeners():
    listeners = []
    for path, ipv6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            listeners += parse_proc_net(read_text(path, 50_000_000), ipv6)
        except OSError:
            continue
    wanted = {f"socket:[{item['inode']}]": item for item in listeners}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                item = wanted.get(os.readlink(f"/proc/{pid}/fd/{fd}"))
            except OSError:
                continue
            if item is None or int(pid) in item["pids"]:
                continue
            item["pids"].append(int(pid))
            try:
                item["comm"].append(read_text(f"/proc/{pid}/comm").strip())
                units = re.findall(r"/([^/\s]+\.service)\s*$", read_text(f"/proc/{pid}/cgroup"),
                                   re.M)
                item["units"] += [u for u in units if u not in item["units"]]
            except OSError:
                continue
    return listeners


def describe(item):
    host = f"[{item['addr']}]" if ":" in item["addr"] else item["addr"]
    try:
        user = pwd.getpwuid(item["uid"]).pw_name
    except KeyError:
        user = str(item["uid"])
    if item["pids"]:
        owner = (f"{clean(','.join(sorted(set(item['comm']))))} pid "
                 f"{','.join(map(str, item['pids'][:3]))}")
        if item["units"]:
            owner += f", {clean(','.join(item['units']))}"
    else:
        owner = "owner unknown (run with sudo)"
    return f"{host}:{item['port']} ({owner}, user {clean(user)})"


def loopback_free(port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        return True, ""
    except OSError as exc:
        return False, os.strerror(exc.errno) if exc.errno else str(exc)
    finally:
        probe.close()


def status_version(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://127.0.0.1:{int(port)}/api/v1/status"
    try:
        with opener.open(url, timeout=3) as response:  # noqa: S310 - fixed loopback URL
            data = json.loads(response.read(1_000_000).decode("utf-8", "replace"))
    except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError):
        return None
    version = (data.get("data") or {}).get("version") if isinstance(data, dict) else None
    return clean(version, 40) if version else None


def check_port(report, listeners, port, role, mode, unit):
    """role: 'preview' (must be free, or held by the existing preview in upgrade mode)
    or 'staging' (must be free)."""
    check = f"port.{role}"
    on_port = [item for item in listeners if item["port"] == port]
    for item in on_port:
        if item["addr"] not in WILDCARDS + ("127.0.0.1", "::1"):
            report.add("INFO", check, f"{describe(item)} listens on a specific address (for "
                       "example the HTTPS gateway); compatible with the loopback listener")
    free, reason = loopback_free(port)
    if free:
        report.add("PASS", check, f"127.0.0.1:{port} is free")
        return
    holders = [item for item in on_port if item["addr"] in WILDCARDS + ("127.0.0.1",)]
    text = "; ".join(describe(item) for item in holders) or f"in use ({reason})"
    preview_held = any(f"{unit}.service" in item["units"] for item in holders)
    unknown_owner = holders and all(not item["pids"] for item in holders)
    version = status_version(port)
    if role == "preview" and mode == "upgrade" and preview_held:
        report.add("PASS", check, f"127.0.0.1:{port} is held by the existing preview "
                   f"({text}); it keeps running until the service is switched to the new "
                   "installation")
    elif role == "preview" and mode == "upgrade" and unknown_owner and version:
        report.add("WARN", check, f"127.0.0.1:{port} answers as preview {version}, but its owner "
                   "is not visible; run the preflight with sudo to confirm it is the "
                   f"{unit} service")
    else:
        hint = (" A wildcard listener blocks the application's loopback address; give that "
                "service an explicit address." if any(i["addr"] in WILDCARDS for i in holders)
                else "")
        answered = f" It answers as preview {version}." if version else ""
        if role == "staging":
            hint += (" Stop it, or pick another free loopback port with --staging-port and use "
                     "that port for the staging run.")
        report.add("FAIL", check, f"127.0.0.1:{port} is not available: {text}.{answered}{hint}")


def inspect_existing(report, unit, props, port):
    """Report the existing preview service; return (state_dir, state_bytes)."""
    if props is None:
        report.add("INFO", "existing", "systemd is not available; no service inspected")
        return None, 0
    if not props:
        report.add("INFO", "existing", f"no {unit} service is installed")
        return None, 0
    workdir = props.get("WorkingDirectory") or ""
    exec_start = props.get("ExecStart") or ""
    report.add("INFO", "existing", f"{unit}: {clean(props.get('ActiveState'))}/"
               f"{clean(props.get('SubState'))}, user {clean(props.get('User') or 'root')}, "
               f"directory {clean(workdir or 'unset')}")
    report.add("INFO", "existing.unit", f"{clean(props.get('FragmentPath'))}; drop-ins: "
               f"{clean(props.get('DropInPaths') or 'none')}")
    if not props.get("User"):
        report.add("WARN", "existing.user", f"{unit} has no User= and runs as root; use a "
                   "dedicated account for the new installation")
    versions = sorted(os.path.basename(p)[len("velociraptor_dfir_agent-"):-len(".dist-info")]
                      for p in glob.glob(os.path.join(
                          workdir, "venv/lib/python3*/site-packages/velociraptor_dfir_agent-*"
                          ".dist-info")))
    running = status_version(int(exec_option(exec_start, "--port") or port))
    report.add("INFO", "existing.version", f"installed {clean(', '.join(versions) or 'unknown')}"
               f"; running {running or 'not answering'}")
    env_file = exec_option(exec_start, "--env-file")
    if not env_file:
        report.add("WARN", "existing.env", "the service has no --env-file; check its "
                   "Environment= settings by hand (systemctl cat)")
        return None, 0
    try:
        values = read_env_keys(env_file)
    except OSError as exc:
        report.add("WARN", "existing.env", f"{clean(env_file)} is not readable "
                   f"({clean(exc.strerror)}); run the preflight with sudo")
        return None, 0
    shown = ", ".join(f"{k}={clean(values[k], 120)}" for k in ENV_KEYS if k in values)
    report.add("INFO", "existing.env", f"{clean(env_file)}: {shown or 'no known keys'}")
    api = values.get("VELOCIRAPTOR_API_CONFIG")
    if api:
        try:
            mode = os.stat(api).st_mode
            world = " (world-readable: restrict it to the service account)" if mode & 0o004 else ""
            report.add("WARN" if world else "INFO", "existing.api",
                       f"API client file {clean(api)} exists{world}; it is reused, not copied")
        except OSError:
            report.add("WARN", "existing.api", f"API client file {clean(api)} is not visible "
                       "(missing, or run with sudo)")
    state = values.get("VR_DFIR_STATE_DIR")
    if not state:
        report.add("WARN", "existing.state", "VR_DFIR_STATE_DIR is not set in the environment "
                   "file; find the state directory before upgrading")
        return None, 0
    if not os.path.isabs(state):
        state = os.path.join(workdir, state)
    if not os.path.isdir(state):
        report.add("WARN", "existing.state", f"state directory {clean(state)} does not exist")
        return None, 0
    size, count, capped, started = 0, 0, False, time.monotonic()
    for root, _dirs, files in os.walk(state):
        for name in files:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
            count += 1
        if count > STATE_WALK_LIMIT or time.monotonic() - started > 60:
            capped = True
            break
    investigations = len(glob.glob(os.path.join(state, "investigations", "*")))
    report.add("INFO", "existing.state", f"{clean(state)}: {'at least ' if capped else ''}"
               f"{human(size)} in {count} files, {investigations} investigation(s)")
    return state, size


def check_internal_overlap(report, preview_state, dest):
    props = systemd_show(INTERNAL_UNIT)
    if not props:
        return
    env_file = exec_option(props.get("ExecStart"), "--env-file")
    try:
        internal = read_env_keys(env_file).get("VR_DFIR_STATE_DIR") if env_file else None
    except OSError:
        internal = None
    workdir = os.path.realpath(props.get("WorkingDirectory") or "/nonexistent")
    if internal and preview_state and os.path.realpath(internal) == os.path.realpath(
            preview_state):
        report.add("FAIL", "existing.state", "the preview and the internal UI use the same "
                   "state directory; do not upgrade until they are separated")
    if os.path.realpath(dest).startswith(workdir.rstrip("/") + "/"):
        report.add("FAIL", "dest", f"{clean(dest)} is inside the internal UI's directory "
                   f"{clean(workdir)}")


def check_neighbours(report, listeners):
    for port, role in NEIGHBOUR_PORTS:
        found = [describe(item) for item in listeners if item["port"] == port]
        report.add("INFO", f"port.{port}", f"{role}: {'; '.join(found) or 'no listener'} "
                   "(not changed)")
    for unit in NEIGHBOUR_UNITS:
        props = systemd_show(unit)
        if props:
            report.add("INFO", "service", f"{unit}: {clean(props.get('ActiveState'))} "
                       "(not changed)")


def scan_gateway(report, port, files=None):
    paths = []
    for pattern in files or NGINX_FILES:
        paths += sorted(glob.glob(pattern))
    listens, origins = [], set()
    for path in paths:
        try:
            lines = read_text(path).splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            match = re.search(r"\blisten\s+([^;]+);", code)
            if match:
                spec = match.group(1).split()[0]
                host, _, listen_port = spec.rpartition(":")
                if listen_port in (str(port), str(INTERNAL_PORT)):
                    listens.append((path, number, spec, host))
            for origin in re.findall(r'"https://[^"|]+:(\d+)\|(\d+)"\s+1\s*;', code):
                origins.add(origin[1])
    if not paths:
        report.add("INFO", "gateway", "no Nginx configuration found; LAN access needs the "
                   "gateway in docs/lan-access.md (or an SSH tunnel)")
        return
    if not listens:
        report.add("INFO", "gateway", f"no Nginx listen lines for {INTERNAL_PORT}/{port}; add the "
                   "gateway (docs/lan-access.md) if LAN access is wanted")
        return
    for path, number, spec, host in listens:
        wildcard = host in ("", "*", "0.0.0.0", "[::]")  # noqa: S104 - config text, not a bind
        report.add("WARN" if wildcard else "INFO", "gateway",
                   f"{clean(path)}:{number}: listen {clean(spec)}" +
                   (" - wildcard listen blocks the loopback backend; use the explicit LAN "
                    "address" if wildcard else ""))
    if any(spec.endswith(f":{port}") for _p, _n, spec, _h in listens) and str(port) not in origins:
        report.add("WARN", "gateway", f"a gateway listens on {port} but no Origin map entry "
                   f'"https://<server>:{port}|{port}" 1; was found; browser actions would get 403')


def check_tools(report):
    missing = [tool for tool in ("systemctl", "sha256sum", "tar", "curl", "sudo")
               if not shutil.which(tool)]
    if missing:
        report.add("WARN", "tools", f"not found: {', '.join(missing)} (used by INSTALL.md)")
    else:
        report.add("PASS", "tools", "systemctl, sha256sum, tar, curl, sudo")


# ------------------------------------------------------------------ main


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--phase", choices=("full", "install"), default="full")
    parser.add_argument("--mode", choices=("auto", "fresh", "upgrade"), default="auto")
    parser.add_argument("--release", default=here, help="extracted release directory")
    parser.add_argument("--dest", help="installation directory (default "
                        "/opt/vr-dfir-ai-preview/<version>)")
    parser.add_argument("--account", help=f"service account (default: the existing unit's "
                        f"User=, else {ACCOUNT})")
    parser.add_argument("--unit", default=UNIT, help="existing preview service name")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--staging-port", type=int, default=18766)
    args = parser.parse_args(argv)
    if args.port == args.staging_port:
        parser.error("--port and --staging-port must differ")
    release = os.path.abspath(args.release)
    report = Report()
    running_as = pwd.getpwuid(os.getuid()).pw_name
    print(f"Velociraptor DFIR AI preview preflight ({args.phase}), running as "
          f"{clean(running_as)}; nothing is changed.")
    info = load_release(release, report) or {}
    dest = args.dest or f"/opt/vr-dfir-ai-preview/{info.get('version', 'unknown')}"
    check_platform(report)
    python_ok = check_python(report)
    if info:
        check_release(release, info, python_ok, report)
    if args.phase == "install":
        check_paths(report, release, dest, running_as, "install")
        return finish(report, None, None)
    props = systemd_show(args.unit)
    mode = args.mode if args.mode != "auto" else ("upgrade" if props else "fresh")
    if mode == "upgrade" and not props:
        report.add("FAIL", "existing", f"--mode upgrade, but no {args.unit} service exists")
    if os.getuid() != 0:
        report.add("WARN", "privileges", "not running as root: listener owners and private "
                   "files may be hidden. Run: sudo bash preflight.sh")
    state, state_size = inspect_existing(report, args.unit, props, args.port)
    account = args.account or (props or {}).get("User") or ACCOUNT
    report.add("INFO", "account", f"{clean(account)} "
               f"{'exists' if account_ids(account) else 'does not exist yet'}")
    check_paths(report, release, dest, account, "full",
                extra_space=2 * state_size if mode == "upgrade" else 0)
    check_internal_overlap(report, state, dest)
    if state and mode == "upgrade":
        backups = nearest_existing("/var/backups")
        free = shutil.disk_usage(backups).free
        report.add("PASS" if free > state_size + MIN_FREE else "WARN", "backup.space",
                   f"{human(free)} free at {clean(backups)} for the state archive "
                   f"(state is {human(state_size)})")
    listeners = socket_listeners()
    check_port(report, listeners, args.port, "preview", mode, args.unit)
    check_port(report, listeners, args.staging_port, "staging", mode, args.unit)
    check_neighbours(report, listeners)
    scan_gateway(report, args.port)
    check_tools(report)
    return finish(report, mode, props)


def finish(report, mode, props):
    failures, warnings = report.count("FAIL"), report.count("WARN")
    if failures:
        print(f"\nResult: NOT READY - {failures} failure(s), {warnings} warning(s). Resolve every "
              "[FAIL] line, then run the preflight again.")
        return 1
    if mode == "upgrade":
        path = "replacing the existing preview (upgrade, Path B)"
    elif mode == "fresh":
        path = "a fresh installation (Path A)"
    else:
        path = "installation"
    print(f"\nResult: READY for {path} - 0 failures, {warnings} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
