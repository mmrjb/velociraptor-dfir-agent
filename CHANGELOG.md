# Changelog

## 0.5.0a1 — Summary-first analyst workspace (release candidate, 2026-10-27)

Experimental release candidate of the public AI preview edition. It reads existing evidence
only; nothing here launches collections, recalculates notebooks or runs model-written queries.

### New

- **Workspace.** Three main destinations (Investigations, Reports, Settings). Each
  investigation has a scope bar (organization, hosts, capture time) and four views:
  Overview, Evidence, Changes and Timeline. Hosts and outliers and Run details open from
  context. Filters, selections and pages are kept in the URL, so Back and reload keep them.
- **Overview.** At most three metrics (Needs review, Represented hosts, Sources read) and two
  charts (findings by severity or review state; hosts, events or sources), a short
  attention list, a "Next step", and a one-line coverage and freshness summary. Counts come
  from the whole saved dataset; findings count once however many rows they cite; failed,
  partial, unread or unverified sources are never counted as read; "not ready" is never 0.
- **One evidence inspector** with summary, all fields and Advanced (exact raw JSON,
  provenance, timestamp basis, mapping). Content-aware renderers for commands (executable
  emphasized; only spans matched by the cited rule highlighted), paths, network addresses
  (documented range labels only), accounts, timestamps with an explicit zone, and JSON with
  inline, searchable expansion. Large integers are transported exactly. Observed parent and
  child processes and same-capture network links are shown only with identity checks.
- **Renderer registry** chosen by source identity (documented Velociraptor artifacts and
  baseline source names), with operator mappings in `VR_DFIR_SOURCE_MAPPINGS`. Unknown
  sources use the generic renderer. Default tables show five to seven mapped columns.
- **Derived index** per investigation (`derived/index-v1.sqlite`, standard library SQLite,
  disposable and atomically rebuilt, cancellable, with progress). It verifies rows on disk,
  row order and content hashes. Evidence files are never rewritten.
- **Timeline.** Events mode uses explicit event-time mappings (ISO-8601 with a zone, or a
  declared epoch unit); untimed rows are counted by reason and can be listed. Capture
  history shows repeated saved captures by capture time and never implies creation time.
- **Changes ("What changed?").** Compare a saved source with an earlier saved capture of the
  same host and source (default: the latest earlier one). Versioned adapters for users,
  groups, services, listening endpoints, cron, SSH authorized keys, configuration settings,
  file metadata, packages and mounts. Removed only when the current capture is complete and
  verified; Added only when the baseline is; otherwise Unknown with the reason. Volatile and
  uncompared fields are ignored but can be shown. Results are stored apart from evidence.
- **Hosts and outliers / Where else seen.** Host comparison and low-prevalence values
  (executables, hashes, commands, UID 0 accounts, services, cron entries, destinations, SSH
  keys) with "Seen on X of Y eligible hosts": one observation per host, complete sources
  that carry the field, same OS family, never across organizations, at least three hosts.
- **Report selection.** Pin findings, evidence rows and changes with notes; pins survive
  reload and restart, recover from their log, and never change evidence or reviews. New
  report scope "Selected evidence" (JSON, Markdown, HTML) beside the full report.
- **AI beside the evidence (optional).** Explicit actions "Explain why this finding deserves
  review", "Suggest a next investigation question" and "Summarize the selected changes",
  through the existing bounded, citation-checked pipeline. Hidden when AI is off.
- **CLI and MCP adapters** over the same services: `overview`, `timeline`, `changes`,
  `hosts`, `where-else`, `pins`, `report --scope selected`; MCP `get_overview`,
  `compare_captures`, `where_else_seen` (19 read-only/local tools in total).

### Changed or fixed in existing behavior

- Markdown reports neutralize CR, NEL, U+2028/U+2029 and other control characters in all
  untrusted text and code spans (previously a carriage return could start a forged line).
- Settings is a page in the main navigation; the organization for new live work is chosen
  on the Start page or in Settings instead of the header.
- Displayed times carry an explicit zone (UTC by default; a local display zone changes
  presentation only).
- The investigation list shows "findings not ready" instead of zero and review progress.

### Retained (not new)

Exact copy and full values; distinct absent/null/empty/false/zero rendering; column
selection, wrap and paging; findings beside evidence with original-row provenance and
no substitute rows; review states; coverage; bounded optional AI; JSON/Markdown/HTML
reports with embedded fonts. Old `/findings/<id>` links redirect to the workspace.

### Compatibility

0.4.0a2 state opens unchanged: the new version only adds `derived/`, `selection.json(l)`
and `report/selected.*` beside existing files. 0.4.0a2 can still read a state used by
0.5.0a1 (rollback). No migration is required.

### Packaging

The offline bundle is `velociraptor-dfir-ai-preview-0.5.0a1-linux-x86_64-cp312.tar.gz`.
Its dependencies are resolved for CPython 3.12.3 with glibc 2.39 (Ubuntu 24.04 x86_64) and
pinned by SHA-256 in `requirements-cp312.txt`, including a bundled pip. The installer
installs only that set, offline and hash-checked, into a virtualenv created without
ensurepip. The bundle carries `RELEASE.json` and `SHA256SUMS`. THIRD_PARTY_NOTICES.md
lists the shipped wheel versions. A read-only `preflight.sh` checks the operating system,
architecture, glibc, Python, release files, the offline dependency set, paths and loopback
port conflicts, and reports the existing preview service and gateway; the installer runs
its install-time subset first.

## 0.4.0a2 — Clearer standalone reports (2026-10-18)

- Show prepared model input separately from saved evidence, including row selection, field
  projection, truncation and recorded request outcomes. Missing metadata stays unknown.
- Sort findings by severity, preserve their identifiers, and summarize their origins/counts.
- Distinguish model citation checks from deterministic citations; neither proves interpretation.
- Embed Inter, Vazirmatn and JetBrains Mono with their font licenses in offline HTML reports.
- Existing saved investigations can rebuild reports locally without inference or API reads.

## 0.4.0a1 — AI preview candidate (2026-10-17)

- Narrow the internal 0.3.1 codebase to analysis of existing flows/hunts and saved bundles.
- Retain one evidence/analysis/report pipeline for GUI, CLI and 16 MCP tools.
- Remove collection launching, Notebook/Master Triage modules and their public interfaces.
- Restrict model adapters to Ollama or none; preserve evidence-only operation and citations.
- Simplify navigation and evidence selection, retain improved value/table/finding views.
- Isolate preview installation, configuration, state and port from internal 0.3.x.
- Enforce complete message budgets on model repairs and finding explanations.
- Add policy tests for forged plans and unsupported persisted jobs, and UI scope/import tests.

The input was the owner's 0.3.1 source archive at commit
`137bd3575dfb0ba1eabf5b0fe07f92bb432c93ae`. It contained the live-proven `hunts()` query fix;
that compatibility fix is retained. Notebook compatibility changes are outside this edition.
