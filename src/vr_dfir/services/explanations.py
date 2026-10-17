"""User-triggered, bounded AI explanations of a finding and its cited evidence."""

from __future__ import annotations

from vr_dfir.analysis.explain import explain
from vr_dfir.errors import DfirError, NotFoundError
from vr_dfir.jsonio import ensure_dir, read_json, sha256_json
from vr_dfir.services.reviews import load_reviews, review_of

MAX_FINDING_EVIDENCE = 20


def _provider(runtime):
    try:
        return runtime.provider(), None
    except DfirError as exc:
        return None, exc.message




def explain_finding(inv, runtime, finding_id: str, *, refresh: bool = False) -> dict:
    from vr_dfir.evidence.bundle import EvidenceBundle
    report_path = inv.report_dir / "report.json"
    if not report_path.is_file():
        raise NotFoundError("The report has not been generated yet.")
    report = read_json(report_path)
    finding = next((f for f in report.get("findings") or [] if f.get("finding_id") == finding_id),
                   None)
    if finding is None:
        raise NotFoundError(f"Finding {finding_id} is not in investigation {inv.id}.")
    review = review_of(load_reviews(inv.path), finding_id)
    deterministic = {k: finding.get(k) for k in ("finding_id", "title", "severity", "confidence",
                                                 "origin", "observation", "interpretation")}
    deterministic["review_status"] = review["state"]
    records = []
    if (inv.evidence_dir / "manifest.json").is_file():
        bundle = EvidenceBundle.open(inv.evidence_dir)
        for eid in (finding.get("evidence_ids") or [])[:MAX_FINDING_EVIDENCE]:
            try:
                rec = bundle.get_record(eid)
            except DfirError:
                continue
            records.append({"data": {"source": rec["full_name"], "host": rec.get("hostname"),
                                     "row": rec["row"]},
                            "identity": {"investigation_id": inv.id, "evidence_id": eid,
                                         "source_key": rec["source_key"],
                                         "row_index": rec["row_index"]}})
        fingerprint = sha256_json({"bundle": bundle.fingerprint(), "finding": finding})
    else:
        fingerprint = sha256_json({"finding": finding})
    scope = {"investigation_id": inv.id, "finding_id": finding_id}
    provider, error = _provider(runtime)
    if provider is None:
        return {"action": "finding_summary", "status": "unavailable", "reason": error,
                "deterministic": deterministic, "scope": scope, "cached": False}
    return explain(provider, runtime.settings.model, action="finding_summary",
                   deterministic=deterministic, records=records, scope=scope,
                   fingerprint=fingerprint, cache_dir=ensure_dir(inv.path / "explanations"),
                   refresh=refresh)
