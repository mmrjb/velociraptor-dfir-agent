// The single evidence inspector. Selecting another row replaces its content in place; the
// table behind it keeps its filters, page and scroll position. Three levels of disclosure:
// summary (the fields that matter for this kind of source and the reason for attention),
// all fields, and Advanced (exact raw JSON, provenance, timestamp basis, mapping).
import { useEffect, useRef, type ReactNode } from "react";
import { ArrowLeft, Link2, X } from "lucide-react";
import type { AddressInfo, Inspect, RowRef } from "../api/types";
import { getField } from "../lib/fields";
import { formatTime } from "../lib/format";
import { formatInstant, useDisplayZone } from "../lib/time";
import { useView } from "../lib/useView";
import { ReviewChip, SeverityBadge } from "./findings";
import { FieldValue } from "./renderers";
import { Banner, ErrorBanner, Spinner } from "./ui";
import { CopyButton } from "./values";

export interface InspectorLevels {
  allFields: boolean;
  advanced: boolean;
  related: boolean;
}

export interface InspectorProps {
  invId: string;
  evidenceId: string;
  levels: InspectorLevels;
  onLevels: (l: InspectorLevels) => void;
  onClose: () => void;
  onOpenEvidence: (eid: string) => void;
  onOpenFinding?: (fid: string) => void;
  back?: { label: string; onClick: () => void } | null;
  focusField?: string | null;
  /** Extra actions (Where else seen, Compare with baseline, Pin) rendered by the workspace. */
  actions?: (data: Inspect) => ReactNode;
  below?: (data: Inspect) => ReactNode;
}

export function EvidenceInspector(props: InspectorProps) {
  const { invId, evidenceId, levels, onLevels, onClose, onOpenEvidence, onOpenFinding, back, focusField, actions, below } = props;
  const view = useView<Inspect>(`/investigations/${invId}/views/inspect/${encodeURIComponent(evidenceId)}`);
  const zone = useDisplayZone();
  const root = useRef<HTMLElement>(null);
  // Only the response for the requested row is shown; never the previous row in its place.
  const data = view.fresh && view.data && view.data.record.evidence_id ? view.data : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector(".dialog-layer")) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") && !root.current?.contains(t)) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (!data || !focusField) return;
    if (!levels.allFields) onLevels({ ...levels, allFields: true });
    const el = root.current?.querySelector<HTMLElement>(`[data-field="${CSS.escape(focusField)}"]`);
    el?.scrollIntoView?.({ block: "nearest" });
    el?.focus?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, focusField]);

  const marksFor = (field: string | null) => (data && field ? data.highlights.filter((h) => h.field === field) : []);
  const addressFor = (field: string | null): AddressInfo | undefined => data?.presentation.addresses.find((a) => a.field === field);
  const kindFor = (field: string) => data?.presentation.kinds[field] ?? "text";

  return (
    <section className="inspector card" aria-label="Evidence inspector" ref={root} tabIndex={-1}>
      <div className="insp-head">
        <div className="insp-title">
          {back && (
            <button className="btn link small" onClick={back.onClick}>
              <ArrowLeft size={13} aria-hidden /> {back.label}
            </button>
          )}
          <h2 dir="auto">{data?.title ?? "Evidence row"}</h2>
          {data && (
            <div className="xsmall muted">
              {data.source.label} · row {data.record.row_index}
              {data.presentation.event_time.utc ? ` · ${formatInstant(data.presentation.event_time.utc, zone)}` : ""}
            </div>
          )}
        </div>
        <button className="btn ghost" onClick={onClose} aria-label="Close inspector (Escape)">
          <X size={18} aria-hidden />
        </button>
      </div>
      <ErrorBanner error={view.error} />
      {view.error?.code === "not_found" && <p className="small">The requested row is not in this investigation. No other row is shown in its place.</p>}
      {view.pending && <Banner tone="info" title={view.pending.state === "building" ? "Preparing the evidence index…" : "The evidence index is not available; see the view behind this panel."}>{view.pending.progress ? `${view.pending.progress.rows_done} of ${view.pending.progress.rows_total} rows indexed` : null}</Banner>}
      {!data && !view.error && !view.pending && <Spinner />}
      {data && (
        <div className="insp-body">
          <div className="insp-attention">
            {data.findings.length ? (
              <>
                <h3>Reason for attention</h3>
                <ul className="plain stack-xs">
                  {data.findings.map((f) => (
                    <li key={f.finding_id} className="row">
                      <SeverityBadge value={f.severity} />
                      <ReviewChip state={f.review_state} />
                      {onOpenFinding ? (
                        <button className="btn link small" onClick={() => onOpenFinding(f.finding_id)} dir="auto">
                          {f.finding_id}: {f.title}
                        </button>
                      ) : (
                        <span dir="auto">
                          {f.finding_id}: {f.title}
                        </span>
                      )}
                      <span className="xsmall muted">{f.origin === "model_proposal" ? "AI proposal" : f.origin === "deterministic_rule" ? "rule" : "analyst"}</span>
                    </li>
                  ))}
                </ul>
                {data.highlights.length > 0 && <p className="xsmall muted">Marked text is what the cited rule matched. The observation is the row itself; interpretation is in the finding.</p>}
              </>
            ) : (
              <p className="small muted">No finding cites this row. It is shown as saved evidence only.</p>
            )}
          </div>

          <dl className="fields summary-fields">
            {data.presentation.summary.map((f) => (
              <div key={f.field ?? f.role ?? f.label} className="field-row" data-field={f.field ?? undefined} tabIndex={-1}>
                <dt>{f.label}</dt>
                <dd>
                  {f.virtual ? (
                    <code dir="ltr">{f.value}</code>
                  ) : (
                    <FieldValue value={getField(data.row, f.field as string)} kind={f.kind} marks={marksFor(f.field)} address={addressFor(f.field)} zone={zone} copyLabel={f.label} />
                  )}
                </dd>
              </div>
            ))}
          </dl>

          <div className="insp-actions row" role="group" aria-label="Evidence actions">
            <CopyButton text={data.raw_text} label="row (exact JSON)" />
            {hasRelated(data) && (
              <button className="btn small" aria-expanded={levels.related} onClick={() => onLevels({ ...levels, related: !levels.related })}>
                <Link2 size={14} aria-hidden /> {levels.related ? "Hide related rows" : "Show related rows"}
              </button>
            )}
            {actions?.(data)}
          </div>

          {levels.related && hasRelated(data) && <Related data={data} onOpen={onOpenEvidence} />}
          {below?.(data)}

          <Disclosure open={levels.allFields} onToggle={(v) => onLevels({ ...levels, allFields: v })} label={`All fields (${Object.keys(data.row ?? {}).length})`}>
            <dl className="fields">
              {Object.keys(data.row ?? {}).map((k) => (
                <div key={k} className="field-row" data-field={k} tabIndex={-1}>
                  <dt className="mono">{k}</dt>
                  <dd>
                    <FieldValue value={data.row[k]} kind={kindFor(k)} marks={marksFor(k)} address={addressFor(k)} zone={zone} copyLabel={k} />
                  </dd>
                </div>
              ))}
            </dl>
          </Disclosure>

          <Disclosure open={levels.advanced} onToggle={(v) => onLevels({ ...levels, advanced: v })} label="Advanced: raw JSON and provenance">
            <div className="stack">
              <div className="row">
                <strong className="small">Original row, exactly as saved</strong>
                <CopyButton text={data.raw_text} label="raw JSON" />
              </div>
              <pre className="pre" dir="ltr">
                {data.raw_text}
              </pre>
              <dl className="kv small">
                <dt>Evidence ID</dt>
                <dd>
                  <code>{data.record.evidence_id}</code> <CopyButton text={data.record.evidence_id} label="evidence ID" compact />
                </dd>
                <dt>Organization</dt>
                <dd>
                  <code>{data.record.org_id}</code>
                </dd>
                <dt>Host</dt>
                <dd>
                  <span dir="auto">{data.record.hostname}</span> <code>{data.record.client_id}</code>
                </dd>
                <dt>Flow{data.record.hunt_id ? " / hunt" : ""}</dt>
                <dd>
                  <code>{data.record.flow_id}</code> {data.record.hunt_id && <code>{data.record.hunt_id}</code>}
                </dd>
                <dt>Artifact / source</dt>
                <dd>
                  <code>{data.record.full_name}</code>
                </dd>
                <dt>Row position</dt>
                <dd>
                  {data.record.row_index} (zero-based position in the server result set) · file <code>{data.record.file}</code> at byte {data.record.line_offset_bytes}
                </dd>
                <dt>Content hash</dt>
                <dd>
                  <code className="hash">{data.record.raw_sha256}</code>
                </dd>
                <dt>Captured / retrieved</dt>
                <dd>
                  {formatTime(data.record.collected_at)} / {formatTime(data.record.retrieved_at)} (capture time is not event time)
                </dd>
                <dt>Event time</dt>
                <dd>
                  {data.presentation.event_time.utc ? (
                    <>
                      {formatInstant(data.presentation.event_time.utc, zone)} from <code>{String(data.presentation.event_time.basis?.field ?? "")}</code> ({String(data.presentation.event_time.basis?.format ?? "")}, {String(data.presentation.event_time.basis?.meaning ?? "")})
                    </>
                  ) : (
                    <>Not available: {data.presentation.event_time.reason}{data.presentation.event_time.detail ? ` (${data.presentation.event_time.detail})` : ""}</>
                  )}
                </dd>
                {data.record.recorded_event_time && (
                  <>
                    <dt>Recorded at retrieval</dt>
                    <dd>
                      {data.record.recorded_event_time} from <code>{data.record.recorded_event_time_field}</code> <span className="muted">— {data.record.recorded_event_time_note}</span>
                    </dd>
                  </>
                )}
                <dt>Presentation</dt>
                <dd>
                  {data.presentation.mapping.label}: {data.presentation.mapping.basis} ({data.presentation.mapping.version})
                </dd>
                {data.presentation.addresses.map((a) => (
                  <AddressRow key={a.field} a={a} />
                ))}
              </dl>
            </div>
          </Disclosure>
        </div>
      )}
    </section>
  );
}

function AddressRow({ a }: { a: AddressInfo }) {
  return (
    <>
      <dt>{a.role === "raddr" ? "Remote address range" : "Local address range"}</dt>
      <dd>
        {a.label} <span className="muted">({a.basis})</span>
      </dd>
    </>
  );
}

function hasRelated(d: Inspect) {
  const r = d.relations;
  return !!(r.parent || r.parent_status || r.children.length || r.connections.length || r.process || r.process_status || d.related_by_finding.length);
}

function RefList({ items, onOpen }: { items: RowRef[]; onOpen: (eid: string) => void }) {
  return (
    <ul className="plain stack-xs">
      {items.map((r) => (
        <li key={r.evidence_id}>
          <button className="btn link small" onClick={() => onOpen(r.evidence_id)} dir="auto">
            {r.title || r.evidence_id}
          </button>{" "}
          <span className="xsmall muted">{r.full_name}</span>
        </li>
      ))}
    </ul>
  );
}

function Related({ data, onOpen }: { data: Inspect; onOpen: (eid: string) => void }) {
  const r = data.relations;
  return (
    <div className="related card tight">
      {(r.parent || r.parent_status) && (
        <div>
          <h3>Parent process</h3>
          {r.parent && <RefList items={[r.parent]} onOpen={onOpen} />}
          {r.parent_status && <p className="xsmall muted">{r.parent_status}</p>}
        </div>
      )}
      {(r.children.length > 0 || r.children_status) && (
        <div>
          <h3>Child processes ({r.children.length})</h3>
          <RefList items={r.children} onOpen={onOpen} />
          {r.children_status && <p className="xsmall muted">{r.children_status}</p>}
        </div>
      )}
      {r.connections.length > 0 && (
        <div>
          <h3>Network connections of this process</h3>
          <RefList items={r.connections} onOpen={onOpen} />
        </div>
      )}
      {(r.process || r.process_status) && (
        <div>
          <h3>Process</h3>
          {r.process && <RefList items={[r.process]} onOpen={onOpen} />}
          {r.process_status && <p className="xsmall muted">{r.process_status}</p>}
        </div>
      )}
      {data.related_by_finding.length > 0 && (
        <div>
          <h3>Other rows cited by the same finding(s)</h3>
          <RefList items={data.related_by_finding} onOpen={onOpen} />
        </div>
      )}
      <p className="xsmall muted">Relations are shown only within the same capture, with PID and start-time checks; nothing is inferred across captures.</p>
    </div>
  );
}

function Disclosure({ open, onToggle, label, children }: { open: boolean; onToggle: (v: boolean) => void; label: string; children: ReactNode }) {
  return (
    <div className="disclosure">
      <button type="button" className="disclosure-head" aria-expanded={open} onClick={() => onToggle(!open)}>
        <span aria-hidden>{open ? "▾" : "▸"}</span> {label}
      </button>
      {open && <div className="disclosure-body">{children}</div>}
    </div>
  );
}
