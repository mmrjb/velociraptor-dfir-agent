"""Analyst review status of findings, kept separate from the generated report.

Severity and confidence describe a finding (set by a deterministic rule or a validated
model proposal); the *review status* records what an analyst decided. It is stored per
investigation in ``reviews.json`` (current state) plus ``reviews.jsonl`` (append-only
history). The report, the evidence bundle, and the original finding are never modified,
and no AI path writes a review status.
"""

from __future__ import annotations

import threading
from pathlib import Path

from vr_dfir.config import current_user
from vr_dfir.errors import NotFoundError, ValidationError
from vr_dfir.evidence.bundle import now_iso
from vr_dfir.jsonio import JsonlWriter, atomic_write_json, read_json

REVIEW_STATES = {
    "unreviewed": "Unreviewed",
    "in_review": "In review",
    "needs_follow_up": "Needs follow-up",
    "confirmed": "Confirmed by analyst",
    "benign": "Benign / expected",
}
MAX_NOTE = 2000
_lock = threading.Lock()


def load_reviews(run_dir: Path) -> dict[str, dict]:
    path = Path(run_dir) / "reviews.json"
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def set_review(run_dir: Path, finding_ids: set[str], finding_id: str, state: str,
               note: str | None = None, *, by: str | None = None) -> dict:
    if finding_id not in finding_ids:
        raise NotFoundError(f"Finding {finding_id} is not in this investigation's report.")
    if state not in REVIEW_STATES:
        raise ValidationError(f"Review status must be one of {', '.join(REVIEW_STATES)}.")
    if note is not None and len(note) > MAX_NOTE:
        raise ValidationError(f"The review note is longer than {MAX_NOTE} characters.")
    entry = {"state": state, "label": REVIEW_STATES[state], "note": (note or "").strip() or None,
             "updated_at": now_iso(), "by": by or current_user()}
    with _lock:
        reviews = load_reviews(run_dir)
        previous = reviews.get(finding_id)
        reviews[finding_id] = entry
        atomic_write_json(Path(run_dir) / "reviews.json", reviews)
        with JsonlWriter(Path(run_dir) / "reviews.jsonl") as log:
            log.write({"finding_id": finding_id, "previous": (previous or {}).get("state"),
                       **entry})
    return entry


def review_of(reviews: dict, finding_id: str) -> dict:
    return reviews.get(finding_id) or {"state": "unreviewed",
                                       "label": REVIEW_STATES["unreviewed"], "note": None,
                                       "updated_at": None, "by": None}
