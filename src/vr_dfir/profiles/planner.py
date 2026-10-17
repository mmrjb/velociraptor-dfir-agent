"""Preflight planning for profile collections and existing-evidence analysis.

Planning never launches anything. Live planning makes read-only metadata calls
(``clients()``, ``artifact_definitions()``, ``flows()``, ``hunts()``); offline planning
uses only the profile, the allowlist, and local rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from vr_dfir import ids
from vr_dfir.errors import DfirError, ValidationError
from vr_dfir.jsonio import sha256_json
from vr_dfir.profiles.models import Collection, Profile
from vr_dfir.velociraptor.artifacts import ArtifactMetadata
from vr_dfir.velociraptor.flow_state import FlowState

PLAN_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PlannedSource:
    name: str | None
    full_name: str
    status: str  # available | unavailable | not_checked | has_results | no_results_reported
    counts_only: bool = False
    note: str = ""


@dataclass
class PlannedCollection:
    collection_ids: list[str]
    artifact: str
    required: bool
    parameters: dict[str, str]
    request_key: str
    metadata_status: str  # found | missing | not_checked
    artifact_type: str | None
    allowlisted: bool
    sources: list[PlannedSource]
    behavior: list[str]
    limits: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    will_launch: bool = False
    description: str = ""


@dataclass
class Plan:
    mode: str  # new_collection | existing_flow | existing_hunt | bundle
    offline: bool
    org_id: str
    target: dict
    profile: dict | None
    collections: list[PlannedCollection] = field(default_factory=list)
    analysts: list[dict] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    retrieval: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    existing: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    plan_version: int = PLAN_VERSION
    synthetic: bool = False
    policy_denied: bool = False
    """True when the operator allowlist (or a missing allowlist) blocks a collection."""

    @property
    def ok(self) -> bool:
        return not self.errors and not any(c.errors and c.required for c in self.collections)

    @property
    def launches(self) -> int:
        return sum(1 for c in self.collections if c.will_launch)

    def fingerprint(self) -> str:
        """Stable digest of everything that determines execution (not timestamps)."""
        return sha256_json({
            "mode": self.mode,
            "org": self.org_id,
            "target": {k: self.target.get(k) for k in ("client_id", "flow_id", "hunt_id",
                                                        "bundle_id")},
            "profile": (self.profile or {}).get("fingerprint"),
            "collections": [
                {"artifact": c.artifact, "parameters": c.parameters, "will_launch": c.will_launch,
                 "sources": [s.full_name for s in c.sources], "limits": c.limits}
                for c in self.collections
            ],
            "existing": {k: self.existing.get(k) for k in ("selected_sources", "client_filter",
                                                             "artifact_filter")},
            "analysis": self.analysis,
            "retrieval": self.retrieval,
            "plan_version": self.plan_version,
        })

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        data["launches"] = self.launches
        data["fingerprint"] = self.fingerprint()
        data["will_start_endpoint_collection"] = self.launches > 0
        return data


def _source_matches(ref_list: list[str], collection_id: str, source: str | None) -> bool:
    name = source or ""
    for ref in ref_list:
        if "/" in ref:
            cid, _, sname = ref.partition("/")
            if cid == collection_id and sname == name:
                return True
        elif ref == name:
            return True
    return False


def _plan_sources(collection: Collection, meta: ArtifactMetadata | None,
                  counts_only: list[str], errors: list[str], warnings: list[str]
                  ) -> list[PlannedSource]:
    art = collection.artifact
    out: list[PlannedSource] = []
    if meta is None:
        if collection.sources == "discover":
            warnings.append(f"{art}: sources will be discovered from live metadata (offline plan).")
            return []
        for s in collection.sources:
            out.append(PlannedSource(s, f"{art}/{s}", "not_checked",
                                     _source_matches(counts_only, collection.id, s)))
        return out
    available = meta.source_names
    if collection.sources == "discover":
        for s in available:
            out.append(PlannedSource(s, f"{art}/{s}" if s else art, "available",
                                     _source_matches(counts_only, collection.id, s)))
        if not out:
            errors.append(f"{art}: metadata lists no sources.")
        return out
    named = set(meta.named_sources)
    if not named:
        errors.append(f"{art} has a single unnamed source; use 'sources: discover'.")
        return out
    for s in collection.sources:
        status = "available" if s in named else "unavailable"
        if status == "unavailable":
            msg = f"{art}: source {s!r} is not defined (available: {sorted(named)})."
            (errors if collection.required else warnings).append(msg)
        out.append(PlannedSource(s, f"{art}/{s}", status,
                                 _source_matches(counts_only, collection.id, s)))
    return out




def plan_existing_flow(service, *, org_id: str | None, client_id: str, flow_id: str,
                       profile: Profile | None = None, profile_meta: dict | None = None,
                       selected_sources: list[str] | None = None) -> Plan:
    """Plan analysis of a previously collected flow. Never launches anything."""
    org = service.resolve_org(org_id)
    cid, fid = ids.client_id(client_id), ids.flow_id(flow_id)
    errors: list[str] = []
    warnings: list[str] = []
    client = None
    try:
        client = service.get_client(cid, org)
    except DfirError as exc:
        errors.append(exc.message)
    status = service.flow_status(cid, fid, org)
    if status.state == FlowState.NOT_FOUND:
        errors.append(f"Flow {fid} was not found for client {cid} in org {org}.")
    elif status.state == FlowState.UNKNOWN:
        warnings.append(f"Flow state is unknown: {status.diagnostic}")
    elif status.active:
        warnings.append(f"Flow is still {status.state.value}; results may be incomplete.")
    metas = service.get_artifacts(status.artifacts, org) if status.artifacts else {}
    sources: list[PlannedSource] = []
    for art in status.artifacts:
        meta = metas.get(art)
        names = meta.source_names if meta else []
        if meta is None:
            warnings.append(f"Metadata for {art} is not available; using sources reported by the "
                            "flow only.")
            names = sorted({n.split("/", 1)[1] if "/" in n else None
                            for n in status.artifacts_with_results if n.split("/")[0] == art},
                           key=lambda x: x or "")
        for s in names:
            full = f"{art}/{s}" if s else art
            has = full in status.artifacts_with_results
            sources.append(PlannedSource(s, full, "has_results" if has else "no_results_reported"))
    if selected_sources:
        wanted = set(selected_sources)
        unknown = wanted - {s.full_name for s in sources} - {s.name for s in sources if s.name}
        if unknown:
            errors.append(f"Selected sources are not part of this flow: {sorted(unknown)}")
        sources = [s for s in sources if s.full_name in wanted or (s.name and s.name in wanted)]
    if profile is not None:
        counts = profile.analysis.counts_only_sources
        for s in sources:
            s.counts_only = any(ref.split("/")[-1] == (s.name or "") for ref in counts)
        prof_artifacts = {c.artifact for c in profile.collections}
        if prof_artifacts and not prof_artifacts & set(status.artifacts):
            warnings.append("The analysis profile's artifacts are not in this flow; only its "
                            "analysis settings apply.")
    coll = PlannedCollection(
        collection_ids=["existing"], artifact=",".join(status.artifacts) or "(unknown)",
        required=True, parameters={}, request_key="", metadata_status="found" if metas else
        "not_checked", artifact_type="client", allowlisted=False, sources=sources,
        behavior=["Existing collection: nothing will be launched."],
        limits=(profile.collections[0].limits.model_dump() if profile else {}),
    )
    return Plan(
        mode="existing_flow", offline=False, org_id=org,
        target={**(client.to_dict() if client else {"client_id": cid}), "flow_id": fid,
                "resolved": client is not None},
        profile=profile_meta, collections=[coll],
        analysts=[{"id": a.id, "objective": a.objective, "inputs": a.inputs}
                  for a in profile.default_analysts()] if profile else [],
        analysis=profile.analysis.model_dump() if profile else {},
        retrieval=profile.retrieval.model_dump() if profile else {},
        errors=errors, warnings=warnings,
        existing={"flow_status": status.to_dict(),
                  "selected_sources": sorted(selected_sources or [])},
        synthetic=service.synthetic,
    )


def plan_existing_hunt(service, *, org_id: str | None, hunt_id: str,
                       client_filter: list[str] | None = None, artifact_filter: str | None = None,
                       selected_sources: list[str] | None = None, profile: Profile | None = None,
                       profile_meta: dict | None = None, max_clients: int = 200) -> Plan:
    org = service.resolve_org(org_id)
    hid = ids.hunt_id(hunt_id)
    errors: list[str] = []
    warnings: list[str] = []
    info = None
    try:
        info = service.hunt_info(hid, org)
    except DfirError as exc:
        errors.append(exc.message)
    artifacts = (info or {}).get("artifacts", [])
    if artifact_filter:
        ids.artifact_name(artifact_filter)
        if artifact_filter not in artifacts:
            errors.append(f"Artifact {artifact_filter} is not part of hunt {hid}.")
        artifacts = [artifact_filter]
    clients = [ids.client_id(c) for c in (client_filter or [])]
    metas = service.get_artifacts(artifacts, org) if artifacts else {}
    sources = []
    for art in artifacts:
        meta = metas.get(art)
        for s in (meta.source_names if meta else [None]):
            sources.append(PlannedSource(s, f"{art}/{s}" if s else art, "available"))
    if selected_sources:
        wanted = set(selected_sources)
        sources = [s for s in sources if s.full_name in wanted or (s.name and s.name in wanted)]
        if not sources:
            errors.append("None of the selected sources belong to this hunt.")
    if info and info.get("state") not in ("STOPPED", "ARCHIVED"):
        warnings.append(f"Hunt state is {info.get('state')}; contributing flows may still change. "
                        "The snapshot will be marked incomplete if flows are still active.")
    coll = PlannedCollection(
        collection_ids=["existing-hunt"], artifact=",".join(artifacts) or "(unknown)",
        required=True, parameters={}, request_key="", metadata_status="found" if metas else
        "not_checked", artifact_type="client", allowlisted=False, sources=sources,
        behavior=["Existing hunt: no hunt is created or restarted; nothing is launched."],
        limits=(profile.collections[0].limits.model_dump() if profile else {}),
    )
    return Plan(
        mode="existing_hunt", offline=False, org_id=org,
        target={"hunt_id": hid, "description": (info or {}).get("description"),
                "resolved": info is not None},
        profile=profile_meta, collections=[coll],
        analysts=[{"id": a.id, "objective": a.objective, "inputs": a.inputs}
                  for a in profile.default_analysts()] if profile else [],
        analysis=profile.analysis.model_dump() if profile else {},
        retrieval=profile.retrieval.model_dump() if profile else {},
        errors=errors, warnings=warnings,
        existing={"hunt": info, "client_filter": sorted(clients),
                  "artifact_filter": artifact_filter,
                  "selected_sources": sorted(selected_sources or []),
                  "max_clients": max_clients},
        synthetic=service.synthetic,
    )


def require_ok(plan: Plan) -> None:
    if not plan.ok:
        raise ValidationError(
            "The plan has blocking problems: " + "; ".join(plan.errors[:5]),
            details={"errors": plan.errors, "warnings": plan.warnings},
            next_action="Fix the listed problems and plan again.")
