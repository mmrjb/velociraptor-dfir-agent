"""Edge cases from independent review: long keys, unkeyed rows, timeline scope, build retry."""

import pytest

from support.bundles import import_bundle, make_bundle, users
from vr_dfir.analysis import compare
from vr_dfir.evidence import presentation as pres
from vr_dfir.services import captures as caps
from vr_dfir.services import comparisons, derived, views
from vr_dfir.services.derived import IndexManager

CID = "C.SYNTH0000000031"
HOUR = 3_600_000_000


def source(flow, day, rows, artifact="Linux.Sys.Users", **extra):
    return {"client_id": CID, "hostname": "edge", "flow_id": flow, "artifact": artifact,
            "collected_at": f"2026-03-0{day}T00:00:00Z", "rows": rows, **extra}


def processes(n):
    return [{"Pid": 100 + i, "Ppid": 1, "Name": f"p{i}", "Exe": f"/usr/bin/p{i}",
             "CommandLine": f"p{i} --run", "Username": "root",
             "StartTime": f"2026-03-01T{i % 24:02d}:{(i * 7) % 60:02d}:00Z"} for i in range(n)]


@pytest.mark.parametrize("value", ["١٢٣", "9" * 5000, "1e400", float("inf"), float("nan"),
                                   "", " 42 ", -7.0, 2.0 ** 70, None, [1]])
def test_number_normalizer_never_raises(value):
    compare._num(value)
    assert compare._num(" 42 ") == compare._num(42.0) == 42


def test_long_comparison_keys_sharing_a_prefix_stay_distinct():
    adapter = compare.ADAPTERS["files"]
    prefix = "/srv/" + "a" * 3000
    one = compare.keyed_row(adapter, {"Path": prefix + "/one"}, {"path": "Path"})
    two = compare.keyed_row(adapter, {"Path": prefix + "/two"}, {"path": "Path"})
    assert one.key != two.key and len(one.key) == 32


def test_long_entity_keys_sharing_a_prefix_stay_distinct_and_bounded():
    mapping = pres.mapping_for("Linux.Sys.Pslist", None)
    prefix = "/opt/" + "b" * 1500
    keys = [pres.entities(mapping, {"Exe": prefix + tail}, {"exe": "Exe"})[0][1]
            for tail in ("/x", "/y")]
    assert keys[0] != keys[1] and all(len(k) <= pres.MAX_ENTITY_KEY for k in keys)
    short = pres.entities(mapping, {"Exe": "/usr/bin/ssh"}, {"exe": "Exe"})[0]
    assert short[1] == short[2] == "/usr/bin/ssh"


def test_rows_without_key_fields_prevent_removed(synth, tmp_path):
    import_bundle(synth, make_bundle(tmp_path / "b1", [
        source("F.SYNTHE1", 1, users([("a", 1), ("b", 2), ("c", 3)]))]))
    rows = users([("a", 1), ("b", 2)]) + [{"User": "", "Uid": 9, "Gid": 9, "Homedir": "/",
                                            "Shell": "/bin/sh", "Description": ""}]
    cur_inv = import_bundle(synth, make_bundle(tmp_path / "b2", [source("F.SYNTHE2", 2, rows)]))
    st, settings = synth.store, synth.runtime.settings
    cur = next(c for c in caps.catalog(st, settings) if c.investigation_id == cur_inv.id)
    base = caps.baselines(st, settings, cur)[0]
    cid = comparisons.run(st, settings, cur, base)
    loaded = comparisons.load(cur_inv, cid)
    items = {i["key_display"]: i for i in comparisons.items(
        cur_inv, cid, show_unchanged=True, limit=50)["items"]}
    assert loaded["summary"]["counts"]["removed"] == 0
    assert items["c"]["state"] == "unknown"
    assert not loaded["summary"]["current_side"]["absence_established"]


def test_failed_time_mapped_source_makes_the_timeline_incomplete(synth, tmp_path):
    inv = import_bundle(synth, make_bundle(tmp_path / "b", [
        source("F.SYNTHE3", 1, [p for p in processes(48) if not "T08" <= p["StartTime"][10:13]
                                <= "T15"], artifact="Linux.Sys.Pslist"),
        {**source("F.SYNTHE4", 1, [], artifact="Linux.Sys.Pslist"), "client_id":
         "C.SYNTH0000000032", "hostname": "edge2", "read_status": "read_failed",
         "result_status": "not_read"}]))
    idx = IndexManager(synth.runtime.settings).require(inv)
    tl = views.timeline(inv, idx)
    assert tl["timed_rows"] == 32 and not tl["complete"]
    assert any("edge2" in s for s in tl["incomplete_sources"])
    gaps = [b for b in tl["buckets"] if b["count"] == 0]
    assert gaps and not any(b["measured"] for b in gaps)  # possible gap, not a measured zero


def test_timeline_edge_buckets_are_clipped_to_the_requested_range(synth, tmp_path):
    inv = import_bundle(synth, make_bundle(tmp_path / "b", [
        source("F.SYNTHE5", 1, processes(48), artifact="Linux.Sys.Pslist")]))
    idx = IndexManager(synth.runtime.settings).require(inv)
    full = views.timeline(inv, idx)
    lo = full["buckets"][0]["from_us"]
    t_from, t_to = lo + 5 * HOUR + 17_000_000, lo + 19 * HOUR + 3_000_000
    tl = views.timeline(inv, idx, time_from=t_from, time_to=t_to)
    assert tl["buckets"][0]["from_us"] == t_from and tl["buckets"][-1]["to_us"] <= t_to
    assert all(t_from <= b["from_us"] < b["to_us"] <= t_to for b in tl["buckets"])
    for b in (tl["buckets"][0], tl["buckets"][-1]):
        rows = views.query_rows(inv, idx, views.RowQuery(time_from=b["from_us"],
                                                         time_to=b["to_us"]), limit=1)
        assert rows["total"] == b["count"]
    total = views.query_rows(inv, idx, views.RowQuery(time_from=t_from, time_to=t_to), limit=1)
    assert sum(b["count"] for b in tl["buckets"]) == total["total"]


def test_cancelled_index_build_is_not_restarted_by_polling(synth, tmp_path):
    inv = import_bundle(synth, make_bundle(tmp_path / "b", [
        source("F.SYNTHE6", 1, users([("a", 1)]))]))
    settings = synth.runtime.settings
    derived.index_path(inv).unlink(missing_ok=True)
    mgr = IndexManager(settings)
    state = derived.BuildState(derived.fingerprint(inv, settings))
    state.stop.set()
    mgr._builds[inv.id] = state
    mgr._run(inv, state)
    assert mgr.ensure(inv)["state"] == "cancelled"
    assert mgr.ensure(inv, wait=0.3)["state"] == "cancelled"
    assert mgr.status(inv)["state"] == "cancelled"
    assert not derived.index_path(inv).exists()
    assert isinstance(mgr.ensure(inv, wait=30, retry=True), derived.Index)


def test_change_values_keep_exact_large_integers(synth, tmp_path):
    from vr_dfir.evidence.exact import BIGINT_KEY
    big = 2 ** 60 + 1
    import_bundle(synth, make_bundle(tmp_path / "b1", [source("F.SYNTHE7", 1, users([("a", 1)]))]))
    cur_inv = import_bundle(synth, make_bundle(tmp_path / "b2", [
        source("F.SYNTHE8", 2, users([("a", big)]))]))
    st, settings = synth.store, synth.runtime.settings
    cur = next(c for c in caps.catalog(st, settings) if c.investigation_id == cur_inv.id)
    cid = comparisons.run(st, settings, cur, caps.baselines(st, settings, cur)[0])
    item = comparisons.items(cur_inv, cid)["items"][0]
    assert item["state"] == "modified"
    assert item["after_values"]["uid"] == {BIGINT_KEY: str(big)}
    assert comparisons.item(cur_inv, cid, item["item_key"]) == item
