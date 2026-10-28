// Finding widgets shared by Overview, Evidence and Reports. Severity (from the rule or model),
// analyst review state, origin and confidence are separate concepts and look different.
import { useEffect, useState } from "react";
import { apiPut, errorMessage } from "../api/client";
import type { ApiError, Review, ReviewState, SourceBrief } from "../api/types";
import { formatTime } from "../lib/format";
import { ageText } from "../lib/time";
import { Badge, ErrorBanner } from "./ui";

export const SEVERITY_TONE: Record<string, "bad" | "warn" | "info" | "neutral"> = { high: "bad", medium: "warn", low: "info", informational: "neutral" };
export const REVIEW_LABELS: Record<ReviewState, string> = {
  unreviewed: "Not reviewed",
  in_review: "In review",
  needs_follow_up: "Needs follow-up",
  confirmed: "Confirmed by analyst",
  benign: "Benign / expected",
};
export const OPEN_REVIEW: ReviewState[] = ["unreviewed", "in_review", "needs_follow_up"];
export const UNREVIEWED: Review = { state: "unreviewed", label: "Not reviewed", note: null, updated_at: null, by: null };
export const ORIGIN_SHORT: Record<string, string> = { deterministic_rule: "Rule", model_proposal: "AI proposal", analyst_input: "Analyst" };

export function SeverityBadge({ value }: { value: string }) {
  return <Badge tone={SEVERITY_TONE[value] ?? "neutral"}>Severity: {value ? value[0].toUpperCase() + value.slice(1) : "-"}</Badge>;
}

export function ReviewChip({ state }: { state: ReviewState }) {
  const cls = state === "confirmed" || state === "needs_follow_up" ? "escalated" : state === "benign" ? "done" : state === "in_review" ? "active" : "";
  return <span className={`review-chip ${cls}`}>{REVIEW_LABELS[state] ?? state}</span>;
}

export function ConfidenceMeter({ value }: { value: string }) {
  const n = value === "high" ? 3 : value === "medium" ? 2 : 1;
  return (
    <span className="confidence" aria-label={`Confidence ${value}`}>
      Confidence: {value}
      <span className="meter" aria-hidden>
        {[1, 2, 3].map((i) => (
          <i key={i} className={i <= n ? "on" : ""} />
        ))}
      </span>
    </span>
  );
}

/** The one main action on a finding: set its review state (with an optional note). */
export function ReviewControl({ invId, findingId, review, onSaved }: { invId: string; findingId: string; review: Review; onSaved: (r: Review) => void }) {
  const [state, setState] = useState<ReviewState>(review.state);
  const [note, setNote] = useState(review.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    setState(review.state);
    setNote(review.note ?? "");
  }, [findingId, review.state, review.note]);
  const changed = state !== review.state || (note || "") !== (review.note || "");
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await apiPut<Review>(`/investigations/${invId}/findings/${encodeURIComponent(findingId)}/review`, { state, note: note.trim() || null }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="review-control" role="group" aria-label="Analyst review">
      <div className="row">
        <label className="field small">
          Review decision
          <select value={state} onChange={(e) => setState(e.target.value as ReviewState)}>
            {Object.entries(REVIEW_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="field small grow">
          Note (optional)
          <input type="text" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <button className="btn primary small" disabled={!changed || busy} onClick={save}>
          {busy ? "Saving…" : "Save decision"}
        </button>
      </div>
      <p className="xsmall muted">
        Stored with this investigation, separately from the finding and its evidence. A confirmed finding is not a declaration that the host is compromised.
        {review.updated_at ? ` Last set ${formatTime(review.updated_at)}${review.by ? ` by ${review.by}` : ""}.` : ""}
      </p>
      <ErrorBanner error={error} />
    </div>
  );
}

const STATE_TONE: Record<string, "ok" | "warn" | "bad" | "neutral" | "info"> = {
  complete: "ok", empty: "info", skipped: "warn", failed: "bad", incomplete: "warn", not_read: "warn", unverified: "bad", not_applicable: "neutral", unknown: "warn",
};

/** Source coverage, one plain state per source. Shown on request from Overview. */
export function CoverageList({ sources, staleDays, onOpen }: { sources: SourceBrief[]; staleDays: number; onOpen?: (s: SourceBrief) => void }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Source</th>
            <th>State</th>
            <th className="num">Saved rows</th>
            <th>Captured</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.source_key}>
              <td>
                {onOpen ? (
                  <button className="btn link" onClick={() => onOpen(s)}>
                    {s.label}
                  </button>
                ) : (
                  s.label
                )}
                <div className="xsmall muted mono">{s.full_name}</div>
              </td>
              <td>
                <Badge tone={STATE_TONE[s.state] ?? "neutral"}>{s.state_label}</Badge>
                {s.stop_reason && <div className="xsmall muted">stopped: {s.stop_reason.replace(/_/g, " ")}</div>}
                {s.counts_only && <div className="xsmall muted">counts only for AI input</div>}
              </td>
              <td className="num">{s.rows}</td>
              <td className="nowrap">
                {s.collected_at ? formatTime(s.collected_at) : "unknown"}
                <div className="xsmall muted">
                  {ageText(s.age_days)}
                  {s.stale ? ` · older than ${staleDays} days` : ""}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
