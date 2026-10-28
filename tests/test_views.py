"""Derived index and summary-first views: whole-dataset totals, honest states, exact rows."""

import hashlib
import json
import threading

import pytest

from support.bundles import import_bundle, make_bundle, users
from support.synthetic import start_scenario
from vr_dfir.errors import NotFoundError
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.services import derived, views
from vr_dfir.services.derived import IndexManager
from vr_dfir.services.reviews import set_review


def run(svc, scenario):
    inv = start_scenario(svc, scenario, no_llm=True)
    svc.execute(inv.id)
    return svc.store.get(inv.id)


@pytest.fixture
def indexed(synth):
    mgr = IndexManager(synth.runtime.settings)

    def _get(scenario):
        inv = run(synth, scenario)
        return inv, mgr.require(inv)
    return _get, mgr


def _files(inv):
    out = {}
    for p in sorted(inv.evidence_dir.rglob("*")):
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_index_verifies_every_source_and_never_changes_evidence(indexed):
    get, _ = indexed
    inv, idx = get("review")
    sources = idx.sources()
    assert sources and all(s["verified"] for s in sources)
    assert sum(s["indexed_rows"] for s in sources) == sum(
        e.coverage.persisted_rows for e in EvidenceBundle.open(inv.evidence_dir).sources())
    before = _files(inv)
    derived.build(inv, inv_settings(inv, idx))
    assert _files(inv) == before


def inv_settings(inv, idx):
    from vr_dfir.config import load_settings
    return load_settings({"VR_DFIR_STATE_DIR": str(inv.path.parent.parent)}, load_dotenv=False)


def test_overview_counts_unique_findings_and_reconciles(indexed):
    get, _ = indexed
    inv, idx = get("review")
    ov = views.overview(inv, idx)
    report = json.loads((inv.report_dir / "report.json").read_text())
    findings = report["findings"]
    assert ov["metrics"][0]["value"] == len(findings) == ov["metrics"][0]["total"]
    sev = {s["key"]: s["count"] for s in ov["charts"][0]["segments"]}
    assert sum(sev.values()) == len(findings)  # a finding citing several rows counts once
    # The process row 141 is cited by several findings; it is still one evidence row.
    cited = views.query_rows(inv, idx, views.RowQuery(cited=True), limit=200)
    unique = {e for f in findings for e in f["evidence_ids"]}
    assert cited["total"] == len(unique) < sum(len(f["evidence_ids"]) for f in findings)
    assert len(ov["metrics"]) <= 3 and len(ov["charts"]) <= 2


def test_review_decisions_change_views_without_reindexing(indexed):
    get, mgr = indexed
    inv, idx = get("review")
    fid = json.loads((inv.report_dir / "report.json").read_text())["findings"][0]["finding_id"]
    built = idx.meta["built_at"]
    set_review(inv.path, {fid}, fid, "benign", "expected", by="test")
    again = mgr.require(inv)
    assert again.meta["built_at"] == built
    ov = views.overview(inv, again)
    assert ov["metrics"][0]["value"] == ov["metrics"][0]["total"] - 1


def test_totals_match_across_pages(indexed):
    get, _ = indexed
    inv, idx = get("review")
    proc = next(s for s in idx.sources() if s["full_name"].endswith("/Processes"))
    q = views.RowQuery(source_keys=[proc["source_key"]])
    first = views.query_rows(inv, idx, q, limit=50)
    seen = []
    for offset in range(0, first["total"], 50):
        seen += [r["evidence_id"] for r in views.query_rows(inv, idx, q, offset=offset,
                                                            limit=50)["records"]]
    assert len(seen) == len(set(seen)) == first["total"] == proc["indexed_rows"] == 160
    hits = views.query_rows(inv, idx, views.RowQuery(q="www-data"), limit=200)
    manual = sum(1 for s in idx.sources() for _, rec in EvidenceBundle.open(
        inv.evidence_dir).iter_records(s["source_key"])
        if "www-data" in json.dumps(rec["row"], ensure_ascii=False).lower())
    assert hits["total"] == manual > 0
    assert len(hits["records"]) == min(manual, 200)


def test_partial_capture_is_not_a_green_zero(indexed):
    get, _ = indexed
    inv, idx = get("partial")
    ov = views.overview(inv, idx)
    src = ov["metrics"][2]
    assert src["value"] < src["total"]
    assert src["breakdown"].get("failed") == 1 and src["breakdown"].get("incomplete") == 1
    states = {s["full_name"].split("/")[-1]: s["state"] for s in ov["coverage"]["sources"]}
    assert states["Services"] == "failed" and states["DebianPackages"] == "skipped"
    assert states["Crontab"] == "empty" and states["NetworkConnections"] == "incomplete"
    assert "resume" in ov["next_action"]["label"].lower()


def test_findings_not_ready_is_not_zero(synth, tmp_path):
    path = make_bundle(tmp_path / "b", [{"client_id": "C.SYNTH0000000011", "hostname": "h1",
                                         "flow_id": "F.SYNTHB1", "artifact": "Linux.Sys.Users",
                                         "rows": users([("root", 0)]),
                                         "collected_at": "2026-01-02T00:00:00Z"}])
    inv = synth.analyze_bundle(path, no_llm=True)  # not executed: no report yet
    idx = IndexManager(synth.runtime.settings).require(inv)
    ov = views.overview(inv, idx)
    assert ov["findings_ready"] is False
    assert ov["metrics"][0]["value"] is None and ov["charts"][0]["state"] == "not_ready"


def test_timeline_counts_reconcile_and_untimed_rows_are_separate(indexed):
    get, _ = indexed
    inv, idx = get("review")
    tl = views.timeline(inv, idx)
    assert sum(b["count"] for b in tl["buckets"]) == tl["timed_rows"] == 160
    total = sum(s["indexed_rows"] for s in idx.sources())
    assert tl["untimed"]["total"] == total - 160
    assert {r["status"] for r in tl["untimed"]["by_reason"]} == {"unmapped"}
    b = next(x for x in tl["buckets"] if x["count"])
    rows = views.query_rows(inv, idx, views.RowQuery(time_from=b["from_us"], time_to=b["to_us"],
                                                     order="time"), limit=200)
    assert rows["total"] == b["count"]
    times = [r["event_time"] for r in rows["records"]]
    assert times == sorted(times)
    untimed = views.query_rows(inv, idx, views.RowQuery(untimed="any"), limit=5)
    assert untimed["total"] == tl["untimed"]["total"]


def test_inspector_returns_the_exact_row_and_honest_relations(indexed):
    get, _ = indexed
    inv, idx = get("review")
    hit = views.query_rows(inv, idx, views.RowQuery(q="kworkerd -o"), limit=1)["records"][0]
    data = views.inspect(inv, idx, hit["evidence_id"])
    exact = EvidenceBundle.open(inv.evidence_dir).get_record(hit["evidence_id"])
    assert data["row"] == exact["row"] and json.loads(data["raw_text"]) == exact["row"]
    assert data["record"]["raw_sha256"] == exact["raw_sha256"]
    rules = {f["rule_id"] for f in data["findings"]}
    assert rules and {h["rule_id"] for h in data["highlights"]} <= rules
    assert data["relations"]["parent"] is None and "not shown" in data["relations"][
        "parent_status"]
    assert data["relations"]["connections"]
    assert data["presentation"]["event_time"]["basis"]["field"] == "StartTime"
    with pytest.raises(NotFoundError):
        views.inspect(inv, idx, "ev-00000000000000000000")


def test_cancelled_build_leaves_no_index_and_no_changes(synth):
    inv = run(synth, "review")
    before = _files(inv)
    reviews_before = (inv.path / "reviews.json").exists()
    state = derived.BuildState("x")
    state.stop.set()
    with pytest.raises(derived.BuildCancelled):
        derived.build(inv, synth.runtime.settings, state=state)
    assert not derived.index_path(inv).exists()
    assert not list(derived.derived_dir(inv).glob("*.tmp"))
    assert _files(inv) == before and (inv.path / "reviews.json").exists() == reviews_before


def test_index_goes_stale_when_inputs_change(indexed):
    get, mgr = indexed
    inv, idx = get("review")
    old = idx.meta["fingerprint"]
    synth_report = inv.report_dir / "report.json"
    synth_report.write_text(synth_report.read_text())  # report rewritten (mtime changes)
    assert mgr.current(inv) is None
    assert mgr.require(inv).meta["fingerprint"] != old


def test_tampered_or_truncated_rows_are_unverified(synth, tmp_path):
    inv = run(synth, "review")
    bundle = EvidenceBundle.open(inv.evidence_dir)
    users_src = next(s for s in bundle.sources() if s.source == "Users")
    path = inv.evidence_dir / users_src.file
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[:-1]))  # one saved row missing on disk
    idx = IndexManager(synth.runtime.settings).require(inv)
    s = next(x for x in idx.sources() if x["source_key"] == users_src.source_key)
    assert not s["verified"] and views.source_state(s) == "unverified"


def test_concurrent_view_requests_build_once(synth):
    inv = run(synth, "review")
    mgr = IndexManager(synth.runtime.settings)
    results = []
    threads = [threading.Thread(target=lambda: results.append(mgr.require(inv)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.meta["built_at"] for r in results}) == 1
    _ = import_bundle  # helper exercised in other modules
