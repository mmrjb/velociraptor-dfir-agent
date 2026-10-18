"""Reports distinguish evidence, prepared model input and inference outcomes."""

import copy
import hashlib
import re

import pytest

from support.synthetic import start_scenario
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.reporting import fonts
from vr_dfir.reporting.render import render_html, render_markdown
from vr_dfir.reporting.report import build_report, model_coverage_summary


def test_report_order_counts_and_citations_do_not_change_findings():
    analysis = {
        "model_findings": [{"finding_id": "M-001", "severity": "informational"}],
        "deterministic_findings": [{"finding_id": "R-001", "severity": "medium"}],
    }
    original = copy.deepcopy(analysis)
    report = build_report({}, bundle=None, analysis=analysis)
    assert [f["finding_id"] for f in report["findings"]] == ["R-001", "M-001"]
    summary = " ".join(report["summary_statements"])
    assert "2 accepted" in summary and "1 model proposal" in summary
    assert "1 deterministic rule" in summary and "1 medium, 1 informational" in summary
    assert "absence of accepted findings" not in summary
    assert "need not have been supplied to a model" in report["citation_validation_note"]
    assert analysis == original


@pytest.mark.parametrize("status", ["valid", "model_error", "skipped_budget"])
def test_prepared_views_are_not_successful_inference_coverage(status):
    coverage = {"sources": [
        {"source_key": "processes", "full_name": "Test/Processes", "client_id": "C.TEST",
         "persisted_rows": 244},
        {"source_key": "users", "full_name": "Test/Users", "client_id": "C.TEST",
         "persisted_rows": 52},
    ]}
    analysis = {
        "model_input_coverage": [{"source_key": "processes", "analyst_id": "review",
                                  "rows_supplied": 2, "sampling": "signals_then_stratified",
                                  "fields": ["Name"], "truncated_values": 1,
                                  "reduced_for_budget": True}],
        "requests": [{"status": status}],
    }
    result = model_coverage_summary(coverage, analysis)
    selected, excluded = result["sources"]
    assert selected["rows_selected"] == 2 and selected["rows_persisted"] == 244
    assert selected["state"] == "Prepared input view"
    assert excluded["state"] == "Not selected" and excluded["rows_selected"] is None
    assert result["request_statuses"] == {status: 1}
    assert "not proof of a successful request" in result["note"]


def test_missing_metadata_and_request_budget_do_not_imply_full_coverage():
    coverage = {"sources": [{"source_key": "x", "full_name": "Test/X", "client_id": "C.TEST",
                             "persisted_rows": 30}]}
    for analysis, state in (({}, "Not recorded"), ({"analysis_status": "not_requested"},
                                                 "Not requested"),
                            ({"skipped_model_coverage": [{"source_key": "x",
                                                           "reason": "request budget"}]},
                             "Skipped")):
        row = model_coverage_summary(coverage, analysis)["sources"][0]
        assert row["state"] == state and row["rows_selected"] is None


def test_rebuild_uses_saved_analysis_and_evidence_without_network(synth, monkeypatch):
    inv = start_scenario(synth, "review")
    synth.execute(inv.id)
    protected = [p for folder in (inv.evidence_dir, inv.path / "analysis")
                 for p in folder.rglob("*") if p.is_file()]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    queries = list(synth.runtime.fake_server.queries)

    def no_model(**kwargs):
        raise AssertionError("Report rebuilding must not request inference")

    monkeypatch.setattr(synth.runtime, "provider", no_model)
    rebuilt = synth.rebuild_report(inv.id)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    assert synth.runtime.fake_server.queries == queries
    assert rebuilt["model_coverage"]["sources"]
    html, md = render_html(rebuilt), render_markdown(rebuilt)
    for text in (html, md):
        assert "Model input coverage" in text and "Evidence coverage" in text
        assert "Prepared input view" in text
    bundle = EvidenceBundle.open(inv.evidence_dir)
    assert all(r["evidence_id"] in bundle.evidence_ids()
               for r in rebuilt["evidence"]["cited_rows"])


def test_standalone_fonts_include_license_without_external_requests(synth):
    if not fonts.ASSETS.is_dir():
        pytest.skip("Built UI fonts are checked by the packaging job")
    inv = start_scenario(synth, "ordinary", no_llm=True)
    synth.execute(inv.id)
    html = (inv.report_dir / "report.html").read_text()
    assert html.count("data:font/woff2;base64,") == 3
    assert "Inter Project Authors" in html and "Vazirmatn Project Authors" in html
    assert "JetBrains Mono Project Authors" in html and "SIL OPEN FONT LICENSE" in html
    assert "font-src data:" in html and "script-src 'none'" in html
    assert not re.search(r'(src|href)="https?://', html)


def test_unbuilt_checkout_can_render_with_system_fonts(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "ASSETS", tmp_path / "missing")
    assert fonts.embedded_fonts() == ("", "")
