// Overview: what needs attention and how complete the saved evidence is. At most three
// metrics and two charts; every number is a whole-dataset aggregate from the server and opens
// the matching evidence. A low count is never presented as "clean".
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowRight } from "lucide-react";
import type { Chart, Metric, Overview } from "../../api/types";
import { BarList, Histogram, PartBar } from "../../components/charts";
import { CoverageList, ReviewChip, SeverityBadge } from "../../components/findings";
import { Banner, ErrorBanner, Spinner } from "../../components/ui";
import { formatTime } from "../../lib/format";
import { useDisplayZone } from "../../lib/time";
import { useScrollRestore } from "../../lib/scroll";
import { useView } from "../../lib/useView";
import { PendingState, useOpenTarget, useWorkspace } from "./shared";

export function OverviewView() {
  const { invId, detail } = useWorkspace();
  const active = !!detail && (detail.job_active || detail.investigation.job?.state === "queued");
  const view = useView<Overview>(active ? null : `/investigations/${invId}/views/overview`, { v: detail?.investigation.updated_at });
  const open = useOpenTarget(invId);
  const [params, setParams] = useSearchParams();
  const coverageOpen = params.get("coverage") === "1";
  useScrollRestore(!!view.data);
  const ov = view.data;

  if (active) {
    return (
      <Banner tone="info" title={detail?.investigation.phase === "analyzing" ? "Analyzing saved evidence…" : "Reading existing evidence…"} action={<Link className="btn small" to={`/investigations/${invId}/run`}>Run details</Link>}>
        <span className="small">The overview is prepared when this step finishes. Nothing new is collected from endpoints.</span>
      </Banner>
    );
  }
  if (view.error) return <ErrorBanner error={view.error} onRetry={view.reload} />;
  if (view.pending) return <PendingState pending={view.pending} invId={invId} onRetry={view.reload} what="the overview" />;
  if (!ov) return <Spinner />;
  const cov = ov.coverage;
  const failed = (cov.states.failed ?? 0) + (cov.states.unverified ?? 0);
  const partial = (cov.states.incomplete ?? 0) + (cov.states.not_read ?? 0);
  return (
    <div className="stack overview">
      {ov.next_action.target ? (
        <button className="next-action" onClick={() => open(ov.next_action.target)}>
          <span>
            <span className="next-label">Next step</span> {ov.next_action.label}
          </span>
          <ArrowRight size={18} aria-hidden />
        </button>
      ) : (
        <p className="next-action static">{ov.next_action.label}</p>
      )}
      <div className="metrics" role="list" aria-label="Summary">
        {ov.metrics.map((m) => (
          <MetricTile key={m.id} m={m} onOpen={() => (m.target.view === "coverage" ? setParams({ coverage: coverageOpen ? "" : "1" }, { replace: true }) : open(m.target))} />
        ))}
      </div>
      <div className="charts">
        {ov.charts.map((c) => (
          <ChartCard key={c.id} chart={c} onFilter={(f) => open({ view: c.id === "events" ? "timeline" : "evidence", filters: f, mode: c.id === "sources" ? "rows" : undefined })} onLink={() => open(c.link)} />
        ))}
      </div>
      <section className="card" aria-label="Findings that need attention">
        <div className="card-head">
          <h2>Needs review</h2>
          {ov.findings_ready && (
            <Link to={`/investigations/${invId}/evidence`} className="small">
              All findings
            </Link>
          )}
        </div>
        {!ov.findings_ready ? (
          <p className="small muted">Findings are not ready: analysis has not produced a report yet. The saved source rows can still be browsed.</p>
        ) : ov.attention.length === 0 ? (
          <p className="small">
            No finding is waiting for review. {ov.metrics[0].total ? "Every finding has a decision." : "No accepted findings were produced; this is not a statement that the host is clean."}
          </p>
        ) : (
          <ul className="plain attention">
            {ov.attention.map((a) => (
              <li key={a.finding_id}>
                <Link to={`/investigations/${invId}/evidence?finding=${encodeURIComponent(a.finding_id)}`} className="attention-row">
                  <SeverityBadge value={a.severity} />
                  <span className="attention-title" dir="auto">
                    {a.title}
                  </span>
                  <ReviewChip state={a.review_state} />
                  <span className="xsmall muted">
                    {a.evidence_count} row(s){a.hosts.length ? ` · ${a.hosts.join(", ")}` : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="coverage-line card tight" aria-label="Coverage and freshness">
        <p className="small">
          <strong>Coverage:</strong> {ov.metrics[2].value} of {cov.sources_total} sources read completely
          {failed ? ` · ${failed} failed or unverified` : ""}
          {partial ? ` · ${partial} partial or not read` : ""} · {cov.rows.toLocaleString()} saved rows · captured {cov.captured_from ? (cov.captured_from === cov.captured_to ? formatTime(cov.captured_from) : `${formatTime(cov.captured_from)} – ${formatTime(cov.captured_to)}`) : "at an unknown time"}
          {cov.stale_sources ? ` · ${cov.stale_sources} source(s) older than ${cov.stale_after_days} days` : ""}{" "}
          <button className="btn link small" aria-expanded={coverageOpen} onClick={() => setParams({ coverage: coverageOpen ? "" : "1" }, { replace: true })}>
            {coverageOpen ? "Hide coverage details" : "Coverage details"}
          </button>
        </p>
        {coverageOpen && (
          <div className="stack">
            <CoverageList sources={cov.sources} staleDays={cov.stale_after_days} onOpen={(s) => open({ view: "evidence", filters: { source_key: s.source_key }, mode: "rows" })} />
            <p className="xsmall muted">
              Read completely includes sources that returned no rows; Velociraptor does not distinguish an empty result from a skipped query unless the client log says so. {ov.scope.note}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function MetricTile({ m, onOpen }: { m: Metric; onOpen: () => void }) {
  return (
    <button className="metric" role="listitem" onClick={onOpen} title={m.detail}>
      <span className="metric-label">{m.label}</span>
      <span className="metric-value">
        {m.value === null ? <span className="muted small">not ready</span> : m.value.toLocaleString()}
        {m.total !== undefined && m.total !== null && <span className="metric-total"> of {m.total.toLocaleString()}</span>}
      </span>
      <span className="metric-detail">{m.detail}</span>
    </button>
  );
}

function ChartCard({ chart, onFilter, onLink }: { chart: Chart; onFilter: (f: Record<string, string>) => void; onLink: () => void }) {
  const [alt, setAlt] = useState(false);
  const zone = useDisplayZone();
  const segs = alt && chart.alternate ? chart.alternate.segments : chart.segments ?? [];
  return (
    <section className="card chart-card" aria-label={chart.title}>
      <div className="card-head">
        <h2>{alt && chart.alternate ? chart.alternate.title : chart.title}</h2>
        {chart.alternate && chart.state === "ready" && (
          <button className="btn link small" onClick={() => setAlt((v) => !v)}>
            {alt ? "By severity" : "By review state"}
          </button>
        )}
        {chart.link && (
          <button className="btn link small" onClick={onLink}>
            {chart.id === "events" ? "Open timeline" : "Hosts and outliers"}
          </button>
        )}
      </div>
      <p className="xsmall muted">Counting {chart.unit}. {chart.note ?? ""}</p>
      {chart.state !== "ready" ? (
        <p className="small muted">Not available yet.</p>
      ) : chart.kind === "donut" ? (
        chart.total ? (
          <PartBar segments={segs} total={chart.total} unit={chart.unit} palette={alt ? "rev" : "sev"} onSelect={(s) => onFilter(s.filter)} label={`${chart.title}: select a category to open its findings`} />
        ) : (
          <p className="small">No accepted findings. This reflects only the retrieved and reviewed data, not a clean host.</p>
        )
      ) : chart.kind === "bars" ? (
        <>
          <BarList bars={chart.bars ?? []} unit={chart.unit} onSelect={(b) => onFilter(b.filter)} label={chart.title} />
          {chart.more ? <p className="xsmall muted">{chart.more} more not shown.</p> : null}
        </>
      ) : (
        <>
          <Histogram buckets={chart.buckets ?? []} unit="rows" zone={zone} onSelect={(b) => onFilter({ from: String(b.from_us), to: String(b.to_us) })} label={chart.title} compact height={110} />
          <p className="xsmall muted">
            {chart.untimed ? `${chart.untimed.toLocaleString()} row(s) without a usable event time are counted separately in the timeline. ` : ""}
            Bucket width {chart.bucket?.label}.
          </p>
        </>
      )}
    </section>
  );
}
