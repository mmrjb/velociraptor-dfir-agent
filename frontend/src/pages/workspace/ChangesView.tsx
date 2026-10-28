// Changes: "What changed between these two observations?" A baseline is an existing saved
// capture of the same host and source; comparing never starts a collection. Only meaningful
// changes are listed by default; unchanged entries and ignored volatile differences are shown
// on request. Absence that cannot be established is Unknown, with the reason.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { Bookmark, BookmarkCheck } from "lucide-react";
import { apiGet, apiPost, errorMessage } from "../../api/client";
import type { ApiError, Capture, ChangeItem, ChangeSources, Comparison, ViewPending } from "../../api/types";
import { AiPanel } from "../../components/AiPanel";
import { BarList } from "../../components/charts";
import type { InspectorLevels } from "../../components/Inspector";
import { Badge, Banner, EmptyState, ErrorBanner, Pager, Spinner } from "../../components/ui";
import { exactText } from "../../components/values";
import { formatTime } from "../../lib/format";
import { useApi } from "../../lib/useApi";
import { useUrlState } from "../../lib/urlState";
import { WorkspaceInspector } from "./InspectorExtras";
import { useWorkspace } from "./shared";

const STATE_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "neutral"> = { added: "info", removed: "bad", modified: "warn", unchanged: "neutral", unknown: "neutral" };
const LEVELS: InspectorLevels = { allFields: false, advanced: false, related: false };

function captureLine(c: Capture) {
  const read = c.read_status === "complete" ? (c.result_status === "rows" ? "read completely" : c.result_status.replace(/_/g, " ")) : `read ${c.read_status.replace(/_/g, " ")}`;
  return `${formatTime(c.collected_at)} · flow ${c.flow_id} · ${c.persisted_rows} rows · ${read}`;
}

export function ChangesView() {
  const ws = useWorkspace();
  const { invId } = ws;
  const [params, set] = useUrlState();
  const sourcesQ = useApi<ChangeSources>(`/investigations/${invId}/changes/sources`);
  const list = sourcesQ.data?.sources ?? [];
  const usable = list.filter((s) => s.default_baseline);
  const src = list.find((s) => s.source_key === params.get("src")) ?? usable[0] ?? null;
  const baseKey = params.get("base");
  const baseline = src ? src.baselines.find((b) => `${b.investigation_id}|${b.source_key}` === baseKey) ?? src.default_baseline : null;
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [pending, setPending] = useState<ViewPending | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setCmp(null);
    setError(null);
    if (!src || !baseline) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = (res: { state: string; comparison_id?: string; progress?: ViewPending["progress"]; error?: string } & Partial<Comparison>) => {
      if (!alive) return;
      if (res.state === "ready") {
        setCmp(res as Comparison);
        setPending(null);
      } else if (res.state === "building") {
        setPending({ state: "building", progress: res.progress });
        timer = setTimeout(() => apiGet<typeof res>(`/investigations/${invId}/changes/${res.comparison_id}`).then(poll).catch((e) => alive && setError(errorMessage(e))), 700);
      } else {
        setPending(null);
        setError({ code: "comparison_failed", message: res.error ?? "The comparison failed." });
      }
    };
    apiPost<Parameters<typeof poll>[0]>(`/investigations/${invId}/changes`, { source_key: src.source_key, baseline_investigation_id: baseline.investigation_id, baseline_source_key: baseline.source_key })
      .then(poll)
      .catch((e) => alive && setError(errorMessage(e)));
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [invId, src?.source_key, baseline?.investigation_id, baseline?.source_key]); // eslint-disable-line react-hooks/exhaustive-deps

  if (sourcesQ.error) return <ErrorBanner error={sourcesQ.error} onRetry={sourcesQ.reload} />;
  if (!sourcesQ.data) return <Spinner />;
  if (!usable.length) {
    return (
      <div className="stack">
        <Banner tone="info" title="No earlier capture to compare with">
          <span className="small">
            Changes compare two saved captures of the same host and source. No source in this investigation has an earlier saved capture in this organization yet. Analyze an earlier existing collection of this host (or import its bundle) to compare; nothing is collected for this.
          </span>
        </Banner>
        {list.length > 0 && (
          <p className="small muted">Comparable sources here: {list.map((s) => s.label).join(", ")}.</p>
        )}
      </div>
    );
  }
  return (
    <div className="stack">
      <div className="card tight stack-sm">
        <div className="row">
          {list.length > 1 && (
            <label className="field small grow">
              What to compare
              <select value={src?.source_key ?? ""} onChange={(e) => set({ src: e.target.value, base: null, cmp: null, state: null, item: null, offset: null })}>
                {list.map((s) => (
                  <option key={s.source_key} value={s.source_key} disabled={!s.default_baseline}>
                    {s.label}
                    {s.default_baseline ? "" : " — no earlier capture"}
                  </option>
                ))}
              </select>
            </label>
          )}
          {src && src.baselines.length > 1 && (
            <label className="field small grow">
              Baseline (earlier saved capture)
              <select value={baseline ? `${baseline.investigation_id}|${baseline.source_key}` : ""} onChange={(e) => set({ base: e.target.value, state: null, item: null, offset: null })}>
                {src.baselines.map((b, i) => (
                  <option key={`${b.investigation_id}|${b.source_key}`} value={`${b.investigation_id}|${b.source_key}`}>
                    {formatTime(b.collected_at)} — flow {b.flow_id}
                    {i === 0 ? " (latest earlier capture)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {src && baseline && (
          <dl className="pair">
            <div>
              <dt>Before (baseline)</dt>
              <dd>{captureLine(baseline)}</dd>
            </div>
            <div>
              <dt>After (this investigation)</dt>
              <dd>{captureLine(src.current)}</dd>
            </div>
            <div>
              <dt>Host and source</dt>
              <dd dir="auto">
                {src.current.hostname || src.current.client_id} · {src.adapter_label} <span className="xsmall muted mono">{src.full_name}</span>
              </dd>
            </div>
          </dl>
        )}
      </div>
      <ErrorBanner error={error} />
      {pending && <Banner tone="info" title="Comparing the saved captures…">{pending.progress ? `${pending.progress.rows_done.toLocaleString()} of ${pending.progress.rows_total.toLocaleString()} rows` : null}</Banner>}
      {cmp && <ComparisonResult invId={invId} cmp={cmp} params={params} set={set} ws={ws} />}
    </div>
  );
}

function ComparisonResult({ invId, cmp, params, set, ws }: { invId: string; cmp: Comparison; params: URLSearchParams; set: ReturnType<typeof useUrlState>[1]; ws: ReturnType<typeof useWorkspace> }) {
  const state = params.get("state");
  const unchanged = params.get("unchanged") === "1";
  const ignored = params.get("ignored") === "1";
  const offset = Number(params.get("offset") ?? 0);
  const itemsQ = useApi<{ items: ChangeItem[]; total: number; offset: number; limit: number }>(`/investigations/${invId}/changes/${cmp.comparison_id}/items`, { state, unchanged, ignored, offset, limit: 50 });
  const itemParam = params.get("item");
  const inPage = itemsQ.data?.items.find((i) => i.item_key === itemParam) ?? null;
  // A selected change outside the current page or filter (for example opened from a pin) is
  // fetched by its key, so the detail always shows that exact change.
  const directQ = useApi<ChangeItem>(itemParam && itemsQ.data && !inPage ? `/investigations/${invId}/changes/${cmp.comparison_id}/items/${encodeURIComponent(itemParam)}` : null);
  const selected = inPage ?? (directQ.data?.item_key === itemParam ? directQ.data : null);
  const [side, setSide] = useState<{ inv: string; eid: string } | null>(null);
  const [levels, setLevels] = useState<InspectorLevels>(LEVELS);
  const s = cmp.summary;
  const d = cmp.definition;
  const bars = useMemo(
    () => (["modified", "added", "removed", "unknown"] as const).map((k) => ({ key: k, label: k[0].toUpperCase() + k.slice(1), count: s.counts[k], filter: { state: k } })),
    [s],
  );
  useEffect(() => setSide(null), [params.get("item")]); // eslint-disable-line react-hooks/exhaustive-deps
  const meaningful = s.counts.added + s.counts.removed + s.counts.modified;
  return (
    <div className="stack">
      {s.state === "incomparable" ? (
        <Banner tone="warn" title="These captures cannot be compared">
          {s.incomparable_reason}
        </Banner>
      ) : (
        <div className="grid cols-2">
          <section className="card" aria-label="Change summary">
            <h2>What changed?</h2>
            <p className="xsmall muted">Counting entities ({d.key_roles.join(" + ")}). Unchanged: {s.counts.unchanged}.</p>
            <BarList bars={bars} unit="entities" selected={state} onSelect={(b) => set({ state: state === b.key ? null : b.key, offset: null, item: null })} label="Changes by type; select one to filter the list" />
            {meaningful === 0 && s.counts.unknown === 0 && <p className="small">No meaningful changes in the compared fields. Unchanged entries are available below.</p>}
          </section>
          <section className="card" aria-label="Comparison limits">
            <h2>Limits of this comparison</h2>
            {s.limitations.length ? (
              <ul className="small">
                {s.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            ) : (
              <p className="small">Both captures were read completely and their saved rows were verified.</p>
            )}
            {s.unknown_reasons.length > 0 && (
              <details className="advanced">
                <summary>Why some entries are unknown</summary>
                <ul className="small">
                  {s.unknown_reasons.map((u) => (
                    <li key={u.reason}>
                      {u.count} × {u.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <p className="xsmall muted">
              Compared fields: {d.compared_roles.join(", ") || "none"}. Ignored as volatile: {d.volatile_roles.join(", ") || "none"}; other fields are not compared. Policy {d.policy_version}.
            </p>
          </section>
        </div>
      )}
      <div className="row">
        <label className="check small">
          <input type="checkbox" checked={unchanged} onChange={(e) => set({ unchanged: e.target.checked ? "1" : null, offset: null })} /> Show unchanged entries
        </label>
        <label className="check small">
          <input type="checkbox" checked={ignored} onChange={(e) => set({ ignored: e.target.checked ? "1" : null, offset: null })} /> Include entries that differ only in ignored fields ({s.unchanged_with_ignored_differences})
        </label>
        {state && (
          <button className="btn link small" onClick={() => set({ state: null, offset: null })}>
            Clear filter
          </button>
        )}
      </div>
      <div className="evidence-layout has-detail">
        <div className="evidence-list">
          <ErrorBanner error={itemsQ.error} />
          <ErrorBanner error={directQ.error} />
          {!itemsQ.data ? (
            <Spinner />
          ) : itemsQ.data.items.length === 0 ? (
            <EmptyState title={state ? "No entries in this category." : "No meaningful changes."} />
          ) : (
            <>
              <ul className="plain change-list" aria-label="Changes">
                {itemsQ.data.items.map((i) => (
                  <li key={i.item_key}>
                    <button className={`list-item ${selected?.item_key === i.item_key ? "selected" : ""}`} onClick={() => set({ item: i.item_key }, { replace: true })} aria-current={selected?.item_key === i.item_key ? "true" : undefined}>
                      <div className="row">
                        <Badge tone={STATE_TONE[i.state]}>{i.state_label}</Badge>
                        <span className="li-title" dir="auto">
                          {i.key_display}
                        </span>
                      </div>
                      {i.changed.length > 0 && <div className="xsmall muted">changed: {i.changed.join(", ")}</div>}
                      {i.reason && <div className="xsmall muted">{i.reason}</div>}
                      {i.ignored_diff && i.state === "unchanged" && <div className="xsmall muted">differs only in ignored fields</div>}
                    </button>
                  </li>
                ))}
              </ul>
              <Pager offset={itemsQ.data.offset} limit={itemsQ.data.limit} total={itemsQ.data.total} onChange={(o) => set({ offset: String(o) })} />
            </>
          )}
          {ws.aiEnabled && meaningful + s.counts.unknown > 0 && (
            <AiPanel path={`/investigations/${invId}/changes/${cmp.comparison_id}/explain`} actions={[{ id: "changes_summary", label: "Summarize the selected changes", body: {} }]} />
          )}
        </div>
        <div className="evidence-detail">
          {selected ? (
            <ChangeDetail invId={invId} cmp={cmp} item={selected} ws={ws} onOpen={(inv, eid) => setSide({ inv, eid })} />
          ) : (
            <div className="card hint">
              <p className="small muted">Select a change to see the before and after values and open both original records.</p>
            </div>
          )}
          {side && (
            <WorkspaceInspector ws={ws} invId={side.inv} evidenceId={side.eid} levels={levels} onLevels={setLevels} onClose={() => setSide(null)} onOpenEvidence={(eid) => setSide({ inv: side.inv, eid })} back={side.inv !== invId ? { label: "Baseline record (from the earlier investigation)", onClick: () => setSide(null) } : null} />
          )}
        </div>
      </div>
    </div>
  );
}

function ChangeDetail({ invId, cmp, item, ws, onOpen }: { invId: string; cmp: Comparison; item: ChangeItem; ws: ReturnType<typeof useWorkspace>; onOpen: (inv: string, eid: string) => void }) {
  const ref = `${cmp.comparison_id}|${item.item_key}`;
  const pinned = ws.pins.isPinned("change", ref);
  const keys = [...new Set([...Object.keys(item.before_values ?? {}), ...Object.keys(item.after_values ?? {})])];
  const baseInv = cmp.definition.baseline.investigation_id;
  return (
    <section className="card" aria-label="Change detail">
      <div className="card-head">
        <h2 dir="auto">
          {item.state_label}: {item.key_display}
        </h2>
        <button className="btn small" aria-pressed={!!pinned} onClick={() => (pinned ? ws.pins.unpin(pinned) : ws.pins.pin({ kind: "change", comparison_id: cmp.comparison_id, item_key: item.item_key }))}>
          {pinned ? <BookmarkCheck size={14} aria-hidden /> : <Bookmark size={14} aria-hidden />} {pinned ? "Pinned" : "Pin to report"}
        </button>
      </div>
      {item.reason && <p className="small">{item.reason}</p>}
      {keys.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Field</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k} className={item.changed.includes(k) ? "changed-row" : undefined}>
                  <th scope="row">
                    {k}
                    {item.changed.includes(k) ? <span className="sr-only"> (changed)</span> : null}
                  </th>
                  <td className="mono" dir="auto">
                    {item.before_values ? exactText(item.before_values[k]) : "—"}
                  </td>
                  <td className="mono" dir="auto">
                    {item.after_values ? exactText(item.after_values[k]) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="row">
        {item.before_eids.map((e, i) => (
          <button key={e} className="btn small" onClick={() => onOpen(baseInv, e)}>
            Open baseline record{item.before_eids.length > 1 ? ` ${i + 1}` : ""}
          </button>
        ))}
        {item.after_eids.map((e, i) => (
          <button key={e} className="btn small" onClick={() => onOpen(invId, e)}>
            Open current record{item.after_eids.length > 1 ? ` ${i + 1}` : ""}
          </button>
        ))}
      </div>
      <p className="xsmall muted">
        Both records are the saved original rows. The baseline record belongs to <Link to={`/investigations/${baseInv}`}>an earlier investigation</Link>.
      </p>
    </section>
  );
}
