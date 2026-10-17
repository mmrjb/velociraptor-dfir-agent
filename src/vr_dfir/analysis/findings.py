"""Structured findings and validation of model output.

Citation validation establishes that each cited evidence ID exists in the investigation
bundle *and* was supplied to the request that produced the finding (or to a validated
intermediate). It does not prove the interpretation is correct; reports say so.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES = ("informational", "low", "medium", "high")
CONFIDENCES = ("low", "medium", "high")
ORIGINS = ("deterministic_rule", "model_proposal", "analyst_input")
MAX_TEXT = 2000
MAX_LIST = 12
_VERDICT_RE = re.compile(
    r"\b(no (threats?|malicious activity|indicators?) (were |was )?(found|detected)|"
    r"(host|system|machine|endpoint) (is|appears) (clean|safe|uncompromised)|"
    r"all clear|clean bill of health)\b", re.I)
_PROBABILITY_RE = re.compile(r"\b\d{1,3}(\.\d+)?\s?%\s*(chance|probability|likelihood|likely)\b",
                             re.I)


@dataclass
class Finding:
    finding_id: str
    title: str
    observation: str
    interpretation: str
    evidence_ids: list[str]
    severity: str
    confidence: str
    confidence_rationale: str
    benign_explanations: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    origin: str = "model_proposal"
    analyst_id: str = ""
    rule_id: str | None = None
    client_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    request_id: str | None = None
    validation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationOutcome:
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    parsed: bool = False

    @property
    def needs_repair(self) -> bool:
        return (not self.parsed) or bool(self.rejected)


def extract_json(text: str) -> Any:
    """Parse model output as JSON. Accepts a single fenced ```json block; nothing else."""
    stripped = (text or "").strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S)
    if fence:
        stripped = fence.group(1)
    return json.loads(stripped)


def _str(value, name: str, errors: list[str], required: bool = True) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            errors.append(f"'{name}' is required")
        return ""
    if not isinstance(value, str):
        errors.append(f"'{name}' must be a string")
        return ""
    if len(value) > MAX_TEXT:
        errors.append(f"'{name}' is longer than {MAX_TEXT} characters")
    return value.strip()


def _str_list(value, name: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors.append(f"'{name}' must be a list of strings")
        return []
    if len(value) > MAX_LIST:
        errors.append(f"'{name}' has more than {MAX_LIST} items")
    return [v.strip()[:MAX_TEXT] for v in value if v.strip()]


ALLOWED_KEYS = {"title", "observation", "interpretation", "evidence_ids", "severity",
                "confidence", "confidence_rationale", "benign_explanations", "missing_context",
                "next_steps"}


def validate_model_output(text: str, *, allowed_ids: set[str], bundle_ids: set[str],
                          analyst_id: str, request_id: str, id_prefix: str) -> ValidationOutcome:
    outcome = ValidationOutcome()
    try:
        data = extract_json(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        outcome.errors.append(f"Output is not valid JSON: {exc}")
        return outcome
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        outcome.errors.append("Output must be a JSON object with a 'findings' list.")
        return outcome
    outcome.parsed = True
    if len(data["findings"]) > 20:
        outcome.errors.append("More than 20 findings; only the first 20 were considered.")
    for n, raw in enumerate(data["findings"][:20], start=1):
        errs: list[str] = []
        if not isinstance(raw, dict):
            outcome.rejected.append({"raw": raw, "reasons": ["finding must be an object"]})
            continue
        extra = sorted(set(raw) - ALLOWED_KEYS)
        if extra:
            errs.append(f"unsupported fields {extra}")
        title = _str(raw.get("title"), "title", errs)
        observation = _str(raw.get("observation"), "observation", errs)
        interpretation = _str(raw.get("interpretation"), "interpretation", errs)
        rationale = _str(raw.get("confidence_rationale"), "confidence_rationale", errs)
        severity = raw.get("severity")
        if severity not in SEVERITIES:
            errs.append(f"severity must be one of {list(SEVERITIES)}")
        confidence = raw.get("confidence")
        if confidence not in CONFIDENCES:
            errs.append(f"confidence must be one of {list(CONFIDENCES)}")
        evidence = raw.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(e, str) for e in evidence):
            errs.append("evidence_ids must be a non-empty list of evidence ID strings")
            evidence = []
        invented = [e for e in evidence if e not in bundle_ids]
        not_supplied = [e for e in evidence if e in bundle_ids and e not in allowed_ids]
        if invented:
            errs.append(f"cites evidence IDs that do not exist in the bundle: {invented[:5]}")
        if not_supplied:
            errs.append(f"cites evidence IDs that were not supplied to this request: "
                        f"{not_supplied[:5]}")
        for label, text_value in (("observation", observation), ("interpretation", interpretation),
                                  ("title", title)):
            if _VERDICT_RE.search(text_value or ""):
                errs.append(f"{label} states an unsupported clean/safe verdict")
            if _PROBABILITY_RE.search(text_value or ""):
                errs.append(f"{label} states an invented numeric probability")
        benign = _str_list(raw.get("benign_explanations"), "benign_explanations", errs)
        missing = _str_list(raw.get("missing_context"), "missing_context", errs)
        steps = _str_list(raw.get("next_steps"), "next_steps", errs)
        if errs:
            outcome.rejected.append({"raw": raw, "reasons": errs, "request_id": request_id})
            continue
        finding = Finding(
            finding_id=f"{id_prefix}-{n:02d}", title=title, observation=observation,
            interpretation=interpretation, evidence_ids=list(dict.fromkeys(evidence)),
            severity=severity, confidence=confidence, confidence_rationale=rationale,
            benign_explanations=benign, missing_context=missing, next_steps=steps,
            origin="model_proposal", analyst_id=analyst_id, request_id=request_id,
            validation={"citations": "all cited evidence IDs exist and were supplied to the "
                                     "request", "schema": "valid"},
        )
        outcome.accepted.append(finding.to_dict())
    return outcome


def repair_instructions(outcome: ValidationOutcome) -> str:
    problems = list(outcome.errors)
    for r in outcome.rejected:
        problems.append("; ".join(r.get("reasons", [])))
    return (
        "Your previous output was rejected by the validator for these reasons:\n- "
        + "\n- ".join(problems[:15])
        + "\nReturn the complete JSON object again. Cite only evidence_id values that appear "
        "in the evidence block. Do not add fields. Do not state that the host is clean or safe."
    )
