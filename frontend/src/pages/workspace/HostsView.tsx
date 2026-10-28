// Hosts and outliers: compare the represented hosts and inspect low-prevalence entities inside
// a defined cohort. Every prevalence shows its denominator of eligible hosts. Without at least
// three eligible hosts there is no outlier list, only an explanation.
import { Link } from "react-router";
import { ArrowLeft } from "lucide-react";
import type { HostsView as HostsData } from "../../api/types";
import { BarList } from "../../components/charts";
import { Banner, EmptyState, ErrorBanner, Pager, Spinner } from "../../components/ui";
import { useUrlState } from "../../lib/urlState";
import { useView } from "../../lib/useView";
import { PendingState, useOpenTarget, useWorkspace } from "./shared";

export function HostsView() {
  const { invId } = useWorkspace();
  const [params, set] = useUrlState();
  const scope = params.get("scope") === "organization" ? "organization" : "investigation";
  const offset = Number(params.get("offset") ?? 0);
  const view = useView<HostsData>(`/investigations/${invId}/views/hosts`, { scope, offset, limit: 50 });
  const open = useOpenTarget(invId);
  const h = view.data;
  return (
    <div className="stack">
      <div className="row between view-head">
        <Link to={`/investigations/${invId}`} className="btn link small">
          <ArrowLeft size={14} aria-hidden /> Overview
        </Link>
        <div className="seg" role="group" aria-label="Cohort">
          <button className={`seg-btn ${scope === "investigation" ? "on" : ""}`} aria-pressed={scope === "investigation"} onClick={() => set({ scope: null, offset: null })}>
            Hosts in this investigation
          </button>
          <button className={`seg-btn ${scope === "organization" ? "on" : ""}`} aria-pressed={scope === "organization"} onClick={() => set({ scope: "organization", offset: null })}>
            Saved investigations of this organization
          </button>
        </div>
      </div>
      <h2>Hosts and outliers</h2>
      <ErrorBanner error={view.error} />
      {view.pending ? (
        <PendingState pending={view.pending} invId={invId} onRetry={view.reload} what="the host cohort" />
      ) : !h ? (
        <Spinner />
      ) : h.unavailable ? (
        <Banner tone="info" title="No cohort can be formed">
          {h.unavailable}
        </Banner>
      ) : (
        <>
          <p className="small muted">
            {h.host_count} host(s){h.org_id ? ` in organization ${h.org_id}` : ""}
            {h.window ? `, one observation per host and source from the ${h.window.days} days up to this investigation` : ""}. {h.note}
          </p>
          <div className="grid cols-2">
            <section className="card" aria-label="Host comparison">
              <h3>Findings needing review by host</h3>
              <p className="xsmall muted">Unique findings per host; a finding citing several hosts counts once for each.</p>
              <BarList
                bars={h.hosts.map((x) => ({ key: x.client_id, label: x.hostname, count: x.findings_open, secondary: `${x.findings_total} finding(s) · ${x.rows.toLocaleString()} rows · ${x.sources_complete}/${x.sources_total} sources read with rows · ${x.os}`, filter: { client_id: x.client_id, review: "open" } }))}
                unit="findings needing review"
                onSelect={(b) => (h.hosts.find((x) => x.client_id === b.key)?.investigations.includes(invId) ? open({ view: "evidence", filters: b.filter }) : undefined)}
                label="Hosts; select one to open its evidence"
              />
            </section>
            <section className="card" aria-label="Cohort eligibility">
              <h3>Who is compared</h3>
              <ul className="small">
                {h.groups.map((g) => (
                  <li key={g.os}>
                    {g.hosts} {g.os === "unknown" ? "host(s) of unknown OS" : `${g.os} host(s)`}:{" "}
                    {g.kinds.length ? g.kinds.map((k) => `${k.label} on ${k.eligible_hosts}`).join(", ") : "no complete source with comparable values"}
                    {!g.usable && <span className="muted"> — fewer than {h.min_cohort} eligible hosts, no outlier list</span>}
                  </li>
                ))}
              </ul>
              {h.excluded.length > 0 && (
                <details className="advanced">
                  <summary>{h.excluded.length} capture(s) excluded</summary>
                  <ul className="xsmall">
                    {h.excluded.map((e) => (
                      <li key={`${e.client_id}${e.reason}`}>
                        {e.hostname || e.client_id}: {e.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          </div>
          <section className="card" aria-label="Low-prevalence entities">
            <h3>Seldom seen in this cohort</h3>
            {h.outliers_total === 0 ? (
              <EmptyState title={h.groups.some((g) => g.usable) ? "Nothing is seen on only a few eligible hosts." : `Outliers need at least ${h.min_cohort} eligible hosts.`}>
                <p className="small">{h.groups.some((g) => g.usable) ? "This does not mean the hosts are clean." : "Try the organization scope if earlier investigations of other hosts are saved."}</p>
              </EmptyState>
            ) : (
              <>
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Kind</th>
                        <th>Value</th>
                        <th>Prevalence</th>
                        <th>Hosts</th>
                        <th>
                          <span className="sr-only">Evidence</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {h.outliers.map((o) => (
                        <tr key={`${o.kind}:${o.key}`}>
                          <td className="nowrap small">{o.label}</td>
                          <td>
                            <code dir="ltr" className="we-key">
                              {o.display}
                            </code>
                          </td>
                          <td className="nowrap">{o.text}</td>
                          <td dir="auto">{o.hosts.join(", ")}</td>
                          <td className="nowrap">
                            {o.refs[0] && (
                              <Link className="btn small" to={`/investigations/${o.refs[0].investigation_id}/evidence?mode=rows&ev=${encodeURIComponent(o.refs[0].evidence_id)}`}>
                                Show evidence
                              </Link>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager offset={h.offset} limit={h.limit} total={h.outliers_total} onChange={(o) => set({ offset: String(o) })} />
              </>
            )}
            <p className="xsmall muted">Counting {h.counting_unit}. Hosts whose source was incomplete or lacks the field are not in the denominator.</p>
          </section>
        </>
      )}
    </div>
  );
}
