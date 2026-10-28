// Rendering of untrusted evidence values. Values are always React text (escaped), shown with
// automatic direction for mixed Persian/Arabic and Latin content, and copied exactly as stored:
// no bidi control characters, truncation markers, or reformatting are added to copied strings.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Check, Copy, Maximize2, X } from "lucide-react";
import { DialogLayer, useDialog } from "./ui";

export type ValueKind = "absent" | "null" | "empty" | "bool" | "number" | "string" | "multiline" | "array" | "object";

// Integers beyond the JavaScript safe range arrive wrapped by the server so no digit is lost.
export const BIGINT_KEY = "__vr_bigint__";

export function isBigInt(v: unknown): v is { [BIGINT_KEY]: string } {
  return !!v && typeof v === "object" && !Array.isArray(v) && Object.keys(v as object).length === 1 && typeof (v as Record<string, unknown>)[BIGINT_KEY] === "string";
}

export function valueKind(v: unknown): ValueKind {
  if (v === undefined) return "absent";
  if (isBigInt(v)) return "number";
  if (v === null) return "null";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number" || typeof v === "bigint") return "number";
  if (typeof v === "string") return v === "" ? "empty" : v.includes("\n") ? "multiline" : "string";
  if (Array.isArray(v)) return "array";
  return "object";
}

/** The exact text copied for a value: strings unchanged, structures as indented JSON. */
export function exactText(v: unknown): string {
  if (v === undefined) return "";
  if (typeof v === "string") return v;
  if (isBigInt(v)) return v[BIGINT_KEY];
  if (v === null || typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return jsonWithBigInts(v);
  } catch {
    return String(v);
  }
}

/** JSON text with wrapped big integers written back as their exact digits. */
export function jsonWithBigInts(v: unknown): string {
  const marks: string[] = [];
  const text = JSON.stringify(
    v,
    (_k, x) => {
      if (isBigInt(x)) {
        marks.push(x[BIGINT_KEY]);
        return `\u0000BIGINT${marks.length - 1}\u0000`;
      }
      return x;
    },
    2,
  );
  return text.replace(/"\\u0000BIGINT(\d+)\\u0000"/g, (_m, i) => marks[Number(i)]);
}

export function summarize(v: unknown): string {
  if (isBigInt(v)) return v[BIGINT_KEY];
  if (Array.isArray(v)) return v.length === 0 ? "[ ] empty list" : `[${v.length} item${v.length === 1 ? "" : "s"}]`;
  if (v && typeof v === "object") {
    const keys = Object.keys(v as object);
    if (keys.length === 0) return "{ } empty object";
    const shown = keys.slice(0, 3).join(", ");
    return `{${shown}${keys.length > 3 ? `, +${keys.length - 3}` : ""}}`;
  }
  return exactText(v);
}

const LONG_TEXT = 60;

export function needsExpansion(v: unknown): boolean {
  const k = valueKind(v);
  if (k === "array") return (v as unknown[]).length > 0;
  if (k === "object") return Object.keys(v as object).length > 0;
  if (k === "multiline") return true;
  return k === "string" && (v as string).length > LONG_TEXT;
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the selection fallback */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

export function CopyButton({ text, label, compact = false }: { text: string; label: string; compact?: boolean }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), 1500);
    return () => clearTimeout(t);
  }, [state]);
  return (
    <button
      type="button"
      className={`btn small ${compact ? "icon ghost" : ""}`}
      aria-label={`Copy ${label}`}
      title={compact ? `Copy ${label}` : undefined}
      onClick={async () => setState((await copyToClipboard(text)) ? "copied" : "failed")}
    >
      {state === "copied" ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
      {!compact && (state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy")}
      <span className="sr-only" aria-live="polite">
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : ""}
      </span>
    </button>
  );
}

/** Inline value: typed rendering, never a tooltip-only truncation; long values get an expand button. */
export function ValueCell({ value, label, expandable = true, onExpand }: { value: unknown; label: string; expandable?: boolean; onExpand?: () => void }) {
  const [open, setOpen] = useState(false);
  const kind = valueKind(value);
  // Short values can still be cut off by a narrow column: offer the full value whenever the
  // rendered text overflows, not only for long strings.
  const textRef = useRef<HTMLSpanElement>(null);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el || !expandable) return;
    const check = () => setOverflows(el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
    check();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [value, expandable]);
  let body: ReactNode;
  switch (kind) {
    case "absent":
      body = (
        <span className="v-empty" aria-label="absent">
          —
        </span>
      );
      break;
    case "null":
      body = <span className="v-null">null</span>;
      break;
    case "empty":
      body = <span className="v-empty">(empty string)</span>;
      break;
    case "bool":
      body = <span className="v-bool">{String(value)}</span>;
      break;
    case "number":
      body = (
        <span className="cellv-text" ref={textRef}>
          {exactText(value)}
        </span>
      );
      break;
    case "array":
    case "object":
      body = <span className="v-summary">{summarize(value)}</span>;
      break;
    default:
      body = (
        <span className="cellv-text" dir="auto" ref={textRef}>
          {value as string}
        </span>
      );
  }
  return (
    <span className="cellv">
      {body}
      {expandable && (needsExpansion(value) || overflows) && (
        <button type="button" className="btn icon ghost small" aria-label={`Show full value of ${label}`} onClick={(e) => { if (onExpand) { e.stopPropagation(); onExpand(); } else setOpen(true); }}>
          <Maximize2 size={13} aria-hidden />
        </button>
      )}
      {open && <ValueViewer title={label} value={value} onClose={() => setOpen(false)} />}
    </span>
  );
}

export function ValueViewer({ title, value, onClose }: { title: string; value: unknown; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const kind = valueKind(value);
  const text = exactText(value);
  useDialog(ref, onClose);
  const structured = kind === "array" || kind === "object";
  return (
    <DialogLayer>
      <div className="overlay" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={`Full value: ${title}`} tabIndex={-1} ref={ref}>
        <div className="card-head">
          <h2 style={{ margin: 0, overflowWrap: "anywhere" }}>{title}</h2>
          <span className="row">
            <CopyButton text={text} label={`${title} (exact value)`} />
            <button type="button" className="btn ghost" onClick={onClose} aria-label="Close">
              <X size={18} aria-hidden />
            </button>
          </span>
        </div>
        <p className="small muted">
          {structured
            ? `${kind === "array" ? "List" : "Object"} · ${summarize(value)} · copied as indented JSON`
            : `${kind === "string" || kind === "multiline" ? `${(value as string).length} characters` : kind}${kind === "multiline" ? ` · ${(value as string).split("\n").length} lines` : ""} · copied exactly as stored`}
        </p>
        {kind === "object" && (
          <div style={{ marginBottom: 10 }}>
            <StructuredFields record={value as Record<string, unknown>} rawJson={false} />
          </div>
        )}
        <pre className={`value-full ${structured ? "code" : ""}`} dir="auto">
          {text}
        </pre>
      </div>
    </DialogLayer>
  );
}

/**
 * Key/value view of a record: preferred (useful) fields first, then the rest in original order.
 * Nested values show a summary with an expand button; the raw JSON is behind a disclosure.
 */
export function StructuredFields({ record, preferred = [], rawJson = true, rawLabel = "Raw JSON (original record)" }: { record: Record<string, unknown>; preferred?: string[]; rawJson?: boolean; rawLabel?: string }) {
  const keys = Object.keys(record ?? {});
  const first = preferred.filter((k) => keys.includes(k));
  const rest = keys.filter((k) => !first.includes(k));
  return (
    <div className="stack">
      {keys.length === 0 ? (
        <p className="muted small">This record has no fields.</p>
      ) : (
        <dl className="fields">
          {[...first, ...rest].map((k) => (
            <FieldRow key={k} name={k} value={record[k]} primary={first.includes(k)} />
          ))}
        </dl>
      )}
      {rawJson && (
        <details className="advanced">
          <summary>{rawLabel}</summary>
          <div className="row" style={{ marginBottom: 6 }}>
            <CopyButton text={exactText(record)} label="raw JSON" />
          </div>
          <pre className="pre" dir="auto">
            {exactText(record)}
          </pre>
        </details>
      )}
    </div>
  );
}

function FieldRow({ name, value, primary }: { name: string; value: unknown; primary: boolean }) {
  return (
    <>
      <dt className={primary ? "primary" : undefined}>{name}</dt>
      <dd>
        <ValueCell value={value} label={name} />
      </dd>
    </>
  );
}
