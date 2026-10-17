import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { Download, Eye, Pause, RefreshCw, RotateCcw } from "lucide-react";
import { apiGet, apiPost, errorMessage } from "../api/client";
import type { ApiError, EventItem, FindingsResponse, InvestigationDetail } from "../api/types";
import { useApp, usePolling } from "../lib/app";
import { elapsed, formatTime, MODE_LABELS, statusLabel } from "../lib/format";
import { Badge, Banner, CopyText, SyntheticBadge, ErrorBanner, JsonView, Spinner, StatusBadge } from "../components/ui";
import { CoverageTable } from "./FindingsPage";

const PHASES = [
  { id: "preparing", label: "Preparing" },
  { id: "reading", label: "Reading Evidence" },
  { id: "analyzing", label: "Analyzing" },
  { id: "report_ready", label: "Report Ready" },
];

const OUTCOME_TEXT: Record<string, { tone: "ok" | "warn" | "bad" | "info"; title: string; text: string }> = {
  complete: { tone: "ok", title: "Complete", text: "All planned work finished. This is not a statement that the target is safe; review findings and coverage." },
  partial: { tone: "warn", title: "Partial", text: "Some sources, reads, or analysis requests did not complete. The report lists what is missing." },
  failed: { tone: "bad", title: "Failed", text: "The investigation could not produce usable evidence. See the errors below." },
  interrupted: { tone: "warn", title: "Interrupted", text: "Local processing stopped. Resume local retrieval when ready." },
  evidence_only: { tone: "info", title: "Evidence only", text: "Evidence was saved and checked with deterministic review signals. No AI analysis was requested." },
};

function describeEvent(e: EventItem): string {
  const d = e.data ?? {};
  switch (e.kind) {
    case "rows_read":
      return `Read ${d.rows} rows of ${d.source}`;
    case "flow_status":
      return `Flow ${d.flow_id}: ${statusLabel(String(d.state))[0]} (raw ${d.raw_state})`;
    case "launch_submitted":
      return `Launching ${d.artifact} (once)`;
    case "launch_acknowledged":
      return `Collection started: ${d.flow_id}`;
    case "analysis_request":
      return `Model request ${d.request} (${d.size_chars} chars)`;
    case "analysis_phase":
      return `Analysis: ${d.step}`;
    case "analysis_finished":
      return `Analysis ${statusLabel(String(d.status))[0]}; ${d.findings} finding(s)`;
    case "reading":
      return `Reading ${d.sources ?? ""} source(s) from ${d.flow_id}`;
    case "report_ready":
      return `Report ready (${statusLabel(String(d.outcome))[0]})`;
    case "error":
      return `Error: ${d.message ?? d.code ?? ""}`;
    default:
      return `${e.kind.replace(/_/g, " ")}${d.message ? `: ${d.message}` : ""}`;
  }
}

export function InvestigationDetailPage() {
  const { id = "" } = useParams();
  const app = useApp();
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [findings, setFindings] = useState<FindingsResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const lastSeq = useRef(0);

  const load = useCallback(async () => {
    try {
      const d = await apiGet<InvestigationDetail>(`/investigations/${id}`);
      setDetail(d);
      app.setInvestigationContext({ id, title: d.investigation.title });
      const ev = await apiGet<{ events: EventItem[]; job_active: boolean }>(`/investigations/${id}/events`, { after: lastSeq.current });
      if (ev.events.length) {
        lastSeq.current = ev.events[ev.events.length - 1].seq;
        setEvents((cur) => [...cur, ...ev.events].slice(-200));
      }
      if (d.investigation.phase === "report_ready" || !d.job_active) {
        setFindings(await apiGet<FindingsResponse>(`/investigations/${id}/findings`));
      }
      setError(null);
      return d.job_active || d.investigation.job?.state === "queued";
    } catch (e) {
      setError(errorMessage(e));
      throw e;
    }
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    lastSeq.current = 0;
    setEvents([]);
    setFindings(null);
    load().catch(() => undefined);
  }, [load]);

  const active = !!detail && (detail.job_active || detail.investigation.job?.state === "queued");
  usePolling(async () => {
    const keep = await load();
    return keep !== false ? undefined : false;
  }, 1500, active);

  const act = async (action: "resume" | "retry-analysis" | "stop", body?: unknown) => {
    setBusy(action);
    try {
      await apiPost(`/investigations/${id}/${action}`, body ?? {});
      app.toast(action === "stop" ? "Stop requested for local processing." : action === "resume" ? "Resuming without recollection" : "Retrying analysis on saved evidence");
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  if (!detail) return error ? <ErrorBanner error={error} onRetry={() => load().catch(() => undefined)} /> : <Spinner />;
  const inv = detail.investigation;
  const phaseIndex = PHASES.findIndex((p) => p.id === inv.phase);
  const outcome = OUTCOME_TEXT[inv.outcome];
  const lastProgress = [...events].reverse().find((e) => e.kind === "rows_read" || e.kind === "analysis_request");
  const counts = inv.counts ?? {};

  return (
    <>
      <div className="page-head">
        <div>
          <h1 dir="auto">{inv.title}</h1>
          <div className="row small muted">
            <CopyText value={inv.investigation_id} label="investigation ID" />
            <span>{MODE_LABELS[inv.mode] ?? inv.mode}</span>
            <span>Created {formatTime(inv.created_at)}</span>
            <span>Elapsed {elapsed(inv.created_at, active ? null : inv.updated_at)}</span>
            {inv.synthetic && <SyntheticBadge />}
          </div>
        </div>
        <div className="row">
          {detail.actions.stop_local_processing && (
            <button className="btn danger" onClick={() => act("stop")} disabled={busy !== null}>
              <Pause size={16} aria-hidden /> Stop Local Processing
            </button>
          )}
          {detail.actions.resume_reading && (
            <button className="btn" onClick={() => act("resume")} disabled={busy !== null}>
              <RotateCcw size={16} aria-hidden /> Resume Reading
            </button>
          )}
          {detail.actions.retry_analysis && (
            <button className="btn" onClick={() => act("retry-analysis", {})} disabled={busy !== null}>
              <RefreshCw size={16} aria-hidden /> Retry Analysis
            </button>
          )}
          {detail.actions.view_evidence && (
            <Link className="btn" to={`/findings/${inv.investigation_id}`}>
              <Eye size={16} aria-hidden /> View Evidence
            </Link>
          )}
          {detail.actions.export_report && (
            <Link className="btn primary" to={`/reports?investigation=${inv.investigation_id}`}>
              <Download size={16} aria-hidden /> Export Report
            </Link>
          )}
        </div>
      </div>
      <ErrorBanner error={error} />
      {inv.synthetic && <Banner tone="synthetic" title="Synthetic test data">This investigation uses synthetic test data, not evidence from a real endpoint{inv.provider?.fake ? ", and a fake test model; its model findings are not a real model assessment" : ""}.</Banner>}
      {outcome && inv.outcome !== "running" && (
        <Banner tone={outcome.tone} title={outcome.title}>
          {outcome.text}
        </Banner>
      )}
      {inv.analysis_status === "failed" && (
        <Banner tone="warn" title="Model unavailable or analysis failed.">
          Evidence was saved; retry analysis after checking the configured model.
        </Banner>
      )}
      {inv.retrieval_status === "incomplete" && !active && (
        <Banner tone="warn" title="Source read incomplete.">
          View the partial evidence or resume reading. Resume never recollects.
        </Banner>
      )}

      <ol className="steps" aria-label="Phases">
        {PHASES.map((p, i) => {
          const cls = inv.phase === p.id ? (inv.phase === "report_ready" ? "done" : "current") : phaseIndex > i ? "done" : "";
          return (
            <li key={p.id} className={cls} aria-current={inv.phase === p.id ? "step" : undefined}>
              {p.label}
              {inv.phase === p.id && active && <span className="spinner" aria-hidden />}
            </li>
          );
        })}
        {inv.phase === "stopped" && <li className="current">Stopped locally</li>}
      </ol>

      <div className="grid cols-2">
        <div className="card">
          <h2>Status</h2>
          <dl className="kv">
            <dt>Outcome</dt>
            <dd>
              <StatusBadge value={inv.outcome} />
            </dd>
            <dt>Collection</dt>
            <dd>
              <StatusBadge value={inv.collection_status} />
            </dd>
            <dt>Evidence retrieval</dt>
            <dd>
              <StatusBadge value={inv.retrieval_status} />
            </dd>
            <dt>Analysis</dt>
            <dd>
              <StatusBadge value={inv.analysis_status} /> {inv.provider && <span className="small muted">{inv.provider.fake ? "fake test model" : `${inv.provider.name} ${inv.provider.model}`}</span>}
            </dd>
            <dt>Org</dt>
            <dd>
              <code>{inv.scope?.org_id ?? "-"}</code>
            </dd>
            <dt>Target</dt>
            <dd>
              <span dir="auto">{inv.scope?.hostname ?? ""}</span> {inv.scope?.client_id && <code>{inv.scope.client_id}</code>} {inv.scope?.hunt_id && <code>{inv.scope.hunt_id}</code>}
            </dd>
            <dt>Flow IDs</dt>
            <dd>{inv.flow_ids?.length ? inv.flow_ids.slice(0, 6).map((f) => <CopyText key={f} value={f} />) : "-"}</dd>
            <dt>Sources</dt>
            <dd>
              {counts.sources_complete ?? 0} of {counts.sources_total ?? 0} read completely · {counts.rows_persisted ?? 0} rows saved
            </dd>
            <dt>Findings</dt>
            <dd>{counts.findings !== undefined ? `${counts.findings} accepted (${counts.model_findings ?? 0} model, ${counts.deterministic_findings ?? 0} deterministic), ${counts.rejected_findings ?? 0} rejected` : "-"}</dd>
            {lastProgress && active && (
              <>
                <dt>Current task</dt>
                <dd>{describeEvent(lastProgress)}</dd>
              </>
            )}
          </dl>
          {detail.collections.length > 0 && (
            <>
              <div className="divider" />
              <h3>Collections</h3>
              {detail.collections.map((c) => (
                <div key={c.collection_ids.join(",")} className="small">
                  <code>{c.artifact}</code> · launch: {c.launch_state} · {c.flow_id ? <code>{c.flow_id}</code> : "no flow"} · <StatusBadge value={c.flow_state} /> {c.raw_state && <span className="muted">(raw {c.raw_state})</span>}
                </div>
              ))}
            </>
          )}
        </div>
        <div className="card">
          <h2>Recent progress</h2>
          {events.length === 0 ? (
            <p className="muted">No progress recorded yet.</p>
          ) : (
            <div className="log" aria-live="polite">
              {events.slice(-15).map((e) => (
                <div key={e.seq}>
                  <span className="muted">{new Date(e.time).toLocaleTimeString()}</span> {describeEvent(e)}
                </div>
              ))}
            </div>
          )}
          {inv.errors?.length > 0 && (
            <>
              <h3 style={{ marginTop: 12 }}>Errors</h3>
              <ul className="small">
                {inv.errors.slice(-6).map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </>
          )}
          <details className="advanced">
            <summary>Show details</summary>
            <JsonView value={{ investigation: inv, collections: detail.collections, existing: detail.existing, recent_events: events.slice(-40) }} summary="Raw diagnostics (sanitized)" />
          </details>
        </div>
      </div>

      {findings?.coverage && (
        <div className="card">
          <div className="card-head">
            <h2>Coverage</h2>
            <Link to={`/findings/${inv.investigation_id}`}>Open Findings &amp; Evidence</Link>
          </div>
          <CoverageTable coverage={findings.coverage} />
        </div>
      )}
      {findings && findings.report_ready && (
        <div className="card">
          <div className="card-head">
            <h2>Findings</h2>
            <Badge tone="neutral">{findings.findings.length} accepted</Badge>
          </div>
          {findings.findings.length === 0 ? (
            <p>No accepted findings. This does not mean the target is clean; review the coverage above.</p>
          ) : (
            <ul>
              {findings.findings.slice(0, 8).map((f) => (
                <li key={f.finding_id}>
                  <Link to={`/findings/${inv.investigation_id}?finding=${encodeURIComponent(f.finding_id)}`}>
                    {f.finding_id}: {f.title}
                  </Link>{" "}
                  <StatusBadge value={f.severity} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
