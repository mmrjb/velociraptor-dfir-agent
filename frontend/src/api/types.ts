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

