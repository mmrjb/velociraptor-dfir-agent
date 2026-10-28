// Small, dependency-free charts drawn from server-side aggregates. Every chart has a text
// equivalent (legend or table) that is also its keyboard and touch control, a hover/focus
// tooltip that never gates a value, and a stated counting unit. Selecting a mark calls
// onSelect with that mark's structured filter; charts never fetch data themselves.
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ChartBar, ChartSegment, HistogramBucket } from "../api/types";
import { formatInstant, type DisplayZone } from "../lib/time";

const nf = new Intl.NumberFormat();

function Tooltip({ tip }: { tip: { x: number; y: number; body: ReactNode } | null }) {
  if (!tip) return null;
  return (
    <div className="chart-tip" role="presentation" style={{ left: tip.x, top: tip.y }}>
      {tip.body}
    </div>
  );
}

/** Part-to-whole as one horizontal stacked bar plus a legend list of buttons. */
export function PartBar({ segments, total, unit, palette, selected, onSelect, label }: { segments: ChartSegment[]; total: number; unit: string; palette: string; selected?: string | null; onSelect: (s: ChartSegment) => void; label: string }) {
  const [tip, setTip] = useState<{ x: number; y: number; body: ReactNode } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const show = (s: ChartSegment, el: HTMLElement) => {
    const box = el.getBoundingClientRect();
    const base = wrap.current?.getBoundingClientRect();
    setTip({ x: box.left - (base?.left ?? 0) + box.width / 2, y: -6, body: <><strong>{nf.format(s.count)}</strong> {s.label} <span className="muted">of {nf.format(total)} {unit}</span></> });
  };
  return (
    <div className="part" ref={wrap}>
      {total > 0 ? (
        <div className="part-bar" aria-hidden>
          {segments.map((s) => (
            <span
              key={s.key}
              className={`part-seg ${palette}-${s.key} ${selected === s.key ? "is-selected" : ""}`}
              style={{ flexGrow: s.count }}
              onPointerEnter={(e) => show(s, e.currentTarget)}
              onPointerLeave={() => setTip(null)}
              onClick={() => onSelect(s)}
            />
          ))}
        </div>
      ) : (
        <div className="part-bar empty" aria-hidden />
      )}
      <Tooltip tip={tip} />
      <ul className="legend" aria-label={label}>
        {segments.map((s) => (
          <li key={s.key}>
            <button type="button" className={`legend-row ${selected === s.key ? "is-selected" : ""}`} aria-pressed={selected === s.key} onClick={() => onSelect(s)} onFocus={(e) => show(s, e.currentTarget)} onBlur={() => setTip(null)}>
              <span className={`swatch ${palette}-${s.key}`} aria-hidden />
              <span className="legend-label">{s.label}</span>
              <span className="legend-value">{nf.format(s.count)}</span>
              <span className="legend-share muted">{total ? `${Math.round((100 * s.count) / total)}%` : ""}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Horizontal comparison bars; each row is a button (label, bar, value, context). */
export function BarList({ bars, unit, onSelect, selected, label }: { bars: ChartBar[]; unit: string; onSelect?: (b: ChartBar) => void; selected?: string | null; label: string }) {
  const max = Math.max(1, ...bars.map((b) => b.count));
  return (
    <ul className="bars" aria-label={label}>
      {bars.map((b) => {
        const body = (
          <>
            <span className="bar-label" dir="auto" title={b.label}>
              {b.label}
            </span>
            <span className="bar-track" aria-hidden>
              <span className="bar-fill" style={{ width: `${(100 * b.count) / max}%` }} />
            </span>
            <span className="bar-value">{nf.format(b.count)}</span>
            {b.secondary && <span className="bar-secondary muted">{b.secondary}</span>}
          </>
        );
        return (
          <li key={b.key}>
            {onSelect ? (
              <button type="button" className={`bar-row ${selected === b.key ? "is-selected" : ""}`} aria-pressed={selected === b.key} aria-label={`${b.label}: ${b.count} ${unit}${b.secondary ? `, ${b.secondary}` : ""}`} onClick={() => onSelect(b)}>
                {body}
              </button>
            ) : (
              <div className="bar-row">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function useWidth(ref: React.RefObject<HTMLElement | null>, fallback = 560) {
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(Math.max(200, el.clientWidth || fallback));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, fallback]);
  return w;
}

/**
 * Event-count histogram. A zero bucket is a measured zero only when its data is complete;
 * otherwise it is drawn as a possible gap. The pointer snaps to the nearest bucket, arrow keys
 * move between buckets, and Enter selects one. A table view lists every bucket.
 */
export function Histogram({ buckets, unit, zone, selectedFrom, onSelect, height = 132, label, compact = false }: { buckets: HistogramBucket[]; unit: string; zone: DisplayZone; selectedFrom?: number | null; onSelect?: (b: HistogramBucket) => void; height?: number; label: string; compact?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [active, setActive] = useState<number | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const left = 34;
  const plotW = Math.max(40, width - left - 4);
  const top = 8;
  const plotH = height - 26 - top;
  const n = Math.max(1, buckets.length);
  const band = plotW / n;
  const barW = Math.max(1, Math.min(24, band - 2));
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const ticks = [0, max];
  useEffect(() => setActive(null), [buckets]);
  const at = (clientX: number) => {
    const rect = box.current?.querySelector("svg")?.getBoundingClientRect();
    if (!rect) return null;
    const i = Math.floor((clientX - rect.left - left) / band);
    return i >= 0 && i < n ? i : null;
  };
  const onKey = (e: KeyboardEvent) => {
    if (!buckets.length) return;
    const cur = active ?? 0;
    if (e.key === "ArrowRight") setActive(Math.min(n - 1, cur + 1));
    else if (e.key === "ArrowLeft") setActive(Math.max(0, cur - 1));
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(n - 1);
    else if ((e.key === "Enter" || e.key === " ") && active !== null && onSelect) onSelect(buckets[active]);
    else return;
    e.preventDefault();
  };
  const tipFor = (i: number) => {
    const b = buckets[i];
    return (
      <>
        <strong>{b.count > 0 || b.measured ? nf.format(b.count) : "possible gap"}</strong> {b.count > 0 || b.measured ? unit : ""}
        <div className="muted">
          {formatInstant(b.start, zone)} – {formatInstant(b.end, zone)}
        </div>
      </>
    );
  };
  return (
    <div className="histo" ref={box}>
      <div
        className="histo-plot"
        tabIndex={0}
        role="group"
        aria-label={`${label}. Use the arrow keys to move between time buckets and Enter to select one.`}
        onKeyDown={onKey}
        onBlur={() => setActive(null)}
      >
        <svg width={width} height={height} role="img" aria-hidden>
          <line x1={left} x2={left + plotW} y1={top + plotH} y2={top + plotH} className="axis" />
          {ticks.map((t) => (
            <text key={t} x={left - 6} y={top + plotH - (plotH * t) / max + 4} className="tick" textAnchor="end">
              {nf.format(t)}
            </text>
          ))}
          {buckets.map((b, i) => {
            const x = left + i * band + (band - barW) / 2;
            const h = (plotH * b.count) / max;
            const sel = selectedFrom === b.from_us;
            return (
              <g key={b.from_us}>
                {!b.measured && b.count === 0 && <rect x={left + i * band} y={top} width={band} height={plotH} className="gap" />}
                {b.count > 0 && <rect x={x} y={top + plotH - Math.max(2, h)} width={barW} height={Math.max(2, h)} rx={Math.min(4, barW / 2)} className={`col ${sel ? "is-selected" : ""} ${active === i ? "is-active" : ""}`} />}
              </g>
            );
          })}
          {active !== null && <line x1={left + active * band + band / 2} x2={left + active * band + band / 2} y1={top} y2={top + plotH} className="crosshair" />}
          {buckets.length > 0 && (
            <>
              <text x={left} y={height - 6} className="tick" textAnchor="start">
                {formatInstant(buckets[0].start, zone, false)}
              </text>
              <text x={left + plotW} y={height - 6} className="tick" textAnchor="end">
                {formatInstant(buckets[buckets.length - 1].end, zone, false)}
              </text>
            </>
          )}
          <rect
            x={left}
            y={0}
            width={plotW}
            height={height}
            fill="transparent"
            style={{ cursor: onSelect ? "pointer" : "default", touchAction: "manipulation" }}
            onPointerMove={(e) => setActive(at(e.clientX))}
            onPointerLeave={() => setActive(null)}
            onClick={(e) => {
              const i = at(e.clientX);
              if (i !== null && onSelect) onSelect(buckets[i]);
            }}
          />
        </svg>
        {active !== null && buckets[active] && (
          <div className="chart-tip" role="status" style={{ left: Math.min(width - 120, Math.max(60, left + active * band + band / 2)), top: 0 }}>
            {tipFor(active)}
          </div>
        )}
      </div>
      {!compact && (
        <details className="advanced" open={tableOpen} onToggle={(e) => setTableOpen((e.target as HTMLDetailsElement).open)}>
          <summary>Show as a table</summary>
          {tableOpen && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>From</th>
                    <th>To</th>
                    <th className="num">{unit}</th>
                    <th>
                      <span className="sr-only">Select</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {buckets.filter((b) => b.count > 0 || !b.measured).map((b) => (
                    <tr key={b.from_us}>
                      <td className="nowrap">{formatInstant(b.start, zone)}</td>
                      <td className="nowrap">{formatInstant(b.end, zone)}</td>
                      <td className="num">{b.count > 0 || b.measured ? nf.format(b.count) : "possible gap"}</td>
                      <td>
                        {onSelect && b.count > 0 && (
                          <button className="btn small" onClick={() => onSelect(b)}>
                            Show rows
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="xsmall muted">Buckets with no rows that are measured zeros are omitted from this table.</p>
            </div>
          )}
        </details>
      )}
    </div>
  );
}
