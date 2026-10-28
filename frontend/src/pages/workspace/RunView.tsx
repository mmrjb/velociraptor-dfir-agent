// Run details: retrieval and analysis status, local actions (resume reading, retry analysis,
// stop) and the progress log. Kept from the existing workflow; opened from the scope bar.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, Pause, RefreshCw, RotateCcw } from "lucide-react";
import { apiGet, apiPost, errorMessage } from "../../api/client";
import type { ApiError, EventItem } from "../../api/types";
import { useApp, usePolling } from "../../lib/app";
import { elapsed, formatTime, MODE_LABELS, statusLabel } from "../../lib/format";
import { Banner, CopyText, ErrorBanner, JsonView, StatusBadge } from "../../components/ui";
import { useWorkspace } from "./shared";

const PHASES = [
  { id: "preparing", label: "Preparing" },
  { id: "reading", label: "Reading evidence" },
  { id: "analyzing", label: "Analyzing" },
  { id: "report_ready", label: "Report ready" },
];

const OUTCOME_TEXT: Record<string, { tone: "ok" | "warn" | "bad" | "info"; title: string; text: string }> = {
  complete: { tone: "ok", title: "Complete", text: "All planned work finished. This is not a statement that the target is safe; review findings and coverage." },
  partial: { tone: "warn", title: "Partial", text: "Some sources, reads, or analysis requests did not complete. The overview lists what is missing." },
  failed: { tone: "bad", title: "Failed", text: "The investigation could not produce usable evidence. See the errors below." },
  interrupted: { tone: "warn", title: "Interrupted", text: "Local processing stopped. Resume local retrieval when ready." },
  evidence_only: { tone: "info", title: "Evidence only", text: "Evidence was saved and checked with deterministic review signals. No AI analysis was requested." },
};

function describeEvent(e: EventItem): string {
  const d = e.data ?? {};
  switch (e.kind) {
    case "rows_read":
      return `Read ${d.rows} rows of ${d.source}`;
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

export function RunView() {
  const { invId, detail, reloadDetail } = useWorkspace();
  const app = useApp();
  const [events, setEvents] = useState<EventItem[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const last = useRef(0);
  const loadEvents = useCallback(async () => {
    const ev = await apiGet<{ events: EventItem[]; job_active: boolean }>(`/investigations/${invId}/events`, { after: last.current });
    if (ev.events.length) {
      last.current = ev.events[ev.events.length - 1].seq;
      setEvents((cur) => [...cur, ...ev.events].slice(-200));
    }
    return ev.job_active;
  }, [invId]);
  useEffect(() => {
    last.current = 0;
    setEvents([]);
    loadEvents().catch((e) => setError(errorMessage(e)));
  }, [loadEvents]);
  const active = !!detail && (detail.job_active || detail.investigation.job?.state === "queued");
  usePolling(async () => {
    const keep = await loadEvents();
    return keep ? undefined : false;
  }, 1500, active);
  if (!detail) return null;
  const inv = detail.investigation;
  const outcome = OUTCOME_TEXT[inv.outcome];
  const phaseIndex = PHASES.findIndex((p) => p.id === inv.phase);
  const counts = inv.counts ?? {};
  const act = async (action: "resume" | "retry-analysis" | "stop") => {
    setBusy(action);
    try {
      await apiPost(`/investigations/${invId}/${action}`, {});
      app.toast(action === "stop" ? "Stop requested for local processing." : action === "resume" ? "Resuming without recollection" : "Retrying analysis on saved evidence");
      reloadDetail();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="stack">
      <div className="row between">
        <Link to={`/investigations/${invId}`} className="btn link small">
          <ArrowLeft size={14} aria-hidden /> Overview
        </Link>
        <div className="row">
          {detail.actions.stop_local_processing && (
            <button className="btn danger" onClick={() => act("stop")} disabled={busy !== null}>
              <Pause size={16} aria-hidden /> Stop local processing
            </button>
          )}
          {detail.actions.resume_reading && (
            <button className="btn" onClick={() => act("resume")} disabled={busy !== null}>
              <RotateCcw size={16} aria-hidden /> Resume reading
            </button>
          )}
          {detail.actions.retry_analysis && (
            <button className="btn" onClick={() => act("retry-analysis")} disabled={busy !== null}>
              <RefreshCw size={16} aria-hidden /> Retry analysis
            </button>
          )}
        </div>
      </div>
      <h2>Run details</h2>
      <ErrorBanner error={error} />
      {outcome && inv.outcome !== "running" && (
        <Banner tone={outcome.tone} title={outcome.title}>
          {outcome.text}
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
          <dl className="kv">
            <dt>Investigation</dt>
            <dd>
              <CopyText value={inv.investigation_id} label="investigation ID" />
            </dd>
            <dt>Evidence</dt>
            <dd>{MODE_LABELS[inv.mode] ?? inv.mode}</dd>
            <dt>Outcome</dt>
            <dd>
              <StatusBadge value={inv.outcome} />
            </dd>
            <dt>Evidence retrieval</dt>
            <dd>
              <StatusBadge value={inv.retrieval_status} />
            </dd>
            <dt>Analysis</dt>
            <dd>
              <StatusBadge value={inv.analysis_status} /> {inv.provider && <span className="small muted">{inv.provider.fake ? "fake test model" : `${inv.provider.name} ${inv.provider.model ?? ""}`}</span>}
            </dd>
            <dt>Created</dt>
            <dd>
              {formatTime(inv.created_at)} · elapsed {elapsed(inv.created_at, active ? null : inv.updated_at)}
            </dd>
            <dt>Sources</dt>
            <dd>
              {counts.sources_complete ?? 0} of {counts.sources_total ?? 0} read completely · {counts.rows_persisted ?? 0} rows saved
            </dd>
            <dt>Findings</dt>
            <dd>{counts.findings !== undefined ? `${counts.findings} accepted (${counts.model_findings ?? 0} AI, ${counts.deterministic_findings ?? 0} rule), ${counts.rejected_findings ?? 0} rejected` : "not produced yet"}</dd>
          </dl>
        </div>
        <div className="card">
          <h3>Recent progress</h3>
          {events.length === 0 ? (
            <p className="muted small">No progress recorded yet.</p>
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
              <h3>Errors</h3>
              <ul className="small">
                {inv.errors.slice(-6).map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </>
          )}
          <JsonView value={{ investigation: inv, collections: detail.collections, existing: detail.existing, recent_events: events.slice(-40) }} summary="Raw diagnostics (sanitized)" />
        </div>
      </div>
    </div>
  );
}
