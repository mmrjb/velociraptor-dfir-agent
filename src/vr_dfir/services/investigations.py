"""Shared read-only investigation pipeline for the CLI, MCP and GUI.

Existing flows, hunts and evidence bundles are retrieved, analyzed and reported.
No backend mutation or collection-launch implementation is included in this edition.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from vr_dfir.analysis.engine import AnalysisContext, run_analysis
from vr_dfir.errors import (
    ConfigurationError,
    DfirError,
    PolicyError,
    ScopeError,
    StaleRequestError,
    ValidationError,
)
from vr_dfir.evidence.bundle import EvidenceBundle, now_iso
from vr_dfir.evidence.retrieval import Budget, SourceRequest, enumerate_hunt_flows, read_flow
from vr_dfir.jsonio import atomic_write_json, atomic_write_text, read_json
from vr_dfir.profiles.models import AnalysisSettings, Analyst, Profile, RetrievalSettings
from vr_dfir.profiles.planner import (
    Plan,
    plan_existing_flow,
    plan_existing_hunt,
    require_ok,
)
from vr_dfir.profiles.store import ProfileRecord, ProfileStore, parse_profile_data
from vr_dfir.reporting.render import render_html, render_markdown
from vr_dfir.reporting.report import build_report
from vr_dfir.services.checkpoints import Checkpoint
from vr_dfir.services.runstore import Investigation, RunStore
from vr_dfir.services.runtime import Runtime
from vr_dfir.velociraptor.flow_state import FlowState, normalize_flow

ProgressFn = Callable[[str, dict], None]

DEFAULT_LIMITS = {"collection_timeout_seconds": 600, "wait_timeout_seconds": 660,
                  "poll_interval_seconds": 3, "page_size": 500, "max_rows_per_source": 10000,
                  "max_bytes_per_source": 50_000_000}


def _profile_meta(record: ProfileRecord | None) -> dict | None:
    if record is None:
        return None
    return {"id": record.profile.id, "name": record.profile.name,
            "fingerprint": record.fingerprint, "origin": record.origin}


class InvestigationService:
    def __init__(self, runtime: Runtime, store: RunStore, profiles: ProfileStore):
        self.runtime = runtime
        self.store = store
        self.profiles = profiles
        self.collection_attempts = 0  # launches attempted through this service instance

    @staticmethod
    def _require_supported_mode(inv: Investigation) -> None:
        if inv.meta.get("mode") not in ("existing_flow", "existing_hunt", "bundle"):
            raise PolicyError("This investigation mode is unavailable in the read-only AI preview. Import a saved evidence bundle instead.")

    # ------------------------------------------------------------------ planning

    def _profile(self, ref: str | None) -> ProfileRecord | None:
        if not ref:
            return None
        return self.profiles.get(ref)


    def plan_existing_flow(self, *, org_id: str | None, client_id: str, flow_id: str,
                           profile_ref: str | None = None,
                           selected_sources: list[str] | None = None) -> Plan:
        record = self._profile(profile_ref)
        return plan_existing_flow(self.runtime.service, org_id=org_id, client_id=client_id,
                                  flow_id=flow_id, profile=record.profile if record else None,
                                  profile_meta=_profile_meta(record),
                                  selected_sources=selected_sources)

    def plan_existing_hunt(self, *, org_id: str | None, hunt_id: str,
                           client_filter: list[str] | None = None,
                           artifact_filter: str | None = None,
                           selected_sources: list[str] | None = None,
                           profile_ref: str | None = None, max_clients: int = 200) -> Plan:
        record = self._profile(profile_ref)
        return plan_existing_hunt(self.runtime.service, org_id=org_id, hunt_id=hunt_id,
                                  client_filter=client_filter, artifact_filter=artifact_filter,
                                  selected_sources=selected_sources,
                                  profile=record.profile if record else None,
                                  profile_meta=_profile_meta(record), max_clients=max_clients)

    # ------------------------------------------------------------------ creation

    def create(self, plan: Plan, *, title: str | None = None, no_llm: bool = False,
               analysis_overrides: dict | None = None, expected_fingerprint: str | None = None
               ) -> Investigation:
        """Create an investigation from a reviewed plan. Nothing runs until :meth:`execute`."""
        require_ok(plan)
        if plan.mode not in ("existing_flow", "existing_hunt") or plan.launches:
            raise PolicyError("This AI preview only analyzes existing evidence; backend mutations are unavailable.")
        if expected_fingerprint and expected_fingerprint != plan.fingerprint():
            raise StaleRequestError("The plan changed since it was reviewed.")
        if plan.synthetic != self.runtime.synthetic:
            raise ScopeError("The plan's data mode does not match this runtime.")
        target = plan.target
        scope = {"org_id": plan.org_id, "client_id": target.get("client_id"),
                 "hostname": target.get("hostname"), "platform": target.get("platform"),
                 "flow_id": target.get("flow_id"), "hunt_id": target.get("hunt_id")}
        if analysis_overrides:
            AnalysisSettings.model_validate({**plan.analysis, **analysis_overrides})
        options = {"no_llm": bool(no_llm),
                   "analysis_overrides": analysis_overrides or {},
                   "plan_fingerprint": plan.fingerprint()}
        default_title = {
            "new_collection": f"{(plan.profile or {}).get('name', 'Collection')} on "
                              f"{target.get('hostname') or target.get('client_id')}",
            "existing_flow": f"Flow {target.get('flow_id')} on "
                             f"{target.get('hostname') or target.get('client_id')}",
            "existing_hunt": f"Hunt {target.get('hunt_id')}",
        }.get(plan.mode, plan.mode)
        inv = self.store.create(mode=plan.mode, title=title or default_title, scope=scope,
                                profile=plan.profile, options=options,
                                synthetic=self.runtime.synthetic)
        plan_dict = plan.to_dict()
        atomic_write_json(inv.path / "plan.json", plan_dict)
        collections = []
        existing = dict(plan.existing)
        if plan.mode == "existing_flow":
            existing["sources"] = [s["full_name"] for s in plan_dict["collections"][0]["sources"]]
            existing["counts_only"] = [s["full_name"] for s in plan_dict["collections"][0]["sources"]
                                       if s["counts_only"]]
        if plan.mode == "existing_hunt":
            existing["sources"] = [s["full_name"] for s in plan_dict["collections"][0]["sources"]]
            existing["hunt_flows"] = []
            existing["enumeration_complete"] = False
        Checkpoint.create(inv.checkpoint_path, investigation_id=inv.id, mode=plan.mode,
                          org_id=plan.org_id, target=target, profile=plan.profile,
                          plan_fingerprint=plan.fingerprint(), collections=collections,
                          existing=existing, synthetic=self.runtime.synthetic)
        inv.event("created", {"mode": plan.mode, "launches_planned": plan.launches})
        return inv

    # ------------------------------------------------------------------ execution

    def execute(self, investigation_id: str, *, stop_event: threading.Event | None = None,
                progress: ProgressFn | None = None, analysis: bool = True) -> dict:
        inv = self.store.get(investigation_id)
        self._require_supported_mode(inv)
        # Saved-bundle analysis never touches Velociraptor, so any runtime may run it.
        if inv.meta.get("mode") != "bundle" and \
                bool(inv.meta.get("synthetic")) != self.runtime.synthetic:
            raise ScopeError("This investigation belongs to a different data mode "
                             "(synthetic test data vs live) than the current runtime.")
        with inv.exclusive():
            inv.update(job={"state": "running", "started_at": now_iso()})

            def emit(kind: str, data: dict) -> None:
                inv.event(kind, data)
                if progress:
                    progress(kind, data)
            try:
                self._run(inv, stop_event, emit, analysis)
            except DfirError as exc:
                inv.add_error(exc.message)
                emit("error", exc.to_dict())
                if inv.meta.get("outcome") == "running":
                    inv.update(outcome="failed")
            except KeyboardInterrupt:
                # Ctrl+C: record a recoverable state before propagating. The endpoint
                # collection (if any) is not cancelled.
                inv.add_error("Interrupted locally (Ctrl+C). Resume from the checkpoint; any "
                              "endpoint collection may continue on the server.")
                inv.update(outcome="interrupted", phase="stopped")
                emit("stopped", {"message": "interrupted locally"})
                if stop_event is not None:
                    stop_event.set()
                raise
            except Exception as exc:  # noqa: BLE001 - recorded, never shown as a traceback
                inv.add_error(f"Unexpected error: {type(exc).__name__}: {str(exc)[:300]}")
                emit("error", {"message": f"{type(exc).__name__}"})
                inv.update(outcome="failed")
            finally:
                state = "stopped" if (stop_event and stop_event.is_set()) else "finished"
                inv.update(job={"state": state, "finished_at": now_iso()})
            return inv.reload()

    def _run(self, inv: Investigation, stop_event, emit: ProgressFn, analysis: bool) -> None:
        checkpoint = Checkpoint.load(inv.checkpoint_path)
        mode = inv.meta["mode"]
        if mode == "bundle":
            inv.update(retrieval_status="not_applicable")
        else:
            bundle = self._bundle(inv, checkpoint, create=True)
            self._read(inv, checkpoint, bundle, stop_event, emit)
        if stop_event is not None and stop_event.is_set():
            inv.update(outcome="interrupted")
            inv.set_phase("stopped")
            emit("stopped", {"message": "Local processing stopped. Any endpoint collection may "
                                        "continue on the server."})
            self._write_report(inv)
            return
        if analysis:
            self._analyze(inv, checkpoint, stop_event, emit)
        self._finalize(inv)

    def _bundle(self, inv: Investigation, checkpoint: Checkpoint, create: bool) -> EvidenceBundle:
        if (inv.evidence_dir / "manifest.json").is_file():
            return EvidenceBundle.open(inv.evidence_dir)
        if not create:
            raise ValidationError("This investigation has no evidence bundle yet.")
        scope = {"org_id": checkpoint.data["org_id"], "mode": inv.meta["mode"],
                 "client_id": checkpoint.data["target"].get("client_id"),
                 "flow_id": checkpoint.data["target"].get("flow_id"),
                 "hunt_id": checkpoint.data["target"].get("hunt_id")}
        return EvidenceBundle.create(inv.evidence_dir, investigation_id=inv.id, scope=scope,
                                     synthetic=bool(inv.meta.get("synthetic")))

    # -- collection --


    # -- retrieval --

    def _read(self, inv: Investigation, cp: Checkpoint, bundle: EvidenceBundle, stop_event,
              emit) -> None:
        inv.set_phase("reading")
        inv.update(retrieval_status="in_progress")
        service = self.runtime.service
        profile = self._profile_from_checkpoint(cp)
        retrieval = (profile.retrieval if profile else RetrievalSettings()).model_dump()
        analysis_settings = profile.analysis if profile else AnalysisSettings()
        budget = Budget(retrieval["max_total_rows"], retrieval["max_total_bytes"])
        for entry in bundle.sources():
            budget.consume(entry.coverage.persisted_rows, entry.coverage.bytes_persisted)
        mode = inv.meta["mode"]
        org = cp.data["org_id"]
        event_fields = analysis_settings.event_time_fields
        if mode == "existing_flow":
            target = cp.data["target"]
            status = service.flow_status(target["client_id"], target["flow_id"], org)
            with cp._lock:
                cp.data["existing"]["flow_status"] = status.to_dict()
                cp.save()
            counts_only = set(cp.data["existing"].get("counts_only") or [])
            requests = []
            for full in cp.data["existing"].get("sources") or []:
                art, _, src = full.partition("/")
                requests.append(SourceRequest(art, src or None, [], full in counts_only))
            limits = (profile.collections[0].limits.model_dump() if profile else DEFAULT_LIMITS)
            emit("reading", {"flow_id": target["flow_id"], "sources": len(requests)})
            read_flow(service, bundle, org_id=org, client_id=target["client_id"],
                      flow_id=target["flow_id"], hostname=target.get("hostname") or "",
                      status=status, sources=requests, limits=limits, budget=budget,
                      retrieval=retrieval, stop_event=stop_event, progress=emit,
                      event_time_fields=event_fields)
            inv.update(flow_ids=[target["flow_id"]])
        elif mode == "existing_hunt":
            self._read_hunt(inv, cp, bundle, budget, retrieval, profile, stop_event, emit,
                            event_fields)
        inv.update(retrieval_status=_aggregate_retrieval(bundle, inv.meta["mode"]),
                   counts=_counts(bundle, inv.meta.get("counts")))

    def _read_hunt(self, inv, cp, bundle, budget, retrieval, profile, stop_event, emit,
                   event_fields) -> None:
        service = self.runtime.service
        org = cp.data["org_id"]
        ex = cp.data["existing"]
        hunt_id = cp.data["target"]["hunt_id"]
        if not ex.get("enumeration_complete"):
            flows, complete = enumerate_hunt_flows(
                service, org_id=org, hunt_id=hunt_id, known=[],
                client_filter=set(ex.get("client_filter") or []) or None,
                max_clients=int(ex.get("max_clients") or 200))
            with cp._lock:
                ex["hunt_flows"] = flows
                ex["enumeration_complete"] = complete
                ex["enumerated_at"] = now_iso()
                cp.save()
        sources = ex.get("sources") or []
        limits = (profile.collections[0].limits.model_dump() if profile else DEFAULT_LIMITS)
        counts_only_refs = profile.analysis.counts_only_sources if profile else []
        flow_ids = []
        for f in ex["hunt_flows"]:
            if stop_event is not None and stop_event.is_set():
                break
            status = service.flow_status(f["client_id"], f["flow_id"], org)
            f["state"] = status.state.value
            hostname = ""
            try:
                hostname = service.get_client(f["client_id"], org).hostname
            except DfirError:
                pass
            requests = []
            for full in sources:
                art, _, src = full.partition("/")
                if status.artifacts and art not in status.artifacts:
                    continue
                co = any(ref.split("/")[-1] == src for ref in counts_only_refs)
                requests.append(SourceRequest(art, src or None, [], co))
            emit("reading", {"flow_id": f["flow_id"], "client_id": f["client_id"],
                             "state": status.state.value})
            read_flow(service, bundle, org_id=org, client_id=f["client_id"],
                      flow_id=f["flow_id"], hostname=hostname, status=status,
                      sources=requests, limits=limits, budget=budget, retrieval=retrieval,
                      hunt_id=hunt_id, stop_event=stop_event, progress=emit,
                      event_time_fields=event_fields)
            flow_ids.append(f["flow_id"])
        with cp._lock:
            cp.save()
        inv.update(flow_ids=flow_ids)

    def _discover_sources(self, artifact: str, org: str, collection_ids: list[str],
                          profile: Profile | None) -> list[SourceRequest]:
        metas = self.runtime.service.get_artifacts([artifact], org)
        meta = metas.get(artifact)
        if meta is None:
            return [SourceRequest(artifact, None, collection_ids, False, defined=False)]
        counts = profile.analysis.counts_only_sources if profile else []
        return [SourceRequest(artifact, s, collection_ids,
                              any(r.split("/")[-1] == (s or "") for r in counts))
                for s in meta.source_names]

    # -- analysis --

    def _analyze(self, inv: Investigation, cp: Checkpoint, stop_event, emit,
                 no_llm: bool | None = None) -> None:
        inv.set_phase("analyzing")
        inv.update(analysis_status="running")
        bundle = EvidenceBundle.open(inv.evidence_dir)
        profile = self._profile_from_checkpoint(cp)
        options = inv.meta.get("options") or {}
        settings = profile.analysis if profile else AnalysisSettings()
        if options.get("analysis_overrides"):
            settings = AnalysisSettings.model_validate(
                {**settings.model_dump(), **options["analysis_overrides"]})
        use_no_llm = options.get("no_llm") if no_llm is None else no_llm
        try:
            provider = self.runtime.provider(no_llm=bool(use_no_llm))
        except ConfigurationError as exc:
            inv.add_error(f"Model provider unavailable: {exc.message}")
            emit("model_unavailable", {"message": exc.message})
            from vr_dfir.analysis.providers import DisabledProvider
            provider = DisabledProvider()
            options = {**options, "provider_error": exc.message}
        analysts = profile.default_analysts() if profile else [
            Analyst(id="review", objective="Describe notable observations and their "
                    "limitations.", inputs=["all"])]
        collection_map = {c.id: [c.artifact] for c in profile.collections} if profile else {}
        if not profile or inv.meta["mode"] != "new_collection":
            # Existing evidence: profile analysts map by artifact; otherwise one review role.
            if not profile or not any(e.artifact in {a for v in collection_map.values() for a in v}
                                      for e in bundle.sources()):
                analysts = [Analyst(id="review", objective=(profile.objective if profile and
                                                            profile.objective else
                                                            "Describe notable observations and "
                                                            "their limitations."),
                                    inputs=["all"])]
                collection_map = {}
        ctx = AnalysisContext(run_dir=inv.path, investigation_id=inv.id,
                              objective=profile.objective if profile else "",
                              guidance=profile.guidance if profile else "",
                              profile_fingerprint=(inv.meta.get("profile") or {}).get("fingerprint"),
                              synthetic=bool(inv.meta.get("synthetic")))
        result = run_analysis(bundle, provider=provider, settings=settings, analysts=analysts,
                              context=ctx, collection_map=collection_map, progress=emit,
                              stop_event=stop_event)
        status = result.analysis_status
        if options.get("provider_error"):
            status = "failed"
            result.errors.insert(0, f"Model provider unavailable: {options['provider_error']}")
            result.analysis_status = "failed"
            atomic_write_json(inv.path / "analysis" / "findings.json", result.to_dict())
        for err in result.errors[:5]:
            inv.add_error(f"Analysis: {err}")
        counts = dict(inv.meta.get("counts") or {})
        counts.update(model_findings=len(result.model_findings),
                      deterministic_findings=len(result.deterministic_findings),
                      rejected_findings=len(result.rejected_findings),
                      findings=len(result.model_findings) + len(result.deterministic_findings))
        inv.update(analysis_status=status, counts=counts,
                   provider={"name": provider.name, "model": provider.model,
                             "fake": provider.is_fake})
        emit("analysis_finished", {"status": status, "findings": counts["findings"]})

    def _profile_from_checkpoint(self, cp: Checkpoint) -> Profile | None:
        meta = cp.data.get("profile")
        if not meta:
            return None
        plan_path = cp.path.parent / "plan.json"
        try:
            record = self.profiles.get(meta["id"])
        except DfirError:
            record = None
        if record is not None and record.fingerprint == meta.get("fingerprint"):
            return record.profile
        # Profile edited or removed since the run started: rebuild analysis settings from
        # the saved plan rather than silently using different settings.
        if plan_path.is_file():
            plan = read_json(plan_path)
            data = {"schema_version": 1, "id": meta["id"], "name": meta.get("name") or meta["id"],
                    "platforms": ["any"],
                    "collections": [{"id": cid, "artifact": c["artifact"].split(",")[0]
                                     if c["artifact"] != "(unknown)" else "Unknown.Artifact"}
                                    for c in plan["collections"] for cid in c["collection_ids"][:1]],
                    "analysis": plan.get("analysis") or {},
                    "retrieval": plan.get("retrieval") or {}}
            try:
                return parse_profile_data(data, source="saved plan")
            except DfirError:
                return None
        return None

    # -- finalize/report --

    def _finalize(self, inv: Investigation) -> None:
        meta = inv.reload()
        outcome = _outcome(meta, inv)
        inv.update(outcome=outcome)
        self._write_report(inv)
        inv.set_phase("report_ready")
        inv.event("report_ready", {"outcome": outcome})

    def _write_report(self, inv: Investigation) -> dict:
        meta = inv.reload()
        bundle = None
        if (inv.evidence_dir / "manifest.json").is_file():
            bundle = EvidenceBundle.open(inv.evidence_dir)
        analysis = None
        apath = inv.path / "analysis" / "findings.json"
        if apath.is_file():
            analysis = read_json(apath)
        plan = read_json(inv.path / "plan.json") if (inv.path / "plan.json").is_file() else None
        cp = read_json(inv.checkpoint_path) if inv.checkpoint_path.is_file() else None
        report = build_report(meta, bundle=bundle, analysis=analysis, plan=plan, checkpoint=cp)
        atomic_write_json(inv.report_dir / "report.json", report)
        atomic_write_text(inv.report_dir / "report.md", render_markdown(report))
        atomic_write_text(inv.report_dir / "report.html", render_html(report))
        return report

    # ------------------------------------------------------------------ recovery actions

    def resume(self, investigation_id: str, *, stop_event=None, progress=None,
               org_id: str | None = None, client_id: str | None = None) -> dict:
        """Continue an interrupted/partial run from its checkpoint. Never relaunches."""
        inv = self.store.get(investigation_id)
        self._require_supported_mode(inv)
        cp = Checkpoint.load(inv.checkpoint_path)
        cp.check_scope(org_id=org_id, client_id=client_id)
        if inv.meta.get("mode") == "new_collection":
            for i, entry in enumerate(cp.data["collections"]):
                if entry["launch"]["state"] == "pending" and inv.meta.get("collection_status") \
                        not in ("pending",):
                    # A pending entry after a run started means the run stopped before this
                    # launch. Resume does not launch: mark it skipped and report it.
                    cp.update_collection(i, state="skipped",
                                         error="Not launched before the run stopped; resume "
                                               "never launches collections.")
        inv.update(outcome="running", phase="preparing")
        return self.execute(investigation_id, stop_event=stop_event, progress=progress)

    def retry_analysis(self, investigation_id: str, *, no_llm: bool | None = None,
                       progress=None) -> dict:
        inv = self.store.get(investigation_id)
        self._require_supported_mode(inv)
        if not (inv.evidence_dir / "manifest.json").is_file():
            raise ValidationError("No saved evidence to analyze.")
        with inv.exclusive():
            inv.update(job={"state": "running", "kind": "retry_analysis",
                            "started_at": now_iso()})
            cp = Checkpoint.load(inv.checkpoint_path)
            if no_llm is not None:
                inv.update(options={**(inv.meta.get("options") or {}), "no_llm": bool(no_llm)})

            def emit(kind, data):
                inv.event(kind, data)
                if progress:
                    progress(kind, data)
            try:
                emit("retry_analysis", {"no_llm": no_llm})
                self._analyze(inv, cp, None, emit)
                self._finalize(inv)
            except DfirError as exc:
                inv.add_error(exc.message)
                emit("error", exc.to_dict())
            finally:
                inv.update(job={"state": "finished", "kind": "retry_analysis",
                                "finished_at": now_iso()})
            return inv.reload()

    def rebuild_report(self, investigation_id: str) -> dict:
        inv = self.store.get(investigation_id)
        self._require_supported_mode(inv)
        return self._write_report(inv)

    # ------------------------------------------------------------------ bundles

    def analyze_bundle(self, bundle_path: Path, *, no_llm: bool = True, title: str | None = None,
                       copy: bool = True, profile_ref: str | None = None) -> Investigation:
        """Analyze a saved evidence bundle with no Velociraptor access."""
        source = EvidenceBundle.open(bundle_path)
        problems = source.verify()
        if problems:
            raise ValidationError("The evidence bundle failed integrity checks.",
                                  details={"problems": problems[:20]})
        record = self._profile(profile_ref)
        scope = dict(source.manifest.get("scope") or {})
        inv = self.store.create(mode="bundle", title=title or f"Bundle {source.bundle_id}",
                                scope={"org_id": scope.get("org_id"),
                                       "client_id": scope.get("client_id"),
                                       "flow_id": scope.get("flow_id"),
                                       "hunt_id": scope.get("hunt_id"),
                                       "source_bundle_id": source.bundle_id},
                                profile=_profile_meta(record),
                                options={"no_llm": no_llm},
                                synthetic=source.synthetic)
        if copy:
            shutil.copytree(bundle_path, inv.evidence_dir)
        Checkpoint.create(inv.checkpoint_path, investigation_id=inv.id, mode="bundle",
                          org_id=scope.get("org_id") or "root", target=scope,
                          profile=_profile_meta(record), plan_fingerprint="bundle",
                          existing={"source_bundle_id": source.bundle_id},
                          synthetic=source.synthetic)
        atomic_write_json(inv.path / "plan.json", {"mode": "bundle", "ok": True, "launches": 0,
                                                    "will_start_endpoint_collection": False,
                                                    "collections": [], "analysis": (
                                                        record.profile.analysis.model_dump()
                                                        if record else {})})
        inv.update(collection_status="not_applicable", retrieval_status="not_applicable",
                   counts=_counts(EvidenceBundle.open(inv.evidence_dir), {}))
        inv.event("created", {"mode": "bundle", "source_bundle_id": source.bundle_id})
        return inv


# ---------------------------------------------------------------------- helpers

def _src(name):
    return name if name else None


def _status_from_dict(data: dict | None, client_id: str, flow_id: str):
    if not data:
        return None
    if data.get("response_shape") in ("flat_context", "nested_context", "hunt_flow_row"):
        status = normalize_flow({"session_id": flow_id, "client_id": client_id,
                                 "state": data.get("raw_state"), "status": data.get("raw_status")},
                                client_id=client_id, flow_id=flow_id)
        # Restore the saved per-query stats so per-source classification is consistent.
        from vr_dfir.velociraptor.flow_state import QueryStat
        status.query_stats = [QueryStat(**q) for q in data.get("query_stats") or []]
        status.artifacts = data.get("artifacts") or []
        status.artifacts_with_results = data.get("artifacts_with_results") or []
        status.start_time = data.get("start_time")
        status.create_time = data.get("create_time")
        status.total_collected_rows = data.get("total_collected_rows")
        return status
    return None


def _aggregate_collection(statuses: list[str]) -> str:
    if not statuses:
        return "not_applicable"
    active = [s for s in statuses if s != "skipped"]
    if not active:
        return "skipped"
    if all(s == "completed" for s in active):
        return "completed"
    if any(s == "launch_outcome_unknown" for s in active):
        return "launch_outcome_unknown"
    if any(s == "interrupted" for s in active):
        return "interrupted"
    if any(s == "wait_timeout" for s in active):
        return "wait_timeout"
    if all(s in ("failed", "refused", "cancelled", "not_found", "error", "unrecognized")
           for s in active):
        return "failed" if "cancelled" not in active else "cancelled"
    return "partial"


def _aggregate_retrieval(bundle: EvidenceBundle, mode: str) -> str:
    entries = bundle.sources()
    if not entries:
        return "failed" if mode != "bundle" else "not_applicable"
    reads = [e.coverage.read_status for e in entries]
    if all(r in ("complete", "not_applicable") for r in reads):
        return "complete"
    if all(r in ("read_failed", "not_started") for r in reads):
        return "failed" if any(r == "read_failed" for r in reads) else "not_started"
    return "incomplete"


def _counts(bundle: EvidenceBundle, previous: dict | None) -> dict:
    counts = dict(previous or {})
    entries = bundle.sources()
    counts.update(
        sources_total=len(entries),
        sources_complete=sum(1 for e in entries if e.coverage.read_status == "complete"),
        sources_with_rows=sum(1 for e in entries if e.coverage.result_status == "rows"),
        rows_persisted=sum(e.coverage.persisted_rows for e in entries),
        clients=len({e.client_id for e in entries}),
    )
    return counts


def _outcome(meta: dict, inv: Investigation) -> str:
    if meta.get("outcome") == "interrupted" or meta.get("phase") == "stopped":
        return "interrupted"
    collection = meta.get("collection_status")
    retrieval = meta.get("retrieval_status")
    analysis = meta.get("analysis_status")
    if collection in ("launch_outcome_unknown", "interrupted"):
        return "interrupted"
    if collection in ("failed", "cancelled") and (meta.get("counts") or {}).get(
            "rows_persisted", 0) == 0:
        return "failed"
    if retrieval == "failed":
        return "failed"
    gaps = False
    bundle_path = inv.evidence_dir / "manifest.json"
    if bundle_path.is_file():
        bundle = EvidenceBundle.open(inv.evidence_dir)
        for e in bundle.sources():
            if e.coverage.result_status in ("source_failed", "unknown", "not_collected",
                                            "unavailable") or e.coverage.read_status in (
                    "incomplete", "read_failed", "not_started"):
                gaps = True
    if collection not in ("completed", "not_applicable", "skipped", None):
        gaps = True
    if retrieval not in ("complete", "not_applicable"):
        gaps = True
    if analysis == "not_requested":
        return "partial" if gaps else "evidence_only"
    if analysis in ("failed", "completed_with_issues", "running", "not_started",
                    "no_model_requests"):
        return "partial"
    return "partial" if gaps else "complete"


def _apply_limit_overrides(profile: Profile, overrides: dict) -> Profile:
    data = profile.model_dump(mode="json")
    for c in data["collections"]:
        c["limits"] = {**c["limits"], **overrides}
    return parse_profile_data(data, source="limit overrides")


def flow_status_snapshot(service, org_id, client_id, flow_id) -> dict:
    """Read-only status helper used by MCP/UI (never launches)."""
    status = service.flow_status(client_id, flow_id, org_id)
    data = status.to_dict()
    data["state_label"] = {
        FlowState.SCHEDULED: "Scheduled", FlowState.QUEUED: "Queued on client",
        FlowState.IN_PROGRESS: "Running on client", FlowState.UNRESPONSIVE: "Client unresponsive",
        FlowState.COMPLETED: "Completed", FlowState.FAILED: "Failed",
        FlowState.CANCELLED: "Cancelled", FlowState.NOT_FOUND: "Not found",
        FlowState.UNKNOWN: "Unknown",
    }[status.state]
    return data


def default_runs_root(settings) -> Path:
    return Path(settings.state_dir) / "investigations"
