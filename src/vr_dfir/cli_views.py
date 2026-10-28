"""CLI adapter for the summary views, changes, cohorts and the report selection.

Each command calls the same shared services as the UI API; nothing here contacts
Velociraptor or a model.
"""

from __future__ import annotations


def _cli():
    from vr_dfir import cli
    return cli


def _index(ctx, inv):
    from vr_dfir.services.derived import IndexManager
    return IndexManager(ctx.settings).require(inv)


def cmd_overview(ctx, out) -> int:
    from vr_dfir.services import views
    inv = ctx.investigation(ctx.args.id)
    ov = views.overview(inv, _index(ctx, inv), stale_days=ctx.settings.stale_after_days)
    lines = [f"{ov['scope']['title']} ({inv.id}) · org {', '.join(ov['scope']['org_ids']) or 'unknown'}",
             f"Next step: {ov['next_action']['label']}"]
    for m in ov["metrics"]:
        value = "not ready" if m["value"] is None else m["value"]
        total = f" of {m['total']}" if m.get("total") is not None else ""
        lines.append(f"  {m['label']}: {value}{total} {m['unit']}")
    for c in ov["charts"]:
        lines.append(f"  {c['title']} ({c['unit']}):")
        for item in c.get("segments") or c.get("bars") or []:
            lines.append(f"    {item['label']}: {item['count']}")
        if c.get("buckets") is not None:
            lines.append(f"    {sum(b['count'] for b in c['buckets'])} timed rows, "
                         f"{c.get('untimed') or 0} without an event time")
    if ov["attention"]:
        lines.append("  Needs review:")
        lines += [f"    {a['finding_id']} [{a['severity']}] {a['title']} ({a['review_label']})"
                  for a in ov["attention"]]
    states = ", ".join(f"{k} {v}" for k, v in ov["coverage"]["states"].items())
    lines.append(f"  Coverage: {states}; {ov['coverage']['rows']} saved rows")
    out.result(ov, "\n".join(lines))
    return _cli().EXIT_OK


def cmd_timeline(ctx, out) -> int:
    from vr_dfir.services import views
    inv = ctx.investigation(ctx.args.id)
    tl = views.timeline(inv, _index(ctx, inv), source_keys=ctx.args.source or [])
    lines = [f"{tl['basis']}; bucket {tl['bucket']['label']}; {tl['timed_rows']} timed rows; "
             f"{tl['untimed']['total']} without a usable event time"]
    lines += [f"  {b['start']}  {b['count']}" + ("" if b["measured"] else "  (possible gap)")
              for b in tl["buckets"] if b["count"] or not b["measured"]]
    lines += [f"  untimed: {r['count']} — {r['label']}" for r in tl["untimed"]["by_reason"]]
    if tl["note"]:
        lines.append(f"  {tl['note']}")
    out.result(tl, "\n".join(lines))
    return _cli().EXIT_OK


def cmd_changes_sources(ctx, out) -> int:
    from vr_dfir.services import captures
    inv = ctx.investigation(ctx.args.id)
    rows = []
    for c in captures.catalog(inv_store(ctx), ctx.settings):
        if c.investigation_id != inv.id:
            continue
        older = captures.baselines(inv_store(ctx), ctx.settings, c)
        rows.append({"source_key": c.source_key, "full_name": c.full_name,
                     "baselines": [b.to_dict() for b in older]})
    text = "\n".join(f"{r['source_key']}: {len(r['baselines'])} earlier capture(s)"
                     + (f", latest {r['baselines'][0]['collected_at']} "
                        f"({r['baselines'][0]['investigation_id']})" if r["baselines"] else "")
                     for r in rows)
    out.result({"sources": rows}, text or "No saved sources.")
    return _cli().EXIT_OK


def inv_store(ctx):
    from vr_dfir.services.runstore import RunStore
    return RunStore(ctx.runs_root(getattr(ctx.args, "runs_dir", None)))


def cmd_changes_compare(ctx, out) -> int:
    from vr_dfir.errors import NotFoundError
    from vr_dfir.services import captures, comparisons
    store = inv_store(ctx)
    inv = store.get(ctx.args.id)
    current = captures.find(store, ctx.settings, inv.id, ctx.args.source)
    if current is None:
        raise NotFoundError("This source is not in the investigation's saved evidence.")
    if ctx.args.baseline:
        base_inv, _, base_key = ctx.args.baseline.partition(":")
        baseline = captures.find(store, ctx.settings, base_inv, base_key or ctx.args.source)
    else:
        older = captures.baselines(store, ctx.settings, current)
        baseline = older[0] if older else None
    if baseline is None:
        raise NotFoundError("No earlier compatible capture of this source was found.")
    cid = comparisons.run(store, ctx.settings, current, baseline)
    loaded = comparisons.load(inv, cid)
    page = comparisons.items(inv, cid, state=ctx.args.state, show_unchanged=ctx.args.unchanged,
                             limit=200)
    s = loaded["summary"]
    lines = [f"{loaded['definition']['adapter_label']}: {current.collected_at} vs baseline "
             f"{baseline.collected_at} ({cid}, policy {loaded['definition']['policy_version']})",
             "  " + ", ".join(f"{k} {v}" for k, v in s["counts"].items())]
    lines += [f"  limit: {x}" for x in s["limitations"]]
    for i in page["items"]:
        detail = ", ".join(f"{k}: {(i['before_values'] or {}).get(k)!r} → "
                           f"{(i['after_values'] or {}).get(k)!r}" for k in i["changed"]
                           if k != "occurrences")
        lines.append(f"  [{i['state_label']}] {i['key_display']}"
                     + (f" — {detail}" if detail else "") + (f" ({i['reason']})" if i["reason"]
                                                            else ""))
    out.result({**loaded, "items": page["items"], "total": page["total"]}, "\n".join(lines))
    return _cli().EXIT_OK


def _members(ctx, inv, idx):
    from vr_dfir.services import captures
    from vr_dfir.services.derived import IndexManager
    store = inv_store(ctx)
    orgs = sorted({s["org_id"] for s in idx.sources() if s["org_id"]})
    if len(orgs) != 1:
        return []
    synthetic = idx.meta.get("synthetic") == "true"
    mgr = IndexManager(ctx.settings)
    ids = sorted({c.investigation_id for c in captures.catalog(store, ctx.settings)
                  if c.org_id == orgs[0] and c.synthetic == synthetic and c.investigation_id
                  != inv.id})
    return [(store.get(i), mgr.require(store.get(i))) for i in ids[:200]]


def cmd_hosts(ctx, out) -> int:
    from vr_dfir.services import cohort
    inv = ctx.investigation(ctx.args.id)
    idx = _index(ctx, inv)
    members = _members(ctx, inv, idx) if ctx.args.scope == "organization" else []
    view = cohort.hosts_view(inv, idx, scope=ctx.args.scope, members=members, limit=200)
    if view["unavailable"]:
        out.result(view, view["unavailable"])
        return _cli().EXIT_OK
    lines = [f"{view['host_count']} host(s); counting {view['counting_unit']}"]
    lines += [f"  {h['hostname']}: {h['findings_open']} finding(s) need review, {h['rows']} rows, "
              f"{h['sources_complete']}/{h['sources_total']} sources read with rows ({h['os']})"
              for h in view["hosts"]]
    lines += [f"  seldom seen: {o['label']} {o['display']} — {o['text']} ({', '.join(o['hosts'])})"
              for o in view["outliers"]]
    if not view["outliers"]:
        lines.append(f"  No outlier list (needs at least {view['min_cohort']} eligible hosts, "
                     "or nothing is seldom seen).")
    out.result(view, "\n".join(lines))
    return _cli().EXIT_OK


def cmd_where_else(ctx, out) -> int:
    from vr_dfir.services import cohort
    inv = ctx.investigation(ctx.args.id)
    idx = _index(ctx, inv)
    members = _members(ctx, inv, idx) if ctx.args.scope == "organization" else []
    view = cohort.where_else(inv, idx, ctx.args.evidence_id, scope=ctx.args.scope,
                             members=members)
    lines = [view["unavailable"]] if view["unavailable"] else []
    for e in view["entities"]:
        lines.append(f"{e['label']} {e['display']}: {e['text']}")
        lines += [f"  {r['hostname'] or r['client_id']}: {r['evidence_id']} ({r['full_name']}, "
                  f"{r['investigation_id']})" for r in e["refs"]]
    out.result(view, "\n".join(lines) or "This row has no values that are compared across hosts.")
    return _cli().EXIT_OK


def cmd_pins_list(ctx, out) -> int:
    from vr_dfir.services.selection import SelectionStore
    inv = ctx.investigation(ctx.args.id)
    pins = SelectionStore(inv.path).list()
    out.result({"pins": pins}, "\n".join(
        f"{p['pin_id']} [{p['kind']}] {p['title']}" + (f" — note: {p['note']}" if p["note"] else "")
        for p in pins) or "No pinned items.")
    return _cli().EXIT_OK


def cmd_pins_add(ctx, out) -> int:
    from vr_dfir.services.derived import IndexManager
    from vr_dfir.services.selection import make_pin
    inv = ctx.investigation(ctx.args.id)
    a = ctx.args
    pin = make_pin(inv, IndexManager(ctx.settings), a.kind, ref=a.ref, comparison_id=a.comparison,
                   item_key=a.item, note=a.note)
    out.result(pin, f"Pinned {pin['pin_id']}: {pin['title']}")
    return _cli().EXIT_OK


def cmd_pins_note(ctx, out) -> int:
    from vr_dfir.services.selection import SelectionStore
    inv = ctx.investigation(ctx.args.id)
    pin = SelectionStore(inv.path).set_note(ctx.args.pin_id, ctx.args.note or None)
    out.result(pin, f"Note saved for {pin['pin_id']}")
    return _cli().EXIT_OK


def cmd_pins_remove(ctx, out) -> int:
    from vr_dfir.services.selection import SelectionStore
    inv = ctx.investigation(ctx.args.id)
    removed = SelectionStore(inv.path).remove(ctx.args.pin_id)
    out.result({"removed": removed}, "Unpinned (evidence and reviews are unchanged)."
               if removed else "That pin was not in the selection.")
    return _cli().EXIT_OK


def write_selected_report(inv, store) -> dict:
    from vr_dfir.jsonio import atomic_write_json, atomic_write_text
    from vr_dfir.reporting.selected import (
        build_selected,
        render_selected_html,
        render_selected_markdown,
    )
    report = build_selected(inv, store)
    atomic_write_json(inv.report_dir / "selected.json", report)
    atomic_write_text(inv.report_dir / "selected.md", render_selected_markdown(report))
    atomic_write_text(inv.report_dir / "selected.html", render_selected_html(report))
    return report


def add_view_parsers(sub, _add, _runs, _group) -> None:
    p = _add(sub, "overview", "summary of an investigation (metrics, charts, attention, coverage)",
             cmd_overview, "  vr-dfir overview inv-20260101-120000-abc123")
    p.add_argument("id")
    _runs(p)
    p = _add(sub, "timeline", "event-count timeline with untimed rows counted apart",
             cmd_timeline)
    p.add_argument("id")
    p.add_argument("--source", action="append", help="source key (repeatable)")
    _runs(p)
    ch = _group(sub, "changes", "compare saved captures (what changed?)")
    p = _add(ch, "sources", "sources and their earlier compatible captures", cmd_changes_sources)
    p.add_argument("id")
    _runs(p)
    p = _add(ch, "compare", "compare one source with an earlier saved capture",
             cmd_changes_compare,
             "  vr-dfir changes compare <ID> --source 'ORG/CLIENT/FLOW/ARTIFACT/Users'")
    p.add_argument("id")
    p.add_argument("--source", required=True, help="source key of this investigation")
    p.add_argument("--baseline", help="INVESTIGATION_ID[:SOURCE_KEY] (default: latest earlier)")
    p.add_argument("--state", choices=["added", "removed", "modified", "unchanged", "unknown"])
    p.add_argument("--unchanged", action="store_true", help="also list unchanged entries")
    _runs(p)
    p = _add(sub, "hosts", "host comparison and seldom-seen values", cmd_hosts)
    p.add_argument("id")
    p.add_argument("--scope", choices=["investigation", "organization"], default="investigation")
    _runs(p)
    p = _add(sub, "where-else", "where else a row's values were seen in the cohort",
             cmd_where_else)
    p.add_argument("id")
    p.add_argument("evidence_id")
    p.add_argument("--scope", choices=["investigation", "organization"], default="investigation")
    _runs(p)
    pi = _group(sub, "pins", "report selection: pinned items and notes")
    p = _add(pi, "list", "pinned items", cmd_pins_list)
    p.add_argument("id")
    _runs(p)
    p = _add(pi, "add", "pin an evidence row, a finding or a change", cmd_pins_add,
             "  vr-dfir pins add <ID> --kind finding --ref R-001 --note 'checked'")
    p.add_argument("id")
    p.add_argument("--kind", required=True, choices=["evidence", "finding", "change"])
    p.add_argument("--ref", help="evidence ID or finding ID")
    p.add_argument("--comparison", help="comparison ID (change pins)")
    p.add_argument("--item", help="change item key (change pins)")
    p.add_argument("--note")
    _runs(p)
    p = _add(pi, "note", "set or clear the note of a pin", cmd_pins_note)
    p.add_argument("id")
    p.add_argument("pin_id")
    p.add_argument("--note", default="")
    _runs(p)
    p = _add(pi, "remove", "unpin (evidence and reviews are unchanged)", cmd_pins_remove)
    p.add_argument("id")
    p.add_argument("pin_id")
    _runs(p)
