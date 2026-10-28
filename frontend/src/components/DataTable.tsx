// Shared table for evidence rows, coverage lists and drill-downs.
//
// * Server-side paging only: the table renders the current page and asks for another offset;
//   it never loads a whole hunt or result table into the browser.
// * Column selection and the wrap mode are per-viewer conveniences kept in localStorage
//   (wrapped in try/catch; the table works without storage).
// * Sticky header (and optional sticky first column), horizontal scrolling inside the table.
// * Long values are never tooltip-only: every truncated cell has a "show full value" button
//   (see ValueCell) with an exact copy action. Evidence tables pass onCellExpand so the button
//   opens the row in the single inspector at that field instead of a separate dialog.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { Columns3, WrapText } from "lucide-react";
import { Pager } from "./ui";
import { ValueCell, valueKind } from "./values";

export type ColumnKind = "id" | "number" | "short" | "text" | "long" | "json";

export interface ColumnDef<R> {
  key: string;
  label?: string;
  kind?: ColumnKind;
  value?: (row: R) => unknown;
  render?: (row: R) => ReactNode;
  sticky?: boolean;
  defaultHidden?: boolean;
}

export interface DataTableProps<R> {
  id: string;
  label: string;
  rows: R[];
  columns: ColumnDef<R>[];
  rowKey: (row: R, index: number) => string;
  total?: number | null;
  offset?: number;
  limit?: number;
  onPageChange?: (offset: number) => void;
  highlightKey?: string | null;
  selectedKey?: string | null;
  onRowClick?: (row: R) => void;
  rowLabel?: (row: R) => string;
  rowActions?: (row: R) => ReactNode;
  empty?: ReactNode;
  toolbarExtra?: ReactNode;
  maxHeight?: string;
  totalNote?: string;
  /** For server tables without a total: whether another page exists. */
  hasMore?: boolean;
  /** Open the row's full value in the inspector (instead of a per-field dialog). */
  onCellExpand?: (row: R, key: string) => void;
}

interface Prefs {
  hidden: string[] | null;
  wrap: boolean;
}

function loadPrefs(id: string): Prefs {
  try {
    const raw = window.localStorage.getItem(`vrdfir.table.${id}`);
    if (raw) {
      const p = JSON.parse(raw) as Partial<Prefs>;
      return { hidden: Array.isArray(p.hidden) ? p.hidden.filter((x) => typeof x === "string") : null, wrap: !!p.wrap };
    }
  } catch {
    /* storage unavailable: defaults */
  }
  return { hidden: null, wrap: false };
}

function savePrefs(id: string, prefs: Prefs) {
  try {
    window.localStorage.setItem(`vrdfir.table.${id}`, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: preferences last for this page only */
  }
}

const LONG_NAME = /(path|exe|commandline|cmdline|command|description|content|message|hypothesis|checks|limit|rawline|reason|indicator|action|evidence[1-4]?|status$)/i;
const ID_NAME = /(^|_)(id|key)$|Id$|ID$|Key$|hash|sha256|md5/;

/** Infer columns from records: union of keys in first-seen order, kind from name and values. */
export function inferColumns<R extends Record<string, unknown>>(rows: R[], opts: { exclude?: string[] } = {}): ColumnDef<R>[] {
  const order: string[] = [];
  for (const r of rows) for (const k of Object.keys(r ?? {})) if (!order.includes(k) && !(opts.exclude ?? []).includes(k)) order.push(k);
  return order.map((key) => ({ key, kind: inferKind(key, rows.map((r) => r?.[key])) }));
}

export function inferKind(key: string, values: unknown[]): ColumnKind {
  const present = values.filter((v) => v !== undefined && v !== null && v !== "");
  if (present.length && present.every((v) => valueKind(v) === "number")) return "number";
  if (present.some((v) => valueKind(v) === "array" || valueKind(v) === "object")) return "json";
  if (ID_NAME.test(key)) return "id";
  if (LONG_NAME.test(key)) return "long";
  const maxLen = Math.max(0, ...present.map((v) => String(v).length));
  if (maxLen <= 16) return "short";
  return maxLen > 80 ? "long" : "text";
}

function cellValue<R>(col: ColumnDef<R>, row: R): unknown {
  if (col.value) return col.value(row);
  return (row as Record<string, unknown>)?.[col.key];
}

export function DataTable<R>(props: DataTableProps<R>) {
  const { id, label, rows, columns, rowKey, total, offset = 0, limit, onPageChange, highlightKey, selectedKey, onRowClick, rowLabel, rowActions, empty, toolbarExtra, maxHeight, totalNote, hasMore, onCellExpand } = props;
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs(id));
  const scrollRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDetailsElement>(null);

  const hidden = useMemo(() => new Set(prefs.hidden ?? columns.filter((c) => c.defaultHidden).map((c) => c.key)), [prefs.hidden, columns]);
  const visible = columns.filter((c) => !hidden.has(c.key));

  const update = (next: Prefs) => {
    setPrefs(next);
    savePrefs(id, next);
  };
  const toggleColumn = (key: string, show: boolean) => {
    const set = new Set(hidden);
    if (show) set.delete(key);
    else set.add(key);
    update({ ...prefs, hidden: [...set] });
  };

  useEffect(() => {
    if (!highlightKey || !scrollRef.current) return;
    const el = scrollRef.current.querySelector<HTMLTableRowElement>(`tr[data-row-key="${CSS.escape(highlightKey)}"]`);
    if (el) {
      el.scrollIntoView?.({ block: "center" });
      el.focus({ preventScroll: true });
    }
  }, [highlightKey, rows]);

  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, row: R) => {
    if (!onRowClick || e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onRowClick(row);
    }
  };

  const start = rows.length ? offset + 1 : 0;
  const end = offset + rows.length;
  return (
    <div className={`dt ${prefs.wrap ? "wrap-mode" : "nowrap-mode"}`}>
      <div className="dt-toolbar">
        <details
          className="dt-columns"
          ref={menuRef}
          onKeyDown={(e) => {
            if (e.key === "Escape" && menuRef.current?.open) {
              menuRef.current.open = false;
              (menuRef.current.querySelector("summary") as HTMLElement | null)?.focus();
            }
          }}
        >
          <summary className="btn small" aria-label={`Choose columns for ${label}`}>
            <Columns3 size={14} aria-hidden /> Columns ({visible.length} of {columns.length})
          </summary>
          <div className="dt-columns-menu" role="group" aria-label="Visible columns">
            <div className="row">
              <button type="button" className="btn small" onClick={() => update({ ...prefs, hidden: [] })}>
                Show all
              </button>
              <button type="button" className="btn small" onClick={() => update({ ...prefs, hidden: null })}>
                Reset
              </button>
            </div>
            {columns.map((c) => (
              <label key={c.key} className="check small">
                <input type="checkbox" checked={!hidden.has(c.key)} onChange={(e) => toggleColumn(c.key, e.target.checked)} />
                <span style={{ overflowWrap: "anywhere" }}>{c.label ?? c.key}</span>
              </label>
            ))}
          </div>
        </details>
        <button type="button" className="btn small" aria-pressed={prefs.wrap} onClick={() => update({ ...prefs, wrap: !prefs.wrap })}>
          <WrapText size={14} aria-hidden /> Wrap text
        </button>
        {toolbarExtra}
        <span className="spacer" />
        <span className="muted">
          {total === null || total === undefined
            ? rows.length
              ? `Rows ${start}–${end}`
              : ""
            : total === 0
              ? "0 rows"
              : `Rows ${start}–${end} of ${total}`}
          {totalNote ? ` · ${totalNote}` : ""}
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="card tight">{empty ?? <p className="muted">No rows.</p>}</div>
      ) : (
        <div className="dt-scroll" ref={scrollRef} tabIndex={0} role="region" aria-label={`${label} (scrollable table)`} style={maxHeight ? ({ "--dt-max-height": maxHeight } as CSSProperties) : undefined}>
          <table aria-label={label}>
            <thead>
              <tr>
                {visible.map((c) => (
                  <th key={c.key} scope="col" className={`${c.sticky ? "sticky-col" : ""} colw-${c.kind ?? "text"}`}>
                    {c.label ?? c.key}
                  </th>
                ))}
                {rowActions && (
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const key = rowKey(row, i);
                const isHighlight = highlightKey === key;
                const isSelected = selectedKey === key;
                return (
                  <tr
                    key={key}
                    data-row-key={key}
                    className={`${isHighlight ? "highlight" : ""} ${isSelected ? "selected" : ""} ${onRowClick ? "clickable" : ""}`}
                    tabIndex={onRowClick ? 0 : isHighlight ? -1 : undefined}
                    aria-selected={onRowClick ? isSelected : undefined}
                    aria-label={rowLabel ? rowLabel(row) : undefined}
                    aria-current={isHighlight ? "true" : undefined}
                    onClick={onRowClick ? (e) => {
                      if ((e.target as HTMLElement).closest("button, a, input, select, summary")) return;
                      onRowClick(row);
                    } : undefined}
                    onKeyDown={(e) => onRowKey(e, row)}
                  >
                    {visible.map((c) => (
                      <td key={c.key} className={`${c.sticky ? "sticky-col" : ""} k-${c.kind ?? "text"} colw-${c.kind ?? "text"}`}>
                        {c.render ? c.render(row) : <ValueCell value={cellValue(c, row)} label={c.label ?? c.key} onExpand={onCellExpand && !c.key.startsWith("__") ? () => onCellExpand(row, c.key) : undefined} />}
                      </td>
                    ))}
                    {rowActions && <td className="nowrap">{rowActions(row)}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {onPageChange && limit && total !== null && total !== undefined && total > limit && <Pager offset={offset} limit={limit} total={total} onChange={onPageChange} />}
      {onPageChange && limit && (total === null || total === undefined) && (
        <div className="pager" aria-label="Pagination">
          <button className="btn small" disabled={offset === 0} onClick={() => onPageChange(Math.max(0, offset - limit))}>
            Previous
          </button>
          <button className="btn small" disabled={hasMore === undefined ? rows.length < limit : !hasMore} onClick={() => onPageChange(offset + limit)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}
