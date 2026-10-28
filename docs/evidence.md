# Evidence, coverage, and report semantics

## Run directory

```
<runs>/<investigation_id>/
  investigation.json   status: phase, collection / retrieval / analysis status, outcome
  plan.json            the reviewed plan (fingerprint, target, collections, limits)
  checkpoint.json      read cursors, flow status snapshots, hunt enumeration
  evidence/            evidence bundle (below)
  analysis/requests/   exact serialized model requests (0600)
  analysis/responses/  raw model output, saved before validation (0600)
  analysis/findings.json  accepted, rejected, deterministic findings, model-input coverage
  report/report.json|md|html
  report/selected.json|md|html  "Selected evidence" report (0.5.0a1, built on request)
  reviews.json|.jsonl  analyst review decisions (current + append-only history)
  selection.json|.jsonl pinned items and notes (current + append-only history)
  derived/index-v1.sqlite        disposable presentation index (0.5.0a1)
  derived/comparisons-v1.sqlite  saved comparison results (0.5.0a1)
  events.jsonl         bounded progress log
```

Files added by 0.5.0a1 sit beside the existing ones; earlier versions ignore them. See
[presentation.md](presentation.md) for the derived index, counting units and comparison rules.

## Evidence bundle (format version 1)

```
evidence/manifest.json
evidence/sources/<client>_<flow>_<artifact>_<source>-<hash>.jsonl
```

`manifest.json` holds `format: vr-dfir-evidence-bundle`, `format_version: 1`, `bundle_id`,
`investigation_id`, `scope`, `synthetic`, `row_index_base: 0`, the evidence-ID scheme, and one
entry per source with org, client, hostname, flow, optional hunt, artifact, source, collection
IDs, collection time, counts-only flag, and coverage.

Each JSONL line:

```json
{"evidence_id": "ev-…", "row_index": 141, "raw_sha256": "…", "retrieved_at": "…",
 "event_time": "…", "event_time_field": "StartTime", "row": {…raw row…}}
```

* `row` is exactly what Velociraptor returned. JSON types are preserved, so `false`, `0`,
  `""`, `null` and missing keys stay distinct. Unicode is stored as UTF-8, not escaped.
* `row_index` is the **zero-based** position in the server result set of that
  org/client/flow/artifact/source: the `start_row` that returns the row.
* `evidence_id` = `ev-` + first 20 hex of
  `sha256("v1|org|client|flow|artifact|source|row_index")`. It is deterministic and scoped, so
  identical rows at different positions, clients, or orgs get different IDs. `raw_sha256` is
  the content hash; it is stored but never used as the identity.
* Each record resolves to: investigation, org, client, hostname, flow, hunt, artifact, source,
  collection time, retrieval time, event time (when a configured time field exists), row
  index, content hash, and file + line (`vr-dfir` UI "Provenance", MCP `get_evidence`).

Older one-based LTH IDs: a bundle that declares `row_index_base: 1` is refused until
`vr-dfir evidence migrate-index-base --input <bundle>` rewrites it. Each legacy ID is kept as
an explicit alias to the new ID of the same row; nothing is shifted silently. (The earlier LTH
output format was not available to this build, so only this documented convention is
supported.)

`vr-dfir evidence verify --input <bundle>` checks row order, IDs, duplicates, and hashes.

## Cursors and resume

Rows are appended in order. On resume the next `start_row` is derived from the JSONL file
itself, and a page whose `start_row` does not equal the number of saved rows is refused. A
crash between appending and updating the manifest therefore cannot lose or duplicate rows; a
partial trailing line from an interrupted write is discarded. Cursors are scoped by
org/client/flow/artifact/source; a checkpoint is refused for a different org or client.

Consistency policy: only flows in a terminal state are read. Rows of a flow that is still
running could still grow, so its sources are recorded as `not_started` with stop reason
`flow_not_terminal` and the snapshot is incomplete.

## Coverage per source

| Field | Values |
|---|---|
| `collection_status` | normalized flow state at read time |
| `result_status` | `rows`, `empty_or_skipped` (success with zero rows; Velociraptor does not distinguish), `skipped_precondition` (only when the client log says so), `source_failed`, `not_collected`, `unavailable`, `unknown` |
| `read_status` | `complete`, `incomplete`, `read_failed`, `not_started`, `not_applicable` |
| `available_rows` | from the flow's per-query `result_rows` when reported, else `unknown` |
| `persisted_rows`, `stop_reason` | `row_budget`, `byte_budget`, `total_row_budget`, `total_byte_budget`, `read_error`, `interrupted`, `flow_not_terminal` |

## What the model sees

The model input is a reproducible view: for each source, rows with deterministic review signals
first, then evenly spaced rows across the whole result set (first and last included), up to
`max_rows_per_source_to_model`. Selected fields can be limited per source; long values are
truncated and counted. Counts-only sources contribute only counts and say so. The **entire
serialized request**, instructions included, must fit `max_model_input_chars`. Larger inputs are
split into bounded chunks plus one synthesis request, capped by `max_model_requests`; anything
that does not fit is recorded as skipped coverage. The report lists, per source, rows saved,
rows supplied, sampling, truncation, and signal rows supplied.

## Findings

Every finding has an ID, title, observation, separate interpretation, evidence IDs, qualitative
severity and confidence with a rationale, plausible benign explanations, missing context,
suggested verification, and origin (`deterministic_rule`, `model_proposal`, or
`analyst_input`).

Model output is accepted only as JSON matching the schema. Each cited evidence ID must exist in
the bundle and must have been supplied to that request (or to a validated intermediate during
synthesis). Unknown fields, numeric probabilities, and "clean/safe" verdicts are rejected. One
repair attempt is allowed per request. Rejected output is stored and listed separately; it is
never shown as a finding.

Citation validation shows that a cited row exists; it does not prove that the interpretation is
correct.

## Outcomes

`complete`, `partial` (coverage gaps, failed or skipped analysis), `failed`, `interrupted`
(stopped locally; resumable), `evidence_only` (no model requested).
Collection, retrieval and analysis statuses are always reported separately. No outcome means
"the host is clean".
