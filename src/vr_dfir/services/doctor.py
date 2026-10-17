"""``doctor`` (configuration) and ``preflight`` (destination server) checks.

Neither command collects endpoint evidence, starts hunts, installs artifacts, downloads
models, or prints key material. Network probes run only when explicitly requested.
"""

from __future__ import annotations

import importlib
import os
import platform
import socket
import stat
import sys
from pathlib import Path

from vr_dfir.config import SKIPPED_IMPLICIT, Settings, describe_file_problem, probe_file
from vr_dfir.errors import DfirError
from vr_dfir.version import __version__

MIN_PYTHON = (3, 10)
CORE_MODULES = [("yaml", "PyYAML"), ("pydantic", "pydantic"), ("httpx", "httpx"),
                ("grpc", "grpcio"), ("pyvelociraptor", "pyvelociraptor")]
OPTIONAL_MODULES = [("mcp", "mcp (MCP server)"), ("fastapi", "fastapi (UI)"),
                    ("uvicorn", "uvicorn (UI)")]


def _check(name: str, status: str, detail: str, **extra) -> dict:
    return {"name": name, "status": status, "detail": detail, **extra}


def _module_version(module: str) -> str | None:
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return None
    return str(getattr(mod, "__version__", "installed"))


def _perm_issue(path: Path) -> str | None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    if mode & (stat.S_IRWXG | stat.S_IRWXO) & (stat.S_IROTH | stat.S_IWOTH):
        return "readable or writable by other users; restrict to the service account (0600/0640)"
    return None


def _api_config_check(settings: Settings) -> dict:
    path = settings.api_config_path
    if not path:
        return _check("velociraptor_api_config", "warn",
                      "Not configured. Live features are disabled; saved and imported evidence "
                      "still work. Set VELOCIRAPTOR_API_CONFIG.")
    p = Path(path)
    state = probe_file(p)
    if state != "file":
        return _check("velociraptor_api_config", "error", f"{p} {describe_file_problem(state)}.")
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _check("velociraptor_api_config", "error",
                      f"{p} could not be parsed as YAML ({type(exc).__name__}).")
    required = ("ca_certificate", "client_private_key", "client_cert", "api_connection_string")
    missing = [k for k in required if not isinstance(data, dict) or not data.get(k)]
    if missing:
        return _check("velociraptor_api_config", "error",
                      f"{p} is missing fields: {', '.join(missing)} (values are not shown).")
    issue = _perm_issue(p)
    if issue:
        return _check("velociraptor_api_config", "warn", f"{p}: {issue}.")
    return _check("velociraptor_api_config", "ok",
                  f"{p} has the required fields (source: {settings.api_config_source}; key "
                  "material is not displayed).")


def _user() -> str:
    from vr_dfir.config import current_user
    return current_user()


def _skipped_implicit_checks() -> list[dict]:
    """Implicit fallback files that exist but were skipped (informational, not errors)."""
    return [_check("implicit_config_skipped", "ok",
                   f"{path} {reason}; skipped (implicit fallback, not required)")
            for path, reason in SKIPPED_IMPLICIT]


def _state_dir_check(settings: Settings) -> dict:
    d = Path(settings.state_dir)
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = d / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return _check("state_dir", "error", f"{d} is not writable: {exc.strerror}.")
    issue = _perm_issue(d)
    return _check("state_dir", "warn" if issue else "ok",
                  f"{d.resolve()} is writable" + (f"; {issue}" if issue else "."))


def _static_assets_check() -> dict:
    from vr_dfir.web.assets import static_dir
    d = static_dir()
    if d and (d / "index.html").is_file():
        return _check("ui_assets", "ok", f"Built UI assets found ({d}).")
    return _check("ui_assets", "warn", "Built UI assets are missing; 'vr-dfir ui' will show a "
                                       "build hint. Install from the release wheel.")


def run_doctor(settings: Settings, *, probe: bool = False, probe_model: bool = False) -> dict:
    checks = [
        _check("version", "ok", f"velociraptor-dfir-agent {__version__}"),
        _check("python", "ok" if sys.version_info >= MIN_PYTHON else "error",
               f"Python {platform.python_version()} (requires >= "
               f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]})"),
    ]
    missing_core = [label for mod, label in CORE_MODULES if _module_version(mod) is None]
    checks.append(_check("core_dependencies", "error" if missing_core else "ok",
                         "missing: " + ", ".join(missing_core) if missing_core else
                         "all core dependencies import"))
    optional = {label: _module_version(mod) is not None for mod, label in OPTIONAL_MODULES}
    checks.append(_check("optional_dependencies", "ok" if all(optional.values()) else "warn",
                         ", ".join(f"{k}: {'yes' if v else 'no'}" for k, v in optional.items())))
    if settings.env_file:
        state = probe_file(Path(settings.env_file).expanduser())
        checks.append(_check("env_file", "ok" if state == "file" else "error",
                             f"{settings.env_file} "
                             + ("loaded" if state == "file" else describe_file_problem(state))
                             + " (direct environment variables take precedence)."))
    checks.extend(_skipped_implicit_checks())
    checks.append(_api_config_check(settings))
    checks.append(_check("default_org", "ok", f"{settings.default_org_id or 'root'} "
                         "(used only when a command does not pass --org-id)"))
    checks.append(_state_dir_check(settings))
    checks.append(_check("backend_access", "ok", "Read-only AI preview; backend mutations are unavailable."))
    m = settings.model
    if m.provider == "none":
        checks.append(_check("model", "ok", "Model analysis disabled (provider none)."))
    elif m.provider == "ollama":
        checks.append(_check("model", "ok" if m.ollama_model else "warn",
                             f"Ollama at {m.ollama_host}, model "
                             f"{m.ollama_model or '(not set: OLLAMA_MODEL)'}; models are never "
                             "downloaded automatically."))
    else:
        checks.append(_check("model", "error", f"Unsupported provider {m.provider!r}."))
    checks.append(_check("dangerous_tools", "warn" if settings.dangerous_tools else "ok",
                         "ENABLE_DANGEROUS_TOOLS is enabled" if settings.dangerous_tools else
                         "disabled"))
    checks.append(_static_assets_check())

    if probe:
        if not settings.api_config_path:
            checks.append(_check("velociraptor_probe", "warn", "Skipped: no API config."))
        else:
            try:
                from vr_dfir.services.runtime import live_runtime
                info = live_runtime(settings).service.probe()
                checks.append(_check("velociraptor_probe", "ok",
                                     f"Reachable in {info['latency_seconds']}s; org "
                                     f"{info['org_id']}; plugin versions "
                                     f"{info['plugin_versions']}", probe=info))
            except DfirError as exc:
                checks.append(_check("velociraptor_probe", "error", exc.message))
    if probe_model:
        try:
            from vr_dfir.analysis.providers import build_provider
            info = build_provider(m).probe()
            status = "ok" if info.get("reachable") and info.get("model_available", True) else (
                "warn" if info.get("reachable") is None else "error")
            checks.append(_check("model_probe", status, str(info), probe=info))
        except DfirError as exc:
            checks.append(_check("model_probe", "error", exc.message))
    statuses = {c["status"] for c in checks}
    result = "error" if "error" in statuses else ("warn" if "warn" in statuses else "ok")
    return {"result": result, "checks": checks, "settings": settings.public_view()}


def run_preflight(settings: Settings, *, require_ui: bool = True) -> dict:
    """Destination-server readiness. Reads local files only; no network access."""
    checks = []
    system = platform.system()
    checks.append(_check("platform", "ok" if system == "Linux" else "error",
                         f"{system} {platform.release()} {platform.machine()}"))
    checks.append(_check("python", "ok" if sys.version_info >= MIN_PYTHON else "error",
                         f"{sys.executable} {platform.python_version()} (requires >= 3.10)"))
    for mod, label in CORE_MODULES:
        v = _module_version(mod)
        checks.append(_check(f"dependency:{label}", "ok" if v else "error",
                             v or "not importable"))
    ui_mods = [("fastapi", "fastapi"), ("uvicorn", "uvicorn")]
    for mod, label in ui_mods:
        v = _module_version(mod)
        checks.append(_check(f"dependency:{label}", "ok" if v else
                             ("error" if require_ui else "warn"), v or "not importable"))
    v = _module_version("mcp")
    checks.append(_check("dependency:mcp", "ok" if v else "warn",
                         v or "not installed (MCP server unavailable; CLI/UI unaffected)"))
    checks.append(_api_config_check(settings))
    checks.append(_state_dir_check(settings))
    if settings.env_file:
        p = Path(settings.env_file)
        state = probe_file(p)
        exists = state == "file"
        issue = _perm_issue(p) if exists else None
        checks.append(_check("config_file", "ok" if exists and not issue else
                             ("warn" if exists else "error"),
                             f"{p}: " + ("present" if exists else describe_file_problem(state))
                             + (f"; {issue}" if issue else "")))
    checks.extend(_skipped_implicit_checks())
    checks.append(_check("backend_access", "ok", "Read-only AI preview; collection is unavailable."))
    if require_ui:
        checks.append(_static_assets_check())
    host, port = settings.ui_host, settings.ui_port
    checks.append(_check("ui_bind", "ok" if host in ("127.0.0.1", "localhost", "::1") else "error",
                         f"{host}:{port}" + ("" if host in ("127.0.0.1", "localhost", "::1") else
                                             " (non-loopback binding is not supported)")))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        checks.append(_check("ui_port", "ok", f"127.0.0.1:{port} is free"))
    except OSError:
        checks.append(_check("ui_port", "warn", f"127.0.0.1:{port} is in use (the service may "
                                               "already be running)"))
    checks.append(_check("user", "warn" if os.geteuid() == 0 else "ok",
                         f"running as {_user()}"
                         + (" (root: run the service as the dedicated account)"
                            if os.geteuid() == 0 else "")))
    statuses = {c["status"] for c in checks}
    result = "error" if "error" in statuses else ("warn" if "warn" in statuses else "ok")
    return {"result": result, "checks": checks}
