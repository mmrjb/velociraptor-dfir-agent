"""Local finding review and evidence-linked explanations; no backend mutations."""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from vr_dfir.errors import NotFoundError
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.jsonio import read_json
from vr_dfir.services.reviews import load_reviews, set_review


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ExplainRequest(_Body):
    refresh: bool = False
    action: str = Field(default="finding_summary", pattern="^(finding_summary|next_question)$")

class ReviewRequest(_Body):
    state: str = Field(max_length=40)
    note: str | None = Field(default=None, max_length=2000)

def _ok(data):
    from fastapi.responses import JSONResponse
    return JSONResponse({"ok": True, "data": data})

def register(app: FastAPI, st) -> None:
    @app.put("/api/v1/investigations/{inv_id}/findings/{finding_id}/review")
    def review(inv_id: str, finding_id: str, body: ReviewRequest):
        _, inv = st.find_investigation(inv_id)
        path = inv.report_dir / "report.json"
        if not path.is_file():
            raise NotFoundError("The report has not been generated yet.")
        found = {f.get("finding_id") for f in read_json(path).get("findings") or []}
        return _ok(set_review(inv.path, found, finding_id, body.state, body.note,
                              by="local UI"))

    @app.get("/api/v1/investigations/{inv_id}/reviews")
    def reviews(inv_id: str):
        _, inv = st.find_investigation(inv_id)
        return _ok(load_reviews(inv.path))

    @app.post("/api/v1/investigations/{inv_id}/findings/{finding_id}/explain")
    def explain_finding_route(inv_id: str, finding_id: str, body: ExplainRequest):
        from vr_dfir.services.explanations import explain_finding
        _, inv = st.find_investigation(inv_id)
        return _ok(explain_finding(inv, st.runtime, finding_id, refresh=body.refresh,
                                   action=body.action))

    @app.get("/api/v1/investigations/{inv_id}/evidence/{evidence_id}/locate")
    def locate_evidence(inv_id: str, evidence_id: str, limit: int = 50):
        """Source and page offset that show one evidence row (exact; no nearby row)."""
        _, inv = st.find_investigation(inv_id)
        if not (inv.evidence_dir / "manifest.json").is_file():
            raise NotFoundError("No evidence has been saved for this investigation yet.")
        rec = EvidenceBundle.open(inv.evidence_dir).get_record(evidence_id)
        limit = max(1, min(limit, 200))
        return _ok({"evidence_id": rec["evidence_id"], "source_key": rec["source_key"],
                    "row_index": rec["row_index"], "full_name": rec["full_name"],
                    "offset": (rec["row_index"] // limit) * limit, "limit": limit})

