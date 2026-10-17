import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Plus, Upload } from "lucide-react";
import { apiGet, errorMessage } from "../api/client";
import type { ApiError, InvestigationSummary } from "../api/types";
import { useApp, usePolling } from "../lib/app";
import { MODE_LABELS, formatTime } from "../lib/format";
import { Banner, SyntheticBadge, EmptyState, ErrorBanner, Spinner, StatusBadge, UrlSearchInput } from "../components/ui";

export function InvestigationsPage() {
  const app = useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const q = params.get("q") ?? "";
  const status = params.get("status") ?? "";

  const load = useCallback(async () => {
    try {
      setItems(await apiGet<InvestigationSummary[]>("/investigations", { q, status }));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [q, status]);

  useEffect(() => {
    app.setInvestigationContext(null);
    load();
  }, [load]); // eslint-disable-line react-hooks/exhaustive-deps

  usePolling(async () => {
    await load();
  }, 3000, !!items?.some((i) => i.job_active));

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const st = app.status;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Investigations</h1>
          <p className="muted">Collect, analyze existing evidence, and review findings that trace back to evidence rows.</p>
        </div>
        <div className="row">
          <Link className="btn primary" to="/investigations/new">
            <Plus size={16} aria-hidden /> New Investigation
          </Link>
          <Link className="btn" to="/investigations/new?mode=import">
            <Upload size={16} aria-hidden /> Import Evidence
          </Link>
        </div>
      </div>
      {st && !st.velociraptor.configured && (
        <Banner tone="info" title="Velociraptor access is not configured on this server.">
          Endpoints, flows, hunts, and new collections need <code>VELOCIRAPTOR_API_CONFIG</code> in the server environment. Saved investigations and imported evidence work without it.
        </Banner>
      )}
      <ErrorBanner error={error} onRetry={load} />
      <div className="card tight">
        <div className="row" style={{ marginBottom: 10 }}>
          <label className="sr-only" htmlFor="inv-search">
            Search investigations
          </label>
          <UrlSearchInput id="inv-search" placeholder="Search by name, host, client, flow or hunt" value={q} onCommit={(v) => setFilter("q", v)} style={{ flex: 1, minWidth: 220 }} />
          <label className="row small">
            Status
            <select aria-label="Filter by status" value={status} onChange={(e) => setFilter("status", e.target.value)}>
              <option value="">All</option>
              <option value="running">In progress</option>
              <option value="complete">Complete</option>
              <option value="partial">Partial</option>
              <option value="evidence_only">Evidence only</option>
              <option value="interrupted">Interrupted</option>
              <option value="failed">Failed</option>
            </select>
          </label>
        </div>
        {items === null ? (
          <Spinner />
        ) : items.length === 0 ? (
          <EmptyState title={q || status ? "No investigations match these filters." : "No investigations yet."}>
            {!q && !status && (
              <div className="row" style={{ justifyContent: "center" }}>
                <Link className="btn primary" to="/investigations/new">
                  New Investigation
                </Link>
                <Link className="btn" to="/investigations/new?mode=import">
                  Import Evidence
                </Link>
              </div>
            )}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Investigation</th>
                  <th>Target / org</th>
                  <th>Mode / profile</th>
                  <th>Created</th>
                  <th>Status</th>
                  <th>Findings</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.investigation_id} className="clickable" onClick={() => navigate(`/investigations/${i.investigation_id}`)}>
                    <td>
                      <Link to={`/investigations/${i.investigation_id}`} onClick={(e) => e.stopPropagation()}>
                        {i.title}
                      </Link>
                      <div className="small muted mono">{i.investigation_id}</div>
                      {i.synthetic && <SyntheticBadge />}
                    </td>
                    <td>
                      <span dir="auto">{i.scope?.hostname || i.scope?.client_id || i.scope?.hunt_id || "-"}</span>
                      {i.scope?.client_id && i.scope?.hostname && <div className="small muted mono">{i.scope.client_id}</div>}
                      <div className="small muted">
                        org <span className="mono">{i.scope?.org_id ?? "-"}</span>
                      </div>
                    </td>
                    <td>
                      {MODE_LABELS[i.mode] ?? i.mode}
                      {i.profile && <div className="small muted">{i.profile.name}</div>}
                    </td>
                    <td className="nowrap">{formatTime(i.created_at)}</td>
                    <td>
                      <div className="stack" style={{ gap: 3 }}>
                        <span>
                          <StatusBadge value={i.outcome === "running" ? i.phase : i.outcome} />
                          {i.job_active && <span className="small muted"> running…</span>}
                        </span>
                        <span className="small">
                          <StatusBadge value={i.collection_status} prefix="Collection" />
                        </span>
                        <span className="small">
                          <StatusBadge value={i.analysis_status} prefix="Analysis" />
                        </span>
                      </div>
                    </td>
                    <td>{i.counts?.findings ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
