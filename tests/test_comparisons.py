"""Baseline comparisons: stable matching, honest absence, compatibility and provenance."""

import hashlib

import pytest

from support.bundles import import_bundle, make_bundle, users
from support.synthetic import start_scenario
from vr_dfir.errors import ValidationError
from vr_dfir.evidence.bundle import EvidenceBundle
from vr_dfir.services import captures as caps
from vr_dfir.services import comparisons


def run(svc, scenario):
    inv = start_scenario(svc, scenario, no_llm=True)
    svc.execute(inv.id)
    return svc.store.get(inv.id)


def capture(svc, inv, source):
    return next(c for c in caps.catalog(svc.store, svc.runtime.settings)
                if c.investigation_id == inv.id and c.source == source)


def compare(svc, current_inv, source, baseline=None):
    cur = capture(svc, current_inv, source)
    base = baseline or caps.baselines(svc.store, svc.runtime.settings, cur)[0]
    cid = comparisons.run(svc.store, svc.runtime.settings, cur, base)
    loaded = comparisons.load(current_inv, cid)
    items = comparisons.items(current_inv, cid, show_unchanged=True, limit=200)["items"]
    return loaded, {i["key_display"]: i for i in items}


@pytest.fixture
def three(synth):
    return {k: run(synth, k) for k in ("review", "review_later", "review_partial")}


def test_meaningful_changes_only(synth, three):
    loaded, items = compare(synth, three["review_later"], "Users")
    counts = loaded["summary"]["counts"]
    assert counts == {"added": 1, "removed": 1, "modified": 1, "unchanged": 4, "unknown": 0}
    assert items["deploy"]["state"] == "added" and items["backup"]["state"] == "removed"
    assert items["admin"]["changed"] == ["shell"]
    assert items["admin"]["before_values"]["shell"] == "/bin/bash"
    assert items["admin"]["after_values"]["shell"] == "/bin/zsh"
    shown = comparisons.items(three["review_later"], loaded["comparison_id"])["items"]
    assert {i["state"] for i in shown} == {"added", "removed", "modified"}  # unchanged hidden


def test_reordered_rows_volatile_fields_and_number_text_do_not_change(synth, three):
    loaded, items = compare(synth, three["review_later"], "NetworkConnections")
    assert loaded["summary"]["counts"]["modified"] == 0
    assert [k for k, i in items.items() if i["state"] == "added"] == ["tcp · 0.0.0.0 · 9100"]
    assert any("listening" in x.lower() for x in loaded["summary"]["limitations"])
    loaded, items = compare(synth, three["review_later"], "Crontab")
    sysupdate = next(i for k, i in items.items() if "sysupdate" in k)
    assert sysupdate["state"] == "modified" and sysupdate["changed"] == ["minute"]


def test_failed_or_empty_current_capture_never_reports_removed(synth, three):
    for source in ("Users", "Crontab", "NetworkConnections"):
        loaded, items = compare(synth, three["review_partial"], source)
        counts = loaded["summary"]["counts"]
        assert counts["removed"] == 0 and counts["unknown"] == len(items) > 0, source
        assert not loaded["summary"]["current_side"]["absence_established"]
        assert all("cannot establish absence" in i["reason"] for i in items.values())
    assert loaded["definition"]["baseline"]["flow_id"] == "F.SYNTH0008"  # latest older capture


def test_short_rows_on_disk_produce_unknown_not_removed(synth, three):
    inv = three["review_later"]
    src = next(s for s in EvidenceBundle.open(inv.evidence_dir).sources() if s.source == "Users")
    path = inv.evidence_dir / src.file
    path.write_text("".join(path.read_text().splitlines(keepends=True)[:-2]))
    loaded, items = compare(synth, inv, "Users")
    assert loaded["summary"]["counts"]["removed"] == 0
    assert not loaded["summary"]["current_side"]["verified"]
    assert any(i["state"] == "unknown" for i in items.values())


def test_both_sides_resolve_to_original_evidence(synth, three):
    loaded, items = compare(synth, three["review_later"], "Users")
    admin = items["admin"]
    before = EvidenceBundle.open(three["review"].evidence_dir).get_record(admin["before_eids"][0])
    after = EvidenceBundle.open(three["review_later"].evidence_dir).get_record(
        admin["after_eids"][0])
    assert before["row"]["User"] == after["row"]["User"] == "admin"
    assert before["row"]["Shell"] != after["row"]["Shell"]
    assert loaded["definition"]["policy_version"].startswith("compare-")


def test_results_are_persisted_apart_from_evidence(synth, three):
    inv = three["review_later"]
    digest = {p: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in inv.evidence_dir.rglob("*") if p.is_file()}
    loaded, _ = compare(synth, inv, "Services")
    assert comparisons.db_path(inv).is_file()
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in inv.evidence_dir.rglob("*") if p.is_file()} == digest
    again, _ = compare(synth, inv, "Services")
    assert again["comparison_id"] == loaded["comparison_id"]


def test_incompatible_captures_are_refused(synth, three):
    st, settings = synth.store, synth.runtime.settings
    later = capture(synth, three["review_later"], "Users")
    debian = capture(synth, three["review"], "DebianPackages")
    rhel = capture(synth, three["review_later"], "RHELPackages")
    with pytest.raises(ValidationError):
        comparisons.run(st, settings, rhel, debian)  # different source
    older = capture(synth, three["review"], "Users")
    with pytest.raises(ValidationError):
        comparisons.run(st, settings, older, later)  # baseline newer than current
    other_host = capture(synth, run(synth, "ordinary"), "Users")
    with pytest.raises(ValidationError):
        comparisons.run(st, settings, later, other_host)


def test_duplicates_and_incompatible_schema(synth, tmp_path):
    base = make_bundle(tmp_path / "b1", [{
        "client_id": "C.SYNTH0000000021", "hostname": "dup", "flow_id": "F.SYNTHD1",
        "artifact": "Linux.Sys.Users", "collected_at": "2026-02-01T00:00:00Z",
        "rows": users([("a", 1), ("b", 2), ("b", 2)])}])
    cur = make_bundle(tmp_path / "b2", [{
        "client_id": "C.SYNTH0000000021", "hostname": "dup", "flow_id": "F.SYNTHD2",
        "artifact": "Linux.Sys.Users", "collected_at": "2026-02-02T00:00:00Z",
        "rows": users([("a", 1), ("a", 5), ("b", 2)])}])
    import_bundle(synth, base)
    cur_inv = import_bundle(synth, cur)
    loaded, items = compare(synth, cur_inv, None)
    assert items["a"]["state"] == "unknown" and "duplicate" in items["a"]["reason"]
    assert items["b"]["state"] == "modified" and items["b"]["changed"] == ["occurrences"]
    odd = make_bundle(tmp_path / "b3", [{
        "client_id": "C.SYNTH0000000021", "hostname": "dup", "flow_id": "F.SYNTHD3",
        "artifact": "Linux.Sys.Users", "collected_at": "2026-02-03T00:00:00Z",
        "rows": [{"Login": None, "Something": 1}]}])
    odd_inv = import_bundle(synth, odd)
    loaded, items = compare(synth, odd_inv, None)
    assert loaded["summary"]["state"] == "incomparable" and not items


def test_capture_history_deduplicates_the_same_flow(synth, three):
    copy = synth.analyze_bundle(three["review"].evidence_dir, no_llm=True, title="Re-analysis")
    synth.execute(copy.id)
    cur = capture(synth, three["review_partial"], "Users")
    hist = caps.history(synth.store, synth.runtime.settings, cur)
    flows = [c.flow_id for c in hist]
    assert flows == ["F.SYNTH0002", "F.SYNTH0008", "F.SYNTH0009"]
    assert hist[0].duplicates


def test_cancelled_comparison_saves_nothing_and_changes_no_evidence(synth, three):
    inv = three["review_later"]
    digest = {p: p.read_bytes() for p in inv.evidence_dir.rglob("*") if p.is_file()}
    cur = capture(synth, inv, "Users")
    base = caps.baselines(synth.store, synth.runtime.settings, cur)[0]
    state = comparisons.RunState("x")
    state.stop.set()
    with pytest.raises(comparisons.ComparisonCancelled):
        comparisons.run(synth.store, synth.runtime.settings, cur, base, state=state)
    cid = comparisons.comparison_id(synth.store, cur, base)
    with pytest.raises(Exception):
        comparisons.load(inv, cid)
    assert {p: p.read_bytes() for p in inv.evidence_dir.rglob("*") if p.is_file()} == digest
    assert not list(comparisons.db_path(inv).parent.glob(".cmp-*.tmp"))
