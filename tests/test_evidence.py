import json

import pytest

from vr_dfir.errors import NotFoundError, ScopeError, ValidationError
from vr_dfir.evidence.bundle import EvidenceBundle, evidence_id, migrate_one_based
from vr_dfir.evidence.retrieval import Budget, SourceRequest, read_flow
from vr_dfir.jsonio import read_json

ORG, C, F = "OSYNTHLAB", "C.SYNTH0000000002", "F.SYNTH0002"
RET = {"read_retries": 2, "retry_backoff_seconds": 0, "max_concurrent_reads": 1}


def new_bundle(tmp_path):
    return EvidenceBundle.create(tmp_path / "b", investigation_id="inv-test-0001",
                                 scope={"org_id": ORG}, synthetic=True)


def read(vr, bundle, sources, limits=None, budget=None, flow=F, client=C):
    status = vr.flow_status(client, flow)
    return read_flow(vr, bundle, org_id=ORG, client_id=client, flow_id=flow, hostname="h",
                     status=status, sources=sources,
                     limits=limits or {"page_size": 50, "max_rows_per_source": 10000},
                     budget=budget or Budget(10**6, 10**9), retrieval=RET,
                     sleep=lambda s: None)


def test_pages_exact_and_stable(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    req = [SourceRequest("LTH.SystemBaseline", "Processes")]
    read(vr, bundle, req, {"page_size": 40, "max_rows_per_source": 10000})  # 160 = 4 full pages
    entry = bundle.sources()[0]
    assert entry.coverage.persisted_rows == 160 and entry.coverage.read_status == "complete"
    assert entry.coverage.pagination_finished and entry.coverage.available_rows == 160
    rows = [r for _, r in bundle.iter_records(entry.source_key)]
    assert [r["row_index"] for r in rows] == list(range(160))
    assert rows[141]["row"]["Name"] == "kworkerd"
    assert bundle.verify() == []


def test_row_budget_marks_incomplete(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    read(vr, bundle, [SourceRequest("LTH.SystemBaseline", "Processes")],
         {"page_size": 50, "max_rows_per_source": 75})
    cov = bundle.sources()[0].coverage
    assert cov.persisted_rows == 75 and cov.read_status == "incomplete"
    assert cov.stop_reason == "row_budget" and not cov.pagination_finished


def test_total_budget_shared(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    budget = Budget(max_total_rows=100, max_total_bytes=10**9)
    read(vr, bundle, [SourceRequest("LTH.SystemBaseline", "Processes"),
                      SourceRequest("LTH.SystemBaseline", "DebianPackages")], budget=budget)
    total = sum(e.coverage.persisted_rows for e in bundle.sources())
    assert total == 100
    assert any(e.coverage.stop_reason == "total_row_budget" for e in bundle.sources())


def test_resume_after_failure_no_loss_no_duplicates(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    req = [SourceRequest("LTH.SystemBaseline", "NetworkConnections")]
    read(vr, bundle, req, {"page_size": 500}, client="C.SYNTH0000000003", flow="F.SYNTH0003")
    entry = bundle.sources()[0]
    assert entry.coverage.persisted_rows == 500 and entry.coverage.read_status == "incomplete"
    assert entry.coverage.stop_reason == "read_error"
    ids_before = [r["evidence_id"] for _, r in bundle.iter_records(entry.source_key)]
    bundle = EvidenceBundle.open(bundle.path)  # e.g. after a restart
    read(vr, bundle, req, {"page_size": 500}, client="C.SYNTH0000000003", flow="F.SYNTH0003")
    entry = bundle.sources()[0]
    records = [r for _, r in bundle.iter_records(entry.source_key)]
    assert len(records) == 1200 and entry.coverage.read_status == "complete"
    assert [r["evidence_id"] for r in records[:500]] == ids_before
    assert len({r["evidence_id"] for r in records}) == 1200
    assert bundle.verify() == []


def test_raw_values_and_unicode_preserved(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    read(vr, bundle, [SourceRequest("Custom.Example", "Notes")], client="C.SYNTH0000000001",
         flow="F.SYNTH0006")
    rows = {r["row"]["Key"]: r["row"] for _, r in bundle.iter_records(bundle.sources()[0].source_key)}
    assert rows["zero_value_example"]["Value"] == 0
    assert rows["false_value_example"]["Value"] is False
    assert rows["empty_string_example"]["Value"] == ""
    assert rows["null_value_example"]["Value"] is None
    bundle2 = new_bundle(tmp_path / "u")
    read(vr, bundle2, [SourceRequest("LTH.SystemBaseline", "Users")])
    text = (bundle2.path / bundle2.sources()[0].file).read_text(encoding="utf-8")
    assert "مدیر سیستم" in text  # stored as UTF-8, not escaped


def test_evidence_ids_scoped_not_content_hash():
    a = evidence_id("O1", "C.1", "F.1", "A.B", "S", 0)
    assert a == evidence_id("O1", "C.1", "F.1", "A.B", "S", 0)
    others = {evidence_id("O2", "C.1", "F.1", "A.B", "S", 0),
              evidence_id("O1", "C.2", "F.1", "A.B", "S", 0),
              evidence_id("O1", "C.1", "F.2", "A.B", "S", 0),
              evidence_id("O1", "C.1", "F.1", "A.B", "T", 0),
              evidence_id("O1", "C.1", "F.1", "A.B", "S", 1)}
    assert a not in others and len(others) == 5


def test_identical_rows_get_distinct_ids(tmp_path):
    bundle = new_bundle(tmp_path)
    key = bundle.ensure_source(org_id=ORG, client_id=C, flow_id=F, artifact="A.B", source="S").source_key
    bundle.append_rows(key, [{"x": 1}, {"x": 1}], 0)
    recs = [r for _, r in bundle.iter_records(key)]
    assert recs[0]["raw_sha256"] == recs[1]["raw_sha256"]
    assert recs[0]["evidence_id"] != recs[1]["evidence_id"]


def test_append_rejects_gaps_and_duplicates(tmp_path):
    bundle = new_bundle(tmp_path)
    key = bundle.ensure_source(org_id=ORG, client_id=C, flow_id=F, artifact="A.B", source="S").source_key
    bundle.append_rows(key, [{"x": 1}], 0)
    with pytest.raises(ScopeError):
        bundle.append_rows(key, [{"x": 2}], 0)
    with pytest.raises(ScopeError):
        bundle.append_rows(key, [{"x": 2}], 5)


def test_crash_partial_line_recovered(tmp_path):
    bundle = new_bundle(tmp_path)
    key = bundle.ensure_source(org_id=ORG, client_id=C, flow_id=F, artifact="A.B", source="S").source_key
    bundle.append_rows(key, [{"x": 1}, {"x": 2}], 0)
    bundle.save()
    path = bundle.path / bundle.source(key).file
    with open(path, "a") as fh:
        fh.write('{"evidence_id": "ev-trunc')  # interrupted write
    reopened = EvidenceBundle.open(bundle.path)
    assert reopened.resume_position(key) == 2


def test_get_record_provenance_and_missing(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    read(vr, bundle, [SourceRequest("LTH.SystemBaseline", "Crontab")])
    key = bundle.sources()[0].source_key
    first = next(r for _, r in bundle.iter_records(key))
    rec = bundle.get_record(first["evidence_id"])
    for k in ("investigation_id", "org_id", "client_id", "hostname", "flow_id", "artifact",
              "source", "collected_at", "retrieved_at", "row_index", "raw_sha256", "file", "line"):
        assert k in rec
    assert rec["row_index"] == 0 and rec["line"] == 1
    with pytest.raises(NotFoundError):
        bundle.get_record("ev-00000000000000000000")
    with pytest.raises(ValidationError):
        bundle.get_record("../../etc/passwd")


def test_search_covers_entire_source(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    read(vr, bundle, [SourceRequest("LTH.SystemBaseline", "Processes")])
    key = bundle.sources()[0].source_key
    page = bundle.read_page(key, offset=0, limit=10, search="KWORKERD")
    assert page["total_matched"] == 1 and page["records"][0]["row_index"] == 141
    assert page["search_scope"] == "entire persisted source"


def test_manifest_validation_rejects_traversal(tmp_path):
    bundle = new_bundle(tmp_path)
    bundle.ensure_source(org_id=ORG, client_id=C, flow_id=F, artifact="A.B", source="S")
    bundle.save()
    manifest = read_json(bundle.path / "manifest.json")
    manifest["sources"][0]["file"] = "sources/../../evil.jsonl"
    (bundle.path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        EvidenceBundle.open(bundle.path)
    manifest["sources"][0]["file"] = "sources/ok.jsonl"
    manifest["format_version"] = 99
    (bundle.path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        EvidenceBundle.open(bundle.path)


def test_verify_detects_tampering(tmp_path):
    bundle = new_bundle(tmp_path)
    key = bundle.ensure_source(org_id=ORG, client_id=C, flow_id=F, artifact="A.B", source="S").source_key
    bundle.append_rows(key, [{"x": 1}], 0)
    bundle.save()
    path = bundle.path / bundle.source(key).file
    path.write_text(path.read_text().replace('"x": 1', '"x": 2'))
    assert any("hash mismatch" in p for p in EvidenceBundle.open(bundle.path).verify())


def test_one_based_migration_is_explicit(tmp_path):
    bundle = new_bundle(tmp_path)
    key = bundle.ensure_source(org_id=ORG, client_id=C, flow_id=F, artifact="A.B", source="S").source_key
    bundle.append_rows(key, [{"x": 1}, {"x": 2}], 0)
    bundle.save()
    # Simulate an older one-based bundle.
    path = bundle.path / bundle.source(key).file
    old = [json.loads(line) for line in path.read_text().splitlines()]
    legacy = []
    for r in old:
        r["row_index"] += 1
        r["evidence_id"] = f"ev-legacy{r['row_index']:011d}"
        legacy.append(r)
    path.write_text("".join(json.dumps(r) + "\n" for r in legacy))
    manifest = read_json(bundle.path / "manifest.json")
    manifest["row_index_base"] = 1
    (bundle.path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        EvidenceBundle.open(bundle.path)  # never read with silently shifted indexes
    result = migrate_one_based(bundle.path)
    assert result["migrated"]
    migrated = EvidenceBundle.open(bundle.path)
    rec = migrated.get_record("ev-legacy00000000001")  # legacy reference -> same row
    assert rec["row_index"] == 0 and rec["row"] == {"x": 1}


def test_non_terminal_flow_not_read(tmp_path, vr, fake_server):
    from support.fake_server import FakeFlow
    flow = FakeFlow(client_id=C, flow_id="F.SYNTHACTIVE", artifacts=["Custom.Example"],
                    results={"Custom.Example/Inventory": [{"Path": "/x"}]}, final_state="IN_PROGRESS")
    fake_server.flows[ORG][(C, flow.flow_id)] = flow
    bundle = new_bundle(tmp_path)
    read(vr, bundle, [SourceRequest("Custom.Example", "Inventory")], flow="F.SYNTHACTIVE")
    cov = bundle.sources()[0].coverage
    assert cov.read_status == "not_started" and cov.stop_reason == "flow_not_terminal"
    assert cov.persisted_rows == 0


def test_result_statuses_preserve_uncertainty(tmp_path, vr):
    bundle = new_bundle(tmp_path)
    read(vr, bundle, [SourceRequest("LTH.SystemBaseline", s) for s in
                      ("Services", "DebianPackages", "Crontab", "Processes")],
         client="C.SYNTH0000000003", flow="F.SYNTH0003")
    status = {e.source: e.coverage.result_status for e in bundle.sources()}
    assert status == {"Services": "source_failed", "DebianPackages": "skipped_precondition",
                      "Crontab": "empty_or_skipped", "Processes": "rows"}
