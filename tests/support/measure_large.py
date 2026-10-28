"""Large synthetic dataset measurements (tests only; never shipped).

    python tests/support/measure_large.py WORK_DIR

Builds two synthetic captures of 8 hosts (process lists, users, listeners, services and a large
package list) with well over 100,000 saved rows, imports them through the production bundle
path, and times each stage in its own process so the reported peak memory belongs to that
stage. Prints one JSON document. Synthetic data only.
"""

from __future__ import annotations

import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

HOSTS = 8
PROCS = 9000
PKGS_BIG = 50_000


def _rows_proc(h: int, n: int) -> list[dict]:
    return [{"Pid": 1000 + i, "Ppid": 1 if i else 0, "Name": f"proc{i % 400}",
             "CommandLine": f"/usr/bin/proc{i % 400} --worker {i} --host h{h}",
             "Exe": f"/usr/bin/proc{i % 400}" if i % 997 else f"/tmp/.x/rare{h}",
             "Username": "root" if i % 3 else "app",
             "CreateTime": f"2026-03-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z"}
            for i in range(n)]


def _sources(capture: int) -> list[dict]:
    day = "2026-03-01T08:00:00Z" if capture == 1 else "2026-03-02T08:00:00Z"
    out = []
    for h in range(HOSTS):
        cid, host, flow = f"C.SYNTHL{h:010d}", f"large-{h:02d}", f"F.SYNTHL{capture}{h:03d}"
        base = {"client_id": cid, "hostname": host, "flow_id": flow, "collected_at": day}
        users = [{"User": f"user{u}", "Uid": 1000 + u, "Gid": 1000 + u, "Homedir": f"/home/u{u}",
                  "Shell": "/bin/bash" if (u + capture) % 50 else "/bin/zsh"} for u in range(300)]
        listeners = [{"Type": "TCP", "Status": "LISTEN", "Laddr": "0.0.0.0", "Lport": 1000 + p,
                      "Raddr": "0.0.0.0", "Rport": 0, "Pid": p + capture, "Name": f"svc{p}"}
                     for p in range(200 + capture * 3)]
        out += [{**base, "artifact": "Linux.Sys.Pslist", "rows": _rows_proc(h, PROCS)},
                {**base, "artifact": "Linux.Sys.Users", "rows": users},
                {**base, "artifact": "Linux.Network.Netstat", "rows": listeners}]
        if h == 0:
            pkgs = [{"Package": f"pkg{i:06d}", "Version": f"1.{i % 9}" if (i + capture) % 997
                     else "2.0", "Architecture": "amd64", "Status": "install ok installed"}
                    for i in range(PKGS_BIG)]
            out.append({**base, "artifact": "Linux.Debian.Packages", "rows": pkgs})
    return out


def stage(name: str, work: Path) -> dict:
    """Run one stage in this process and return its timing (called in a child process)."""
    sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "src"),
                    str(Path(__file__).resolve().parents[1])]
    from support.bundles import make_bundle
    from support.synthetic import scenario_service
    from vr_dfir.services import captures, cohort, comparisons, views
    from vr_dfir.services.derived import IndexManager
    svc = scenario_service(work / "state")
    ids = json.loads((work / "ids.json").read_text()) if (work / "ids.json").is_file() else {}
    t0 = time.perf_counter()
    result: dict = {}
    if name == "import":
        for capture in (1, 2):
            path = make_bundle(work / f"bundle{capture}", _sources(capture), org_id="OSYNTHLAB")
            inv = svc.analyze_bundle(path, no_llm=True, title=f"Large capture {capture}")
            svc.execute(inv.id)
            ids[f"c{capture}"] = inv.id
        (work / "ids.json").write_text(json.dumps(ids))
        result["rows_per_capture"] = sum(len(s["rows"]) for s in _sources(1))
    else:
        inv = svc.store.get(ids["c2"])
        mgr = IndexManager(svc.runtime.settings)
        if name == "index_cold":
            idx = mgr.require(inv, timeout=3600)
            result["indexed_rows"] = int(idx.meta["rows"])
        else:
            idx = mgr.require(inv)

            def timed(label, fn):
                start = time.perf_counter()
                value = fn()
                result[label] = round(time.perf_counter() - start, 3)
                return value
            ov = timed("overview_s", lambda: views.overview(inv, idx))
            result["overview_json_bytes"] = len(json.dumps(ov))
            proc = next(s for s in idx.sources() if s["full_name"] == "Linux.Sys.Pslist")
            q = views.RowQuery(source_keys=[proc["source_key"]])
            timed("rows_first_page_s", lambda: views.query_rows(inv, idx, q, limit=50))
            page = timed("rows_last_page_s", lambda: views.query_rows(inv, idx, q,
                                                                     offset=PROCS - 50, limit=50))
            result["rows_page_json_bytes"] = len(json.dumps(page))
            timed("rows_filtered_all_sources_s",
                  lambda: views.query_rows(inv, idx, views.RowQuery(cited=True), limit=50))
            timed("search_all_rows_s",
                  lambda: views.query_rows(inv, idx, views.RowQuery(q="rare3"), limit=50))
            eid = page["records"][0]["evidence_id"]
            timed("inspect_s", lambda: views.inspect(inv, idx, eid))
            tl = timed("timeline_s", lambda: views.timeline(inv, idx))
            result["timeline_buckets"] = len(tl["buckets"])
            timed("hosts_s", lambda: cohort.hosts_view(inv, idx, scope="investigation"))
            timed("where_else_s", lambda: cohort.where_else(inv, idx, eid, scope="investigation"))
            store, st = svc.store, svc.runtime.settings
            cur = next(c for c in captures.catalog(store, st) if c.investigation_id == inv.id
                       and c.artifact == "Linux.Debian.Packages")
            base = captures.baselines(store, st, cur)[0]
            cid = timed("compare_100k_rows_cold_s", lambda: comparisons.run(store, st, cur, base))
            timed("compare_warm_s", lambda: comparisons.run(store, st, cur, base))
            result["compare_counts"] = comparisons.load(inv, cid)["summary"]["counts"]
            timed("compare_page_s", lambda: comparisons.items(inv, cid, limit=50))
            result["comparison_db_bytes"] = comparisons.db_path(inv).stat().st_size
            result["index_db_bytes"] = idx.path.stat().st_size
    result["stage_seconds"] = round(time.perf_counter() - t0, 2)
    result["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return result


def main() -> None:
    work = Path(sys.argv[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 2:
        print(json.dumps(stage(sys.argv[2], work)))
        return
    report = {"hardware": {"cpu": platform.processor() or platform.machine(),
                           "cpus": os.cpu_count(), "python": platform.python_version(),
                           "platform": platform.platform()}}
    try:
        with open("/proc/cpuinfo") as f:
            report["hardware"]["cpu"] = next(line.split(":", 1)[1].strip() for line in f
                                             if line.startswith("model name"))
        with open("/proc/meminfo") as f:
            report["hardware"]["mem_total"] = f.readline().split(":", 1)[1].strip()
    except (OSError, StopIteration):
        pass
    for name in ("import", "index_cold", "warm"):
        out = subprocess.run([sys.executable, __file__, str(work), name], check=True,
                             capture_output=True, text=True)
        report[name] = json.loads(out.stdout.strip().splitlines()[-1])
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
