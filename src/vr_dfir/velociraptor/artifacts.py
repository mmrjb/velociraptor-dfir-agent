"""Artifact metadata model and namespace-independent discovery.

Metadata comes from ``artifact_definitions()`` (permission ``READ_RESULTS``). The
row shape follows the ``Artifact`` protobuf (Velociraptor v0.77.3). Discovery never
executes an artifact or its precondition; precondition text is returned as
metadata only.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from vr_dfir.errors import ValidationError

ARTIFACT_TYPES = ("client", "client_event", "server", "server_event", "internal", "notebook")
TYPE_FILTERS = ARTIFACT_TYPES + ("any",)
MAX_REGEX_LENGTH = 200
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 2000

# Parameter types whose semantics are applied by Velociraptor's artifact compiler
# (services/launcher/parameters.go, v0.77.3).
LOCALLY_VALIDATED_TYPES = {
    "", "string", "regex", "yara", "int", "int64", "integer", "float", "bool", "choices",
    "multichoice", "json", "json_array", "regex_array", "csv", "timestamp", "hidden",
}
NOT_LOCALLY_VALIDATED_TYPES = {
    "upload", "upload_file", "server_metadata", "starlark", "xml", "yaml", "artifactset",
    "redacted",
}

_OS_HINT_RE = re.compile(r"\bOS\s*(?:=~|=)\s*['\"]\^?(linux|windows|darwin|freebsd)", re.I)


@dataclass
class ParameterMeta:
    name: str
    type: str = "string"
    default: str | None = None
    description: str = ""
    friendly_name: str = ""
    choices: list[str] = field(default_factory=list)
    validating_regex: str = ""
    artifact_type: str = ""

    @property
    def validation_support(self) -> str:
        if self.type in LOCALLY_VALIDATED_TYPES:
            return "local"
        if self.type in NOT_LOCALLY_VALIDATED_TYPES:
            return "server_only"
        return "unknown_type"


@dataclass
class SourceMeta:
    name: str | None
    description: str = ""
    has_precondition: bool = False
    precondition: str = ""

    @property
    def display_name(self) -> str:
        return self.name or "(default)"


@dataclass
class ArtifactMetadata:
    name: str
    type: str
    description: str = ""
    author: str = ""
    sources: list[SourceMeta] = field(default_factory=list)
    parameters: list[ParameterMeta] = field(default_factory=list)
    precondition: str = ""
    required_permissions: list[str] = field(default_factory=list)
    implied_permissions: list[str] = field(default_factory=list)
    resources: dict = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    built_in: bool | None = None
    is_alias: bool | None = None
    hidden: bool | None = None
    tags: list[str] = field(default_factory=list)
    synthetic: bool = False

    @property
    def source_names(self) -> list[str | None]:
        return [s.name for s in self.sources]

    @property
    def named_sources(self) -> list[str]:
        return [s.name for s in self.sources if s.name]

    def parameter(self, name: str) -> ParameterMeta | None:
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def compatibility(self) -> dict:
        """Compatibility information with its limitations. Never executes the precondition."""
        texts = [self.precondition] + [s.precondition for s in self.sources]
        hints = sorted({m.group(1).lower() for t in texts if t for m in _OS_HINT_RE.finditer(t)})
        return {
            "has_artifact_precondition": bool(self.precondition),
            "sources_with_precondition": [s.display_name for s in self.sources if s.has_precondition],
            "os_hints_from_precondition_text": hints,
            "limitations": (
                "OS hints are parsed from precondition text only; the precondition is not "
                "executed. A name or namespace is not proof of OS compatibility."
            ),
        }

    def summary(self, details: bool = False) -> dict:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "sources": [s.display_name for s in self.sources],
            "parameter_names": [p.name for p in self.parameters],
        }
        if self.synthetic:
            out["synthetic"] = True
        if details:
            out["author"] = self.author
            out["parameters"] = [
                {**asdict(p), "validation_support": p.validation_support} for p in self.parameters
            ]
            out["source_details"] = [
                {"name": s.display_name, "description": s.description,
                 "has_precondition": s.has_precondition, "precondition": s.precondition}
                for s in self.sources
            ]
            out["precondition"] = self.precondition
            out["compatibility"] = self.compatibility()
            out["required_permissions"] = self.required_permissions
            out["implied_permissions"] = self.implied_permissions
            out["resources"] = self.resources
            out["tools"] = self.tools
            out["built_in"] = self.built_in
            out["hidden"] = self.hidden
            out["tags"] = self.tags
            out["collection_behavior"] = self.collection_behavior()
        return out

    def collection_behavior(self) -> list[str]:
        """Facts from metadata that matter before collecting. Not a safety verdict."""
        notes = []
        if self.tools:
            notes.append(f"Declares external tools ({', '.join(self.tools)}); the client may "
                         "download them.")
        if self.required_permissions:
            notes.append("Requires permissions: " + ", ".join(self.required_permissions) + ".")
        if self.resources:
            notes.append("Declares default resource limits: "
                         + ", ".join(f"{k}={v}" for k, v in sorted(self.resources.items())) + ".")
        if any(s.name and s.name.lower().startswith("upload") for s in self.sources):
            notes.append("Has an upload-named source; it may upload files to the server.")
        return notes


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_artifact(row: dict, *, synthetic: bool = False) -> ArtifactMetadata:
    """Build metadata from one ``artifact_definitions()`` row."""
    if not isinstance(row, dict) or not row.get("name"):
        raise ValidationError("Artifact definition row has no name.")
    params = []
    for p in _as_list(row.get("parameters")):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        params.append(ParameterMeta(
            name=str(p["name"]),
            type=str(p.get("type") or "string").strip().lower(),
            default=None if p.get("default") is None else str(p.get("default")),
            description=str(p.get("description") or ""),
            friendly_name=str(p.get("friendly_name") or ""),
            choices=[str(c) for c in _as_list(p.get("choices"))],
            validating_regex=str(p.get("validating_regex") or ""),
            artifact_type=str(p.get("artifact_type") or ""),
        ))
    sources = []
    for s in _as_list(row.get("sources")):
        if not isinstance(s, dict):
            continue
        pre = str(s.get("precondition") or "")
        sources.append(SourceMeta(
            name=(str(s["name"]) if s.get("name") else None),
            description=str(s.get("description") or ""),
            has_precondition=bool(pre),
            precondition=pre,
        ))
    resources = row.get("resources") if isinstance(row.get("resources"), dict) else {}
    resources = {k: v for k, v in resources.items() if v not in (None, 0, 0.0, "")}
    tools = []
    for t in _as_list(row.get("tools")):
        if isinstance(t, dict) and t.get("name"):
            tools.append(str(t["name"]))
        elif isinstance(t, str):
            tools.append(t)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return ArtifactMetadata(
        name=str(row["name"]),
        type=str(row.get("type") or "client").strip().lower(),
        description=str(row.get("description") or "").strip(),
        author=str(row.get("author") or ""),
        sources=sources,
        parameters=params,
        precondition=str(row.get("precondition") or ""),
        required_permissions=[str(x) for x in _as_list(row.get("required_permissions"))],
        implied_permissions=[str(x) for x in _as_list(row.get("implied_permissions"))],
        resources=resources,
        tools=tools,
        built_in=row.get("built_in"),
        is_alias=row.get("is_alias"),
        hidden=metadata.get("hidden"),
        tags=[str(x) for x in _as_list(metadata.get("tags"))],
        synthetic=synthetic or bool(row.get("_synthetic")),
    )


def compile_name_filter(name_regex: str | None) -> re.Pattern | None:
    """Compile a user regex for artifact names (case-insensitive like VQL ``=~``)."""
    if name_regex is None or name_regex in ("", "."):
        return None
    if len(name_regex) > MAX_REGEX_LENGTH:
        raise ValidationError(f"Name filter is longer than {MAX_REGEX_LENGTH} characters.")
    try:
        return re.compile(name_regex, re.IGNORECASE)
    except re.error as exc:
        raise ValidationError(
            f"Invalid name regex {name_regex!r}: {exc}.",
            next_action="Use a regular expression such as '^LTH\\.' or use exact-name lookup.",
        ) from exc


def check_type_filter(artifact_type: str) -> str:
    value = (artifact_type or "client").strip().lower()
    if value not in TYPE_FILTERS:
        raise ValidationError(
            f"Unknown artifact type filter {artifact_type!r}. Use one of: {', '.join(TYPE_FILTERS)}.")
    return value


def filter_catalog(catalog: list[ArtifactMetadata], *, name_regex: str | None = None,
                   artifact_type: str = "client", text: str | None = None,
                   limit: int = DEFAULT_LIST_LIMIT, offset: int = 0,
                   details: bool = False) -> dict:
    """Deterministic, bounded catalog filtering (ordered by name)."""
    pattern = compile_name_filter(name_regex)
    type_filter = check_type_filter(artifact_type)
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_LIST_LIMIT}.")
    if offset < 0:
        raise ValidationError("offset must not be negative.")
    needle = (text or "").strip().lower()
    matched = []
    for art in sorted(catalog, key=lambda a: a.name.lower()):
        if type_filter != "any" and art.type != type_filter:
            continue
        if pattern is not None and not pattern.search(art.name):
            continue
        if needle and needle not in art.name.lower() and needle not in art.description.lower():
            continue
        matched.append(art)
    page = matched[offset: offset + limit]
    return {
        "artifacts": [a.summary(details) for a in page],
        "total_matched": len(matched),
        "returned": len(page),
        "offset": offset,
        "truncated": offset + len(page) < len(matched),
        "ordering": "name ascending (case-insensitive)",
        "filters": {"name_regex": name_regex or None, "type": type_filter, "text": text or None},
        "regex_semantics": "Python re, case-insensitive (Velociraptor =~ is case-insensitive)",
    }


# Legacy OS-specific discovery (upstream tool scope): name regex applied unanchored except
# Windows, and a *regex* type filter "client" (which also matches client_event), as upstream.
LEGACY_SCOPES = {
    "windows": r"^windows\.",
    "linux": r"linux\.",
    "macos": r"macos\.",
}


def legacy_summarize(catalog: list[ArtifactMetadata], scope: str, name_regex: str = ".",
                     include_parameter_details: bool = False) -> list[dict]:
    scope_re = re.compile(LEGACY_SCOPES[scope], re.IGNORECASE)
    name_re = compile_name_filter(name_regex)

    def shorten(desc: str) -> str:
        return desc.strip().split(".")[0][:120].rstrip() + "..." if desc else ""

    out = []
    for art in sorted(catalog, key=lambda a: a.name):
        if "client" not in art.type:
            continue
        if not scope_re.search(art.name):
            continue
        if name_re is not None and not name_re.search(art.name):
            continue
        out.append({
            "name": art.name,
            "short_description": shorten(art.description),
            "parameters": (
                [asdict(p) for p in art.parameters] if include_parameter_details
                else [p.name for p in art.parameters]
            ),
        })
    return out
