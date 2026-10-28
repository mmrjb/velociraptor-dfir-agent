// Content-aware rendering of evidence values in the inspector (the frontend side of the
// renderer registry). The server decides the kind of a field from the source mapping; this
// file only draws it. Every value is React text (never HTML), keeps its exact original for
// Copy, and highlights only the spans a finding's rule actually matched.
import { useMemo, useState, type ReactNode } from "react";
import type { AddressInfo, Highlight } from "../api/types";
import { formatInstant, type DisplayZone } from "../lib/time";
import { CopyButton, exactText, isBigInt, summarize, valueKind } from "./values";

interface Zone {
  start: number;
  end: number;
  cls: string;
}

/** Text split at span boundaries; highlight spans become <mark>, other zones get classes. */
export function Segmented({ text, marks = [], zones = [] }: { text: string; marks?: Highlight[]; zones?: Zone[] }) {
  const ranges = marks.map((m) => [m.start ?? 0, m.end ?? text.length] as [number, number]).filter(([a, b]) => b > a && a < text.length);
  if (!ranges.length && !zones.length) return <>{text}</>;
  const cuts = new Set<number>([0, text.length]);
  for (const [a, b] of ranges) {
    cuts.add(Math.max(0, a));
    cuts.add(Math.min(text.length, b));
  }
  for (const z of zones) {
    cuts.add(Math.max(0, z.start));
    cuts.add(Math.min(text.length, z.end));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const out: ReactNode[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const [a, b] = [points[i], points[i + 1]];
    if (b <= a) continue;
    const piece = text.slice(a, b);
    const cls = zones.filter((z) => z.start <= a && z.end >= b).map((z) => z.cls).join(" ");
    const marked = ranges.some(([x, y]) => x <= a && y >= b);
    out.push(
      marked ? (
        <mark key={a} className={`hl ${cls}`} title="Matched by the rule of a finding that cites this row">
          {piece}
        </mark>
      ) : cls ? (
        <span key={a} className={cls}>
          {piece}
        </span>
      ) : (
        piece
      ),
    );
  }
  return <>{out}</>;
}

/** Index after the executable token (quotes respected). Display only; the text is unchanged. */
export function executableEnd(cmd: string): number {
  let quote: string | null = null;
  let i = 0;
  while (i < cmd.length && /\s/.test(cmd[i])) i++;
  for (; i < cmd.length; i++) {
    const c = cmd[i];
    if (quote) {
      if (c === quote) quote = null;
    } else if (c === '"' || c === "'") quote = c;
    else if (/\s/.test(c)) return i;
  }
  return cmd.length;
}

export function CommandValue({ text, marks }: { text: string; marks: Highlight[] }) {
  const end = executableEnd(text);
  return (
    <code className="cmd" dir="ltr">
      <Segmented text={text} marks={marks} zones={[{ start: 0, end, cls: "cmd-exe" }]} />
    </code>
  );
}

export function PathValue({ text, marks }: { text: string; marks: Highlight[] }) {
  const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\")) + 1;
  return (
    <code className="path" dir="ltr">
      <Segmented text={text} marks={marks} zones={[{ start: 0, end: cut, cls: "path-dir" }, { start: cut, end: text.length, cls: "path-base" }]} />
    </code>
  );
}

export function AddressValue({ value, info, marks }: { value: unknown; info?: AddressInfo; marks: Highlight[] }) {
  const text = exactText(value);
  return (
    <span className="addr">
      <code dir="ltr">
        <Segmented text={text} marks={marks} />
      </code>
      {info && (
        <span className="addr-class" title={`Basis: ${info.basis}. This describes the address range only, not intent or reputation.`}>
          {info.label}
        </span>
      )}
    </span>
  );
}

export function TimestampValue({ value, zone }: { value: unknown; zone: DisplayZone }) {
  if (typeof value !== "string") return <PlainValue value={value} marks={[]} />;
  const explicit = /([zZ]|[+-]\d{2}:?\d{2}|UTC)$/.test(value.trim());
  const d = new Date(value);
  if (!explicit || Number.isNaN(d.getTime())) {
    return (
      <span>
        <span dir="ltr">{value}</span> <span className="muted small">({explicit ? "not parseable" : "no time zone in the value"})</span>
      </span>
    );
  }
  return (
    <span>
      {formatInstant(value, zone)}{" "}
      <span className="muted small" dir="ltr">
        original {value}
      </span>
    </span>
  );
}

function PlainValue({ value, marks }: { value: unknown; marks: Highlight[] }) {
  const kind = valueKind(value);
  const [all, setAll] = useState(false);
  switch (kind) {
    case "absent":
      return <span className="v-empty">— (absent)</span>;
    case "null":
      return <span className="v-null">null</span>;
    case "empty":
      return <span className="v-empty">(empty string)</span>;
    case "bool":
      return <span className="v-bool">{String(value)}</span>;
    case "number":
      return <span className="num-value">{exactText(value)}</span>;
    case "array":
    case "object":
      return <JsonValue value={value} />;
    default: {
      const text = value as string;
      const long = text.length > 600 || text.split("\n").length > 8;
      return (
        <span className="text-value">
          <span className={long && !all ? "clamp" : undefined} dir="auto">
            <Segmented text={text} marks={marks} />
          </span>
          {long && (
            <button type="button" className="btn link small" onClick={() => setAll((v) => !v)} aria-expanded={all}>
              {all ? "Show less" : `Show all ${text.length} characters`}
            </button>
          )}
        </span>
      );
    }
  }
}

/** Typed summary with inline expansion; the expanded tree is searchable. */
export function JsonValue({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const size = Array.isArray(value) ? value.length : value && typeof value === "object" ? Object.keys(value as object).length : 0;
  return (
    <span className="json-value">
      <button type="button" className="btn link small" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {summarize(value)} {open ? "▾" : "▸"}
      </button>
      {open && (
        <span className="json-tree">
          {size > 12 && (
            <input type="search" className="json-filter" placeholder="Filter keys and values" aria-label="Filter nested values" value={filter} onChange={(e) => setFilter(e.target.value)} />
          )}
          <JsonTree value={value} filter={filter.toLowerCase()} depth={0} />
        </span>
      )}
    </span>
  );
}

function JsonTree({ value, filter, depth }: { value: unknown; filter: string; depth: number }) {
  const entries = useMemo(() => (Array.isArray(value) ? value.map((v, i) => [String(i), v] as [string, unknown]) : Object.entries((value as Record<string, unknown>) ?? {})), [value]);
  const shown = filter ? entries.filter(([k, v]) => k.toLowerCase().includes(filter) || exactText(v).toLowerCase().includes(filter)) : entries;
  if (depth > 8) return <span className="muted small">(deeper levels are in the raw JSON)</span>;
  return (
    <dl className="tree">
      {shown.slice(0, 500).map(([k, v]) => (
        <div key={k} className="tree-row">
          <dt>{k}</dt>
          <dd>{v && typeof v === "object" && !isBigInt(v) ? <JsonTree value={v} filter="" depth={depth + 1} /> : <PlainValue value={v} marks={[]} />}</dd>
        </div>
      ))}
      {shown.length > 500 && <p className="muted small">{shown.length - 500} more entries are in the raw JSON.</p>}
      {filter && shown.length === 0 && <p className="muted small">No nested value matches.</p>}
    </dl>
  );
}

/** One field value by renderer kind, with an exact Copy action. */
export function FieldValue({ value, kind, marks, address, zone, copyLabel }: { value: unknown; kind: string; marks: Highlight[]; address?: AddressInfo; zone: DisplayZone; copyLabel: string }) {
  let body: ReactNode;
  const vk = valueKind(value);
  if (vk === "string" || vk === "multiline") {
    const text = value as string;
    if (kind === "command") body = <CommandValue text={text} marks={marks} />;
    else if (kind === "path") body = <PathValue text={text} marks={marks} />;
    else if (kind === "address") body = <AddressValue value={text} info={address} marks={marks} />;
    else if (kind === "timestamp") body = <TimestampValue value={text} zone={zone} />;
    else if (kind === "hash") body = <code className="hash">{text}</code>;
    else body = <PlainValue value={value} marks={marks} />;
  } else if (kind === "address" && address) {
    body = <AddressValue value={value} info={address} marks={marks} />;
  } else {
    body = <PlainValue value={value} marks={marks} />;
  }
  const copyable = vk !== "absent";
  return (
    <span className="field-value">
      <span className="field-body">{body}</span>
      {copyable && <CopyButton text={exactText(value)} label={copyLabel} compact />}
    </span>
  );
}
