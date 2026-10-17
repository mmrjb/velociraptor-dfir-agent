"""Investigation profile schema (version 1).

Profiles are declarative data. Every model forbids unknown fields, so a profile cannot
smuggle in execution-bearing keys (``query``, ``vql``, ``command``, ``callback`` ...).
String values are checked for template syntax. Profiles are loaded with
``yaml.safe_load`` only.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vr_dfir.ids import ARTIFACT_NAME_RE, PARAMETER_NAME_RE, PROFILE_ID_RE, SOURCE_NAME_RE

SCHEMA_VERSION = 1
PLATFORMS = ("linux", "windows", "macos", "freebsd", "any")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TEMPLATE_RE = re.compile(r"(\{\{|\{%|\$\{|<%)")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")

ScalarParam = str | int | float | bool
ParamValue = ScalarParam | list[ScalarParam]


def _no_templates(value: str, where: str) -> str:
    if _TEMPLATE_RE.search(value):
        raise ValueError(
            f"{where} contains template syntax ({{{{, {{%, ${{ or <%). Profiles are data; "
            "template evaluation is not supported.")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CollectionLimits(_Strict):
    collection_timeout_seconds: Annotated[int, Field(ge=10, le=86400)] = 600
    """Server-side query timeout passed to collect_client(timeout=...)."""
    max_collection_rows: Annotated[int, Field(ge=1, le=100_000_000)] | None = None
    """Optional server-side row limit passed to collect_client(max_rows=...)."""
    max_upload_bytes: Annotated[int, Field(ge=1, le=10**13)] | None = None
    """Optional server-side upload limit passed to collect_client(max_bytes=...)."""
    wait_timeout_seconds: Annotated[int, Field(ge=10, le=172800)] = 660
    """Application wait deadline. Expiry stops local waiting only; it does not cancel."""
    poll_interval_seconds: Annotated[float, Field(ge=0.05, le=300)] = 3
    page_size: Annotated[int, Field(ge=1, le=10_000)] = 500
    max_rows_per_source: Annotated[int, Field(ge=1, le=5_000_000)] = 10_000
    max_bytes_per_source: Annotated[int, Field(ge=1024, le=10**10)] = 50_000_000
    urgent: bool = False


class Collection(_Strict):
    id: str
    artifact: str
    required: bool = True
    parameters: dict[str, ParamValue] = Field(default_factory=dict)
    sources: list[str] | Literal["discover"] = "discover"
    behavior_notes: list[str] = Field(default_factory=list)
    limits: CollectionLimits = Field(default_factory=CollectionLimits)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID_RE.fullmatch(v):
            raise ValueError("collection id must be lowercase letters, digits, '-' or '_'")
        return v

    @field_validator("artifact")
    @classmethod
    def _artifact(cls, v: str) -> str:
        if not ARTIFACT_NAME_RE.fullmatch(v):
            raise ValueError("artifact must be an exact Velociraptor artifact name")
        return v

    @field_validator("parameters")
    @classmethod
    def _params(cls, v: dict) -> dict:
        for key, value in v.items():
            if not PARAMETER_NAME_RE.fullmatch(key):
                raise ValueError(f"invalid parameter name {key!r}")
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str):
                    _no_templates(item, f"parameter {key}")
        return v

    @field_validator("sources")
    @classmethod
    def _sources(cls, v):
        if v == "discover":
            return v
        if not v:
            raise ValueError("sources must be 'discover' or a non-empty list")
        seen = set()
        for name in v:
            if not SOURCE_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid source name {name!r}")
            if name in seen:
                raise ValueError(f"duplicate source {name!r}")
            seen.add(name)
        return v

    @field_validator("behavior_notes")
    @classmethod
    def _notes(cls, v: list[str]) -> list[str]:
        return [_no_templates(n, "behavior note") for n in v]

    @model_validator(mode="after")
    def _wait(self):
        if self.limits.wait_timeout_seconds < self.limits.collection_timeout_seconds:
            # Allowed, but planning reports it as a warning.
            pass
        return self


class Analyst(_Strict):
    id: str
    objective: str
    inputs: list[str]
    """Collection IDs, or ``collection/Source`` references."""

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID_RE.fullmatch(v):
            raise ValueError("analyst id must be lowercase letters, digits, '-' or '_'")
        return v

    @field_validator("objective")
    @classmethod
    def _objective(cls, v: str) -> str:
        if not v or len(v) > 2000:
            raise ValueError("objective must be 1-2000 characters")
        return _no_templates(v, "analyst objective")

    @field_validator("inputs")
    @classmethod
    def _inputs(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("analyst inputs must not be empty")
        return v


class AnalysisSettings(_Strict):
    counts_only_sources: list[str] = Field(default_factory=list)
    """Source names (or ``collection/Source``) whose rows are not supplied to the model."""
    max_model_input_chars: Annotated[int, Field(ge=1000, le=2_000_000)] = 12000
    max_output_tokens: Annotated[int, Field(ge=64, le=32768)] = 1024
    max_model_requests: Annotated[int, Field(ge=1, le=50)] = 6
    max_rows_per_source_to_model: Annotated[int, Field(ge=1, le=5000)] = 40
    max_field_chars: Annotated[int, Field(ge=20, le=20000)] = 300
    sampling: Literal["signals_then_stratified", "stratified", "head"] = "signals_then_stratified"
    model_input_fields: dict[str, list[str]] = Field(default_factory=dict)
    """Per-source field selection for the model view (source name -> field names)."""
    field_aliases: dict[str, list[str]] = Field(default_factory=dict)
    """Logical name -> candidate column names, used by deterministic review signals."""
    event_time_fields: list[str] = Field(default_factory=lambda: [
        "EventTime", "Timestamp", "StartTime", "Mtime", "Time", "_ts"])

    @field_validator("model_input_fields", "field_aliases")
    @classmethod
    def _fields(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for key, fields in v.items():
            if not SOURCE_NAME_RE.fullmatch(key.split("/")[-1]) and not _FIELD_RE.fullmatch(key):
                raise ValueError(f"invalid key {key!r}")
            for f in fields:
                if not _FIELD_RE.fullmatch(f):
                    raise ValueError(f"invalid field name {f!r}")
        return v


class RetrievalSettings(_Strict):
    max_total_rows: Annotated[int, Field(ge=1, le=50_000_000)] = 200_000
    max_total_bytes: Annotated[int, Field(ge=1024, le=10**11)] = 500_000_000
    max_concurrent_reads: Annotated[int, Field(ge=1, le=8)] = 2
    read_retries: Annotated[int, Field(ge=0, le=5)] = 2
    retry_backoff_seconds: Annotated[float, Field(ge=0, le=60)] = 1.0


class Profile(_Strict):
    schema_version: int
    id: str
    name: str
    description: str = ""
    purpose: str = ""
    platforms: list[str]
    objective: str = ""
    guidance: str = ""
    collections: list[Collection]
    analysts: list[Analyst] = Field(default_factory=list)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v}; this release supports {SCHEMA_VERSION}")
        return v

    @field_validator("id")
    @classmethod
    def _pid(cls, v: str) -> str:
        if not PROFILE_ID_RE.fullmatch(v):
            raise ValueError("profile id must be lowercase letters, digits, '-' or '_' (max 64)")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v or len(v) > 120:
            raise ValueError("name must be 1-120 characters")
        return _no_templates(v, "name")

    @field_validator("description", "purpose", "objective", "guidance")
    @classmethod
    def _text(cls, v: str) -> str:
        if len(v) > 4000:
            raise ValueError("text fields must be at most 4000 characters")
        return _no_templates(v, "text field")

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("platforms must not be empty")
        bad = [p for p in v if p not in PLATFORMS]
        if bad:
            raise ValueError(f"unsupported platforms {bad}; use {list(PLATFORMS)}")
        return v

    @field_validator("collections")
    @classmethod
    def _collections(cls, v: list[Collection]) -> list[Collection]:
        if not v:
            raise ValueError("at least one collection is required")
        if len(v) > 20:
            raise ValueError("at most 20 collections per profile")
        ids = [c.id for c in v]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate collection ids: {dupes}")
        return v

    @model_validator(mode="after")
    def _references(self):
        coll = {c.id: c for c in self.collections}
        analyst_ids = [a.id for a in self.analysts]
        dupes = sorted({i for i in analyst_ids if analyst_ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate analyst ids: {dupes}")
        if len(self.analysts) > 10:
            raise ValueError("at most 10 analysts per profile")
        for analyst in self.analysts:
            for ref in analyst.inputs:
                cid, _, source = ref.partition("/")
                if cid not in coll:
                    raise ValueError(f"analyst {analyst.id} references unknown collection {cid!r}")
                c = coll[cid]
                if source and c.sources != "discover" and source not in c.sources:
                    raise ValueError(
                        f"analyst {analyst.id} references source {source!r} not listed in "
                        f"collection {cid!r}")
        for ref in self.analysis.counts_only_sources:
            if "/" in ref:
                cid, _, source = ref.partition("/")
                if cid not in coll:
                    raise ValueError(f"counts_only_sources references unknown collection {cid!r}")
        return self

    def default_analysts(self) -> list[Analyst]:
        """Profiles without analysts get one review covering every collection."""
        if self.analysts:
            return self.analysts
        return [Analyst(id="review", objective=self.objective or
                        "Describe notable observations and their limitations.",
                        inputs=[c.id for c in self.collections])]


def json_schema() -> dict[str, Any]:
    schema = Profile.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://example.invalid/velociraptor-dfir-agent/profile.schema.json"
    schema["title"] = "Velociraptor DFIR Agent investigation profile (schema_version 1)"
    return schema
