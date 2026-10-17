export type Tone = "ok" | "warn" | "bad" | "info" | "neutral" | "synthetic" | "teal";

// Every status colour is paired with a text label.
const LABELS: Record<string, [string, Tone]> = {
  // outcomes
  complete: ["Complete", "ok"],
  partial: ["Partial", "warn"],
  failed: ["Failed", "bad"],
  interrupted: ["Interrupted", "warn"],
  evidence_only: ["Evidence only", "info"],
  running: ["In progress", "info"],
  // phases
  preparing: ["Preparing", "neutral"],
  collecting: ["Collecting", "info"],
  reading: ["Reading Evidence", "info"],
  analyzing: ["Analyzing", "info"],
  report_ready: ["Report Ready", "ok"],
  stopped: ["Stopped locally", "warn"],
  // collection
  not_applicable: ["Not applicable", "neutral"],
  pending: ["Pending", "neutral"],
  waiting: ["Waiting for client", "info"],
  completed: ["Completed", "ok"],
  cancelled: ["Cancelled on server", "bad"],
  wait_timeout: ["Wait timeout (may still run)", "warn"],
  launch_outcome_unknown: ["Launch outcome unknown", "bad"],
  not_started: ["Not started", "neutral"],
  skipped: ["Skipped", "neutral"],
  scheduled: ["Scheduled", "info"],
  queued: ["Queued on client", "info"],
  in_progress: ["Running", "info"],
  unresponsive: ["Client unresponsive", "warn"],
  not_found: ["Not found", "bad"],
  unknown: ["Unknown", "warn"],
  // retrieval
  incomplete: ["Incomplete", "warn"],
  read_failed: ["Read failed", "bad"],
  // analysis
  not_requested: ["No AI (evidence only)", "neutral"],
  completed_with_issues: ["Completed with issues", "warn"],
  no_model_requests: ["No model requests", "warn"],
  // result status
  rows: ["Rows", "ok"],
  empty_or_skipped: ["Empty or skipped", "warn"],
  skipped_precondition: ["Skipped (precondition)", "warn"],
  source_failed: ["Source failed", "bad"],
  not_collected: ["Not collected", "bad"],
  unavailable: ["Unavailable", "bad"],
  // severity / confidence
  informational: ["Informational", "neutral"],
  low: ["Low", "info"],
  medium: ["Medium", "warn"],
  high: ["High", "bad"],
};

export function statusLabel(value: string | null | undefined): [string, Tone] {
  if (!value) return ["-", "neutral"];
  return LABELS[value] ?? [value.replace(/_/g, " "), "neutral"];
}

export const MODE_LABELS: Record<string, string> = {
  existing_flow: "Existing Collection",
  existing_hunt: "Existing Hunt",
  bundle: "Imported / Saved Evidence",
};

export const ORIGIN_LABELS: Record<string, string> = {
  deterministic_rule: "Deterministic rule",
  model_proposal: "Model proposal",
  analyst_input: "Analyst input",
};

export function formatTime(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function elapsed(from: string | null | undefined, to?: string | null): string {
  if (!from) return "-";
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return "-";
  const s = Math.max(0, Math.round((end - start) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function displayValue(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export function shortId(value: string, keep = 10): string {
  return value.length > keep + 3 ? `${value.slice(0, keep)}…` : value;
}

