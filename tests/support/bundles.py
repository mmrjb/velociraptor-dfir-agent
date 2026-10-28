"""Build small synthetic evidence bundles for tests (never shipped).

``make_bundle`` writes a version-1 bundle with the production EvidenceBundle API, so imports,
indexes and comparisons read exactly the format the application writes.
"""

from __future__ import annotations

from pathlib import Path

from vr_dfir.evidence.bundle import EvidenceBundle


def make_bundle(path: Path, sources: list[dict], *, org_id: str | None = "OSYNTHLAB",
                synthetic: bool = True) -> Path:
    """``sources``: dicts with client_id, hostname, flow_id, artifact, source, rows,
    collected_at and optional read_status/result_status/org_id."""
    bundle = EvidenceBundle.create(path, investigation_id="inv-20260101-000000-synth0",
                                   scope={"org_id": org_id, "mode": "bundle"}, synthetic=synthetic)
    for s in sources:
        org = s.get("org_id", org_id)
        entry = bundle.ensure_source(org_id=org or "", client_id=s["client_id"],
                                     flow_id=s["flow_id"], artifact=s["artifact"],
                                     source=s.get("source"), hostname=s.get("hostname", ""),
                                     collected_at=s.get("collected_at"))
        rows = s.get("rows") or []
        if rows:
            bundle.append_rows(entry.source_key, rows, 0)
        bundle.set_coverage(entry.source_key, read_status=s.get("read_status", "complete"),
                            result_status=s.get("result_status", "rows" if rows else
                                                "empty_or_skipped"),
                            pagination_finished=True, collection_status="completed")
    bundle.save()
    return Path(path)


def import_bundle(svc, path: Path, title: str = "Synthetic bundle"):
    inv = svc.analyze_bundle(path, no_llm=True, title=title)
    svc.execute(inv.id)
    return svc.store.get(inv.id)


def users(names_uids: list[tuple[str, int]], shell: str = "/bin/bash") -> list[dict]:
    return [{"User": n, "Uid": u, "Gid": u, "Homedir": f"/home/{n}", "Shell": shell,
             "Description": ""} for n, u in names_uids]
