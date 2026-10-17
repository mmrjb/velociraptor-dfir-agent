// User-triggered AI interpretation. Always separated from deterministic results and labelled;
// never requested automatically on page load.
import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { apiPost, errorMessage } from "../api/client";
import type { ApiError, Explanation, ExplanationItem } from "../api/types";
import { ErrorBanner } from "./ui";

export interface AiAction {
  id: string;
  label: string;
  body: Record<string, unknown>;
}

export function AiPanel({ path, actions, title = "AI assistance (optional)", onCitation, disabledReason }: { path: string; actions: AiAction[]; title?: string; onCitation?: (citation: Record<string, unknown>) => void; disabledReason?: string | null }) {
  const [result, setResult] = useState<Explanation | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // An interpretation belongs to one subject (finding, endpoint, page of rows): when the
  // subject changes, the previous result is cleared and late responses are discarded.
  const scope = `${path}\n${JSON.stringify(actions.map((a) => a.body))}`;
  const scopeRef = useRef(scope);
  useEffect(() => {
    scopeRef.current = scope;
    setResult(null);
    setError(null);
    setBusy(null);
  }, [scope]);

  const run = async (action: AiAction, refresh = false) => {
    const asked = scope;
    setBusy(action.id);
    setError(null);
    try {
      const res = await apiPost<Explanation>(path, { ...action.body, refresh });
      if (scopeRef.current === asked) setResult(res);
    } catch (e) {
      if (scopeRef.current === asked) setError(errorMessage(e));
    } finally {
      if (scopeRef.current === asked) setBusy(null);
    }
  };

  return (
    <section className="card ai-panel" aria-label={title}>
      <div className="card-head">
        <h2>
          <Sparkles size={16} aria-hidden /> {title}
        </h2>
        <span className="row">
          {actions.map((a) => (
            <button key={a.id} className="btn small" disabled={busy !== null || !!disabledReason} onClick={() => run(a)}>
              {busy === a.id ? "Asking…" : a.label}
            </button>
          ))}
        </span>
      </div>
      <p className="small muted">
        Runs only when you click. The model receives a bounded set of records as untrusted data, must cite them, and cannot change weights, scores, risk levels, evidence, or review status.
      </p>
      {disabledReason && <p className="small">{disabledReason}</p>}
      <ErrorBanner error={error} />
      {result && <ExplanationView result={result} onCitation={onCitation} onRefresh={() => run(actions.find((a) => a.body.action === result.action || a.id === result.action) ?? actions[0], true)} />}
    </section>
  );
}

function ExplanationView({ result, onCitation, onRefresh }: { result: Explanation; onCitation?: (c: Record<string, unknown>) => void; onRefresh: () => void }) {
  if (result.status !== "ok") {
    return (
      <div className="banner warn" role="status">
        <div className="banner-body">
          <strong>{result.status === "unavailable" ? "AI interpretation unavailable" : result.status === "unsupported" ? "Not enough data for an interpretation" : result.status === "rejected" ? "The model's answer failed validation" : "The AI request failed"}</strong>
          <div>{result.reason}</div>
          <div className="small muted">Deterministic results are unaffected.</div>
        </div>
      </div>
    );
  }
  return (
    <div className="stack">
      <div className="ai-label">{result.label}</div>
      <p className="small muted">
        {result.provider} {result.model} · prompt {result.prompt_version} · {result.records_supplied} record(s) supplied
        {result.records_omitted ? `, ${result.records_omitted} omitted for the budget` : ""}
        {result.cached ? " · cached" : ""} {result.created_at ? `· ${result.created_at}` : ""}{" "}
        <button className="btn link small" onClick={onRefresh}>
          Ask again
        </button>
      </p>
      {result.summary && <p dir="auto">{result.summary}</p>}
      <ItemList title="Why" items={result.points} onCitation={onCitation} />
      <ItemList title="Gaps" items={result.gaps} onCitation={onCitation} />
      <ItemList title="Next steps" items={result.next_steps} onCitation={onCitation} />
      {result.rejected && result.rejected.length > 0 && (
        <details className="advanced">
          <summary>{result.rejected.length} statement(s) rejected by validation</summary>
          <ul className="small">
            {result.rejected.map((r, i) => (
              <li key={i}>
                <span dir="auto">{r.text || "(empty)"}</span> — {r.reasons.join("; ")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ItemList({ title, items, onCitation }: { title: string; items?: ExplanationItem[]; onCitation?: (c: Record<string, unknown>) => void }) {
  if (!items || items.length === 0) return null;
  return (
    <>
      <h3>{title}</h3>
      <ul>
        {items.map((it, i) => (
          <li key={i}>
            <span dir="auto">{it.text}</span>{" "}
            {it.refs.map((ref, j) => (
              <button key={ref} className="btn link small" onClick={() => onCitation?.(it.citations[j] ?? {})} aria-label={`Open cited record ${ref}`} disabled={!onCitation}>
                [{ref}]
              </button>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}
