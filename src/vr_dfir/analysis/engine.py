"""Bounded analysis over a saved evidence bundle.

Order of work:

1. Deterministic review signals over every persisted row (no model involved).
2. If a model is selected: per analyst role, bounded request(s) over a reproducible view,
   at most one repair attempt per request, and a synthesis step only when chunked.
3. Every model finding is validated; accepted and rejected findings are stored apart.

The exact serialized request, prompt version, provider/model, options, profile
fingerprint, and evidence-selection metadata are written to ``analysis/requests`` (mode
0600) before the call; raw output is written to ``analysis/responses`` before validation.
A request record does not prove the model server consumed every token.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vr_dfir.analysis.findings import Finding, repair_instructions, validate_model_output
from vr_dfir.analysis.model_input import (
    PROMPT_VERSION,
    build_source_view,
    coverage_record,
    plan_requests,
    request_size,
    synthesis_request,
)
from vr_dfir.analysis.providers import DisabledProvider, Provider
from vr_dfir.analysis.signals import RULES
from vr_dfir.errors import ModelError, ModelUnavailable
from vr_dfir.evidence.bundle import EvidenceBundle, now_iso
from vr_dfir.jsonio import atomic_write_json, atomic_write_text, ensure_dir
from vr_dfir.profiles.models import AnalysisSettings, Analyst

ProgressFn = Callable[[str, dict], None]
MAX_EVIDENCE_PER_RULE_FINDING = 10


@dataclass
class AnalysisContext:
    run_dir: Path
    investigation_id: str
    objective: str = ""
    guidance: str = ""
    profile_fingerprint: str | None = None
    synthetic: bool = False


@dataclass
class AnalysisResult:
    analysis_status: str
    provider: dict
    prompt_version: str
    started_at: str
    finished_at: str = ""
    deterministic_findings: list[dict] = field(default_factory=list)
    model_findings: list[dict] = field(default_factory=list)
    rejected_findings: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    model_input_coverage: list[dict] = field(default_factory=list)
    skipped_model_coverage: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    analysts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _deterministic_findings(bundle: EvidenceBundle, aliases: dict | None) -> list[dict]:
    from vr_dfir.analysis.signals import evaluate_row
    groups: dict[tuple[str, str], dict] = {}
    hash_clients: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for entry in bundle.sources():
        for _, record in bundle.iter_records(entry.source_key):
            row = record["row"]
            sig = evaluate_row(row, aliases)
            if not sig.rule_ids:
                continue
            for rule_id in sig.rule_ids:
                g = groups.setdefault((rule_id, entry.client_id), {
                    "ids": [], "count": 0, "sources": set(), "hostname": entry.hostname,
                    "examples": []})
                g["count"] += 1
                g["sources"].add(entry.full_name)
                if len(g["ids"]) < MAX_EVIDENCE_PER_RULE_FINDING:
                    g["ids"].append(record["evidence_id"])
                if len(g["examples"]) < 3 and isinstance(row, dict):
                    g["examples"].append(_example(row))
            if isinstance(row, dict):
                for key in ("SHA256", "Sha256", "ExeSHA256"):
                    value = row.get(key)
                    if isinstance(value, str) and len(value) == 64:
                        hash_clients[value][entry.client_id].append(record["evidence_id"])
    findings = []
    n = 0
    for (rule_id, client_id), g in sorted(groups.items()):
        rule = RULES[rule_id]
        n += 1
        more = g["count"] - len(g["ids"])
        findings.append(Finding(
            finding_id=f"R-{n:03d}",
            title=rule.title,
            observation=(f"{g['count']} row(s) on {g['hostname'] or client_id} ({client_id}) in "
                         f"{', '.join(sorted(g['sources']))} match this review rule"
                         + (f" ({more} more not cited)" if more > 0 else "") + ". Examples: "
                         + "; ".join(g["examples"])),
            interpretation=("Review signal: " + rule.rationale + " A rule match does not "
                            "establish malicious intent."),
            evidence_ids=g["ids"], severity=rule.severity, confidence="medium",
            confidence_rationale="Deterministic rule match on the cited rows; context such as "
                                 "ownership, change history, and intent was not assessed.",
            benign_explanations=list(rule.benign), missing_context=[
                "Only the persisted rows were evaluated; sampled or missing sources may hide "
                "related activity."],
            next_steps=list(rule.next_steps), origin="deterministic_rule",
            analyst_id="rules", rule_id=rule_id, client_ids=[client_id],
            sources=sorted(g["sources"]),
            validation={"citations": "generated from persisted rows"},
        ).to_dict())
    for digest, per_client in sorted(hash_clients.items()):
        if len(per_client) < 2:
            continue
        n += 1
        cited = [ids_[0] for ids_ in per_client.values()][:MAX_EVIDENCE_PER_RULE_FINDING]
        findings.append(Finding(
            finding_id=f"R-{n:03d}",
            title="Same flagged file hash on several clients",
            observation=(f"SHA-256 {digest[:16]}... appears in flagged rows on "
                         f"{len(per_client)} clients: {', '.join(sorted(per_client))}."),
            interpretation=("Cross-client pattern within the retrieved evidence only. It does "
                            "not cover clients or rows that were not retrieved."),
            evidence_ids=cited, severity="low", confidence="medium",
            confidence_rationale="Exact hash equality across cited rows.",
            benign_explanations=["The same approved tool deployed to several hosts"],
            missing_context=["Clients outside the retrieved subset were not examined"],
            next_steps=["Identify the file and whether its deployment was approved"],
            origin="deterministic_rule", analyst_id="rules", rule_id="cross_client_hash",
            client_ids=sorted(per_client), sources=[],
            validation={"citations": "generated from persisted rows"},
        ).to_dict())
    return findings


def _example(row: dict) -> str:
    parts = []
    for key in ("Name", "User", "Exe", "Path", "CommandLine", "Command", "Raddr", "Rport", "Uid"):
        if key in row and row[key] not in (None, ""):
            parts.append(f"{key}={str(row[key])[:80]}")
        if len(parts) >= 3:
            break
    return ", ".join(parts) or "(row)"


def _sources_for(analyst: Analyst, bundle: EvidenceBundle, collection_map: dict[str, list[str]]):
    """Resolve analyst inputs (collection IDs or collection/Source refs) to bundle sources."""
    entries = bundle.sources()
    if not collection_map:
        return entries
    out = []
    for entry in entries:
        for ref in analyst.inputs:
            cid, _, src = ref.partition("/")
            if cid in entry.collection_ids or (cid in collection_map and entry.artifact in
                                               collection_map[cid]):
                if not src or src == (entry.source or ""):
                    out.append(entry)
                    break
    return out


def run_analysis(bundle: EvidenceBundle, *, provider: Provider, settings: AnalysisSettings,
                 analysts: list[Analyst], context: AnalysisContext,
                 collection_map: dict[str, list[str]] | None = None,
                 progress: ProgressFn | None = None,
                 stop_event: threading.Event | None = None) -> AnalysisResult:
    analysis_dir = ensure_dir(context.run_dir / "analysis")
    requests_dir = ensure_dir(analysis_dir / "requests")
    responses_dir = ensure_dir(analysis_dir / "responses")
    provider_info = {"name": provider.name, "model": provider.model,
                     "fake": provider.is_fake, "hosted": provider.is_hosted,
                     "options": provider.options()}
    result = AnalysisResult(analysis_status="running", provider=provider_info,
                            prompt_version=PROMPT_VERSION, started_at=now_iso())
    aliases = settings.field_aliases or None
    if progress:
        progress("analysis_phase", {"step": "deterministic review signals"})
    result.deterministic_findings = _deterministic_findings(bundle, aliases)

    if isinstance(provider, DisabledProvider):
        result.analysis_status = "not_requested"
        result.notes.append("No model was selected (--no-llm). Only deterministic review "
                            "signals and coverage are reported.")
        result.finished_at = now_iso()
        _save(analysis_dir, result)
        return result
    if provider.is_fake:
        result.notes.append("FAKE MODEL (test fixture): model findings below are generated "
                            "deterministically and are not a real model assessment.")

    bundle_ids = bundle.evidence_ids()
    budget_requests = settings.max_model_requests
    used_requests = 0
    request_failures = 0
    request_successes = 0
    model_counter = 0
    trusted_base = {"investigation_objective": context.objective,
                    "guidance": context.guidance,
                    "synthetic_test_data": context.synthetic}
    collection_map = collection_map or {}

    def call(req_id: str, kind: str, messages: list[dict], allowed: set[str], analyst_id: str,
             selection: dict) -> tuple[list[dict], list[dict], str | None]:
        """Run one request (+ at most one repair). Returns (accepted, rejected, error)."""
        nonlocal used_requests
        record = {
            "request_id": req_id, "kind": kind, "analyst_id": analyst_id,
            "prompt_version": PROMPT_VERSION, "provider": provider.name, "model": provider.model,
            "options": provider.options(), "max_output_tokens": settings.max_output_tokens,
            "profile_fingerprint": context.profile_fingerprint,
            "investigation_id": context.investigation_id, "created_at": now_iso(),
            "selection": selection, "allowed_evidence_ids": sorted(allowed),
            "messages": messages,
            "note": "This is the exact serialized request. It does not prove the model server "
                    "consumed every token (context truncation behaviour is server-specific).",
        }
        atomic_write_json(requests_dir / f"{req_id}.json", record)
        attempts = [messages]
        accepted: list[dict] = []
        rejected: list[dict] = []
        error = None
        for attempt in range(2):
            if request_size(attempts[-1]) > settings.max_model_input_chars:
                error = "max_model_input_chars exceeded; repair was not sent"
                result.requests.append({"request_id": req_id, "attempt": attempt + 1,
                                        "kind": kind, "status": "skipped_budget", "error": error})
                break
            if used_requests >= budget_requests:
                error = "max_model_requests reached before this request could run"
                break
            used_requests += 1
            try:
                response = provider.complete(attempts[-1], max_output_tokens=settings.max_output_tokens)
            except (ModelUnavailable, ModelError) as exc:
                error = exc.message
                result.requests.append({"request_id": req_id, "attempt": attempt + 1,
                                        "kind": kind, "status": "model_error", "error": error,
                                        "size_chars": len(str(attempts[-1]))})
                break
            suffix = "" if attempt == 0 else ".repair"
            atomic_write_text(responses_dir / f"{req_id}{suffix}.raw.txt", response.text)
            outcome = validate_model_output(response.text, allowed_ids=allowed,
                                            bundle_ids=bundle_ids, analyst_id=analyst_id,
                                            request_id=req_id, id_prefix=f"M-{req_id}")
            result.requests.append({
                "request_id": req_id, "attempt": attempt + 1, "kind": kind,
                "status": "valid" if not outcome.needs_repair else "issues",
                "accepted": len(outcome.accepted), "rejected": len(outcome.rejected),
                "errors": outcome.errors[:5], "duration_seconds": response.duration_seconds,
                "server_metadata": response.server_metadata,
            })
            accepted = outcome.accepted
            rejected = outcome.rejected
            if not outcome.needs_repair:
                error = None
                break
            error = "; ".join(outcome.errors[:3]) or f"{len(outcome.rejected)} finding(s) rejected"
            if attempt == 0:
                attempts.append(attempts[-1] + [
                    {"role": "assistant", "content": response.text[:20000]},
                    {"role": "user", "content": repair_instructions(outcome)},
                ])
                record["repair_requested"] = True
                atomic_write_json(requests_dir / f"{req_id}.repair.json",
                                  {**record, "messages": attempts[-1]})
        return accepted, rejected, error

    for analyst in analysts[:10]:
        if stop_event is not None and stop_event.is_set():
            result.errors.append("Analysis was stopped locally before all analysts ran.")
            break
        entries = _sources_for(analyst, bundle, collection_map)
        info = {"analyst_id": analyst.id, "objective": analyst.objective,
                "sources": [e.full_name for e in entries], "requests": 0, "status": "pending"}
        result.analysts.append(info)
        if not entries:
            info["status"] = "no_sources"
            continue
        views = []
        for entry in entries:
            name_key = entry.source or entry.artifact
            fields = (settings.model_input_fields.get(entry.full_name)
                      or settings.model_input_fields.get(name_key))
            view, _ = build_source_view(
                bundle, entry, max_rows=settings.max_rows_per_source_to_model,
                max_field_chars=settings.max_field_chars, sampling=settings.sampling,
                fields=fields, aliases=aliases)
            views.append(view)
        trusted = {**trusted_base, "analyst_role": analyst.id,
                   "analyst_objective": analyst.objective}
        remaining = max(0, budget_requests - used_requests)
        if remaining == 0:
            info["status"] = "skipped_budget"
            for v in views:
                result.skipped_model_coverage.append({"source_key": v.source_key,
                                                      "reason": "max_model_requests reached"})
            continue
        planned, skipped = plan_requests(analyst.id, trusted, views,
                                         max_chars=settings.max_model_input_chars,
                                         max_requests=remaining)
        result.skipped_model_coverage.extend(skipped)
        chunk_findings: list[dict] = []
        for req in planned:
            if progress:
                progress("analysis_request", {"analyst": analyst.id, "request": req.request_id,
                                              "size_chars": req.size_chars})
            selection = {"sources": [coverage_record(v) for v in req.views],
                         "size_chars": req.size_chars,
                         "max_model_input_chars": settings.max_model_input_chars}
            accepted, rejected, error = call(req.request_id, req.kind, req.messages,
                                             req.allowed_ids, analyst.id, selection)
            info["requests"] += 1
            if error and not accepted:
                request_failures += 1
                result.errors.append(f"{req.request_id}: {error}")
            else:
                request_successes += 1
            chunk_findings.extend(accepted)
            result.rejected_findings.extend(rejected)
        for v in views:
            result.model_input_coverage.append({**coverage_record(v), "analyst_id": analyst.id})
        final = chunk_findings
        if len(planned) > 1 and chunk_findings:
            syn = synthesis_request(analyst.id, trusted, chunk_findings,
                                    settings.max_model_input_chars)
            if syn is not None and used_requests < budget_requests:
                accepted, rejected, error = call(syn.request_id, "synthesis", syn.messages,
                                                 syn.allowed_ids, analyst.id,
                                                 {"validated_chunk_findings": len(chunk_findings)})
                result.rejected_findings.extend(rejected)
                if accepted:
                    final = accepted
                elif error:
                    result.errors.append(f"{syn.request_id}: {error}; chunk findings kept")
        for f in final:
            model_counter += 1
            f["finding_id"] = f"M-{model_counter:03d}"
            f["sources"] = sorted({e.full_name for e in entries
                                   if any(i in {r['evidence_id'] for v in views
                                                if v.source_key == e.source_key for r in v.rows}
                                          for i in f["evidence_ids"])})
            f["client_ids"] = sorted({e.client_id for e in entries})
        result.model_findings.extend(final)
        info["status"] = "completed" if planned and not any(
            e.startswith(analyst.id) for e in result.errors) else (
            "failed" if planned else "no_requests")

    if request_successes == 0 and request_failures > 0:
        result.analysis_status = "failed"
    elif request_failures or result.rejected_findings or result.skipped_model_coverage or \
            (stop_event is not None and stop_event.is_set()):
        result.analysis_status = "completed_with_issues"
    elif request_successes == 0:
        result.analysis_status = "no_model_requests"
    else:
        result.analysis_status = "completed"
    result.finished_at = now_iso()
    _save(analysis_dir, result)
    return result


def _save(analysis_dir: Path, result: AnalysisResult) -> None:
    atomic_write_json(analysis_dir / "findings.json", result.to_dict())
