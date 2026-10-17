"""Operator-controlled exact-artifact allowlist for automated profile collection.

Safety is not inferred from ``type: client`` or from the absence of a dangerous-tool
label. If no allowlist is configured, automated collection is refused; planning and
analysis of existing evidence still work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from vr_dfir.config import describe_file_problem, probe_file
from vr_dfir.errors import ConfigurationError
from vr_dfir.ids import ARTIFACT_NAME_RE


@dataclass
class Allowlist:
    artifacts: frozenset[str] = field(default_factory=frozenset)
    source: str = "not configured"
    configured: bool = False

    def allows(self, artifact: str) -> bool:
        return artifact in self.artifacts

    def to_dict(self) -> dict:
        return {"configured": self.configured, "source": self.source,
                "artifacts": sorted(self.artifacts)}


def load_allowlist(path: str | None) -> Allowlist:
    if not path:
        return Allowlist()
    p = Path(path).expanduser()
    state = probe_file(p)
    if state == "missing":
        raise ConfigurationError(
            f"Collection allowlist file not found: {p}",
            next_action="Create it or unset VR_DFIR_COLLECTION_ALLOWLIST.")
    if state != "file":
        raise ConfigurationError(
            f"Collection allowlist {p} {describe_file_problem(state)}.",
            next_action="Give the service account read access or unset "
                        "VR_DFIR_COLLECTION_ALLOWLIST.")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigurationError(f"Collection allowlist {p} could not be read "
                                 f"({exc.strerror or type(exc).__name__}).") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Collection allowlist is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ConfigurationError("Collection allowlist must be a mapping with 'version: 1'.")
    names = data.get("allowed_artifacts") or []
    if not isinstance(names, list):
        raise ConfigurationError("'allowed_artifacts' must be a list of exact artifact names.")
    bad = [n for n in names if not isinstance(n, str) or not ARTIFACT_NAME_RE.fullmatch(n)]
    if bad:
        raise ConfigurationError(f"Invalid artifact names in allowlist: {bad[:5]}")
    return Allowlist(frozenset(names), str(p), True)
