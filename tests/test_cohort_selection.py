"""Host cohorts, Where else seen, and the persistent report selection (pins and notes)."""

import json
import threading

from support.bundles import import_bundle, make_bundle, users
from vr_dfir.services import cohort
from vr_dfir.services.derived import IndexManager
from vr_dfir.services.reviews import load_reviews, set_review
from vr_dfir.services.selection import SelectionStore

T = "2026-03-01T00:00:00Z"


def host(n, rows_users, *, org="OSYNTHLAB", flow=None, collected=T, procs=None, **extra):
    cid = f"C.SYNTH00000001{n:02d}"
    out = [{"client_id": cid, "hostname": f"h{n}", "flow_id": flow or f"F.SYNTHC{n}",
            "artifact": "Linux.Sys.Users", "rows": rows_users, "collected_at": collected,
            "org_id": org, **extra}]
    if procs is not None:
        out.append({"client_id": cid, "hostname": f"h{n}", "flow_id": flow or f"F.SYNTHC{n}",
                    "artifact": "Linux.Sys.Pslist", "rows": procs, "collected_at": collected,
                    "org_id": org})
    return out


def proc(exe, sha=None):
    row = {"Pid": 10, "Ppid": 1, "Name": exe.rsplit("/", 1)[-1], "Exe": exe,
           "CommandLine": exe, "Username": "root", "CreateTime": T}
    if sha:
        row["Hash"] = {"SHA256": sha}
    return row


def test_denominator_counts_only_eligible_hosts(synth, tmp_path):
    common = [proc("/usr/sbin/sshd")]
    sources = []
    sources += host(1, users([("root", 0)]), procs=common + [proc("/opt/rare/tool", "a" * 64)])
    sources += host(2, users([("root", 0)]), procs=common)
    sources += host(3, users([("root", 0), ("ops", 0)]), procs=common)
    # Host 4 read its process list only partially: not eligible, listed as excluded.
    sources += host(4, users([("root", 0)]))
    sources.append({"client_id": "C.SYNTH0000000104", "hostname": "h4", "flow_id": "F.SYNTHC4",
                    "artifact": "Linux.Sys.Pslist", "rows": [proc("/opt/rare/tool")],
                    "collected_at": T, "read_status": "incomplete"})
    inv = import_bundle(synth, make_bundle(tmp_path / "b", sources))
    idx = IndexManager(synth.runtime.settings).require(inv)
    view = cohort.hosts_view(inv, idx, scope="investigation")
    assert view["host_count"] == 4
    rare = next(o for o in view["outliers"] if o["key"] == "/opt/rare/tool")
    assert (rare["seen"], rare["eligible"]) == (1, 3)
    assert rare["text"] == "Seen on 1 of 3 eligible hosts"
    ops = next(o for o in view["outliers"] if o["kind"] == "privileged_account")
    assert ops["key"] == "ops" and ops["eligible"] == 4
    # Hashes exist on one host only: no host without the field joins the denominator.
    assert not any(o["kind"] == "file_hash" for o in view["outliers"])
    with idx.db() as conn:
        eid = conn.execute("SELECT r.evidence_id FROM rows r JOIN entities e ON e.sid=r.sid AND "
                           "e.row_index=r.row_index WHERE e.kind='executable_path' AND "
                           "e.key='/opt/rare/tool' LIMIT 1").fetchone()[0]
    we = cohort.where_else(inv, idx, eid, scope="investigation")
    exe = next(e for e in we["entities"] if e["kind"] == "executable_path")
    assert exe["text"] == "Seen on 1 of 3 eligible hosts"
    assert exe["also_seen_not_eligible"] == ["h4"]


def test_no_meaningful_cohort_gives_an_explanation(synth, tmp_path):
    inv = import_bundle(synth, make_bundle(tmp_path / "b", host(1, users([("root", 0)]))))
    idx = IndexManager(synth.runtime.settings).require(inv)
    view = cohort.hosts_view(inv, idx, scope="investigation")
    assert view["outliers"] == [] and view["min_cohort"] == 3
    assert all(not g["usable"] for g in view["groups"])


def test_organizations_never_merge_and_repeated_captures_count_once(synth, tmp_path):
    mgr = IndexManager(synth.runtime.settings)
    a1 = import_bundle(synth, make_bundle(tmp_path / "a1", host(1, users([("root", 0)])) +
                                          host(2, users([("root", 0)]))))
    a2 = import_bundle(synth, make_bundle(tmp_path / "a2", host(
        1, users([("root", 0), ("x", 0)]), flow="F.SYNTHC1B", collected="2026-02-27T00:00:00Z")))
    b = import_bundle(synth, make_bundle(tmp_path / "b", host(5, users([("root", 0)]),
                                                              org="OOTHER"), org_id="OOTHER"))
    members = [(m, mgr.require(m)) for m in (a2, b)]
    view = cohort.hosts_view(a1, mgr.require(a1), scope="organization", members=members)
    names = {h["hostname"] for h in view["hosts"]}
    assert names == {"h1", "h2"} and view["org_id"] == "OSYNTHLAB"
    h1 = next(h for h in view["hosts"] if h["hostname"] == "h1")
    assert h1["sources_total"] == 1  # one observation per host and source
    unknown = import_bundle(synth, make_bundle(tmp_path / "u", host(7, users([("root", 0)]),
                                                                    org=None), org_id=None))
    v = cohort.hosts_view(unknown, mgr.require(unknown), scope="organization", members=members)
    assert v["unavailable"] and not v["hosts"]
    mixed = import_bundle(synth, make_bundle(tmp_path / "m", host(1, users([("root", 0)])) +
                                             host(9, users([("root", 0)]), org="OOTHER")))
    v = cohort.hosts_view(mixed, mgr.require(mixed), scope="investigation")
    assert v["unavailable"] and not v["hosts"]


def test_pins_persist_recover_and_never_touch_reviews(synth, tmp_path):
    inv = import_bundle(synth, make_bundle(tmp_path / "b", host(1, users([("root", 0)]))))
    store = SelectionStore(inv.path)
    pin = store.add("evidence", "ev-0123456789abcdef0123", title="row", snapshot={"x": 1},
                    note="first")
    assert store.add("evidence", "ev-0123456789abcdef0123", title="row", snapshot={}) == pin
    store.set_note(pin["pin_id"], "checked with the owner")
    set_review(inv.path, {"R-001"}, "R-001", "confirmed", None, by="t")
    restarted = SelectionStore(inv.path)
    assert restarted.list()[0]["note"] == "checked with the owner"
    (inv.path / "selection.json").write_text("{corrupt")
    assert SelectionStore(inv.path).list()[0]["note"] == "checked with the owner"
    assert json.loads((inv.path / "selection.json").read_text())["pins"]
    assert SelectionStore(inv.path).remove(pin["pin_id"])
    assert load_reviews(inv.path)["R-001"]["state"] == "confirmed"
    again = SelectionStore(inv.path).add("evidence", "ev-0123456789abcdef0123", title="row",
                                         snapshot={})
    assert again["note"] is None  # a re-pin does not resurrect an old note


def test_concurrent_pins_are_not_lost(synth, tmp_path):
    inv = import_bundle(synth, make_bundle(tmp_path / "b", host(1, users([("root", 0)]))))

    def add(n):
        SelectionStore(inv.path).add("finding", f"R-{n:03d}", title=str(n), snapshot={})
    threads = [threading.Thread(target=add, args=(n,)) for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(SelectionStore(inv.path).list()) == 20
