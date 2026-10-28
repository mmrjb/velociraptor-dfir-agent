// Landing page: recent investigations with their hosts, capture age, progress and review
// status, a Continue action, and one primary action (Start investigation). Saved evidence and
// bundle import stay available when the Velociraptor API is not configured or unreachable.
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Plus } from "lucide-react";
import { apiGet, errorMessage } from "../api/client";
import type { ApiError, InvestigationSummary } from "../api/types";
import { useApp, usePolling } from "../lib/app";
import { formatTime, MODE_LABELS } from "../lib/format";
import { Banner, EmptyState, ErrorBanner, Spinner, StatusBadge, SyntheticBadge, UrlSearchInput } from "../components/ui";

type Row = InvestigationSummary & { report_ready?: boolean; reviews_decided?: number };

export function InvestigationsPage() {
  const app = useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<Row[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const q = params.get("q") ?? "";

  const load = useCallback(async () => {
    try {
      setItems(await apiGet<Row[]>("/investigations", { q }));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [q]);

  useEffect(() => {
    app.setInvestigationContext(null);
    load();
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  usePolling(async () => {
    await load();
  }, 3000, !!items?.some((i) => i.job_active));

  const vr = app.status?.velociraptor;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Investigations</h1>
          <p className="muted">Continue a saved investigation, or start one from existing evidence.</p>
        </div>
        <Link className="btn primary" to="/investigations/new">
          <Plus size={16} aria-hidden /> Start investigation
        </Link>
      </div>
      {vr && !vr.configured && (
        <Banner tone="info" title="Velociraptor is not connected" action={<Link className="btn small" to="/investigations/new?mode=import">Import evidence</Link>}>
          Saved investigations and bundle import work without it. Live collections and hunts need the API configuration (see Settings).
        </Banner>
      )}
      {vr?.configured && vr.last_test?.reachable === false && (
        <Banner tone="warn" title="Velociraptor is unreachable" action={<Link className="btn small" to="/settings">Check connection</Link>}>
          Saved evidence remains available.
        </Banner>
      )}
      <ErrorBanner error={error} onRetry={load} />
      {((items?.length ?? 0) > 6 || q) && (
        <div className="row" style={{ marginBottom: 10 }}>
          <label className="sr-only" htmlFor="inv-search">
            Search investigations
          </label>
          <UrlSearchInput id="inv-search" placeholder="Search by title, host, client, flow or hunt" value={q} onCommit={(v) => setParams(v ? { q: v } : {}, { replace: true })} style={{ flex: 1, minWidth: 220 }} />
        </div>
      )}
      {items === null ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="card">
          <EmptyState title={q ? "No investigations match this search." : "No investigations yet."}>
            {!q && <p className="small">Start one from an existing collection, an existing hunt, or an evidence bundle.</p>}
          </EmptyState>
        </div>
      ) : (
        <ul className="plain inv-list" aria-label="Recent investigations">
          {items.map((i) => {
            const findings = i.counts?.findings;
            const running = i.outcome === "running" || i.job_active;
            const hosts = i.mode === "existing_hunt" ? `${i.counts?.clients ?? "?"} host(s), hunt ${i.scope?.hunt_id ?? ""}` : i.scope?.hostname || i.scope?.client_id || (i.counts?.clients ? `${i.counts.clients} host(s)` : "-");
            return (
              <li key={i.investigation_id} className="inv-card">
                <div className="inv-main">
                  <Link to={`/investigations/${i.investigation_id}`} className="inv-title" dir="auto">
                    {i.title}
                  </Link>
                  <div className="small muted">
                    <span dir="auto">{hosts}</span> · org <span className="mono">{i.scope?.org_id || "unknown"}</span> · {MODE_LABELS[i.mode] ?? i.mode} · saved {formatTime(i.updated_at)} {i.synthetic && <SyntheticBadge />}
                  </div>
                </div>
                <div className="inv-status">
                  <StatusBadge value={running ? i.phase : i.outcome} />
                  <span className="small">
                    {running ? "processing…" : !i.report_ready ? "findings not ready" : findings === 0 ? "no accepted findings" : `${i.reviews_decided ?? 0} of ${findings} reviewed`}
                  </span>
                </div>
                <button className="btn" onClick={() => navigate(`/investigations/${i.investigation_id}`)} aria-label={`Continue ${i.title}`}>
                  Continue
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
