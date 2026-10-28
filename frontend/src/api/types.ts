// Types for the private local UI API (/api/v1). Kept in sync with the Python services;
// tests/test_api_contract.py checks that every field declared here (in the CONTRACT
// interfaces) is present in real API responses.

export interface ApiError {
  code: string;
  message: string;
  next_action?: string;
  correlation_id?: string;
  details?: Record<string, unknown>;
}

// CONTRACT: session
export interface SessionInfo {
  csrf_token: string;
  version: string;
  recovered_jobs: string[];
}

// CONTRACT: status
export interface StatusInfo {
  version: string;
  velociraptor: {
    configured: boolean;
    default_org: string;
    last_test: { reachable: boolean; error?: string; latency_seconds?: number } | null;
    synthetic: boolean;
  };
  model: {
    provider: string;
    model: string | null;
    hosted: boolean;
    configured: boolean;
    last_test: { reachable: boolean | null; error?: string; model_available?: boolean } | null;
    budgets: Record<string, number>;
  };
  allowlist: { configured: boolean; source?: string; artifacts?: string[]; error?: string } | null;
  edition: string;
  backend_access: "read_only";
  tested_at: string | null;
  setup_hint: string | null;
}

export interface Org {
  OrgId: string;
  Name: string;
}

// CONTRACT: client
export interface ClientInfo {
  client_id: string;
  org_id: string;
  hostname: string;
  fqdn: string;
  os_type: string;
  os_release: string;
  platform: string;
  last_seen: string | null;
  last_ip: string;
  labels: string[];
  synthetic: boolean;
}

export interface FlowSummary {
  flow_id: string;
  artifacts: string[];
  created: string | null;
  state: string;
  raw_state: string;
  creator: string | null;
  total_collected_rows: number | null;
}

export interface HuntSummary {
  hunt_id: string;
  description: string;
  created: string | null;
  state: string;
  artifacts: string[];
}

export interface ParameterMeta {
  name: string;
  type: string;
  default: string | null;
  description: string;
  friendly_name: string;
  choices: string[];
  validation_support: "local" | "server_only" | "unknown_type";
}

export interface ArtifactSummary {
  name: string;
  type: string;
  description: string;
  sources: string[];
  parameter_names: string[];
  synthetic?: boolean;
}

export interface ArtifactDetail extends ArtifactSummary {
  author: string;
  parameters: ParameterMeta[];
  source_details: { name: string; description: string; has_precondition: boolean; precondition: string }[];
  precondition: string;
  compatibility: { os_hints_from_precondition_text: string[]; limitations: string; has_artifact_precondition: boolean; sources_with_precondition: string[] };
  required_permissions: string[];
  resources: Record<string, unknown>;
  tools: string[];
  collection_behavior: string[];
}

export interface ArtifactList {
  artifacts: ArtifactSummary[];
  total_matched: number;
  returned: number;
  offset: number;
  truncated: boolean;
  org_id: string;
}

export interface ProfileSummary {
  id: string;
  name: string;
  description: string;
  purpose: string;
  platforms: string[];
  collections: { id: string; artifact: string; required: boolean; sources: string[] | "discover"; behavior_notes: string[] }[];
  collection_count: number;
  analyst_count: number;
  origin: "bundled" | "user" | "directory" | "file";
  editable: boolean;
  fingerprint: string;
  validation: string;
}

export type ParamValue = string | number | boolean | (string | number | boolean)[];

export interface ProfileLimits {
  collection_timeout_seconds: number;
  max_collection_rows?: number | null;
  max_upload_bytes?: number | null;
  wait_timeout_seconds: number;
  poll_interval_seconds: number;
  page_size: number;
  max_rows_per_source: number;
  max_bytes_per_source: number;
  urgent: boolean;
}

export interface ProfileCollection {
  id: string;
  artifact: string;
  required: boolean;
  parameters: Record<string, ParamValue>;
  sources: string[] | "discover";
  behavior_notes: string[];
  limits: ProfileLimits;
}

export interface ProfileDoc {
  schema_version: number;
  id: string;
  name: string;
  description: string;
  purpose: string;
  platforms: string[];
  objective: string;
  guidance: string;
  collections: ProfileCollection[];
  analysts: { id: string; objective: string; inputs: string[] }[];
  analysis: {
    counts_only_sources: string[];
    max_model_input_chars: number;
    max_output_tokens: number;
    max_model_requests: number;
    max_rows_per_source_to_model: number;
    max_field_chars: number;
    sampling: string;
    model_input_fields: Record<string, string[]>;
    field_aliases: Record<string, string[]>;
    event_time_fields: string[];
  };
  retrieval: Record<string, number>;
}

export interface ValidationIssue {
  location: string;
  message: string;
  severity?: "warning";
}

export interface ProfileValidation {
  valid: boolean;
  mode: string;
  issues: ValidationIssue[];
  live_issues?: ValidationIssue[];
  profile?: ProfileDoc;
  yaml?: string;
}

export interface PlannedSource {
  name: string | null;
  full_name: string;
  status: string;
  counts_only: boolean;
  note: string;
}

export interface PlannedCollection {
  collection_ids: string[];
  artifact: string;
  required: boolean;
  parameters: Record<string, string>;
  metadata_status: string;
  artifact_type: string | null;
  allowlisted: boolean;
  sources: PlannedSource[];
  behavior: string[];
  limits: Record<string, number | boolean | null>;
  errors: string[];
  warnings: string[];
  will_launch: boolean;
  description: string;
}

// CONTRACT: plan
export interface Plan {
  mode: "existing_flow" | "existing_hunt";
  offline: boolean;
  org_id: string;
  target: Record<string, unknown> & { client_id?: string; hostname?: string; platform?: string; hunt_id?: string; flow_id?: string; candidates?: ClientInfo[] };
  profile: { id: string; name: string; fingerprint: string } | null;
  collections: PlannedCollection[];
  analysts: { id: string; objective: string; inputs: string[] }[];
  errors: string[];
  warnings: string[];
  ok: boolean;
  launches: number;
  fingerprint: string;
  will_start_endpoint_collection: boolean;
  policy_denied: boolean;
  synthetic: boolean;
}

export interface PlanResponse {
  plan: Plan;
  plan_token: string | null;
  primary_action: string;
}

// CONTRACT: investigation
export interface InvestigationSummary {
  investigation_id: string;
  title: string;
  mode: string;
  scope: { org_id?: string; client_id?: string; hostname?: string; flow_id?: string; hunt_id?: string; platform?: string };
  profile: { id: string; name: string } | null;
  created_at: string;
  updated_at: string;
  phase: string;
  collection_status: string;
  retrieval_status: string;
  analysis_status: string;
  outcome: string;
  counts: Record<string, number>;
  synthetic: boolean;
  flow_ids: string[];
  job: { state: string } | null;
  job_active?: boolean;
}

export interface InvestigationDetail {
  investigation: InvestigationSummary & { errors: string[]; phase_times: Record<string, string>; provider?: { name: string; model: string; fake: boolean } };
  plan: Plan | null;
  collections: { artifact: string; collection_ids: string[]; launch_state: string; flow_id: string | null; flow_state: string | null; raw_state: string | null; wait: { outcome: string; polls: number; diagnostics: string[] } | null }[];
  existing: Record<string, unknown>;
  job_active: boolean;
  actions: { resume_reading: boolean; retry_analysis: boolean; view_evidence: boolean; export_report: boolean; stop_local_processing: boolean };
}

export interface EventItem {
  seq: number;
  time: string;
  kind: string;
  data: Record<string, unknown>;
}

export interface EvidenceRef {
  evidence_id: string;
  client_id?: string;
  hostname?: string;
  flow_id?: string;
  artifact?: string;
  source?: string | null;
  row_index?: number;
  unresolved?: boolean;
}

// CONTRACT: finding
export interface Finding {
  finding_id: string;
  title: string;
  observation: string;
  interpretation: string;
  evidence_ids: string[];
  severity: "informational" | "low" | "medium" | "high";
  confidence: "low" | "medium" | "high";
  confidence_rationale: string;
  benign_explanations: string[];
  missing_context: string[];
  next_steps: string[];
  origin: "deterministic_rule" | "model_proposal" | "analyst_input";
  client_ids: string[];
  sources: string[];
  evidence_refs: EvidenceRef[];
}

// CONTRACT: coverage_source
export interface CoverageSource {
  source_key: string;
  full_name: string;
  client_id: string;
  hostname: string;
  flow_id: string;
  counts_only: boolean;
  collection_status: string;
  read_status: string;
  result_status: string;
  available_rows: number | "unknown";
  persisted_rows: number;
  stop_reason: string | null;
  error: string | null;
  notes: string[];
}

export interface FindingsResponse {
  findings: Finding[];
  rejected_findings: { reasons: string[]; request_id: string; title: string | null }[];
  coverage: { sources: CoverageSource[]; counts: Record<string, number>; gaps: string[] } | null;
  summary?: string[];
  analysis?: { analysis_status: string; model_input_coverage: Record<string, unknown>[]; skipped_model_coverage: Record<string, unknown>[]; errors: string[]; notes: string[] };
  provider?: { name: string; model: string; fake: boolean };
  status?: Record<string, string>;
  synthetic?: boolean;
  citation_note?: string;
  report_ready: boolean;
}

// CONTRACT: evidence_record
export interface EvidenceRecord {
  evidence_id: string;
  investigation_id: string;
  org_id: string;
  client_id: string;
  hostname: string;
  flow_id: string;
  hunt_id: string | null;
  artifact: string;
  source: string | null;
  full_name: string;
  source_key: string;
  collected_at: string | null;
  retrieved_at: string;
  event_time: string | null;
  row_index: number;
  raw_sha256: string;
  file: string;
  line: number | null;
  synthetic: boolean;
  row: Record<string, unknown>;
}

export interface EvidencePage {
  records: { evidence_id: string; row_index: number; raw_sha256: string; retrieved_at: string; event_time: string | null; row: Record<string, unknown> }[];
  total_matched: number;
  offset: number;
  limit: number;
  search_scope: string | null;
  scan_truncated: boolean;
  source: { source_key: string; full_name: string; client_id: string; hostname: string; flow_id: string; org_id: string; coverage: Record<string, unknown> };
}

export interface BundleSources {
  bundle_id: string;
  synthetic: boolean;
  sources: { source_key: string; full_name: string; client_id: string; hostname: string; flow_id: string; hunt_id: string | null; counts_only: boolean; coverage: Record<string, unknown> }[];
}

export interface Preferences {
  selected_org: string | null;
  page_size: number;
  default_no_llm: boolean;
  analysis_budget: Record<string, number> | null;
}

// ---------------------------------------------------------------- reviews and AI

export type ReviewState = "unreviewed" | "in_review" | "needs_follow_up" | "confirmed" | "benign";

export interface Review {
  state: ReviewState;
  label: string;
  note: string | null;
  updated_at: string | null;
  by: string | null;
}

export interface ExplanationItem {
  text: string;
  refs: string[];
  citations: Record<string, unknown>[];
}

export interface Explanation {
  action: string;
  status: "ok" | "rejected" | "unavailable" | "unsupported" | "failed";
  label?: string;
  reason?: string;
  cached?: boolean;
  provider?: string;
  model?: string;
  prompt_version?: string;
  summary?: string | null;
  points?: ExplanationItem[];
  gaps?: ExplanationItem[];
  next_steps?: ExplanationItem[];
  rejected?: { section: string; text: string; reasons: string[] }[];
  records_supplied?: number;
  records_omitted?: number;
  created_at?: string;
  deterministic?: Record<string, unknown>;
}


// ---------------------------------------------------------------- summary-first views (0.5)

export type ViewState = "ready" | "building" | "evidence_changing" | "no_evidence" | "failed" | "cancelled" | "stale";

export interface ViewPending {
  state: Exclude<ViewState, "ready">;
  progress?: { rows_done: number; rows_total: number; elapsed_seconds: number };
  error?: string;
}

export interface ViewTarget {
  view: "evidence" | "hosts" | "coverage" | "timeline" | "run" | "report" | "changes";
  filters?: Record<string, string>;
  mode?: "findings" | "rows";
}

export interface Metric {
  id: string;
  label: string;
  unit: string;
  value: number | null;
  total?: number | null;
  detail: string;
  breakdown?: Record<string, number>;
  target: ViewTarget;
}

export interface ChartSegment {
  key: string;
  label: string;
  count: number;
  filter: Record<string, string>;
}

export interface ChartBar extends ChartSegment {
  secondary?: string;
}

export interface HistogramBucket {
  start: string;
  end: string;
  from_us: number;
  to_us: number;
  count: number;
  measured: boolean;
}

export interface Chart {
  id: "severity" | "hosts" | "events" | "sources";
  kind: "donut" | "bars" | "histogram";
  title: string;
  unit: string;
  state: "ready" | "not_ready";
  note?: string | null;
  total?: number | null;
  segments?: ChartSegment[];
  alternate?: { title: string; unit: string; segments: ChartSegment[] };
  bars?: ChartBar[];
  more?: number;
  buckets?: HistogramBucket[];
  bucket?: { width_us: number; label: string };
  complete?: boolean;
  untimed?: number;
  link?: ViewTarget;
}

export interface SourceBrief {
  source_key: string;
  label: string;
  full_name: string;
  domain: string;
  client_id: string;
  hostname: string;
  org_id: string;
  flow_id: string;
  hunt_id: string | null;
  rows: number;
  state: string;
  state_label: string;
  read_status: string;
  result_status: string;
  stop_reason: string | null;
  collected_at: string | null;
  age_days: number | null;
  stale: boolean;
  timed_rows: number;
  untimed_rows: number;
  counts_only: boolean;
  mapping?: MappingInfo;
  cited_rows?: number;
}

export interface MappingInfo {
  id: string;
  label: string;
  basis: string;
  configured: boolean;
  version: string;
}

export interface AttentionItem {
  finding_id: string;
  title: string;
  severity: string;
  origin: string;
  review_state: ReviewState;
  review_label: string;
  evidence_count: number;
  hosts: string[];
}

// CONTRACT: overview
export interface Overview {
  state: "ready";
  scope: { investigation_id: string; title: string; mode: string; org_ids: string[]; hosts: { client_id: string; hostname: string }[]; synthetic: boolean; note: string };
  status: Record<string, string | null>;
  findings_ready: boolean;
  metrics: Metric[];
  charts: Chart[];
  attention: AttentionItem[];
  coverage: {
    sources_total: number;
    states: Record<string, number>;
    state_labels: Record<string, string>;
    captured_from: string | null;
    captured_to: string | null;
    stale_sources: number;
    stale_after_days: number;
    rows: number;
    sources: SourceBrief[];
    retrieval_status: string | null;
  };
  next_action: { label: string; target: ViewTarget | null };
  index: { built_at: string; normalizer_version: string };
}

export interface ColumnPlanItem {
  key: string;
  label: string;
  kind: string;
  role?: string;
  virtual?: boolean;
}

export interface TableSource {
  source_key: string;
  full_name: string;
  label: string;
  domain: string;
  mapping: MappingInfo;
  columns: { default: ColumnPlanItem[]; other: ColumnPlanItem[] };
  fields: Record<string, string>;
  client_id: string;
  hostname: string;
  flow_id: string;
  org_id: string;
  state: string;
  state_label: string;
  rows: number;
  read_status: string;
  result_status: string;
  stop_reason: string | null;
  available_rows: number | null;
}

export interface RowRecord {
  evidence_id: string;
  row_index: number;
  source_key: string;
  full_name: string;
  client_id: string;
  hostname: string;
  title: string;
  time_status: string;
  event_time: string | null;
  cited_by: string[];
  row: Record<string, unknown>;
}

// CONTRACT: rows_page
export interface RowsPage {
  state: "ready";
  records: RowRecord[];
  total: number;
  offset: number;
  limit: number;
  filters: { key: string; value: string }[];
  scan_truncated: boolean;
  scope: string;
  source: TableSource | null;
  counting_unit: string;
}

export interface Highlight {
  rule_id: string;
  field: string;
  start: number | null;
  end: number | null;
}

export interface RowRef {
  evidence_id: string;
  title: string;
  full_name: string;
  event_time: string | null;
}

export interface AddressInfo {
  field: string;
  role: string;
  address: string;
  class: string;
  label: string;
  basis: string;
  port: unknown;
  port_field: string | null;
}

export interface SummaryField {
  field: string | null;
  role: string | null;
  label: string;
  kind: string;
  value?: string;
  virtual?: boolean;
}

export interface InspectActions {
  where_else: { available: boolean; reason?: string };
  compare: { available: boolean; reason?: string; capture?: Record<string, unknown> };
  pin: { pinned: boolean; pin_id?: string | null; note?: string | null };
}

// CONTRACT: inspect
export interface Inspect {
  state: "ready";
  record: {
    evidence_id: string; investigation_id: string; org_id: string; client_id: string; hostname: string;
    flow_id: string; hunt_id: string | null; artifact: string; source: string | null; full_name: string;
    source_key: string; collected_at: string | null; retrieved_at: string | null; row_index: number;
    row_index_base: number; raw_sha256: string; file: string; line_offset_bytes: number;
    recorded_event_time: string | null; recorded_event_time_field: string | null; recorded_event_time_note: string;
  };
  row: Record<string, unknown>;
  raw_text: string;
  title: string;
  source: TableSource;
  presentation: {
    mapping: MappingInfo;
    domain: string;
    summary: SummaryField[];
    kinds: Record<string, string>;
    addresses: AddressInfo[];
    event_time: { status: string; utc: string | null; reason: string | null; detail: string; basis: Record<string, unknown> | null };
  };
  highlights: Highlight[];
  findings: { finding_id: string; title: string; severity: string; confidence: string; origin: string; rule_id: string | null; review_state: ReviewState; review_label: string }[];
  entities: { kind: string; key: string; display: string; label: string }[];
  relations: {
    parent: RowRef | null; parent_status: string | null; children: RowRef[]; children_status: string | null;
    connections: RowRef[]; process: RowRef | null; process_status: string | null;
  };
  related_by_finding: RowRef[];
  actions?: InspectActions;
}

export interface Timeline {
  state: "ready";
  mode: "events";
  basis: string;
  counting_unit: string;
  range: { from: string | null; to: string | null };
  bucket: { width_us: number; label: string };
  buckets: HistogramBucket[];
  timed_rows: number;
  complete: boolean;
  incomplete_sources: string[];
  untimed: { total: number; by_reason: { status: string; count: number; label: string }[] };
  mappings: { source_key: string; label: string; rules: { role: string; format: string; meaning: string }[]; statuses: Record<string, number>; basis: Record<string, unknown> | null }[];
  note: string | null;
}

export interface SourcesView {
  state: "ready";
  sources: SourceBrief[];
  state_labels: Record<string, string>;
}

// ---------------------------------------------------------------- report selection (pins)

export type PinKind = "evidence" | "finding" | "change";

// CONTRACT: pin
export interface Pin {
  pin_id: string;
  kind: PinKind;
  ref: string;
  title: string;
  note: string | null;
  created_at: string;
  updated_at: string;
  snapshot: Record<string, unknown>;
  resolved?: boolean;
}

// ---------------------------------------------------------------- changes, history, cohorts

export interface Capture {
  investigation_id: string;
  title: string;
  mode: string;
  synthetic: boolean;
  org_id: string;
  client_id: string;
  hostname: string;
  flow_id: string;
  hunt_id: string | null;
  artifact: string;
  source: string | null;
  full_name: string;
  source_key: string;
  collected_at: string | null;
  collected_ts: number | null;
  read_status: string;
  result_status: string;
  persisted_rows: number;
  stop_reason: string | null;
  domain: string;
  duplicates: string[];
  is_current?: boolean;
  complete?: boolean;
}

export interface ChangeSummary {
  state: "complete" | "incomparable";
  incomparable_reason: string | null;
  counts: Record<"added" | "removed" | "modified" | "unchanged" | "unknown", number>;
  unchanged_with_ignored_differences: number;
  unknown_reasons: { reason: string; count: number }[];
  limitations: string[];
  current_side: { rows: number; manifest_rows: number; keyed: number; skipped: number; unkeyed: number; verified: boolean; absence_established: boolean };
  baseline_side: { rows: number; manifest_rows: number; keyed: number; skipped: number; unkeyed: number; verified: boolean; absence_established: boolean };
}

export interface ChangeSource {
  source_key: string;
  full_name: string;
  label: string;
  adapter: string;
  adapter_label: string;
  current: Capture;
  baselines: Capture[];
  default_baseline: Capture | null;
  cached: { comparison_id: string; created_at: string; baseline: Capture; summary: ChangeSummary; policy_version: string }[];
  reason: string | null;
}

export interface ChangeSources {
  sources: ChangeSource[];
  not_comparable: { source_key: string; full_name: string; hostname: string }[];
  note: string;
}

export interface Comparison {
  state: "ready";
  comparison_id: string;
  created_at: string;
  definition: {
    comparison_id: string; policy_version: string; normalizer_version: string; adapter: string; adapter_label: string;
    key_roles: string[]; compared_roles: string[]; volatile_roles: string[]; current: Capture; baseline: Capture;
  };
  summary: ChangeSummary;
}

export interface ChangeItem {
  item_key: string;
  state: "added" | "removed" | "modified" | "unchanged" | "unknown";
  state_label: string;
  key_display: string;
  changed: string[];
  before_eids: string[];
  after_eids: string[];
  before_values: Record<string, unknown> | null;
  after_values: Record<string, unknown> | null;
  ignored_diff: boolean;
  reason: string | null;
}

export interface CaptureHistory {
  state: "ready";
  current: Capture;
  captures: Capture[];
  comparisons: ChangeSource["cached"];
  enough_history: boolean;
  basis: string;
  counting_unit: string;
}

export interface CohortRef {
  evidence_id: string;
  title: string;
  full_name: string;
  hostname: string;
  client_id: string;
  investigation_id: string;
  eligible_host?: boolean;
}

export interface HostsView {
  state: "ready";
  scope: "investigation" | "organization";
  org_id: string | null;
  window: { from: string | null; to: string | null; days: number } | null;
  unavailable: string | null;
  hosts: { client_id: string; hostname: string; os: string; findings_open: number; findings_total: number; rows: number; sources_complete: number; sources_total: number; investigations: string[] }[];
  host_count: number;
  excluded: { client_id: string; hostname: string; investigation_id: string; reason: string }[];
  groups: { os: string; hosts: number; kinds: { kind: string; label: string; eligible_hosts: number }[]; usable: boolean }[];
  outliers: { kind: string; label: string; key: string; display: string; seen: number; eligible: number; os: string; text: string; hosts: string[]; refs: CohortRef[] }[];
  outliers_total: number;
  offset: number;
  limit: number;
  min_cohort: number;
  counting_unit: string;
  note: string;
}

export interface WhereElse {
  state: "ready";
  evidence_id: string;
  scope: "investigation" | "organization";
  org_id: string | null;
  window: { from: string | null; to: string | null; days: number } | null;
  unavailable: string | null;
  host_os: string;
  other_os_hosts: string[];
  excluded: { client_id: string; hostname: string; investigation_id: string; reason: string }[];
  entities: { kind: string; label: string; key: string; display: string; seen: number; eligible: number; text: string; also_seen_not_eligible: string[]; not_eligible_hosts: string[]; refs: CohortRef[]; cohort_too_small: boolean }[];
  counting_unit: string;
  note: string;
}
