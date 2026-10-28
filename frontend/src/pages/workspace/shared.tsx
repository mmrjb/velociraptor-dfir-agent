// Pieces shared by the investigation views: workspace context, index-building state, filter
// chips, the evidence rows table and navigation to a view target.
import { useMemo, type ReactNode } from "react";
import { useNavigate, useOutletContext } from "react-router";
import { X } from "lucide-react";
import { apiPost } from "../../api/client";
import type { InvestigationDetail, RowRecord, RowsPage, SourcesView, ViewPending, ViewTarget } from "../../api/types";
import { DataTable, type ColumnDef } from "../../components/DataTable";
import { Banner, Spinner } from "../../components/ui";
import { getField } from "../../lib/fields";
import { FILTER_KEYS, type SetParams } from "../../lib/urlState";
import { formatInstant, useDisplayZone } from "../../lib/time";

export interface WorkspaceCtx {
  invId: string;
  detail: InvestigationDetail | null;
  reloadDetail: () => void;
  sources: SourcesView | null;
  aiEnabled: boolean;
  pins: PinsApi;
}

export interface PinsApi {
  count: number;
  isPinned: (kind: string, ref: string) => string | null;
  pin: (body: Record<string, unknown>) => Promise<void>;
  unpin: (pinId: string) => Promise<void>;
  open: () => void;
  reload: () => void;
}

export function useWorkspace(): WorkspaceCtx {
  return useOutletContext<WorkspaceCtx>();
}

export function PendingState({ pending, invId, what = "this view", onRetry }: { pending: ViewPending; invId: string; what?: string; onRetry?: () => void }) {
  const retry = (
    <button className="btn small" onClick={() => apiPost(`/investigations/${invId}/views/index/rebuild`, {}).then(() => onRetry?.()).catch(() => undefined)}>
      Build the index again
    </button>
  );
  if (pending.state === "building") {
    const p = pending.progress;
    return (
      <Banner
        tone="info"
        title={`Preparing ${what}: indexing the saved evidence`}
        action={
          <button className="btn small" onClick={() => apiPost(`/investigations/${invId}/views/index/cancel`, {}).catch(() => undefined)}>
            Cancel
          </button>
        }
      >
        <span className="small">
          {p ? `${p.rows_done.toLocaleString()} of ${p.rows_total.toLocaleString()} rows (${p.elapsed_seconds}s). ` : ""}
          This runs once per evidence version; saved evidence is not changed. You can keep using other views.
        </span>
      </Banner>
    );
  }
  if (pending.state === "evidence_changing") {
    return (
      <Banner tone="info" title="Evidence is still being read or analyzed">
        <span className="small">This view opens when the current processing step finishes. Progress is under Run details.</span>
      </Banner>
    );
  }
  if (pending.state === "no_evidence") {
    return (
      <Banner tone="warn" title="No evidence has been saved for this investigation">
        <span className="small">Retrieval did not save any source rows. See Run details for what happened.</span>
      </Banner>
    );
  }
  if (pending.state === "cancelled") {
    return (
      <Banner tone="warn" title="Indexing was cancelled" action={retry}>
        <span className="small">Saved evidence, reviews and notes are unchanged. {what[0].toUpperCase() + what.slice(1)} opens once the index is built.</span>
      </Banner>
    );
  }
  return (
    <Banner tone="bad" title="The evidence index could not be built" action={retry}>
      <span className="small">{pending.error ?? "Unknown error."} Saved evidence is unchanged.</span>
    </Banner>
  );
}

const CHIP_LABELS: Record<string, string> = {
  sev: "Severity", review: "Review", origin: "Origin", host: "Host", src: "Source", from: "From", to: "To", untimed: "Untimed rows",
  ek: "Entity kind", ekey: "Entity", q: "Search", fq: "Finding text", cited: "Cited rows only",
};

export function FilterChips({ params, set, labels = {} }: { params: URLSearchParams; set: SetParams; labels?: Record<string, (v: string) => string> }) {
  const zone = useDisplayZone();
  const active = FILTER_KEYS.filter((k) => params.get(k));
  if (!active.length) return null;
  const show = (k: string, v: string) => {
    if (labels[k]) return labels[k](v);
    if (k === "from" || k === "to") return formatInstant(new Date(Number(v) / 1000).toISOString(), zone);
    if (k === "review" && v === "open") return "Needs review";
    if (k === "cited") return "yes";
    return v.replace(/_/g, " ");
  };
  const clear: Record<string, null> = Object.fromEntries([...FILTER_KEYS, "offset"].map((k) => [k, null]));
  return (
    <div className="chips" role="group" aria-label="Active filters">
      {active.map((k) => (
        <span key={k} className="chip">
          <span className="chip-key">{CHIP_LABELS[k] ?? k}:</span> <span dir="auto">{show(k, params.get(k) as string)}</span>
          <button type="button" className="chip-x" aria-label={`Remove filter ${CHIP_LABELS[k] ?? k}`} onClick={() => set({ [k]: null, offset: null, ...(k === "from" ? { to: null } : {}), ...(k === "ek" ? { ekey: null } : {}) })}>
            <X size={12} aria-hidden />
          </button>
        </span>
      ))}
      <button type="button" className="btn link small" onClick={() => set(clear)}>
        Clear filters
      </button>
      <span className="xsmall muted">Pins, notes and review decisions are kept.</span>
    </div>
  );
}

export { getField };

function schedule(row: Record<string, unknown>, fields: Record<string, string>): string {
  const ev = fields.event ? getField(row, fields.event) : undefined;
  if (typeof ev === "string" && ev) return ev;
  return ["minute", "hour", "dom", "month", "dow"].map((r) => (fields[r] ? String(getField(row, fields[r]) ?? "") : "")).join(" ").trim();
}

/** Evidence rows: source-specific columns for one source, a common summary across sources. */
export function RowsTable({ page, selected, onSelect, onPage, onExpand, label, empty, timeFirst = false }: { page: RowsPage; selected: string | null; onSelect: (eid: string) => void; onPage: (offset: number) => void; onExpand?: (eid: string, field: string) => void; label: string; empty?: ReactNode; timeFirst?: boolean }) {
  const zone = useDisplayZone();
  const src = page.source;
  const columns: ColumnDef<RowRecord>[] = useMemo(() => {
    const time: ColumnDef<RowRecord> = { key: "__time", label: "Event time", kind: "short", value: (r) => (r.event_time ? formatInstant(r.event_time, zone) : r.time_status === "ok" ? "" : "no event time") };
    if (src) {
      const plan = [...src.columns.default.map((c) => ({ ...c, hidden: false })), ...src.columns.other.map((c) => ({ ...c, hidden: true }))];
      const cols: ColumnDef<RowRecord>[] = plan.map((c) => ({
        key: c.key,
        label: c.label,
        kind: c.kind === "command" || c.kind === "path" ? "long" : c.kind === "number" || c.kind === "port" ? "number" : c.kind === "hash" ? "id" : "text",
        defaultHidden: c.hidden,
        value: c.virtual ? (r: RowRecord) => schedule(r.row, src.fields) : (r: RowRecord) => getField(r.row, c.key),
      }));
      return [
        { key: "__row", label: "Row", kind: "number", sticky: true, value: (r: RowRecord) => r.row_index },
        ...(timeFirst ? [time] : []),
        ...cols,
        { key: "__cited", label: "Findings", kind: "short", value: (r: RowRecord) => (r.cited_by.length ? r.cited_by.join(", ") : ""), defaultHidden: !page.records.some((r) => r.cited_by.length) },
        { key: "__eid", label: "Evidence ID", kind: "id", value: (r: RowRecord) => r.evidence_id, defaultHidden: true },
      ];
    }
    return [
      ...(timeFirst ? [time] : []),
      { key: "__title", label: "Row", kind: "text", sticky: true, value: (r: RowRecord) => r.title },
      { key: "__source", label: "Source", kind: "text", value: (r: RowRecord) => r.full_name },
      { key: "__host", label: "Host", kind: "short", value: (r: RowRecord) => r.hostname || r.client_id },
      ...(timeFirst ? [] : [time]),
      { key: "__cited", label: "Findings", kind: "short", value: (r: RowRecord) => r.cited_by.join(", ") },
      { key: "__eid", label: "Evidence ID", kind: "id", value: (r: RowRecord) => r.evidence_id, defaultHidden: true },
    ];
  }, [src, zone, timeFirst, page.records]);
  return (
    <DataTable
      id={src ? `rows-${src.mapping.id}-${src.full_name}` : "rows-mixed"}
      label={label}
      rows={page.records}
      columns={columns}
      rowKey={(r) => r.evidence_id}
      total={page.total}
      offset={page.offset}
      limit={page.limit}
      onPageChange={onPage}
      highlightKey={selected}
      selectedKey={selected}
      onRowClick={(r) => onSelect(r.evidence_id)}
      onCellExpand={onExpand ? (r, key) => onExpand(r.evidence_id, key) : undefined}
      rowLabel={(r) => `${r.title} (row ${r.row_index})`}
      totalNote={page.scan_truncated ? "search stopped after 250,000 rows; narrow the scope" : undefined}
      empty={empty}
    />
  );
}

export function LoadingRows({ loading }: { loading: boolean }) {
  return loading ? <Spinner label="Loading rows" /> : null;
}

/** Navigate to a server-provided view target inside the investigation. */
export function useOpenTarget(invId: string) {
  const navigate = useNavigate();
  return (target: ViewTarget | null | undefined, extra: Record<string, string> = {}) => {
    if (!target) return;
    const map: Record<string, string> = { severity: "sev", review: "review", client_id: "host", source_key: "src", origin: "origin" };
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...(target.filters ?? {}), ...extra })) qs.set(map[k] ?? k, v);
    if (target.mode) qs.set("mode", target.mode);
    const base = `/investigations/${invId}`;
    const path = target.view === "evidence" ? `${base}/evidence` : target.view === "hosts" ? `${base}/hosts` : target.view === "timeline" ? `${base}/timeline` : target.view === "run" ? `${base}/run` : target.view === "changes" ? `${base}/changes` : target.view === "report" ? `/reports` : `${base}?coverage=1`;
    if (target.view === "report") qs.set("investigation", invId);
    navigate(`${path}${qs.toString() ? `?${qs}` : ""}`);
  };
}
