// Timeline: two separately named modes. Events uses each source's mapped event time; rows
// without a usable event time are counted apart and can be inspected. Capture history shows
// repeated saved captures of one host and source by capture time: appearing in a later capture
// does not mean something was created then. Times are stored in UTC; the display zone is a
// presentation preference only.
import { useState } from "react";
import { Link } from "react-router";
import type { CaptureHistory, RowsPage, Timeline } from "../../api/types";
import { Histogram } from "../../components/charts";
import type { InspectorLevels } from "../../components/Inspector";
import { Banner, EmptyState, ErrorBanner, Spinner } from "../../components/ui";
import { useApp } from "../../lib/app";
import { formatTime } from "../../lib/format";
import { formatInstant, setDisplayZone, useDisplayZone } from "../../lib/time";
import { useUrlState } from "../../lib/urlState";
import { useView } from "../../lib/useView";
import { WorkspaceInspector } from "./InspectorExtras";
import { FilterChips, PendingState, RowsTable, useWorkspace } from "./shared";

const LEVELS: InspectorLevels = { allFields: false, advanced: false, related: false };

export function TimelineView() {
  const [params, set] = useUrlState();
  const mode = params.get("mode") === "captures" ? "captures" : "events";
  const zone = useDisplayZone();
  return (
    <div className="stack">
      <div className="row between view-head">
        <div className="seg" role="tablist" aria-label="Timeline mode">
          <button role="tab" aria-selected={mode === "events"} className={`seg-btn ${mode === "events" ? "on" : ""}`} onClick={() => set({ mode: null })}>
            Events
          </button>
          <button role="tab" aria-selected={mode === "captures"} className={`seg-btn ${mode === "captures" ? "on" : ""}`} onClick={() => set({ mode: "captures", from: null, to: null, untimed: null, ev: null })}>
            Capture history
          </button>
        </div>
        <label className="row small">
          Time zone
          <select value={zone} onChange={(e) => setDisplayZone(e.target.value as "UTC" | "local")}>
            <option value="UTC">UTC</option>
            <option value="local">This browser's local time</option>
          </select>
        </label>
      </div>
      {mode === "events" ? <EventsMode params={params} set={set} /> : <CapturesMode params={params} set={set} />}
    </div>
  );
}

function EventsMode({ params, set }: { params: URLSearchParams; set: ReturnType<typeof useUrlState>[1] }) {
  const ws = useWorkspace();
  const { invId, sources } = ws;
  const app = useApp();
  const zone = useDisplayZone();
  const timedSources = (sources?.sources ?? []).filter((s) => s.timed_rows > 0);
  const src = params.get("src");
  const host = params.get("host");
  const tl = useView<Timeline>(`/investigations/${invId}/views/timeline`, { source_key: src, client_id: host, buckets: window.innerWidth < 640 ? 24 : 60 });
  const from = params.get("from");
  const to = params.get("to");
  const untimed = params.get("untimed");
  const offset = Number(params.get("offset") ?? 0);
  const listing = !!(from || untimed);
  const rows = useView<RowsPage>(listing ? `/investigations/${invId}/views/rows` : null, {
    source_key: src, client_id: host, from_us: from, to_us: to, untimed, order: untimed ? "source" : "time", offset, limit: app.preferences?.page_size ?? 50,
  });
  const ev = params.get("ev");
  const [levels, setLevels] = useState<InspectorLevels>(LEVELS);
  const data = tl.data;
  const hosts = [...new Map((sources?.sources ?? []).map((s) => [s.client_id, s.hostname || s.client_id])).entries()];
  return (
    <>
      <div className="row">
        {timedSources.length > 1 && (
          <select aria-label="Source" value={src ?? ""} onChange={(e) => set({ src: e.target.value || null, from: null, to: null, offset: null })}>
            <option value="">All sources with event times</option>
            {timedSources.map((s) => (
              <option key={s.source_key} value={s.source_key}>
                {s.label}
              </option>
            ))}
          </select>
        )}
        {hosts.length > 1 && (
          <select aria-label="Host" value={host ?? ""} onChange={(e) => set({ host: e.target.value || null, from: null, to: null, offset: null })}>
            <option value="">All hosts</option>
            {hosts.map(([c, h]) => (
              <option key={c} value={c}>
                {h}
              </option>
            ))}
          </select>
        )}
        <FilterChips params={params} set={set} />
      </div>
      {tl.error ? (
        <ErrorBanner error={tl.error} />
      ) : tl.pending ? (
        <PendingState pending={tl.pending} invId={invId} onRetry={tl.reload} what="the timeline" />
      ) : !data ? (
        <Spinner />
      ) : (
        <section className="card" aria-label="Events over time">
          <div className="card-head">
            <h2>Events over time</h2>
            <span className="xsmall muted">
              {data.timed_rows.toLocaleString()} rows with a mapped event time · bucket {data.bucket.label}
            </span>
          </div>
          <p className="xsmall muted">
            {data.basis}. Counting {data.counting_unit}. Capture time and analysis time are never used as event time.
          </p>
          {data.timed_rows === 0 ? (
            <EmptyState title="No saved row has a usable event time.">
              <p className="small">The sources here have no mapped event-time field, or their times lack a time zone. Their rows are still in Evidence.</p>
            </EmptyState>
          ) : (
            <Histogram buckets={data.buckets} unit="rows" zone={zone} selectedFrom={from ? Number(from) : null} onSelect={(b) => set({ from: String(b.from_us), to: String(b.to_us), untimed: null, offset: null, ev: null })} label="Event-count histogram" />
          )}
          {!data.complete && <Banner tone="warn" title="Some sources with event times were not read completely">{data.incomplete_sources.join("; ")}. Hatched intervals with no rows may be collection gaps, not measured zeros.</Banner>}
          <div className="row small">
            <strong>{data.untimed.total.toLocaleString()} row(s) without a usable event time</strong>
            {data.untimed.total > 0 && (
              <button className="btn small" onClick={() => set({ untimed: "any", from: null, to: null, offset: null, ev: null })}>
                Show these rows
              </button>
            )}
          </div>
          {data.untimed.by_reason.length > 0 && (
            <ul className="xsmall muted">
              {data.untimed.by_reason.map((r) => (
                <li key={r.status}>
                  {r.count.toLocaleString()}: {r.label}
                </li>
              ))}
            </ul>
          )}
          <details className="advanced">
            <summary>Event-time mappings</summary>
            <ul className="small">
              {data.mappings.map((m) => (
                <li key={m.source_key}>
                  {m.label}: {m.rules.length ? m.rules.map((r) => `${r.meaning || r.role} (${r.format})`).join(", ") : "no event-time mapping"}
                  {m.basis ? ` — field ${String(m.basis.field)}` : ""}
                </li>
              ))}
            </ul>
            <p className="xsmall muted">Numbers are interpreted only with a declared unit and text only with an explicit time zone. Operators can map custom sources in VR_DFIR_SOURCE_MAPPINGS.</p>
          </details>
        </section>
      )}
      {listing && (
        <div className={`evidence-layout ${ev ? "has-detail" : ""}`}>
          <div className="evidence-list">
            <h2 className="small">{untimed ? "Rows without a usable event time" : `Events from ${formatInstant(new Date(Number(from) / 1000).toISOString(), zone)}`}</h2>
            {rows.pending ? (
              <PendingState pending={rows.pending} invId={invId} onRetry={rows.reload} />
            ) : rows.error ? (
              <ErrorBanner error={rows.error} />
            ) : !rows.data ? (
              <Spinner />
            ) : (
              <RowsTable page={rows.data} selected={ev} onSelect={(eid) => set({ ev: eid }, { replace: true })} onPage={(o) => set({ offset: String(o) })} label="Timeline rows" timeFirst={!untimed} empty={<EmptyState title="No rows in this interval." />} />
            )}
          </div>
          <div className="evidence-detail">
            {ev && <WorkspaceInspector ws={ws} invId={invId} evidenceId={ev} levels={levels} onLevels={setLevels} onClose={() => set({ ev: null }, { replace: true })} onOpenEvidence={(eid) => set({ ev: eid }, { replace: true })} />}
          </div>
        </div>
      )}
    </>
  );
}

function CapturesMode({ params, set }: { params: URLSearchParams; set: ReturnType<typeof useUrlState>[1] }) {
  const { invId, sources } = useWorkspace();
  const list = sources?.sources ?? [];
  const src = params.get("src") ?? list.find((s) => s.domain !== "generic" && s.rows > 0)?.source_key ?? list[0]?.source_key ?? null;
  const hist = useView<CaptureHistory>(src ? `/investigations/${invId}/views/captures` : null, { source_key: src });
  const h = hist.data;
  const max = Math.max(1, ...(h?.captures ?? []).map((c) => c.persisted_rows));
  return (
    <>
      {list.length > 1 && (
        <label className="field small">
          Source
          <select value={src ?? ""} onChange={(e) => set({ src: e.target.value })}>
            {list.map((s) => (
              <option key={s.source_key} value={s.source_key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <ErrorBanner error={hist.error} />
      {!h ? (
        <Spinner />
      ) : !h.enough_history ? (
        <Banner tone="info" title="Not enough history">
          Only one saved capture of this host and source exists in this organization. History needs repeated captures; none are invented from the current snapshot.
        </Banner>
      ) : (
        <section className="card" aria-label="Capture history">
          <h2>Capture history</h2>
          <p className="xsmall muted">
            {h.basis} Counting {h.counting_unit}. Incomplete captures are marked and not joined to their neighbours.
          </p>
          <ol className="captures">
            {h.captures.map((c, i) => {
              const prev = h.captures[i - 1];
              return (
                <li key={`${c.investigation_id}|${c.source_key}`} className={`capture ${c.is_current ? "is-current" : ""} ${c.complete ? "" : "is-partial"}`}>
                  <span className="cap-time">{formatTime(c.collected_at)}</span>
                  <span className="bar-track" aria-hidden>
                    <span className={`bar-fill ${c.complete ? "" : "partial"}`} style={{ width: `${(100 * c.persisted_rows) / max}%` }} />
                  </span>
                  <span className="cap-rows">
                    {c.persisted_rows.toLocaleString()} rows{c.complete ? "" : ` · ${c.read_status !== "complete" ? `read ${c.read_status.replace(/_/g, " ")}` : c.result_status.replace(/_/g, " ")}`}
                  </span>
                  <span className="cap-links xsmall">
                    {c.is_current ? <strong>this investigation</strong> : <Link to={`/investigations/${c.investigation_id}`}>open</Link>}
                    {prev && (
                      <>
                        {" · "}
                        <Link to={`/investigations/${c.investigation_id}/changes?src=${encodeURIComponent(c.source_key)}&base=${encodeURIComponent(`${prev.investigation_id}|${prev.source_key}`)}`}>what changed since the previous capture?</Link>
                      </>
                    )}
                    {c.duplicates.length > 0 && <span className="muted"> · same flow also saved in {c.duplicates.length} other investigation(s)</span>}
                  </span>
                </li>
              );
            })}
          </ol>
        </section>
      )}
    </>
  );
}
