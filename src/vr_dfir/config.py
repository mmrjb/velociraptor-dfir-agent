"""Configuration loading with upstream-compatible precedence.

Precedence (highest first):

1. Variables already present in the process environment (shell, systemd, MCP client).
2. The dotenv file named by ``VELOCIRAPTOR_ENV_FILE``.
3. ``./.env`` in the current working directory.
4. Built-in defaults.

Dotenv files never override existing variables. Secrets are never logged;
:meth:`Settings.public_view` is the only representation sent to a browser or
printed by ``doctor``.

Implicit fallbacks (``./.env``, ``./api_client.yaml``, ``~/.config/api_client.yaml``) are
optional: when one cannot be inspected (for example the service account runs from a
root-private directory) it is skipped and recorded in :data:`SKIPPED_IMPLICIT`. A file that
was requested explicitly (``VELOCIRAPTOR_ENV_FILE`` / ``--env-file``) must be readable;
otherwise :class:`~vr_dfir.errors.ConfigurationError` explains why.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from vr_dfir.errors import ConfigurationError

TRUE_VALUES = {"1", "true", "yes", "on", "y"}

SUPPORTED_PROVIDERS = ("ollama", "none")

# Implicit fallback files that exist but could not be inspected or read: (path, reason).
SKIPPED_IMPLICIT: list[tuple[str, str]] = []


def current_user() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return str(os.getuid())


def probe_file(path: Path | str) -> str:
    """State of a configuration file without raising.

    ``"file"`` (a readable regular file), ``"missing"``, ``"not_file"``, ``"inaccessible"``
    (a parent directory cannot be searched), or ``"unreadable"`` (no read permission).
    """
    p = Path(path)
    try:
        mode = p.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except OSError:
        return "inaccessible"
    if not stat.S_ISREG(mode):
        return "not_file"
    if not os.access(p, os.R_OK):
        return "unreadable"
    return "file"


_STATE_TEXT = {
    "missing": "does not exist",
    "not_file": "is not a regular file",
    "inaccessible": "is in a directory that this account ({user}) cannot access",
    "unreadable": "is not readable by this account ({user})",
}


def describe_file_problem(state: str) -> str:
    return _STATE_TEXT.get(state, state).format(user=current_user())


def _note_skipped(path: Path, reason: str) -> None:
    entry = (str(path), reason)
    if entry not in SKIPPED_IMPLICIT:
        SKIPPED_IMPLICIT.append(entry)


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key or not key.replace("_", "").isalnum():
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def _read_explicit(p: Path) -> str:
    state = probe_file(p)
    if state != "file":
        raise ConfigurationError(
            f"The configuration file {p} {describe_file_problem(state)}.",
            next_action=("Check the path given with --env-file or VELOCIRAPTOR_ENV_FILE."
                         if state in ("missing", "not_file") else
                         "Give the service account read access (for example root:vrdfir, "
                         "mode 0640) or point --env-file at a readable copy."),
            details={"path": str(p), "state": state})
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"The configuration file {p} is not UTF-8 text.") from exc
    except OSError as exc:
        raise ConfigurationError(
            f"The configuration file {p} could not be read "
            f"({exc.strerror or type(exc).__name__}).", details={"path": str(p)}) from exc


def _read_implicit(p: Path) -> str | None:
    state = probe_file(p)
    if state == "missing":
        return None
    if state != "file":
        _note_skipped(p, describe_file_problem(state))
        return None
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _note_skipped(p, f"could not be read ({type(exc).__name__})")
        return None


def load_env_file(path: Path | str, environ: dict | None = None, *,
                  explicit: bool = False) -> list[str]:
    """Load ``KEY=value`` lines without overriding existing variables.

    Returns the keys that were set. Values are never logged. An ``explicit`` file must be
    readable (:class:`ConfigurationError` otherwise); an implicit fallback that cannot be
    read is skipped and recorded in :data:`SKIPPED_IMPLICIT`.
    """
    env = os.environ if environ is None else environ
    p = Path(path).expanduser()
    text = _read_explicit(p) if explicit else _read_implicit(p)
    if text is None:
        return []
    loaded = []
    for line in text.splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in env:
            env[key] = value
            loaded.append(key)
    return loaded


_ENV_LOADED = False


def load_environment(force: bool = False) -> None:
    """Load dotenv files once per process using the documented precedence."""
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    configured = os.environ.get("VELOCIRAPTOR_ENV_FILE", "").strip()
    if configured:
        load_env_file(configured, explicit=True)
    try:
        cwd = Path.cwd()
    except OSError:  # the working directory was removed or cannot be resolved
        cwd = None
    if cwd is not None:
        load_env_file(cwd / ".env")
    _ENV_LOADED = True


def _flag(env: dict, name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in TRUE_VALUES


def _int(env: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _float(env: dict, name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


@dataclass
class ModelSettings:
    provider: str = "ollama"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    timeout_seconds: float = 120.0
    num_ctx: int = 8192
    max_output_tokens: int = 1024
    temperature: float = 0.0
    max_input_chars: int = 12000
    max_requests: int = 6

    @property
    def model_name(self) -> str:
        if self.provider == "ollama":
            return self.ollama_model
        return ""

    @property
    def is_hosted(self) -> bool:
        return False


@dataclass
class Settings:
    api_config_path: str = ""
    api_config_source: str = "unset"
    default_org_id: str = ""
    state_dir: Path = field(default_factory=lambda: Path("~/.local/state/vr-dfir-ai-preview").expanduser())
    profile_dirs: list[Path] = field(default_factory=list)
    allowlist_path: str = ""
    dangerous_tools: bool = False
    debug_vql: bool = False
    grpc_timeout_seconds: int = 60
    ui_host: str = "127.0.0.1"
    ui_port: int = 8766
    import_max_bytes: int = 50 * 1024 * 1024
    env_file: str = ""
    workspace_config: str = ""
    model: ModelSettings = field(default_factory=ModelSettings)
    # Presentation: operator source mappings (JSON file), internal network ranges for display
    # labels, and the capture age after which a source is shown as stale.
    source_mappings_path: str = ""
    internal_networks: str = ""
    stale_after_days: int = 7

    def public_view(self) -> dict:
        """Configuration status safe to show in a browser or terminal (no secrets)."""
        return {
            "velociraptor": {
                "api_config_configured": bool(self.api_config_path),
                "api_config_path": self.api_config_path or None,
                "api_config_source": self.api_config_source,
                "api_config_readable": bool(self.api_config_path)
                and os.access(self.api_config_path, os.R_OK),
                "default_org_id": self.default_org_id or "root",
                "api_timeout_seconds": self.grpc_timeout_seconds,
            },
            "model": {
                "provider": self.model.provider,
                "model": self.model.model_name or None,
                "endpoint": (
                    self.model.ollama_host if self.model.provider == "ollama"
                    else None
                ),
                "hosted": self.model.is_hosted,
                "timeout_seconds": self.model.timeout_seconds,
                "num_ctx": self.model.num_ctx,
                "max_output_tokens": self.model.max_output_tokens,
                "max_input_chars": self.model.max_input_chars,
                "max_requests": self.model.max_requests,
            },
            "state_dir": str(self.state_dir),
            "allowlist_path": self.allowlist_path or None,
            "workspace_config": self.workspace_config or None,
            "dangerous_tools_enabled": self.dangerous_tools,
            "env_file": self.env_file or None,
            "presentation": {
                "source_mappings_path": self.source_mappings_path or None,
                "internal_networks": self.internal_networks or None,
                "stale_after_days": self.stale_after_days,
            },
        }


def resolve_api_config(env: dict) -> tuple[str, str]:
    explicit = str(env.get("VELOCIRAPTOR_API_CONFIG", "")).strip()
    if explicit:
        # Readability of an explicit path is reported by doctor and on first use.
        return str(Path(explicit).expanduser()), "VELOCIRAPTOR_API_CONFIG"
    candidates = [(Path("api_client.yaml"), "./api_client.yaml")]
    try:
        candidates.append((Path.home() / ".config" / "api_client.yaml",
                           "~/.config/api_client.yaml"))
    except (RuntimeError, KeyError):  # no resolvable home directory for this account
        pass
    for candidate, label in candidates:
        state = probe_file(candidate)
        if state == "file":
            try:
                return str(candidate.resolve()), label
            except OSError:
                return str(candidate.absolute()), label
        if state != "missing":
            _note_skipped(candidate, describe_file_problem(state))
    return "", "unset"


def load_settings(environ: dict | None = None, *, load_dotenv: bool = True) -> Settings:
    if load_dotenv and environ is None:
        load_environment()
    env = dict(os.environ if environ is None else environ)
    api_path, api_source = resolve_api_config(env)
    provider = str(env.get("VELOCIRAPTOR_MODEL_PROVIDER", "ollama")).strip().lower() or "ollama"
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError("The AI preview supports only ollama or none; hosted model providers are unavailable.")
    model = ModelSettings(
        provider=provider,
        ollama_host=str(env.get("OLLAMA_HOST", "http://127.0.0.1:11434")).strip()
        or "http://127.0.0.1:11434",
        ollama_model=str(env.get("OLLAMA_MODEL", "")).strip(),
        timeout_seconds=_float(env, "VR_DFIR_MODEL_TIMEOUT_SECONDS", 120.0),
        num_ctx=_int(env, "VR_DFIR_MODEL_NUM_CTX", 8192, 512, 1_048_576),
        max_output_tokens=_int(env, "VR_DFIR_MODEL_MAX_OUTPUT_TOKENS", 1024, 64, 32768),
        temperature=_float(env, "VR_DFIR_MODEL_TEMPERATURE", 0.0),
        max_input_chars=_int(env, "VR_DFIR_MAX_MODEL_INPUT_CHARS", 12000, 1000, 2_000_000),
        max_requests=_int(env, "VR_DFIR_MAX_MODEL_REQUESTS", 6, 1, 50),
    )
    if not model.ollama_host.startswith(("http://", "https://")):
        model.ollama_host = "http://" + model.ollama_host
    profile_dirs = [
        Path(p).expanduser() for p in str(env.get("VR_DFIR_PROFILE_DIRS", "")).split(":") if p.strip()
    ]
    return Settings(
        api_config_path=api_path,
        api_config_source=api_source,
        default_org_id=str(env.get("VELOCIRAPTOR_ORG_ID", "")).strip(),
        state_dir=Path(str(env.get("VR_DFIR_STATE_DIR", "~/.local/state/vr-dfir-ai-preview"))).expanduser(),
        profile_dirs=profile_dirs,
        allowlist_path=str(env.get("VR_DFIR_COLLECTION_ALLOWLIST", "")).strip(),
        dangerous_tools=False,
        debug_vql=_flag(env, "VELOCIRAPTOR_DEBUG_VQL"),
        grpc_timeout_seconds=_int(env, "VR_DFIR_GRPC_TIMEOUT_SECONDS", 60, 5, 3600),
        ui_host=str(env.get("VR_DFIR_UI_HOST", "127.0.0.1")).strip() or "127.0.0.1",
        ui_port=_int(env, "VR_DFIR_UI_PORT", 8766, 1, 65535),
        import_max_bytes=_int(env, "VR_DFIR_IMPORT_MAX_BYTES", 50 * 1024 * 1024, 1024,
                              2 * 1024 * 1024 * 1024),
        env_file=str(env.get("VELOCIRAPTOR_ENV_FILE", "")).strip(),
        workspace_config="",
        model=model,
        source_mappings_path=str(Path(str(env["VR_DFIR_SOURCE_MAPPINGS"]).strip()).expanduser())
        if str(env.get("VR_DFIR_SOURCE_MAPPINGS", "")).strip() else "",
        internal_networks=_networks(env.get("VR_DFIR_INTERNAL_NETWORKS", "")),
        stale_after_days=_int(env, "VR_DFIR_STALE_AFTER_DAYS", 7, 1, 3650),
    )


def _networks(value) -> str:
    """Validated comma-separated CIDR list (display labels only)."""
    from vr_dfir.analysis.networks import parse_internal
    text = str(value or "").strip()
    parse_internal(text)
    return text


# Keys that must never be copied into logs, model requests, or browser responses.
SECRET_KEYS = frozenset({
    "AZURE_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "client_private_key",
    "ca_certificate",
    "client_cert",
})


def child_process_env(base: dict | None = None) -> dict:
    """Environment for a child MCP bridge: inherits the effective configuration.

    Dotenv values loaded into this process are already in ``os.environ`` so the
    child sees the same precedence result. Nothing is printed.
    """
    load_environment()
    env = dict(os.environ if base is None else base)
    return env
