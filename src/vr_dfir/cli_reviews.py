"""Finding review and evidence-linked AI explanations."""

from __future__ import annotations


def _cli():
    from vr_dfir import cli
    return cli

def cmd_findings_review(ctx, out) -> int:
    cli = _cli()
    from vr_dfir.services.reviews import set_review
    inv = ctx.investigation(ctx.args.id)
    report = cli._report_data(inv)
    found = {f.get("finding_id") for f in report.get("findings") or []}
    entry = set_review(inv.path, found, ctx.args.finding_id, ctx.args.state, ctx.args.note,
                       by=f"cli:{_user()}")
    out.result({"investigation_id": inv.id, "finding_id": ctx.args.finding_id, "review": entry},
               f"{ctx.args.finding_id}: review status {entry['label']}"
               + (f" — {entry['note']}" if entry.get("note") else ""))
    return cli.EXIT_OK


def cmd_findings_explain(ctx, out) -> int:
    from vr_dfir.services.explanations import explain_finding
    inv = ctx.investigation(ctx.args.id)
    result = explain_finding(inv, ctx.runtime(), ctx.args.finding_id, refresh=ctx.args.refresh)
    return _print_explanation(out, result)


def _user() -> str:
    from vr_dfir.config import current_user
    return current_user()


def _print_explanation(out, result: dict) -> int:
    cli = _cli()
    lines = [f"{result.get('label') or 'AI interpretation'}",
             f"  Status: {result.get('status')}"
             + (" (cached)" if result.get("cached") else "")
             + (f" — {result.get('reason')}" if result.get("reason") else "")]
    if result.get("model"):
        lines.append(f"  Model: {result.get('provider')} {result.get('model')}; prompt "
                     f"{result.get('prompt_version')}; records supplied "
                     f"{result.get('records_supplied')}, omitted {result.get('records_omitted')}")
    if result.get("summary"):
        lines.append(f"  Summary: {result['summary']}")
    for section, title in (("points", "Points"), ("gaps", "Gaps"), ("next_steps", "Next steps")):
        items = result.get(section) or []
        if items:
            lines.append(f"  {title}:")
            for it in items:
                lines.append(f"    - {it['text']} [{', '.join(it['refs'])}]")
    if result.get("rejected"):
        lines.append(f"  {len(result['rejected'])} statement(s) rejected by validation "
                     "(see --json).")
    out.result(result, "\n".join(lines))
    status = result.get("status")
    if status == "ok":
        return cli.EXIT_OK
    if status in ("unavailable", "unsupported"):
        return cli.EXIT_PARTIAL
    return cli.EXIT_FAIL


def add_findings_parsers(fi, _add, _runs) -> None:
    from vr_dfir.services.reviews import REVIEW_STATES
    p = _add(fi, "review", "set the analyst review status of a finding", cmd_findings_review,
             "  vr-dfir findings review <ID> M-01 --state needs_follow_up --note 'check parent'")
    p.add_argument("id")
    p.add_argument("finding_id")
    p.add_argument("--state", required=True, choices=list(REVIEW_STATES))
    p.add_argument("--note")
    _runs(p)
    p = _add(fi, "explain", "AI summary of a finding and its cited evidence (optional)",
             cmd_findings_explain)
    p.add_argument("id")
    p.add_argument("finding_id")
    p.add_argument("--refresh", action="store_true")
    _runs(p)


