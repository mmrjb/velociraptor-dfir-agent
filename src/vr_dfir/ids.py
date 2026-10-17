"""Validation of Velociraptor identifiers and generation of safe local names.

All identifiers that reach a VQL template or the filesystem pass through these
validators. Hostnames and other evidence values are never used as raw paths.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone

from vr_dfir.errors import ValidationError

# Velociraptor IDs: client "C.<hex>" or "server", flow "F.<base32>[.H]", hunt "H.<base32>",
# org "O<...>" or "root". Patterns are deliberately permissive in length but strict in
# character class so they cannot carry VQL syntax.
CLIENT_ID_RE = re.compile(r"^(C\.[A-Za-z0-9]{1,64}|server)$")
FLOW_ID_RE = re.compile(r"^F\.[A-Za-z0-9]{1,64}(\.[A-Za-z0-9]{1,8})?$")
HUNT_ID_RE = re.compile(r"^H\.[A-Za-z0-9]{1,64}$")
ORG_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,200}$")
SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- ]{0,120}$")
PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LOCAL_ID_RE = re.compile(r"^[a-z]{2,5}-[A-Za-z0-9_-]{4,80}$")
FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _check(value: str, pattern: re.Pattern, kind: str, example: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{kind} must be a string.")
    cleaned = value.strip()
    if not pattern.fullmatch(cleaned):
        raise ValidationError(
            f"Invalid {kind}: {cleaned[:80]!r}. Expected a value like {example}.",
        )
    return cleaned


def client_id(value: str) -> str:
    return _check(value, CLIENT_ID_RE, "client ID", "C.1a2b3c4d5e6f7a8b")


def flow_id(value: str) -> str:
    return _check(value, FLOW_ID_RE, "flow ID", "F.CRUU3KIE5D73G")


def hunt_id(value: str) -> str:
    return _check(value, HUNT_ID_RE, "hunt ID", "H.CRUU3KIE5D73G")


def org_id(value: str | None) -> str:
    """Return a validated org ID. ``root`` is the default org."""
    if value is None or not str(value).strip():
        return "root"
    return _check(str(value), ORG_ID_RE, "org ID", "O123 or root")


def artifact_name(value: str) -> str:
    return _check(value, ARTIFACT_NAME_RE, "artifact name", "LTH.SystemBaseline")


def source_name(value: str) -> str:
    return _check(value, SOURCE_NAME_RE, "source name", "Processes")


def parameter_name(value: str) -> str:
    return _check(value, PARAMETER_NAME_RE, "parameter name", "ProcessRegex")


def profile_id(value: str) -> str:
    return _check(value, PROFILE_ID_RE, "profile ID", "lth-baseline")


def local_id(value: str, kind: str = "local ID") -> str:
    return _check(value, LOCAL_ID_RE, kind, "inv-20260101-120000-abc123")


def field_name(value: str) -> str:
    return _check(value, FIELD_NAME_RE, "field name", "CommandLine")


def safe_filename(value: str, *, max_length: int = 80, fallback: str = "item") -> str:
    """Map an arbitrary string to a safe, readable file name component.

    A short hash suffix keeps distinct inputs distinct after sanitisation.
    """
    text = str(value)
    cleaned = _SAFE_NAME_RE.sub("_", text).strip("._") or fallback
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    cleaned = cleaned[: max(1, max_length - 9)]
    return f"{cleaned}-{digest}"


def new_local_id(prefix: str) -> str:
    """Create a sortable local identifier such as ``inv-20260101-120000-3fa9c1``."""
    if not re.fullmatch(r"[a-z]{2,5}", prefix):
        raise ValueError("prefix must be 2-5 lowercase letters")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"
