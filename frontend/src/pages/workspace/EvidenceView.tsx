// Evidence: findings (default) or source rows beside one inspector. Filters, the selected
// finding/row, mode, source and page live in the URL; opening or closing the inspector and
// Back keep the list, its filters and its scroll position.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { Bookmark, BookmarkCheck, ChevronLeft, ChevronRight } from "lucide-react";
import type { Finding, FindingsResponse, Review, ReviewState, RowsPage } from "../../api/types";
import { AiPanel } from "../../components/AiPanel";
import type { InspectorLevels } from "../../components/Inspector";
import { ConfidenceMeter, OPEN_REVIEW, ORIGIN_SHORT, REVIEW_LABELS, ReviewChip, ReviewControl, SeverityBadge, UNREVIEWED } from "../../components/findings";
import { Banner, EmptyState, ErrorBanner, Spinner, UrlSearchInput } from "../../components/ui";
import { useApp } from "../../lib/app";
import { useScrollRestore } from "../../lib/scroll";
import { useApi } from "../../lib/useApi";
import { useUrlState } from "../../lib/urlState";
import { useView } from "../../lib/useView";
import { WorkspaceInspector } from "./InspectorExtras";
import { FilterChips, PendingState, RowsTable, useWorkspace } from "./shared";

const SEVERITIES = ["high", "medium", "low", "informational"];
const LEVELS: InspectorLevels = { allFields: false, advanced: false, related: false };

function useNarrow() {
  const query = "(max-width: 1100px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setNarrow(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return narrow;
}

export function EvidenceView() {
  const ws = useWorkspace();
  const { invId, sources } = ws;
  const app = useApp();
  const [params, set] = useUrlState();
  const findingsQ = useApi<FindingsResponse>(`/investigations/${invId}/findings`);
  const reviewsQ = useApi<Record<string, Review>>(`/investigations/${invId}/reviews`);
  const data = findingsQ.data;
  const reportReady = !!data?.report_ready;
  const mode = params.get("mode") ?? (data && !reportReady ? "rows" : "findings");
  const ev = params.get("ev");
  const findingId = params.get("finding");
  const [levels, setLevels] = useState<InspectorLevels>(LEVELS);
  const [focusField, setFocusField] = useState<string | null>(null);
  const narrow = useNarrow();
  const reviews = reviewsQ.data ?? {};
  const reviewOf = useCallback((fid: string) => reviews[fid] ?? UNREVIEWED, [reviews]);
  const hostList = useMemo(() => [...new Map((sources?.sources ?? []).map((s) => [s.client_id, s.hostname || s.client_id])).entries()], [sources]);
  const hostName = (cid: string) => hostList.find(([c]) => c === cid)?.[1] ?? cid;
  const srcLabel = (key: string) => sources?.sources.find((s) => s.source_key === key)?.label ?? key;
  useScrollRestore(!!data);
  useEffect(() => {
    if (narrow && findingId && !ev) setTimeout(() => document.querySelector(".finding-panel")?.scrollIntoView({ block: "start" }), 50);
  }, [narrow, findingId, ev]);

  const openRow = (eid: string, field: string | null = null) => {
    setFocusField(field);
    set({ ev: eid }, { replace: true });
    if (narrow) setTimeout(() => document.querySelector(".inspector")?.scrollIntoView({ block: "start" }), 50);
  };
  const closeRow = () => {
    const prev = ev;
    set({ ev: null }, { replace: true });
    setTimeout(() => {
      const row = prev ? document.querySelector<HTMLElement>(`tr[data-row-key="${CSS.escape(prev)}"]`) : null;
      row?.focus({ preventScroll: false });
    }, 30);
  };

  return (
    <div className="stack">
      <div className="row between view-head">
        <div className="seg" role="tablist" aria-label="Evidence view">
          <button role="tab" aria-selected={mode === "findings"} className={`seg-btn ${mode === "findings" ? "on" : ""}`} onClick={() => set({ mode: "findings", ev: null, offset: null })}>
            Findings{data?.report_ready ? ` (${data.findings.length})` : ""}
          </button>
          <button role="tab" aria-selected={mode === "rows"} className={`seg-btn ${mode === "rows" ? "on" : ""}`} onClick={() => set({ mode: "rows", ev: null, finding: null })}>
            Source rows
          </button>
        </div>
        <FilterChips params={params} set={set} labels={{ host: hostName, src: srcLabel, review: (v) => (v === "open" ? "Needs review" : REVIEW_LABELS[v as ReviewState] ?? v) }} />
      </div>
      <ErrorBanner error={findingsQ.error} />
      {data?.provider?.fake && (
        <Banner tone="synthetic" title="Fake model (test fixture)">
          AI proposals in this investigation come from a deterministic test fixture, not a real model.
        </Banner>
      )}
      <div className={`evidence-layout ${ev || findingId ? "has-detail" : ""}`}>
        <div className="evidence-list">
          {mode === "findings" ? (
            !data ? (
              <Spinner />
            ) : !reportReady ? (
              <Banner tone="info" title="Findings are not ready yet">
                Analysis has not produced a report. <button className="btn link" onClick={() => set({ mode: "rows" })}>Browse the saved source rows</button>
              </Banner>
            ) : (
              <FindingList findings={data.findings} reviewOf={reviewOf} params={params} set={set} hosts={hostList} rejected={data.rejected_findings.length} />
            )
          ) : (
            <RowsPane invId={invId} params={params} set={set} selected={ev} onSelect={(eid) => openRow(eid)} onExpand={(eid, f) => openRow(eid, f)} />
          )}
        </div>
        <div className="evidence-detail">
          {ev ? (
            <WorkspaceInspector
              ws={ws}
              invId={invId}
              evidenceId={ev}
              levels={levels}
              onLevels={setLevels}
              onClose={closeRow}
              onOpenEvidence={(eid) => openRow(eid)}
              onOpenFinding={(fid) => set({ mode: "findings", finding: fid, ev: null })}
              focusField={focusField}
              back={findingId && mode === "findings" ? { label: `Back to finding ${findingId}`, onClick: () => set({ ev: null }, { replace: true }) } : narrow ? { label: "Back to the list", onClick: closeRow } : null}
            />
          ) : mode === "findings" && findingId && data ? (
            <FindingPanel
              invId={invId}
              finding={data.findings.find((f) => f.finding_id === findingId) ?? null}
              review={reviewOf(findingId)}
              onReviewed={(r) => reviewsQ.setData({ ...reviews, [findingId]: r })}
              onOpenRow={(eid) => openRow(eid)}
              aiEnabled={ws.aiEnabled}
              pinned={ws.pins.isPinned("finding", findingId)}
              onPin={(on) => (on ? ws.pins.pin({ kind: "finding", ref: findingId }) : ws.pins.unpin(ws.pins.isPinned("finding", findingId) as string)).catch((e) => app.toast(String(e?.message ?? e)))}
              nav={<FindingNav findings={data.findings} reviewOf={reviewOf} params={params} set={set} current={findingId} />}
            />
          ) : (
            <div className="card hint">
              <p className="muted small">{mode === "findings" ? "Select a finding to see why it needs attention, its evidence rows, and the review decision." : "Select a row to inspect its fields, the reason for attention and its provenance."}</p>
            </div>
          )}
          {data?.citation_note && mode === "findings" && <p className="xsmall muted">{data.citation_note}</p>}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ findings

function filterFindings(findings: Finding[], reviewOf: (id: string) => Review, params: URLSearchParams) {
  const sev = params.get("sev");
  const review = params.get("review");
  const origin = params.get("origin");
  const host = params.get("host");
  const fq = (params.get("fq") ?? "").toLowerCase();
  return findings.filter(
    (f) =>
      (!sev || f.severity === sev) &&
      (!origin || f.origin === origin) &&
      (!host || f.client_ids.includes(host)) &&
      (!review || (review === "open" ? OPEN_REVIEW.includes(reviewOf(f.finding_id).state) : reviewOf(f.finding_id).state === review)) &&
      (!fq || `${f.finding_id} ${f.title} ${f.observation}`.toLowerCase().includes(fq)),
  );
}

function FindingList({ findings, reviewOf, params, set, hosts, rejected }: { findings: Finding[]; reviewOf: (id: string) => Review; params: URLSearchParams; set: ReturnType<typeof useUrlState>[1]; hosts: [string, string][]; rejected: number }) {
  const shown = filterFindings(findings, reviewOf, params);
  const current = params.get("finding");
  const sevCounts = SEVERITIES.map((s) => [s, findings.filter((f) => f.severity === s).length] as const).filter(([, n]) => n);
  return (
    <div className="stack">
      <div className="card tight stack-sm">
        <div className="row">
          <label className="field small grow">
            <span className="sr-only">Search findings</span>
            <UrlSearchInput value={params.get("fq") ?? ""} onCommit={(v) => set({ fq: v }, { replace: true })} placeholder="Search findings" />
          </label>
          <select aria-label="Review state" value={params.get("review") ?? ""} onChange={(e) => set({ review: e.target.value || null })}>
            <option value="">Any review state</option>
            <option value="open">Needs review</option>
            {Object.entries(REVIEW_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div className="row">
          {sevCounts.map(([s, n]) => (
            <button key={s} className={`btn small ${params.get("sev") === s ? "" : "ghost"}`} aria-pressed={params.get("sev") === s} onClick={() => set({ sev: params.get("sev") === s ? null : s })}>
              {s[0].toUpperCase() + s.slice(1)} ({n})
            </button>
          ))}
          {hosts.length > 1 && (
            <select aria-label="Host" value={params.get("host") ?? ""} onChange={(e) => set({ host: e.target.value || null })}>
              <option value="">All hosts</option>
              {hosts.map(([c, h]) => (
                <option key={c} value={c}>
                  {h}
                </option>
              ))}
            </select>
          )}
        </div>
        <p className="xsmall muted">
          {shown.length} of {findings.length} finding(s). Severity, review decision and confidence are separate.
        </p>
      </div>
      {findings.length === 0 ? (
        <div className="card">
          <p>
            <strong>No accepted findings.</strong>
          </p>
          <p className="small">This is not a clean result. It reflects only the retrieved and reviewed data; check coverage on the Overview and browse the source rows.</p>
        </div>
      ) : shown.length === 0 ? (
        <EmptyState title="No findings match these filters." />
      ) : (
        <ul className="plain finding-list" aria-label="Findings">
          {shown.map((f) => (
            <li key={f.finding_id}>
              <button className={`list-item ${current === f.finding_id ? "selected" : ""}`} aria-current={current === f.finding_id ? "true" : undefined} onClick={() => set({ finding: f.finding_id, ev: null })}>
                <div className="li-title" dir="auto">
                  {f.title}
                </div>
                <div className="li-meta">
                  <SeverityBadge value={f.severity} />
                  <ReviewChip state={reviewOf(f.finding_id).state} />
                  <span className="xsmall muted">
                    {f.finding_id} · {ORIGIN_SHORT[f.origin]} · {f.evidence_ids.length} row(s)
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
      {rejected > 0 && <p className="xsmall muted">{rejected} AI proposal(s) failed validation and are not findings; they are listed in the full report.</p>}
    </div>
  );
}

function FindingNav({ findings, reviewOf, params, set, current }: { findings: Finding[]; reviewOf: (id: string) => Review; params: URLSearchParams; set: ReturnType<typeof useUrlState>[1]; current: string }) {
  const shown = filterFindings(findings, reviewOf, params);
  const pos = shown.findIndex((f) => f.finding_id === current);
  const go = (d: number) => shown[pos + d] && set({ finding: shown[pos + d].finding_id, ev: null }, { replace: true });
  return (
    <nav className="detail-nav" aria-label="Finding navigation">
      <button className="btn small ghost" onClick={() => go(-1)} disabled={pos <= 0}>
        <ChevronLeft size={14} aria-hidden /> Previous
      </button>
      <span className="xsmall muted">{pos >= 0 ? `${pos + 1} of ${shown.length}` : "not in the current filter"}</span>
      <button className="btn small ghost" onClick={() => go(1)} disabled={pos < 0 || pos >= shown.length - 1}>
        Next <ChevronRight size={14} aria-hidden />
      </button>
    </nav>
  );
}

function FindingPanel({ invId, finding: f, review, onReviewed, onOpenRow, aiEnabled, pinned, onPin, nav }: { invId: string; finding: Finding | null; review: Review; onReviewed: (r: Review) => void; onOpenRow: (eid: string) => void; aiEnabled: boolean; pinned: string | null; onPin: (on: boolean) => void; nav: React.ReactNode }) {
  if (!f) return <Banner tone="warn" title="This finding is not in the investigation's report.">It may come from an older report. No other finding is shown in its place.</Banner>;
  const more: [string, string[]][] = [
    ["Plausible benign explanations", f.benign_explanations],
    ["Missing context", f.missing_context],
    ["Suggested verification", f.next_steps],
  ];
  return (
    <section className="card finding-panel" aria-label={`Finding ${f.finding_id}`}>
      {nav}
      <div className="card-head">
        <h2 dir="auto">{f.title}</h2>
        <button className="btn small" aria-pressed={!!pinned} onClick={() => onPin(!pinned)}>
          {pinned ? <BookmarkCheck size={14} aria-hidden /> : <Bookmark size={14} aria-hidden />} {pinned ? "Pinned" : "Pin to report"}
        </button>
      </div>
      <div className="attrs">
        <SeverityBadge value={f.severity} />
        <ReviewChip state={review.state} />
        <ConfidenceMeter value={f.confidence} />
        <span className="xsmall muted">
          {f.finding_id} · {ORIGIN_SHORT[f.origin]}
        </span>
      </div>
      <h3>What was observed</h3>
      <p dir="auto">{f.observation}</p>
      <h3>Why it needs attention</h3>
      <p dir="auto">{f.interpretation}</p>
      <ReviewControl invId={invId} findingId={f.finding_id} review={review} onSaved={onReviewed} />
      <h3>Show evidence ({f.evidence_refs.length})</h3>
      <ul className="plain stack-xs evidence-refs">
        {f.evidence_refs.map((r) =>
          r.unresolved ? (
            <li key={r.evidence_id} className="field-error">
              <code>{r.evidence_id}</code> could not be resolved; no other row is shown in its place.
            </li>
          ) : (
            <li key={r.evidence_id}>
              <button className="btn link small" onClick={() => onOpenRow(r.evidence_id)} aria-label={`Open evidence ${r.evidence_id}`}>
                {r.artifact}/{r.source ?? ""} row {r.row_index}
              </button>{" "}
              <span className="xsmall muted" dir="auto">
                {r.hostname || r.client_id}
              </span>
            </li>
          ),
        )}
      </ul>
      {f.evidence_ids.length > f.evidence_refs.length && <p className="xsmall muted">The finding cites more rows than are listed.</p>}
      <details className="advanced">
        <summary>More detail</summary>
        <p className="small muted">Confidence rationale: {f.confidence_rationale}</p>
        {more.map(([label, items]) =>
          items.length ? (
            <div key={label}>
              <h3>{label}</h3>
              <ul>
                {items.map((x) => (
                  <li key={x} dir="auto">
                    {x}
                  </li>
                ))}
              </ul>
            </div>
          ) : null,
        )}
      </details>
      {aiEnabled && f.evidence_refs.some((r) => !r.unresolved) && (
        <AiPanel
          path={`/investigations/${invId}/findings/${encodeURIComponent(f.finding_id)}/explain`}
          actions={[
            { id: "finding_summary", label: "Explain why this finding deserves review", body: { action: "finding_summary" } },
            { id: "next_question", label: "Suggest a next investigation question", body: { action: "next_question" } },
          ]}
          onCitation={(c) => typeof c.evidence_id === "string" && onOpenRow(c.evidence_id)}
        />
      )}
      {!aiEnabled && (
        <p className="xsmall muted">
          AI explanations are off. Review, filtering, charts, comparison and reports work without them (<Link to="/settings">Settings</Link>).
        </p>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ rows

function RowsPane({ invId, params, set, selected, onSelect, onExpand }: { invId: string; params: URLSearchParams; set: ReturnType<typeof useUrlState>[1]; selected: string | null; onSelect: (eid: string) => void; onExpand: (eid: string, field: string) => void }) {
  const { sources } = useWorkspace();
  const app = useApp();
  const list = useMemo(() => sources?.sources ?? [], [sources]);
  const scoped = ["sev", "review", "origin", "host", "from", "to", "untimed", "ek", "cited"].some((k) => params.get(k));
  // Default: the source with the most rows cited by findings, then the largest mapped source.
  const preferred = [...list].sort((a, b) => (b.cited_rows ?? 0) - (a.cited_rows ?? 0) || Number(b.domain !== "generic") - Number(a.domain !== "generic") || b.rows - a.rows)[0];
  const src = params.get("src") ?? (scoped ? null : preferred?.source_key ?? null);
  const limit = app.preferences?.page_size ?? 50;
  const offset = Number(params.get("offset") ?? 0);
  const q = {
    source_key: src ?? undefined,
    client_id: params.get("host") ?? undefined,
    severity: params.get("sev") ?? undefined,
    review: params.get("review") ?? undefined,
    origin: params.get("origin") ?? undefined,
    finding: params.get("rfinding") ?? undefined,
    cited: params.get("cited") === "1" ? true : undefined,
    from_us: params.get("from") ?? undefined,
    to_us: params.get("to") ?? undefined,
    untimed: params.get("untimed") ?? undefined,
    entity_kind: params.get("ek") ?? undefined,
    entity_key: params.get("ekey") ?? undefined,
    q: params.get("q") ?? undefined,
    offset,
    limit,
  };
  const page = useView<RowsPage>(sources ? `/investigations/${invId}/views/rows` : null, q);
  const current = list.find((s) => s.source_key === src);
  return (
    <div className="stack">
      <div className="card tight stack-sm">
        <div className="row">
          {list.length > 1 && (
            <label className="field small grow">
              Source
              <select value={src ?? ""} onChange={(e) => set({ src: e.target.value || null, offset: null, ev: null, q: null })}>
                {scoped && <option value="">All sources (filtered)</option>}
                {list.map((s) => (
                  <option key={s.source_key} value={s.source_key}>
                    {s.label} — {s.rows.toLocaleString()} row{s.rows === 1 ? "" : "s"}{s.cited_rows ? `, ${s.cited_rows} cited` : ""}{s.state !== "complete" ? ` (${s.state_label.toLowerCase()})` : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field small grow">
            Search in {src ? "this source" : "the filtered rows"}
            <UrlSearchInput value={params.get("q") ?? ""} onCommit={(v) => set({ q: v, offset: null })} placeholder="Plain text, e.g. /tmp or 203.0.113" />
          </label>
        </div>
        {current && current.state !== "complete" && (
          <p className="small">
            <strong>{current.state_label}.</strong> {current.stop_reason ? `Stopped: ${current.stop_reason.replace(/_/g, " ")}. ` : ""}Zero or few rows here do not mean nothing was present.
          </p>
        )}
      </div>
      {page.pending ? (
        <PendingState pending={page.pending} invId={invId} onRetry={page.reload} what="the rows" />
      ) : page.error ? (
        <ErrorBanner error={page.error} />
      ) : !page.data ? (
        <Spinner label="Loading rows" />
      ) : (
        <RowsTable
          page={page.data}
          selected={selected}
          onSelect={onSelect}
          onExpand={onExpand}
          onPage={(o) => set({ offset: String(o) })}
          label={page.data.source ? `Rows of ${page.data.source.label}` : "Filtered evidence rows"}
          empty={<EmptyState title={params.get("q") ? "No rows match this search." : "No saved rows in this scope."}>{!params.get("q") && <p className="small">Check the source state above: zero rows can mean empty, skipped, failed or not read.</p>}</EmptyState>}
        />
      )}
    </div>
  );
}
