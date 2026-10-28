"""Synthetic demonstration bundles (tests and lab demos only; never shipped in the wheel).

    python tests/support/demo_bundles.py OUTPUT_DIR

writes version-1 evidence bundle archives marked ``synthetic``:

* ``fleet-baseline.tar.gz`` - five Linux hosts (documented ``Linux.Sys.Pslist``,
  ``Linux.Sys.Users`` and ``Generic.Client.Info/BasicInformation`` sources). One host runs a
  rare executable and has a second UID 0 account; one host's process list was only partly read.
* ``fleet-later.tar.gz`` - a later capture of two of those hosts with an added account and a
  changed shell, for baseline comparison of imported evidence.
* ``lth-*.tar.gz`` (``--lth``) - the synthetic LTH baseline scenarios: one host captured three
  times (with real changes, then with failed and empty sources), and a partial capture.

Addresses, names and IDs are synthetic (``SYNTH``, RFC 5737/1918 ranges).
"""

from __future__ import annotations

import hashlib
import sys
import tarfile
import tempfile
from pathlib import Path

from support.bundles import make_bundle

ORG = "OSYNTHLAB"
DAY1 = "2026-03-01T08:00:00Z"
DAY2 = "2026-03-02T08:00:00Z"
COMMON = [
    ("systemd", "/usr/lib/systemd/systemd", "/sbin/init", "root"),
    ("sshd", "/usr/sbin/sshd", "sshd: /usr/sbin/sshd -D", "root"),
    ("cron", "/usr/sbin/cron", "/usr/sbin/cron -f", "root"),
    ("nginx", "/usr/sbin/nginx", "nginx: master process /usr/sbin/nginx", "root"),
]


def _procs(n: int, extra: list[tuple] = ()) -> list[dict]:
    rows = []
    for i, (name, exe, cmd, user) in enumerate(list(COMMON) + list(extra)):
        rows.append({"Pid": 100 + i, "Ppid": 1 if i else 0, "Name": name, "CommandLine": cmd,
                     "Exe": exe, "Hash": {"SHA256": hashlib.sha256(
                         f"{exe}|{n if i >= len(COMMON) else 'standard'}".encode()).hexdigest()},
                     "Username": user, "CreateTime": f"2026-03-01T0{i % 8}:{10 + n:02d}:00Z",
                     "RSS": 1024 * (i + 1), "Deleted": False})
    return rows


def _users(extra: list[tuple] = (), shell: str = "/bin/bash") -> list[dict]:
    base = [("root", 0, "/root"), ("admin", 1000, "/home/admin"), ("www-data", 33, "/var/www")]
    return [{"User": u, "Description": "", "Uid": uid, "Gid": uid, "Homedir": home,
             "Shell": shell if u == "admin" else "/bin/bash"}
            for u, uid, home in base + list(extra)]


def _host(n: int, *, procs, users, collected=DAY1, flow=None, pslist_status="complete"):
    cid = f"C.SYNTH00000002{n:02d}"
    name = f"demo-web-{n:02d}"
    fid = flow or f"F.SYNTHDEMO{n:02d}"
    return [
        {"client_id": cid, "hostname": name, "flow_id": fid, "artifact": "Generic.Client.Info",
         "source": "BasicInformation", "collected_at": collected,
         "rows": [{"Name": "velociraptor", "Hostname": name, "OS": "linux",
                   "Platform": "debian", "Architecture": "amd64"}]},
        {"client_id": cid, "hostname": name, "flow_id": fid, "artifact": "Linux.Sys.Pslist",
         "collected_at": collected, "rows": procs, "read_status": pslist_status},
        {"client_id": cid, "hostname": name, "flow_id": fid, "artifact": "Linux.Sys.Users",
         "collected_at": collected, "rows": users},
    ]


def write(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    rare = [("agentd", "/opt/.cache/agentd", "/opt/.cache/agentd --beacon 203.0.113.77:8443",
             "www-data")]
    baseline = []
    baseline += _host(1, procs=_procs(1), users=_users())
    baseline += _host(2, procs=_procs(2, rare), users=_users([("ops", 0, "/var/tmp/.ops")]))
    baseline += _host(3, procs=_procs(3), users=_users())
    baseline += _host(4, procs=_procs(4), users=_users())
    baseline += _host(5, procs=_procs(5, rare), users=_users(), pslist_status="incomplete")
    later = []
    later += _host(1, procs=_procs(1), users=_users([("deploy", 1001, "/home/deploy")]),
                   collected=DAY2, flow="F.SYNTHDEMOB01")
    later += _host(2, procs=_procs(2, rare), users=_users([("ops", 0, "/var/tmp/.ops")],
                                                         shell="/bin/zsh"),
                   collected=DAY2, flow="F.SYNTHDEMOB02")
    paths = []
    for name, sources in (("fleet-baseline", baseline), ("fleet-later", later)):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = make_bundle(Path(tmp) / name, sources, org_id=ORG, synthetic=True)
            archive = out / f"{name}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(bundle, arcname=name)
            paths.append(archive)
    return paths


def write_lth(out: Path) -> list[Path]:
    """Export the synthetic LTH baseline scenarios (review host captured three times)."""
    import vr_dfir.evidence.retrieval as retrieval
    import vr_dfir.profiles.store as profile_store
    from support.synthetic import scenario_service, start_scenario
    retrieval.time.sleep = lambda s: None
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "analysis_profiles"
    profile_store.bundled_profile_dir = lambda: fixtures
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    with tempfile.TemporaryDirectory() as tmp:
        svc = scenario_service(Path(tmp) / "state")
        for name in ("review", "review_later", "review_partial", "partial"):
            inv = start_scenario(svc, name, no_llm=True)
            svc.execute(inv.id)
            archive = out / f"lth-{name.replace('_', '-')}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(svc.store.get(inv.id).evidence_dir, arcname=f"lth-{name}")
            paths.append(archive)
    return paths


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    for p in write(target) + (write_lth(target) if "--lth" in sys.argv else []):
        print(p)
