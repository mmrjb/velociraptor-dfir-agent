// Findings & Evidence workspace: a filterable finding list beside the selected finding and its
// evidence. Filters live in the URL, so previous/next navigation and reloads keep them. Evidence
// links open the exact saved row (located on the server) and highlight it; nearby rows are never
// substituted. Severity, analyst review status, and confidence are shown as separate concepts.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { apiGet, apiPut, errorMessage } from "../api/client";
import type { ApiError, BundleSources, CoverageSource, EvidencePage, EvidenceRecord, EvidenceRef, Finding, FindingsResponse, InvestigationSummary, Review, ReviewState } from "../api/types";
import { useApp } from "../lib/app";
import { ORIGIN_LABELS, formatTime } from "../lib/format";
import { useApi } from "../lib/useApi";
import { AiPanel } from "../components/AiPanel";
import { DataTable, inferColumns, type ColumnDef } from "../components/DataTable";
import { Badge, Banner, EmptyState, ErrorBanner, Spinner, StatusBadge, SyntheticBadge, UrlSearchInput } from "../components/ui";
import { CopyButton, StructuredFields } from "../components/values";

export function CoverageTable({ coverage }: { coverage: NonNullable<FindingsResponse["coverage"]> }) {
  return (
    <>
      <DataTable
        id="coverage"
        label="Source coverage"
        rows={coverage.sources}
        rowKey={(s) => s.source_key}
        columns={[
          { key: "full_name", label: "Source", kind: "text", sticky: true },
          { key: "client", label: "Client", kind: "text", render: (s: CoverageSource) => (<span><span dir="auto">{s.hostname}</span> <code className="small">{s.client_id}</code></span>) },
          { key: "result_status", label: "Result", kind: "short", render: (s: CoverageSource) => (<span className="row"><StatusBadge value={s.result_status} />{s.counts_only && <Badge tone="neutral" title="Rows from this source were not supplied to the model">Counts only</Badge>}</span>) },
          { key: "read_status", label: "Read", kind: "short", render: (s: CoverageSource) => (<span><StatusBadge value={s.read_status} />{s.stop_reason && <div className="small muted">stop: {s.stop_reason.replace(/_/g, " ")}</div>}</span>) },
          { key: "persisted_rows", label: "Rows saved", kind: "number" },
          { key: "available_rows", label: "Available", kind: "number", value: (s: CoverageSource) => String(s.available_rows) },
          { key: "notes", label: "Notes", kind: "long", value: (s: CoverageSource) => s.error || s.notes?.join("; ") || "" },
        ]}
      />
      {coverage.gaps.length > 0 && (
        <details className="advanced" style={{ marginTop: 8 }}>
          <summary>{coverage.gaps.length} coverage gap(s)</summary>
          <ul className="small">
            {coverage.gaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function InvestigationPicker() {
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    apiGet<InvestigationSummary[]>("/investigations").then(setItems).catch((e) => setError(errorMessage(e)));
  }, []);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Findings &amp; Evidence</h1>
          <p className="muted">Choose an investigation to review its findings and the evidence rows behind them.</p>
        </div>
      </div>
      <ErrorBanner error={error} />
      {items === null ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="card">
          <EmptyState title="No investigations yet.">
            <Link to="/investigations">Start one from Investigations</Link>
          </EmptyState>
        </div>
      ) : (
        <ul className="list plain">
          {items.map((i) => (
            <li key={i.investigation_id}>
              <Link className="list-item" to={`/findings/${i.investigation_id}`} style={{ textDecoration: "none" }}>
                <div className="row between">
                  <span className="li-title" dir="auto">
                    {i.title} {i.synthetic && <SyntheticBadge />}
                  </span>
                  <span className="row">
                    <StatusBadge value={i.outcome === "running" ? i.phase : i.outcome} />
                    <Badge tone="neutral">{i.counts?.findings ?? 0} findings</Badge>
                  </span>
                </div>
                <div className="small muted">
                  <code>{i.investigation_id}</code> · {formatTime(i.created_at)}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function FindingsPage() {
  const { id } = useParams();
  if (!id) return <InvestigationPicker />;
  return <FindingsView id={id} />;
}

type SetParam = (updates: Record<string, string | null>, replace?: boolean) => void;

const SEVERITY_TONE: Record<string, "bad" | "warn" | "info" | "neutral"> = { high: "bad", medium: "warn", low: "info", informational: "neutral" };
const REVIEW_LABELS: Record<ReviewState, string> = { unreviewed: "Unreviewed", in_review: "In review", needs_follow_up: "Needs follow-up", confirmed: "Confirmed by analyst", benign: "Benign / expected" };
const UNREVIEWED: Review = { state: "unreviewed", label: "Unreviewed", note: null, updated_at: null, by: null };

export function SeverityBadge({ value }: { value: string }) {
  return <Badge tone={SEVERITY_TONE[value] ?? "neutral"}>Severity: {value[0]?.toUpperCase() + value.slice(1)}</Badge>;
}

export function ReviewChip({ review }: { review: Review }) {
  const cls = review.state === "confirmed" || review.state === "needs_follow_up" ? "escalated" : review.state === "benign" ? "done" : review.state === "in_review" ? "active" : "";
  return <span className={`review-chip ${cls}`}>Review: {REVIEW_LABELS[review.state] ?? review.label}</span>;
}

export function ConfidenceMeter({ value }: { value: string }) {
  const n = value === "high" ? 3 : value === "medium" ? 2 : 1;
  return (
    <span className="confidence" aria-label={`Confidence ${value}`}>
      Confidence: {value}
      <span className="meter" aria-hidden>
        {[1, 2, 3].map((i) => (
          <i key={i} className={i <= n ? "on" : ""} />
        ))}
      </span>
    </span>
  );
}

function FindingsView({ id }: { id: string }) {
  const app = useApp();
  const [params, setParams] = useSearchParams();
  const [title, setTitle] = useState("");
  const dataQ = useApi<FindingsResponse>(`/investigations/${id}/findings`);
  const reviewsQ = useApi<Record<string, Review>>(`/investigations/${id}/reviews`);
  const data = dataQ.data;
  const tab = params.get("tab") ?? "findings";

  const setParam: SetParam = useCallback(
    (updates, replace = false) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(updates)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      setParams(next, { replace });
    },
    [params, setParams],
  );

  useEffect(() => {
    apiGet<{ investigation: InvestigationSummary }>(`/investigations/${id}`)
      .then((d) => {
        setTitle(d.investigation.title);
        app.setInvestigationContext({ id, title: d.investigation.title });
      })
      .catch(() => undefined);
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Findings &amp; Evidence</h1>
          <p className="muted">
            <Link to={`/investigations/${id}`} dir="auto">
              {title || id}
            </Link>{" "}
            {data?.synthetic && <SyntheticBadge />}
          </p>
        </div>
        <Link className="btn" to="/findings">
          Choose another investigation
        </Link>
      </div>
      <ErrorBanner error={dataQ.error} />
      {data?.provider?.fake && (
        <Banner tone="synthetic" title="Fake model (test fixture)">
          Model findings in this investigation come from a deterministic test fixture, not from a real model.
        </Banner>
      )}
      <div className="tabs" role="tablist">
        {[["findings", "Findings"], ["evidence", "Evidence explorer"], ["coverage", "Coverage"]].map(([key, label]) => (
          <button key={key} className="tab" role="tab" aria-selected={tab === key} onClick={() => setParam({ tab: key })}>
            {label}
          </button>
        ))}
      </div>
      {!data ? (
        !dataQ.error && <Spinner />
      ) : !data.report_ready && tab !== "evidence" ? (
        <Banner tone="info" title="The report is not ready yet.">
          Findings appear after evidence is read and analyzed. <Link to={`/investigations/${id}`}>Open the investigation</Link> to follow progress.
        </Banner>
      ) : tab === "findings" ? (
        <FindingsWorkspace id={id} data={data} reviews={reviewsQ.data ?? {}} onReviewed={(fid, r) => reviewsQ.setData({ ...(reviewsQ.data ?? {}), [fid]: r })} params={params} setParam={setParam} />
      ) : tab === "evidence" ? (
        <EvidenceExplorer id={id} params={params} setParam={setParam} />
      ) : (
        <div className="card">
          {data.coverage && <CoverageTable coverage={data.coverage} />}
          {data.analysis && (
            <details className="advanced" style={{ marginTop: 12 }}>
              <summary>Model input coverage (rows actually given to the model)</summary>
              <DataTable id="model-coverage" label="Model input coverage" rows={data.analysis.model_input_coverage} rowKey={(_, i) => String(i)} columns={inferColumns(data.analysis.model_input_coverage)} />
              {data.analysis.skipped_model_coverage.length > 0 && <p className="small">Skipped for budget: {data.analysis.skipped_model_coverage.map((s) => String(s.source_key)).join(", ")}</p>}
            </details>
          )}
        </div>
      )}
    </>
  );
}

function FindingsWorkspace({ id, data, reviews, onReviewed, params, setParam }: { id: string; data: FindingsResponse; reviews: Record<string, Review>; onReviewed: (fid: string, r: Review) => void; params: URLSearchParams; setParam: SetParam }) {
  const findings = data.findings;
  const f = {
    q: params.get("fq") ?? "",
    sev: params.get("sev") ?? "",
    conf: params.get("conf") ?? "",
    rev: params.get("rev") ?? "",
    origin: params.get("origin") ?? "",
    source: params.get("src") ?? "",
    client: params.get("cl") ?? "",
  };
  const sources = useMemo(() => [...new Set(findings.flatMap((x) => x.sources))].sort(), [findings]);
  const clients = useMemo(() => [...new Set(findings.flatMap((x) => x.client_ids))].sort(), [findings]);
  const reviewOf = (fid: string) => reviews[fid] ?? UNREVIEWED;
  const filtered = findings.filter(
    (x) =>
      (!f.sev || x.severity === f.sev) &&
      (!f.conf || x.confidence === f.conf) &&
      (!f.rev || reviewOf(x.finding_id).state === f.rev) &&
      (!f.origin || x.origin === f.origin) &&
      (!f.source || x.sources.includes(f.source)) &&
      (!f.client || x.client_ids.includes(f.client)) &&
      (!f.q || `${x.finding_id} ${x.title} ${x.observation} ${x.interpretation}`.toLowerCase().includes(f.q.toLowerCase())),
  );
  const selected = params.get("finding");
  const current = findings.find((x) => x.finding_id === selected) ?? null;
  const pos = current ? filtered.findIndex((x) => x.finding_id === current.finding_id) : -1;
  const go = (delta: number) => {
    const next = filtered[pos + delta];
    if (next) setParam({ finding: next.finding_id, evidence: null });
  };
  const counts = data.coverage?.counts ?? {};
  const activeFilters = Object.values(f).filter(Boolean).length;
  return (
    <div className="workspace">
      <div className="workspace-list" aria-label="Finding list">
        <div className="card tight stack">
          <label className="field small">
            Search findings
            <UrlSearchInput value={f.q} onCommit={(v) => setParam({ fq: v }, true)} placeholder="ID, title, observation" />
          </label>
          <div className="filters">
            <FilterSelect label="Severity" value={f.sev} options={["high", "medium", "low", "informational"]} onChange={(v) => setParam({ sev: v })} />
            <FilterSelect label="Review status" value={f.rev} options={Object.keys(REVIEW_LABELS)} labels={REVIEW_LABELS} onChange={(v) => setParam({ rev: v })} />
            <FilterSelect label="Confidence" value={f.conf} options={["high", "medium", "low"]} onChange={(v) => setParam({ conf: v })} />
            <FilterSelect label="Origin" value={f.origin} options={Object.keys(ORIGIN_LABELS)} labels={ORIGIN_LABELS} onChange={(v) => setParam({ origin: v })} />
            {sources.length > 1 && <FilterSelect label="Source" value={f.source} options={sources} onChange={(v) => setParam({ src: v })} />}
            {clients.length > 1 && <FilterSelect label="Client" value={f.client} options={clients} onChange={(v) => setParam({ cl: v })} />}
          </div>
          <div className="row small between">
            <span className="muted">
              {filtered.length} of {findings.length} finding(s)
            </span>
            {activeFilters > 0 && (
              <button className="btn link small" onClick={() => setParam({ fq: null, sev: null, conf: null, rev: null, origin: null, src: null, cl: null })}>
                Clear filters
              </button>
            )}
          </div>
          <div className="row small">
            <span className="muted">Coverage:</span>
            {Object.entries(counts).map(([k, v]) => (
              <StatusBadge key={k} value={k} prefix={String(v)} />
            ))}
          </div>
        </div>
        {findings.length === 0 ? (
          <div className="card">
            <p>
              <strong>No accepted findings.</strong>
            </p>
            <p className="small">This is not a clean result. It reflects only the retrieved and reviewed data; check the coverage gaps.</p>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState title="No findings match these filters." />
        ) : (
          <ul className="list plain" aria-label="Findings">
            {filtered.map((x) => (
              <li key={x.finding_id}>
                <button className={`list-item ${current?.finding_id === x.finding_id ? "selected" : ""}`} onClick={() => setParam({ finding: x.finding_id, evidence: null })} aria-current={current?.finding_id === x.finding_id ? "true" : undefined}>
                  <div className="li-title" dir="auto">
                    {x.title}
                  </div>
                  <div className="li-meta">
                    <SeverityBadge value={x.severity} />
                    <ReviewChip review={reviewOf(x.finding_id)} />
                    <ConfidenceMeter value={x.confidence} />
                  </div>
                  <div className="small muted">
                    {x.finding_id} · {ORIGIN_LABELS[x.origin]} · {x.evidence_ids.length} evidence row(s)
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
        {data.rejected_findings.length > 0 && (
          <details className="advanced card tight">
            <summary>{data.rejected_findings.length} rejected model proposal(s)</summary>
            <p className="small muted">These failed validation (for example invented citations) and are not findings.</p>
            <ul className="small">
              {data.rejected_findings.map((r, i) => (
                <li key={i}>
                  {r.title ?? "(untitled)"}: {r.reasons.join("; ")}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
      <div className="workspace-detail">
        {current && (
          <nav className="detail-nav" aria-label="Finding navigation">
            <button className="btn small" onClick={() => go(-1)} disabled={pos <= 0}>
              <ChevronLeft size={14} aria-hidden /> Previous finding
            </button>
            <span className="small muted">{pos >= 0 ? `Finding ${pos + 1} of ${filtered.length}` : "Not in the current filter"}</span>
            <button className="btn small" onClick={() => go(1)} disabled={pos < 0 || pos >= filtered.length - 1}>
              Next finding <ChevronRight size={14} aria-hidden />
            </button>
            {activeFilters > 0 && <span className="small muted">({activeFilters} filter(s) applied)</span>}
          </nav>
        )}
        {current ? (
          <FindingDetail id={id} finding={current} review={reviewOf(current.finding_id)} onReviewed={onReviewed} activeEvidence={params.get("evidence")} setParam={setParam} />
        ) : (
          <div className="card">
            <p className="muted">Select a finding to see its observation, interpretation, review status, and the evidence rows behind it.</p>
          </div>
        )}
        {!current && params.get("evidence") && <EvidenceRow id={id} evidenceId={params.get("evidence")!} onClose={() => setParam({ evidence: null })} setParam={setParam} />}
        {data.citation_note && <p className="small muted">{data.citation_note}</p>}
      </div>
    </div>
  );
}

function FilterSelect({ label, value, options, labels, onChange }: { label: string; value: string; options: string[]; labels?: Record<string, string>; onChange: (v: string) => void }) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {labels?.[o] ?? o}
          </option>
        ))}
      </select>
    </label>
  );
}

function FindingDetail({ id, finding: f, review, onReviewed, activeEvidence, setParam }: { id: string; finding: Finding; review: Review; onReviewed: (fid: string, r: Review) => void; activeEvidence: string | null; setParam: SetParam }) {
  const resolved = f.evidence_refs.filter((r) => !r.unresolved);
  const sections: [string, string[]][] = [
    ["Plausible benign explanations", f.benign_explanations],
    ["Missing context", f.missing_context],
    ["Suggested verification", f.next_steps],
  ];
  return (
    <>
      <section className="card" aria-label={`Finding ${f.finding_id}`}>
        <div className="card-head">
          <h2 dir="auto">
            {f.finding_id}: {f.title}
          </h2>
          <Badge tone={f.origin === "model_proposal" ? "info" : "teal"}>{ORIGIN_LABELS[f.origin]}</Badge>
        </div>
        <div className="attrs" role="group" aria-label="Severity, review status and confidence">
          <div className="attr">
            <span className="attr-label">Severity</span>
            <SeverityBadge value={f.severity} />
          </div>
          <div className="attr">
            <span className="attr-label">Analyst review</span>
            <ReviewChip review={review} />
          </div>
          <div className="attr">
            <span className="attr-label">Confidence</span>
            <ConfidenceMeter value={f.confidence} />
          </div>
        </div>
        <h3>Observation</h3>
        <p dir="auto">{f.observation}</p>
        <h3>Interpretation</h3>
        <p dir="auto">{f.interpretation}</p>
        <p className="small muted">Confidence rationale: {f.confidence_rationale}</p>
        {sections.map(([label, items]) =>
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
        <ReviewEditor id={id} finding={f} review={review} onReviewed={onReviewed} />
      </section>
      <section className="card" aria-label="Supporting evidence">
        <h2>Supporting evidence ({f.evidence_refs.length})</h2>
        <div className="stack">
          {f.evidence_refs.map((r) => (
            <EvidenceRefCard key={r.evidence_id} id={id} r={r} active={activeEvidence === r.evidence_id} setParam={setParam} />
          ))}
        </div>
      </section>
      {activeEvidence && <EvidenceRow id={id} evidenceId={activeEvidence} onClose={() => setParam({ evidence: null })} setParam={setParam} />}
      {resolved.length > 0 && (
        <AiPanel
          path={`/investigations/${id}/findings/${f.finding_id}/explain`}
          actions={[{ id: "finding_summary", label: "Summarize this finding", body: {} }]}
          onCitation={(c) => typeof c.evidence_id === "string" && setParam({ evidence: c.evidence_id })}
        />
      )}
    </>
  );
}

function ReviewEditor({ id, finding, review, onReviewed }: { id: string; finding: Finding; review: Review; onReviewed: (fid: string, r: Review) => void }) {
  const [state, setState] = useState<ReviewState>(review.state);
  const [note, setNote] = useState(review.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    setState(review.state);
    setNote(review.note ?? "");
  }, [finding.finding_id, review.state, review.note]);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onReviewed(finding.finding_id, await apiPut<Review>(`/investigations/${id}/findings/${finding.finding_id}/review`, { state, note: note || null }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const changed = state !== review.state || (note || "") !== (review.note || "");
  return (
    <details className="advanced" open={review.state !== "unreviewed"}>
      <summary>Analyst review status</summary>
      <div className="row">
        <label className="field small">
          Status
          <select value={state} onChange={(e) => setState(e.target.value as ReviewState)}>
            {Object.entries(REVIEW_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="field small" style={{ flex: 1, minWidth: 200 }}>
          Note (optional)
          <input type="text" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button className="btn small primary" disabled={!changed || busy} onClick={save}>
          {busy ? "Saving…" : "Save review"}
        </button>
      </div>
      <p className="small muted">Stored locally with this investigation, separately from the report. {review.updated_at && `Last set ${formatTime(review.updated_at)} by ${review.by}.`}</p>
      <ErrorBanner error={error} />
    </details>
  );
}

// The located page must use the explorer's page size, or the row can fall outside it.
async function locateEvidence(id: string, evidenceId: string, setParam: SetParam, pageSize: number) {
  const loc = await apiGet<{ source_key: string; offset: number; row_index: number }>(`/investigations/${id}/evidence/${encodeURIComponent(evidenceId)}/locate`, { limit: pageSize });
  setParam({ tab: "evidence", source: loc.source_key, offset: String(loc.offset), eq: null, hl: evidenceId });
}

function EvidenceRefCard({ id, r, active, setParam }: { id: string; r: EvidenceRef; active: boolean; setParam: SetParam }) {
  const [error, setError] = useState<ApiError | null>(null);
  const pageSize = useApp().preferences?.page_size ?? 50;
  if (r.unresolved) {
    return (
      <div className="evidence-card">
        <span className="field-error">
          <code>{r.evidence_id}</code> — citation could not be resolved (validation issue)
        </span>
      </div>
    );
  }
  return (
    <div className={`evidence-card ${active ? "active" : ""}`}>
      <div className="ev-head">
        <span className="row">
          <code>{r.evidence_id}</code>
          <CopyButton text={r.evidence_id} label={`evidence ID ${r.evidence_id}`} compact />
        </span>
        <span className="row">
          <button className="btn small" onClick={() => setParam({ evidence: active ? null : r.evidence_id })} aria-label={`Open evidence ${r.evidence_id}`} aria-pressed={active}>
            {active ? "Hide row" : "Show row"}
          </button>
          <button className="btn small" onClick={() => locateEvidence(id, r.evidence_id, setParam, pageSize).catch((e) => setError(errorMessage(e)))} aria-label={`Open evidence ${r.evidence_id} in its source table`}>
            Open in source table
          </button>
        </span>
      </div>
      <div className="small muted">
        {r.artifact}/{r.source ?? ""} · row {r.row_index} · <span dir="auto">{r.hostname}</span> <code>{r.client_id}</code> · {r.flow_id}
      </div>
      <ErrorBanner error={error} />
    </div>
  );
}

const PREFERRED_FIELDS = ["Name", "User", "Username", "Pid", "Ppid", "Exe", "CommandLine", "Path", "Command", "Laddr", "Lport", "Raddr", "Rport", "Status", "Unit", "Description", "Hash"];

function EvidenceRow({ id, evidenceId, onClose, setParam }: { id: string; evidenceId: string; onClose: () => void; setParam: SetParam }) {
  const q = useApi<EvidenceRecord>(`/investigations/${id}/evidence/${encodeURIComponent(evidenceId)}`);
  const [error, setError] = useState<ApiError | null>(null);
  const pageSize = useApp().preferences?.page_size ?? 50;
  const rec = q.data;
  return (
    <section className="card" aria-label="Evidence row">
      <div className="card-head">
        <h2>Evidence row</h2>
        <span className="row">
          {rec && (
            <button className="btn small" onClick={() => locateEvidence(id, evidenceId, setParam, pageSize).catch((e) => setError(errorMessage(e)))}>
              Open in source table
            </button>
          )}
          <button className="btn small" onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      <ErrorBanner error={error} />
      {q.error ? (
        <Banner tone="bad" title="Invalid or missing citation">
          {q.error.message} No nearby row is substituted.
        </Banner>
      ) : !rec ? (
        <Spinner />
      ) : (
        <>
          <dl className="kv small">
            <dt>Evidence ID</dt>
            <dd>
              <code>{rec.evidence_id}</code> <CopyButton text={rec.evidence_id} label="evidence ID" compact />
            </dd>
            <dt>Client</dt>
            <dd>
              <code>{rec.client_id}</code> <span dir="auto">{rec.hostname}</span> · org <code>{rec.org_id}</code>
            </dd>
            <dt>Flow</dt>
            <dd>
              <code>{rec.flow_id}</code> {rec.hunt_id && <>· hunt <code>{rec.hunt_id}</code></>}
            </dd>
            <dt>Artifact / source</dt>
            <dd>
              <code>{rec.full_name}</code>
            </dd>
            <dt>Row index</dt>
            <dd>
              {rec.row_index} <span className="muted">(zero-based position in the server result set)</span>
            </dd>
            <dt>Collected / retrieved</dt>
            <dd>
              {formatTime(rec.collected_at)} / {formatTime(rec.retrieved_at)}
            </dd>
            {rec.event_time && (
              <>
                <dt>Event time</dt>
                <dd>{rec.event_time}</dd>
              </>
            )}
            <dt>Raw SHA-256</dt>
            <dd>
              <code className="small">{rec.raw_sha256}</code>
            </dd>
          </dl>
          <div style={{ marginTop: 10 }}>
            <StructuredFields record={rec.row} preferred={PREFERRED_FIELDS} rawLabel="Raw JSON (original row, unmodified)" />
          </div>
        </>
      )}
    </section>
  );
}

type EvRecord = EvidencePage["records"][number];

function EvidenceExplorer({ id, params, setParam }: { id: string; params: URLSearchParams; setParam: SetParam }) {
  const app = useApp();
  const sourcesQ = useApi<BundleSources>(`/investigations/${id}/sources`);
  const sourceKey = params.get("source") ?? sourcesQ.data?.sources[0]?.source_key ?? null;
  const offset = Number(params.get("offset") ?? 0);
  const limit = app.preferences?.page_size ?? 50;
  const q = params.get("eq") ?? "";
  const highlight = params.get("hl");
  const [search, setSearch] = useState(q);
  const [open, setOpen] = useState<EvRecord | null>(null);
  const pageQ = useApi<EvidencePage>(sourceKey ? `/investigations/${id}/evidence` : null, { source_key: sourceKey, offset, limit, q });
  const page = pageQ.data;
  useEffect(() => setSearch(q), [q]);
  useEffect(() => {
    if (!page || !highlight) return;
    setOpen(page.records.find((r) => r.evidence_id === highlight) ?? null);
  }, [page, highlight]);
  const columns: ColumnDef<EvRecord>[] = useMemo(() => {
    const cols = inferColumns((page?.records ?? []).map((r) => r.row as Record<string, unknown>));
    return [
      { key: "__row", label: "Row", kind: "number", sticky: true, value: (r: EvRecord) => r.row_index },
      ...cols.map((c) => ({ ...c, value: (r: EvRecord) => (r.row as Record<string, unknown>)[c.key] })),
      { key: "__eid", label: "Evidence ID", kind: "id", value: (r: EvRecord) => r.evidence_id, defaultHidden: true },
    ];
  }, [page]);
  return (
    <div className="stack">
      <ErrorBanner error={sourcesQ.error ?? pageQ.error} />
      <div className="card tight">
        <div className="row">
          <label className="field small" style={{ minWidth: 260, flex: 1 }}>
            Source
            <select aria-label="Evidence source" value={sourceKey ?? ""} onChange={(e) => setParam({ source: e.target.value, offset: "0", eq: null, hl: null })}>
              {sourcesQ.data?.sources.map((s) => (
                <option key={s.source_key} value={s.source_key}>
                  {s.full_name} — {s.hostname || s.client_id} ({String(s.coverage.persisted_rows)} rows)
                </option>
              ))}
            </select>
          </label>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              setParam({ eq: search || null, offset: "0", hl: null });
            }}
          >
            <label className="field small">
              Search the entire saved source (plain text)
              <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. /tmp or 203.0.113" />
            </label>
            <button className="btn" type="submit">
              Search
            </button>
          </form>
        </div>
        {page && (
          <div className="row small" style={{ marginTop: 8 }}>
            <StatusBadge value={String(page.source.coverage.result_status)} />
            <StatusBadge value={String(page.source.coverage.read_status)} />
            <span className="muted">
              {String(page.source.coverage.persisted_rows)} rows saved · available {String(page.source.coverage.available_rows ?? "unknown")} · client <code>{page.source.client_id}</code> · flow <code>{page.source.flow_id}</code>
            </span>
            {q && <Badge tone="info">{page.total_matched} match(es) across the saved source</Badge>}
            {highlight && <Badge tone="warn">Highlighted: {highlight}</Badge>}
          </div>
        )}
      </div>
      {!page ? (
        sourceKey && !pageQ.error ? <Spinner /> : null
      ) : (
        <DataTable
          id={`evidence-${page.source.full_name}`}
          label={`Evidence rows of ${page.source.full_name}`}
          rows={page.records}
          columns={columns}
          rowKey={(r) => r.evidence_id}
          total={page.total_matched}
          offset={offset}
          limit={limit}
          onPageChange={(o) => setParam({ offset: String(o), hl: null })}
          highlightKey={highlight}
          selectedKey={open?.evidence_id ?? null}
          onRowClick={(r) => {
            setOpen(r);
            setParam({ hl: r.evidence_id }, true);
          }}
          rowLabel={(r) => `Row ${r.row_index} (${r.evidence_id})`}
          empty={<EmptyState title={q ? "No rows match this search." : "This source has no saved rows."}>{!q && <p className="small">See its coverage status above: zero rows can mean empty, skipped, or not read.</p>}</EmptyState>}
        />
      )}
      {open && (
        <section className="card" aria-label="Selected evidence row">
          <div className="card-head">
            <h2>
              Row {open.row_index} <code className="small">{open.evidence_id}</code>
            </h2>
            <span className="row">
              <button className="btn small" onClick={() => setParam({ tab: "findings", evidence: open.evidence_id })}>
                Provenance
              </button>
              <button className="btn small" onClick={() => setOpen(null)}>
                Close
              </button>
            </span>
          </div>
          <StructuredFields record={open.row as Record<string, unknown>} preferred={PREFERRED_FIELDS} rawLabel="Raw JSON (original row, unmodified)" />
        </section>
      )}
    </div>
  );
}
