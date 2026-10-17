import { Component, useEffect, useRef, useState, type InputHTMLAttributes, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, CheckCircle2, Copy, Info, X, XCircle } from "lucide-react";
import type { ApiError } from "../api/types";
import { statusLabel, type Tone } from "../lib/format";

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      {children}
    </span>
  );
}

export function StatusBadge({ value, prefix }: { value: string | null | undefined; prefix?: string }) {
  const [label, tone] = statusLabel(value);
  return (
    <Badge tone={tone}>
      {prefix ? `${prefix}: ` : ""}
      {label}
    </Badge>
  );
}

export function SyntheticBadge() {
  return (
    <Badge tone="synthetic" title="Synthetic test data; not collected from a real endpoint">
      Synthetic data
    </Badge>
  );
}

const BANNER_ICON: Record<string, ReactNode> = {
  info: <Info size={18} aria-hidden />,
  warn: <AlertTriangle size={18} aria-hidden />,
  bad: <XCircle size={18} aria-hidden />,
  ok: <CheckCircle2 size={18} aria-hidden />,
  synthetic: <Info size={18} aria-hidden />,
};

export function Banner({ tone = "info", title, children, action }: { tone?: "info" | "warn" | "bad" | "ok" | "synthetic"; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={`banner ${tone}`} role={tone === "bad" ? "alert" : "status"}>
      {BANNER_ICON[tone]}
      <div className="banner-body">
        {title && <strong>{title}</strong>}
        {children && <div>{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function ErrorBanner({ error, onRetry }: { error: ApiError | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <Banner
      tone="bad"
      title={error.message}
      action={
        onRetry ? (
          <button className="btn small" onClick={onRetry}>
            Try again
          </button>
        ) : undefined
      }
    >
      {error.next_action && <div>{error.next_action}</div>}
      {(error.correlation_id || error.details) && (
        <details>
          <summary>Show details</summary>
          <pre className="pre">{JSON.stringify({ code: error.code, correlation_id: error.correlation_id, details: error.details }, null, 2)}</pre>
        </details>
      )}
    </Banner>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p>
        <strong>{title}</strong>
      </p>
      {children}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="row muted" role="status">
      <span className="spinner" aria-hidden /> {label}…
    </span>
  );
}

export function CopyText({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="copy">
      <code>{value}</code>
      <button
        type="button"
        className="btn ghost small"
        aria-label={`Copy ${label ?? value}`}
        onClick={() => {
          navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            },
            () => undefined,
          );
        }}
      >
        {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
      </button>
    </span>
  );
}

export function JsonView({ value, summary = "Raw JSON" }: { value: unknown; summary?: string }) {
  return (
    <details>
      <summary>{summary}</summary>
      <pre className="pre" dir="auto">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

export function Pager({ offset, limit, total, onChange }: { offset: number; limit: number; total: number; onChange: (offset: number) => void }) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="pager" aria-label="Pagination">
      <span className="muted small">
        {total === 0 ? "0 items" : `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`}
      </span>
      <button className="btn small" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </button>
      <span className="small">
        Page {page} of {pages}
      </span>
      <button className="btn small" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        Next
      </button>
    </div>
  );
}

// Open dialogs, topmost last. Only the topmost one reacts to Escape and keeps Tab focus.
const dialogStack: symbol[] = [];
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** Focus, Escape, focus trap, and focus return for a modal dialog (independent of onClose identity). */
export function useDialog(ref: RefObject<HTMLElement | null>, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const id = Symbol("dialog");
    dialogStack.push(id);
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== id) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      const root = ref.current;
      if (e.key !== "Tab" || !root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!root.contains(active) || (e.shiftKey && (active === first || active === root))) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    ref.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey, true);
      dialogStack.splice(dialogStack.indexOf(id), 1);
      if (previous && document.contains(previous)) previous.focus?.();
    };
  }, [ref]);
}

/**
 * Renders a dialog above the page (outside any table or transformed container). Clicks inside
 * do not reach the components behind it, such as a clickable table row.
 */
export function DialogLayer({ children }: { children: ReactNode }) {
  return createPortal(<div className="dialog-layer" onClick={(e) => e.stopPropagation()}>{children}</div>, document.body);
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useDialog(ref, onClose);
  return (
    <DialogLayer>
      <div className="overlay" onClick={onClose} />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "Details"} tabIndex={-1} ref={ref}>
        <div className="drawer-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="btn ghost" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </DialogLayer>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useDialog(ref, onClose);
  return (
    <DialogLayer>
      <div className="overlay" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div className="card-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="btn ghost" onClick={onClose} aria-label="Close">
            <X size={18} aria-hidden />
          </button>
        </div>
        {children}
      </div>
    </DialogLayer>
  );
}

// Evidence values are untrusted: always rendered as text (React escapes), with automatic
// text direction so Persian/Arabic values display correctly.
export function EvidenceValue({ value, clamp = false }: { value: unknown; clamp?: boolean }) {
  const text = value === null ? "null" : value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value);
  const typed = value === null || typeof value === "boolean" || typeof value === "number";
  return (
    <span className={`cell-value evidence-value ${clamp ? "clamped" : ""}`} dir="auto" title={clamp && text.length > 160 ? text.slice(0, 2000) : undefined}>
      {typed ? <em>{text}</em> : text}
    </span>
  );
}

// A search box bound to a URL parameter. The input keeps its own text, so router transitions
// never reset the caret or drop keystrokes; the URL follows after a short pause (or on Enter).
export function UrlSearchInput({ value, onCommit, delay = 250, ...rest }: { value: string; onCommit: (value: string) => void; delay?: number } & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type">) {
  const [text, setText] = useState(value);
  const committed = useRef(value);
  const commit = useRef(onCommit);
  commit.current = onCommit;
  useEffect(() => {
    if (value !== committed.current) {
      committed.current = value;
      setText(value);
    }
  }, [value]);
  useEffect(() => {
    if (text === committed.current) return;
    const timer = setTimeout(() => {
      committed.current = text;
      commit.current(text);
    }, delay);
    return () => clearTimeout(timer);
  }, [text, delay]);
  return (
    <input
      {...rest}
      type="search"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && text !== committed.current) {
          committed.current = text;
          commit.current(text);
        }
        rest.onKeyDown?.(e);
      }}
    />
  );
}

// Contains a rendering failure (for example on hostile notebook content) to one region
// instead of unmounting the whole application.
export class ErrorBoundary extends Component<{ label: string; resetKey?: string; children: ReactNode }, { error: Error | null; resetKey?: string }> {
  state: { error: Error | null; resetKey?: string } = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  static getDerivedStateFromProps(props: { resetKey?: string }, state: { error: Error | null; resetKey?: string }) {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  render() {
    if (this.state.error) {
      return (
        <Banner tone="bad" title={`${this.props.label} could not be displayed`}>
          <span className="small">{this.state.error.message || "Rendering failed."}</span>
        </Banner>
      );
    }
    return this.props.children;
  }
}
