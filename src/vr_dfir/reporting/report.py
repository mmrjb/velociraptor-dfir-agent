"""Canonical report (JSON). Markdown and HTML render this same structure."""

from __future__ import annotations

from collections import Counter, defaultdict

from vr_dfir.evidence.bundle import EvidenceBundle, now_iso
from vr_dfir.version import __version__

REPORT_VERSION = 1
MAX_CITED_ROWS = 400

OUTCOME_LABELS = {
    "complete": "Complete",
    "partial": "Partial - coverage gaps",
    "failed": "Failed",
    "interrupted": "Interrupted - recoverable",
    "evidence_only": "Evidence only (no AI analysis)",
    "running": "In progress",
}

CITATION_NOTE = (
    "Citation validation confirms that each cited evidence ID exists in this investigation's "
    "evidence bundle and was supplied to the analysis request that produced the finding. It "
    "does not prove that the interpretation is correct."
)


def coverage_summary(bundle: EvidenceBundle | None) -> dict:
    if bundle is None:
        return {"sources": [], "counts": {}, "gaps": ["No evidence was retrieved."]}
    sources = []
    counts: Counter = Counter()
    gaps = []
    for entry in bundle.sources():
        cov = entry.coverage
        counts[cov.result_status] += 1
        item = {
            "source_key": entry.source_key, "full_name": entry.full_name,
            "client_id": entry.client_id, "hostname": entry.hostname, "flow_id": entry.flow_id,
            "hunt_id": entry.hunt_id, "counts_only": entry.counts_only,
            "collection_status": cov.collection_status, "read_status": cov.read_status,
            "result_status": cov.result_status,
            "available_rows": cov.available_rows if cov.available_rows is not None else "unknown",
            "available_rows_basis": cov.available_rows_basis,
            "retrieved_rows": cov.retrieved_rows, "persisted_rows": cov.persisted_rows,
            "pagination_finished": cov.pagination_finished, "stop_reason": cov.stop_reason,
            "error": cov.error, "notes": cov.notes,
        }
        sources.append(item)
        where = f"{entry.full_name} on {entry.hostname or entry.client_id}"
        if cov.result_status == "source_failed":
            gaps.append(f"{where}: the client reported an error ({cov.error}).")
        elif cov.result_status == "skipped_precondition":
            gaps.append(f"{where}: skipped by a precondition (verified in the flow log).")
        elif cov.result_status == "empty_or_skipped":
            gaps.append(f"{where}: zero rows; empty and skipped cannot be distinguished.")
        elif cov.result_status in ("unknown", "not_collected", "unavailable"):
            gaps.append(f"{where}: {cov.result_status.replace('_', ' ')}"
                        + (f" ({cov.error})" if cov.error else "") + ".")
        if cov.read_status in ("incomplete", "read_failed"):
            gaps.append(f"{where}: retrieval {cov.read_status.replace('_', ' ')} "
                        f"(stop reason: {cov.stop_reason or 'unknown'}; "
                        f"{cov.persisted_rows} rows saved).")
        if entry.counts_only:
            gaps.append(f"{where}: counts only - rows were not supplied to the model.")
    return {"sources": sources, "counts": dict(counts), "gaps": gaps}


def _evidence_refs(bundle: EvidenceBundle | None, findings: list[dict]) -> tuple[dict, list[str]]:
    rows: dict[str, dict] = {}
    problems: list[str] = []
    if bundle is None:
        return rows, problems
    for f in findings:
        for eid in f.get("evidence_ids", []):
            if eid in rows or len(rows) >= MAX_CITED_ROWS:
                continue
            try:
                rows[eid] = bundle.get_record(eid)
            except Exception as exc:  # noqa: BLE001 - reported as a validation issue
                problems.append(f"Finding {f.get('finding_id')}: evidence {eid} could not be "
                                f"resolved ({exc}).")
    return rows, problems


def summarize_outcome(meta: dict, coverage: dict, analysis: dict | None) -> list[str]:
    statements = []
    sources = coverage.get("sources", [])
    with_rows = sum(1 for s in sources if s["result_status"] == "rows")
    statements.append(f"{len(sources)} source(s) in scope; {with_rows} returned rows.")
    if coverage.get("gaps"):
        statements.append(f"{len(coverage['gaps'])} coverage gap(s) are listed below; they "
                          "limit what this report can say.")
    status = (analysis or {}).get("analysis_status")
    if status == "not_requested":
        statements.append("No model analysis was requested; findings are deterministic review "
                          "signals only.")
    elif status == "failed":
        statements.append("Model analysis failed; evidence was saved and analysis can be "
                          "retried without recollection.")
    elif status == "completed_with_issues":
        statements.append("Model analysis completed with issues (rejected findings, failed "
                          "or skipped requests).")
    if (analysis or {}).get("provider", {}).get("fake"):
        statements.append("FAKE MODEL output (test fixture) is included; it is not a real model "
                          "assessment.")
    statements.append("The absence of accepted findings is not evidence that the target is "
                      "clean; it reflects only the reviewed, retrieved data.")
    return statements


def build_report(meta: dict, *, bundle: EvidenceBundle | None, analysis: dict | None,
                 plan: dict | None = None, checkpoint: dict | None = None) -> dict:
    coverage = coverage_summary(bundle)
    det = list((analysis or {}).get("deterministic_findings", []))
    model = list((analysis or {}).get("model_findings", []))
    findings = model + det
    cited, problems = _evidence_refs(bundle, findings)
    for f in findings:
        f["evidence_refs"] = [
            {k: cited[e][k] for k in ("evidence_id", "client_id", "hostname", "flow_id",
                                      "hunt_id", "artifact", "source", "row_index", "org_id",
                                      "raw_sha256")}
            if e in cited else {"evidence_id": e, "unresolved": True}
            for e in f.get("evidence_ids", [])
        ]
    hunt = None
    if meta.get("mode") == "existing_hunt":
        hunt = _hunt_section(meta, coverage, findings, checkpoint)
    report = {
        "report_version": REPORT_VERSION,
        "generator": f"velociraptor-dfir-agent {__version__}",
        "generated_at": now_iso(),
        "synthetic": bool(meta.get("synthetic")),
        "data_label": ("SYNTHETIC TEST DATA - not collected from real endpoints"
                       if meta.get("synthetic") else None),
        "investigation": {
            "id": meta.get("investigation_id"), "title": meta.get("title"),
            "mode": meta.get("mode"), "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"), "phase": meta.get("phase"),
            "outcome": meta.get("outcome"),
            "outcome_label": OUTCOME_LABELS.get(meta.get("outcome", ""), meta.get("outcome")),
        },
        "scope": meta.get("scope", {}),
        "profile": meta.get("profile"),
        "status": {
            "collection": meta.get("collection_status"),
            "retrieval": meta.get("retrieval_status"),
            "analysis": meta.get("analysis_status"),
            "outcome": meta.get("outcome"),
            "note": ("Collection, retrieval, and analysis are reported separately. A completed "
                     "collection does not mean the analysis completed or that the target is "
                     "safe."),
        },
        "timing": {"created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
                   "phases": meta.get("phase_times", {})},
        "provider": (analysis or {}).get("provider") or {"name": "none"},
        "analysis": {k: (analysis or {}).get(k) for k in (
            "analysis_status", "prompt_version", "requests", "errors", "notes", "analysts",
            "model_input_coverage", "skipped_model_coverage")},
        "summary_statements": summarize_outcome(meta, coverage, analysis),
        "coverage": coverage,
        "findings": findings,
        "rejected_findings": [
            {"reasons": r.get("reasons"), "request_id": r.get("request_id"),
             "title": (r.get("raw") or {}).get("title") if isinstance(r.get("raw"), dict) else None}
            for r in (analysis or {}).get("rejected_findings", [])
        ],
        "evidence": {"cited_rows": list(cited.values()), "cited_rows_limit": MAX_CITED_ROWS,
                     "bundle_id": bundle.bundle_id if bundle else None,
                     "resolution_problems": problems,
                     "note": "Only cited rows are embedded. The full evidence remains in the "
                             "restricted evidence bundle."},
        "errors": meta.get("errors", []),
        "citation_validation_note": CITATION_NOTE,
        "hunt": hunt,
        "next_steps": _next_steps(meta, coverage, analysis),
    }
    return report


def _hunt_section(meta: dict, coverage: dict, findings: list[dict], checkpoint: dict | None
                  ) -> dict:
    flows = ((checkpoint or {}).get("existing") or {}).get("hunt_flows", [])
    states = Counter(f.get("state") for f in flows)
    by_client = defaultdict(list)
    for f in findings:
        for cid in f.get("client_ids", []):
            by_client[cid].append(f["finding_id"])
    return {
        "hunt_id": meta.get("scope", {}).get("hunt_id"),
        "contributing_flows_seen": len(flows),
        "flow_states": dict(states),
        "enumeration_complete": ((checkpoint or {}).get("existing") or {}).get(
            "enumeration_complete"),
        "clients_with_findings": dict(by_client),
        "coverage_note": ("Coverage applies only to the enumerated and retrieved flows. Never "
                          "read as whole-fleet coverage when budgets or filters limited the "
                          "subset."),
        "flows": flows[:500],
    }


def _next_steps(meta: dict, coverage: dict, analysis: dict | None) -> list[str]:
    steps = []
    if any(s["read_status"] in ("incomplete", "read_failed") for s in coverage.get("sources", [])):
        steps.append("Resume reading to retrieve the remaining rows (no recollection).")
    status = (analysis or {}).get("analysis_status")
    if status in ("failed", "completed_with_issues"):
        steps.append("Retry analysis after checking the configured model (uses saved evidence).")
    if status == "not_requested":
        steps.append("Optionally run model analysis on the saved evidence.")
    if any(s["result_status"] in ("source_failed", "unknown", "not_collected")
           for s in coverage.get("sources", [])):
        steps.append("Review the client-side errors for failed sources before drawing "
                     "conclusions.")
    return steps
