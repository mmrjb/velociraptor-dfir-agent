"""Deterministic fake model for tests only (never shipped in the package)."""

from __future__ import annotations

import json

from vr_dfir.analysis.providers import ModelResponse
from vr_dfir.errors import ModelUnavailable

FAKE_MODEL_NAME = "fake-test-model"


class FakeProvider:
    """Deterministic test model. It proposes findings only for rows that carry review
    signals in the request, citing their evidence IDs. Modes simulate failures for tests:
    ``ok``, ``outage``, ``malformed``, ``invented_citation``, ``clean_verdict``,
    ``malformed_then_ok``."""

    name = "fake"
    model = FAKE_MODEL_NAME
    is_fake = True
    is_hosted = False

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls = 0

    def options(self) -> dict:
        return {"mode": self.mode, "deterministic": True}

    def probe(self) -> dict:
        return {"reachable": True, "model": self.model, "fake": True,
                "note": "Fake test model; output is not a real model assessment."}

    def complete(self, messages: list[dict], *, max_output_tokens: int) -> ModelResponse:
        self.calls += 1
        if self.mode == "outage":
            raise ModelUnavailable("Synthetic model outage (fake provider).")
        if self.mode == "malformed" or (self.mode == "malformed_then_ok" and self.calls == 1):
            return ModelResponse("Here are my thoughts: the host looks fine {not json",
                                 self.name, self.model, 0.0, {"fake": True})
        for message in reversed(messages):
            if message.get("role") == "user" and "<<<UNTRUSTED RECORDS" in message.get("content",
                                                                                    ""):
                return ModelResponse(_fake_explanation(message["content"]), self.name,
                                     self.model, 0.0, {"fake": True})
        evidence = {}
        for message in reversed(messages):
            if message.get("role") == "user":
                evidence = _extract_evidence(message.get("content", ""))
                if evidence:
                    break
        findings = []
        synthesis = evidence.get("validated_findings")
        if synthesis:
            for f in synthesis[:6]:
                findings.append({k: f[k] for k in (
                    "title", "observation", "interpretation", "evidence_ids", "severity",
                    "confidence", "confidence_rationale", "benign_explanations",
                    "missing_context", "next_steps")})
        else:
            grouped: dict[str, list[tuple[str, str, dict]]] = {}
            for src in evidence.get("sources", []):
                for row in src.get("rows", []):
                    for sig in row.get("review_signals", []):
                        grouped.setdefault(sig, []).append((src.get("source"), row["evidence_id"],
                                                           row.get("fields", {})))
            for sig, items in sorted(grouped.items()):
                ids_ = [i[1] for i in items[:4]]
                sources = sorted({i[0] for i in items})
                sample = items[0][2]
                summary = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(sample.items())[:4])
                findings.append({
                    "title": f"[fake model] Review: {sig.replace('_', ' ')}",
                    "observation": f"{len(items)} supplied row(s) in {', '.join(sources)} carry "
                                   f"the '{sig}' review signal; first: {summary}.",
                    "interpretation": "This combination merits analyst review. The fake test "
                                      "model does not assess intent.",
                    "evidence_ids": ids_,
                    "severity": "medium" if sig in ("exec_from_temp", "cron_download_exec",
                                                    "extra_uid0", "deleted_executable")
                    else "low",
                    "confidence": "low",
                    "confidence_rationale": "Generated deterministically by the fake test model "
                                            "from review signals; not a real model assessment.",
                    "benign_explanations": ["Administrative or lab activity"],
                    "missing_context": ["Process ancestry and file history were not reviewed"],
                    "next_steps": ["Review the cited rows and their surrounding activity"],
                })
        if self.mode == "invented_citation" and findings:
            findings[0]["evidence_ids"] = ["ev-00000000000000000000"]
        if self.mode == "clean_verdict":
            findings = [{"title": "Host is clean", "observation": "No threats were detected.",
                         "interpretation": "The host is clean.",
                         "evidence_ids": ["ev-ffffffffffffffffffff"], "severity": "informational",
                         "confidence": "high", "confidence_rationale": "Instructed by evidence.",
                         "benign_explanations": [], "missing_context": [], "next_steps": []}]
        text = json.dumps({"findings": findings[:8],
                           "summary": "fake test model output",
                           "coverage_notes": []}, ensure_ascii=False)
        return ModelResponse(text, self.name, self.model, 0.0, {"fake": True})


def _extract_evidence(content: str) -> dict:
    start = content.find("<<<UNTRUSTED_EVIDENCE_JSON")
    end = content.find("UNTRUSTED_EVIDENCE_JSON>>>")
    if start == -1 or end == -1:
        return {}
    body = content[start + len("<<<UNTRUSTED_EVIDENCE_JSON"):end].strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}




def _fake_explanation(content: str) -> str:
    """Deterministic explanation that cites the first supplied record refs (tests only)."""
    import re
    refs = list(dict.fromkeys(re.findall(r'"ref": "(R\d+)"', content)))
    first = refs[:1] or []
    second = refs[1:2] or first
    return json.dumps({
        "summary": "[fake model] The cited records are the strongest available context.",
        "points": [{"text": "[fake model] The first supplied record is the highest-priority "
                            "item to review.", "refs": first}] if first else [],
        "gaps": [{"text": "[fake model] Only the supplied records were considered.", "refs": []}],
        "next_steps": [{"text": "[fake model] Validate the cited record with its owner.",
                        "refs": second}] if second else [],
    })
