// Contextual inspector actions: Where else seen, Compare with baseline and Pin to report.
// Each appears only when the server reports supporting data for the selected row.
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Bookmark, BookmarkCheck, GitCompare, MapPin } from "lucide-react";
import type { Inspect, WhereElse } from "../../api/types";
import { EvidenceInspector, type InspectorProps } from "../../components/Inspector";
import { useView } from "../../lib/useView";
import { Banner, ErrorBanner, Spinner } from "../../components/ui";
import { PendingState, type WorkspaceCtx } from "./shared";

export function InspectorActions({ data, ws, whereElse, onWhereElse }: { data: Inspect; ws: WorkspaceCtx; whereElse: string | null; onWhereElse: (scope: string | null) => void }) {
  const [busy, setBusy] = useState(false);
  const a = data.actions;
  const pinned = ws.pins.isPinned("evidence", data.record.evidence_id);
  const togglePin = async () => {
    setBusy(true);
    try {
      if (pinned) await ws.pins.unpin(pinned);
      else await ws.pins.pin({ kind: "evidence", ref: data.record.evidence_id });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {a?.where_else.available && (
        <button className="btn small" aria-expanded={!!whereElse} onClick={() => onWhereElse(whereElse ? null : "investigation")}>
          <MapPin size={14} aria-hidden /> Where else was this seen?
        </button>
      )}
      {a?.compare.available && (
        <Link className="btn small" to={`/investigations/${ws.invId}/changes?src=${encodeURIComponent(data.record.source_key)}`}>
          <GitCompare size={14} aria-hidden /> Compare with baseline
        </Link>
      )}
      <button className="btn small" aria-pressed={!!pinned} disabled={busy} onClick={togglePin}>
        {pinned ? <BookmarkCheck size={14} aria-hidden /> : <Bookmark size={14} aria-hidden />} {pinned ? "Pinned" : "Pin to report"}
      </button>
    </>
  );
}

export function WhereElsePanel({ invId, data, entityKey: scope, onClose }: { invId: string; data: Inspect; entityKey: string; onClose: () => void }) {
  const [current, setCurrent] = useState(scope);
  const view = useView<WhereElse>(`/investigations/${invId}/views/where-else/${encodeURIComponent(data.record.evidence_id)}`, { scope: current });
  const we = view.data;
  return (
    <section className="where-else card tight" aria-label="Where else was this seen?">
      <div className="card-head">
        <h3>Where else was this seen?</h3>
        <span className="row">
          <div className="seg small" role="group" aria-label="Cohort">
            <button className={`seg-btn ${current === "investigation" ? "on" : ""}`} aria-pressed={current === "investigation"} onClick={() => setCurrent("investigation")}>
              This investigation
            </button>
            <button className={`seg-btn ${current === "organization" ? "on" : ""}`} aria-pressed={current === "organization"} onClick={() => setCurrent("organization")}>
              Saved investigations of this organization
            </button>
          </div>
          <button className="btn link small" onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      <ErrorBanner error={view.error} />
      {view.pending ? (
        <PendingState pending={view.pending} invId={invId} onRetry={view.reload} what="the cohort" />
      ) : !we ? (
        <Spinner />
      ) : we.unavailable ? (
        <Banner tone="info" title="No cohort for this scope">
          {we.unavailable}
        </Banner>
      ) : (
        <div className="stack">
          {we.entities.map((e) => (
            <div key={`${e.kind}:${e.key}`} className="we-entity">
              <div className="row between">
                <span>
                  <span className="xsmall muted">{e.label}</span>{" "}
                  <code dir="ltr" className="we-key">
                    {e.display}
                  </code>
                </span>
                <strong>{e.text}</strong>
              </div>
              {e.cohort_too_small && <p className="xsmall muted">Fewer than three eligible hosts: prevalence says little here.</p>}
              {e.also_seen_not_eligible.length > 0 && <p className="xsmall">Also observed on {e.also_seen_not_eligible.join(", ")}, whose source was incomplete (not in the denominator).</p>}
              {e.not_eligible_hosts.length > 0 && <p className="xsmall muted">Not eligible (no complete source with this field): {e.not_eligible_hosts.join(", ")}.</p>}
              {e.refs.length > 0 && (
                <ul className="plain stack-xs">
                  {e.refs.map((r) => (
                    <li key={`${r.investigation_id}:${r.evidence_id}`} className="small">
                      <Link to={`/investigations/${r.investigation_id}/evidence?mode=rows&ev=${encodeURIComponent(r.evidence_id)}`} dir="auto">
                        {r.hostname || r.client_id}: {r.title}
                      </Link>{" "}
                      <span className="xsmall muted">
                        {r.full_name}
                        {r.investigation_id !== invId ? " · other investigation" : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          <p className="xsmall muted">
            Counting {we.counting_unit}
            {we.host_os !== "unknown" ? `, ${we.host_os} hosts only` : ""}
            {we.other_os_hosts.length ? ` (other OS not compared: ${we.other_os_hosts.join(", ")})` : ""}
            {we.window ? `, captures from ${we.window.days} days up to this investigation` : ""}. {we.note} Low prevalence is a reason to review, not a verdict.
          </p>
        </div>
      )}
    </section>
  );
}

/**
 * The single inspector with its workspace actions. Pin, Where else seen and Compare apply to
 * rows of the open investigation; a baseline row from an earlier investigation is shown as
 * plain evidence.
 */
export function WorkspaceInspector(props: Omit<InspectorProps, "actions" | "below"> & { ws: WorkspaceCtx }) {
  const { ws, ...rest } = props;
  const [whereElse, setWhereElse] = useState<string | null>(null);
  useEffect(() => setWhereElse(null), [rest.evidenceId]);
  const own = rest.invId === ws.invId;
  return (
    <EvidenceInspector
      // A new row remounts the inspector, so the previous row is never shown in its place.
      key={`${rest.invId}|${rest.evidenceId}`}
      {...rest}
      actions={own ? (d) => <InspectorActions data={d} ws={ws} whereElse={whereElse} onWhereElse={setWhereElse} /> : undefined}
      below={own ? (d) => (whereElse ? <WhereElsePanel invId={ws.invId} data={d} entityKey={whereElse} onClose={() => setWhereElse(null)} /> : null) : undefined}
    />
  );
}
