"""Markdown and self-contained HTML renderings of the canonical JSON report.

All untrusted content is escaped. The HTML uses inline CSS and native ``<details>``
elements only: no JavaScript, no external assets, no network requests.
"""

from __future__ import annotations

import html
import json

_BADGE = {
    "rows": ("ok", "Rows"), "empty_or_skipped": ("warn", "Empty or skipped"),
    "skipped_precondition": ("warn", "Skipped (precondition)"),
    "source_failed": ("bad", "Source failed"), "not_collected": ("bad", "Not collected"),
    "unavailable": ("bad", "Unavailable"), "unknown": ("warn", "Unknown"),
    "complete": ("ok", "Read complete"), "incomplete": ("warn", "Read incomplete"),
    "read_failed": ("bad", "Read failed"), "not_started": ("warn", "Not read"),
    "not_applicable": ("muted", "Not applicable"), "in_progress": ("warn", "Reading"),
}


def _md_escape(text) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("`", "\\`").replace("*", "\\*").replace("_", "\\_").replace("[", "\\[")
    return s.replace("\n", " ")


def render_markdown(report: dict) -> str:
    inv = report["investigation"]
    out = []
    title = inv.get("title") or inv.get("id")
    out.append(f"# Investigation report: {_md_escape(title)}")
    if report.get("data_label"):
        out.append(f"\n> **{report['data_label']}**")
    out.append("")
    out.append(f"- Investigation ID: `{inv.get('id')}`")
    out.append(f"- Mode: {_md_escape(inv.get('mode'))}")
    out.append(f"- Outcome: **{_md_escape(inv.get('outcome_label'))}**")
    st = report["status"]
    out.append(f"- Collection: {_md_escape(st.get('collection'))}; Retrieval: "
               f"{_md_escape(st.get('retrieval'))}; Analysis: {_md_escape(st.get('analysis'))}")
    prov = report.get("provider") or {}
    out.append(f"- Provider/model: {_md_escape(prov.get('name'))} / "
               f"{_md_escape(prov.get('model') or '-')}"
               + (" (FAKE MODEL)" if prov.get("fake") else ""))
    scope = report.get("scope") or {}
    out.append(f"- Scope: org `{scope.get('org_id')}`"
               + (f", client `{scope.get('client_id')}`" if scope.get("client_id") else "")
               + (f" ({_md_escape(scope.get('hostname'))})" if scope.get("hostname") else "")
               + (f", flow `{scope.get('flow_id')}`" if scope.get("flow_id") else "")
               + (f", hunt `{scope.get('hunt_id')}`" if scope.get("hunt_id") else ""))
    if report.get("profile"):
        p = report["profile"]
        out.append(f"- Profile: {_md_escape(p.get('name'))} (`{p.get('id')}`, fingerprint "
                   f"`{str(p.get('fingerprint'))[:12]}`)")
    out.append(f"- Generated: {report['generated_at']} by {report['generator']}")
    out.append("\n## Summary\n")
    for s in report["summary_statements"]:
        out.append(f"- {_md_escape(s)}")
    out.append("\n## Coverage\n")
    out.append("| Source | Client | Result | Read | Rows saved | Available | Notes |")
    out.append("|---|---|---|---|---|---|---|")
    for s in report["coverage"]["sources"]:
        note = s.get("error") or "; ".join(s.get("notes") or []) or (
            "counts only" if s.get("counts_only") else "")
        out.append(f"| {_md_escape(s['full_name'])} | {_md_escape(s.get('hostname') or s['client_id'])} "
                   f"| {_md_escape(s['result_status'])} | {_md_escape(s['read_status'])} | "
                   f"{s['persisted_rows']} | {_md_escape(s['available_rows'])} | {_md_escape(note)} |")
    if report["coverage"]["gaps"]:
        out.append("\n**Coverage gaps**\n")
        for g in report["coverage"]["gaps"]:
            out.append(f"- {_md_escape(g)}")
    out.append("\n## Findings\n")
    if not report["findings"]:
        out.append("No accepted findings. This does not mean the target is clean; see coverage.")
    for f in report["findings"]:
        out.append(f"### {f['finding_id']}: {_md_escape(f['title'])}\n")
        out.append(f"- Origin: {_md_escape(f['origin'])}; Severity: {_md_escape(f['severity'])}; "
                   f"Confidence: {_md_escape(f['confidence'])}")
        out.append(f"- Observation: {_md_escape(f['observation'])}")
        out.append(f"- Interpretation: {_md_escape(f['interpretation'])}")
        out.append(f"- Confidence rationale: {_md_escape(f['confidence_rationale'])}")
        for label, key in (("Benign explanations", "benign_explanations"),
                           ("Missing context", "missing_context"),
                           ("Suggested verification", "next_steps")):
            if f.get(key):
                out.append(f"- {label}: " + "; ".join(_md_escape(x) for x in f[key]))
        refs = []
        for r in f.get("evidence_refs", []):
            if r.get("unresolved"):
                refs.append(f"`{r['evidence_id']}` (UNRESOLVED)")
            else:
                refs.append(f"`{r['evidence_id']}` ({_md_escape(r['artifact'])}/"
                            f"{_md_escape(r.get('source') or '')} row {r['row_index']}, "
                            f"{r['client_id']}, {r['flow_id']})")
        out.append("- Evidence: " + ", ".join(refs))
        out.append("")
    if report["rejected_findings"]:
        out.append("\n## Rejected model output\n")
        out.append("These proposals failed validation and are not findings.\n")
        for r in report["rejected_findings"]:
            out.append(f"- {_md_escape(r.get('title') or '(untitled)')}: "
                       + _md_escape("; ".join(r.get("reasons") or [])))
    if report.get("hunt"):
        h = report["hunt"]
        out.append("\n## Hunt coverage\n")
        out.append(f"- Contributing flows enumerated: {h['contributing_flows_seen']} "
                   f"(enumeration complete: {h['enumeration_complete']})")
        out.append(f"- Flow states: {_md_escape(json.dumps(h['flow_states']))}")
        out.append(f"- {_md_escape(h['coverage_note'])}")
    if report.get("errors"):
        out.append("\n## Errors\n")
        for e in report["errors"]:
            out.append(f"- {_md_escape(e)}")
    if report.get("next_steps"):
        out.append("\n## Suggested follow-up\n")
        for s in report["next_steps"]:
            out.append(f"- {_md_escape(s)}")
    out.append(f"\n---\n{_md_escape(report['citation_validation_note'])}\n")
    return "\n".join(out) + "\n"


_CSS = """
:root{--bg:#f6f8fa;--surface:#fff;--text:#1f2933;--muted:#5f6b7a;--border:#d9e0e7;
--accent:#1f6feb;--ok:#1a7f37;--warn:#9a6700;--bad:#c62828}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",
Roboto,"Noto Sans",sans-serif;background:var(--bg);color:var(--text)}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 8px}
h2{font-size:17px;margin:28px 0 8px}h3{font-size:15px;margin:0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
padding:14px 16px;margin:10px 0}.synthetic{background:#fff4ce;border:1px solid #e3c55a;
padding:8px 12px;border-radius:6px;font-weight:600}.meta{color:var(--muted)}
table{border-collapse:collapse;width:100%;background:var(--surface)}th,td{border:1px solid
var(--border);padding:6px 8px;text-align:left;vertical-align:top}th{background:#eef2f6}
.badge{display:inline-block;border-radius:10px;padding:1px 8px;font-size:12px;
border:1px solid currentColor}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.muted{color:var(--muted)}code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,
Consolas,monospace;font-size:12.5px}td.v{white-space:pre-wrap;word-break:break-word}
details>summary{cursor:pointer;color:var(--accent)}a{color:var(--accent)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px}
@media (max-width:700px){table{display:block;overflow-x:auto}}
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _badge(status) -> str:
    cls, label = _BADGE.get(str(status), ("muted", str(status)))
    return f'<span class="badge {cls}">{_e(label)}</span>'


def render_html(report: dict) -> str:
    inv = report["investigation"]
    st = report["status"]
    prov = report.get("provider") or {}
    scope = report.get("scope") or {}
    parts = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        "style-src 'unsafe-inline'; img-src 'none'; script-src 'none'\">",
        f"<title>Investigation report {_e(inv.get('id'))}</title>",
        f"<style>{_CSS}</style></head><body><main>",
    ]
    if report.get("data_label"):
        parts.append(f'<p class="synthetic">{_e(report["data_label"])}</p>')
    parts.append(f"<h1>Investigation report: {_e(inv.get('title') or inv.get('id'))}</h1>")
    parts.append('<div class="card kv">')
    rows = [
        ("Investigation ID", f"<code>{_e(inv.get('id'))}</code>"),
        ("Mode", _e(inv.get("mode"))),
        ("Outcome", f"<strong>{_e(inv.get('outcome_label'))}</strong>"),
        ("Collection", _e(st.get("collection"))), ("Retrieval", _e(st.get("retrieval"))),
        ("Analysis", _e(st.get("analysis"))),
        ("Provider / model", _e(prov.get("name")) + " / " + _e(prov.get("model") or "-")
         + (" <strong>(FAKE MODEL)</strong>" if prov.get("fake") else "")),
        ("Org", f"<code>{_e(scope.get('org_id'))}</code>"),
    ]
    if scope.get("client_id"):
        rows.append(("Client", f"<code>{_e(scope.get('client_id'))}</code> "
                     f"<span dir=\"auto\">{_e(scope.get('hostname'))}</span>"))
    if scope.get("flow_id"):
        rows.append(("Flow", f"<code>{_e(scope.get('flow_id'))}</code>"))
    if scope.get("hunt_id"):
        rows.append(("Hunt", f"<code>{_e(scope.get('hunt_id'))}</code>"))
    if report.get("profile"):
        p = report["profile"]
        rows.append(("Profile", f"{_e(p.get('name'))} (<code>{_e(p.get('id'))}</code>)"))
    rows.append(("Generated", f"{_e(report['generated_at'])} - {_e(report['generator'])}"))
    for k, v in rows:
        parts.append(f'<div class="meta">{_e(k)}</div><div>{v}</div>')
    parts.append("</div>")
    parts.append(f'<p class="meta">{_e(st.get("note"))}</p>')
    parts.append("<h2>Summary</h2><ul>")
    for s in report["summary_statements"]:
        parts.append(f"<li>{_e(s)}</li>")
    parts.append("</ul>")

    parts.append("<h2>Coverage</h2><table><thead><tr><th>Source</th><th>Client</th>"
                 "<th>Result</th><th>Read</th><th>Rows saved</th><th>Available</th>"
                 "<th>Notes</th></tr></thead><tbody>")
    for s in report["coverage"]["sources"]:
        note = s.get("error") or "; ".join(s.get("notes") or [])
        if s.get("counts_only"):
            note = ("Counts only (rows not given to the model). " + (note or "")).strip()
        parts.append(
            f"<tr><td><code>{_e(s['full_name'])}</code></td>"
            f"<td><span dir=\"auto\">{_e(s.get('hostname'))}</span><br><code>{_e(s['client_id'])}</code></td>"
            f"<td>{_badge(s['result_status'])}</td><td>{_badge(s['read_status'])}"
            + (f"<br><span class=\"meta\">stop: {_e(s.get('stop_reason'))}</span>"
               if s.get("stop_reason") else "")
            + f"</td><td>{_e(s['persisted_rows'])}</td><td>{_e(s['available_rows'])}</td>"
            f"<td>{_e(note)}</td></tr>")
    parts.append("</tbody></table>")
    if report["coverage"]["gaps"]:
        parts.append('<div class="card"><strong>Coverage gaps</strong><ul>')
        for g in report["coverage"]["gaps"]:
            parts.append(f"<li>{_e(g)}</li>")
        parts.append("</ul></div>")

    parts.append("<h2>Findings</h2>")
    if not report["findings"]:
        parts.append('<p class="card">No accepted findings. This does not mean the target is '
                     "clean; review the coverage above.</p>")
    for f in report["findings"]:
        sev_cls = {"high": "bad", "medium": "warn"}.get(f["severity"], "muted")
        parts.append(f'<section class="card" id="{_e(f["finding_id"])}">')
        parts.append(f"<h3>{_e(f['finding_id'])}: {_e(f['title'])}</h3>")
        parts.append(f'<p class="meta">Origin: {_e(f["origin"])} &middot; Severity: '
                     f'<span class="badge {sev_cls}">{_e(f["severity"])}</span> &middot; '
                     f'Confidence: {_e(f["confidence"])}</p>')
        parts.append(f"<p><strong>Observation.</strong> <span dir=\"auto\">{_e(f['observation'])}</span></p>")
        parts.append(f"<p><strong>Interpretation.</strong> {_e(f['interpretation'])}</p>")
        parts.append(f"<p class=\"meta\">Confidence rationale: {_e(f['confidence_rationale'])}</p>")
        for label, key in (("Benign explanations", "benign_explanations"),
                           ("Missing context", "missing_context"),
                           ("Suggested verification", "next_steps")):
            if f.get(key):
                parts.append(f"<p><strong>{label}.</strong></p><ul>"
                             + "".join(f"<li>{_e(x)}</li>" for x in f[key]) + "</ul>")
        parts.append("<p><strong>Evidence.</strong> ")
        links = []
        for r in f.get("evidence_refs", []):
            if r.get("unresolved"):
                links.append(f'<span class="bad">{_e(r["evidence_id"])} (unresolved)</span>')
            else:
                links.append(f'<a href="#{_e(r["evidence_id"])}"><code>{_e(r["evidence_id"])}</code></a>'
                             f' <span class="meta">({_e(r["artifact"])}/{_e(r.get("source") or "")} '
                             f'row {_e(r["row_index"])})</span>')
        parts.append(", ".join(links) + "</p></section>")

    if report["rejected_findings"]:
        parts.append("<h2>Rejected model output</h2><p class=\"meta\">These proposals failed "
                     "validation and are not findings.</p><ul>")
        for r in report["rejected_findings"]:
            parts.append(f"<li>{_e(r.get('title') or '(untitled)')}: "
                         f"{_e('; '.join(r.get('reasons') or []))}</li>")
        parts.append("</ul>")

    if report.get("hunt"):
        h = report["hunt"]
        parts.append("<h2>Hunt coverage</h2><div class=\"card\">")
        parts.append(f"<p>Contributing flows enumerated: {_e(h['contributing_flows_seen'])} "
                     f"(enumeration complete: {_e(h['enumeration_complete'])})</p>")
        parts.append(f"<p>Flow states: <code>{_e(json.dumps(h['flow_states']))}</code></p>")
        parts.append(f"<p class=\"meta\">{_e(h['coverage_note'])}</p></div>")

    parts.append("<h2>Evidence rows cited by findings</h2>")
    parts.append(f'<p class="meta">{_e(report["evidence"]["note"])}</p>')
    for row in report["evidence"]["cited_rows"]:
        parts.append(f'<section class="card" id="{_e(row["evidence_id"])}">')
        parts.append(f"<h3><code>{_e(row['evidence_id'])}</code></h3>")
        parts.append(f'<p class="meta">{_e(row["artifact"])}/{_e(row.get("source") or "")} '
                     f"&middot; row {_e(row['row_index'])} &middot; client "
                     f"<code>{_e(row['client_id'])}</code> <span dir=\"auto\">{_e(row.get('hostname'))}</span> "
                     f"&middot; flow <code>{_e(row['flow_id'])}</code> &middot; org "
                     f"<code>{_e(row['org_id'])}</code></p>")
        parts.append("<table><tbody>")
        raw = row.get("row")
        if isinstance(raw, dict):
            for k, v in raw.items():
                shown = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                parts.append(f"<tr><th class=\"mono\">{_e(k)}</th><td class=\"v mono\" "
                             f"dir=\"auto\">{_e(shown)}</td></tr>")
        parts.append("</tbody></table>")
        parts.append("<details><summary>Raw JSON and provenance</summary><pre class=\"mono\">"
                     + _e(json.dumps({k: v for k, v in row.items()}, ensure_ascii=False,
                                     indent=2)) + "</pre></details></section>")
    if report["evidence"].get("resolution_problems"):
        parts.append('<div class="card bad"><strong>Citation problems</strong><ul>'
                     + "".join(f"<li>{_e(p)}</li>" for p in report["evidence"]["resolution_problems"])
                     + "</ul></div>")
    if report.get("errors"):
        parts.append("<h2>Errors</h2><ul>" + "".join(f"<li>{_e(e)}</li>" for e in report["errors"])
                     + "</ul>")
    if report.get("next_steps"):
        parts.append("<h2>Suggested follow-up</h2><ul>"
                     + "".join(f"<li>{_e(s)}</li>" for s in report["next_steps"]) + "</ul>")
    parts.append(f'<p class="meta">{_e(report["citation_validation_note"])}</p>')
    parts.append("</main></body></html>")
    return "\n".join(parts)
