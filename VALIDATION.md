# Validation — AI preview 0.5.0a1 candidate, 0.4.0a2 and earlier live checks

This record concerns this narrowed candidate, not the internal 0.3.1 release. Automated tests
use synthetic fixtures and fake providers. Operator-reported live lab checks are listed
separately below; none of these checks establishes detection accuracy or model quality.

## Independent package review (2026-10-28, Ubuntu 24.04, synthetic data)

The reviewed offline archive has SHA-256
`ee39d24ebc96696bec9b04bb7ae3a02c5263bd4ebaedfb8ce333569cfb8a68a3`.
Its source snapshot records local commit `2af5fae77ba3e0c2d023b1ce6334874c007c0366`
and tree `f99b540e79b275d8b5dbca27eadcf7c8684e5ddf`. Publication imports that snapshot
with documentation updates; the private development history is not part of the import.

- Archive SHA-256 and all 72 internal manifest entries verified. The archive contains
  no symbolic links, hard links, devices or paths outside its extraction directory.
- All 66 Python source files matched the application wheel byte for byte. The wheel
  includes 21 built UI files; no frontend build is required on the destination.
- Offline installation and `pip check` passed on Ubuntu 24.04 with the system CPython
  3.12.3 and glibc 2.39, without an installed `ensurepip` module. The installed command
  reported `vr-dfir 0.5.0a1`.
- A focused regression run using CPython 3.12.14 on the same Ubuntu environment:
  **117 passed, 2 skipped**. It covered presentation, views, comparisons, cohort and
  selection, edge cases, the view API, preview policy, web API, reporting and preflight.
  The skipped cases required `PREFLIGHT_RELEASE_DIR` or built fonts in the source tree;
  the separate offline install and installed-UI browser checks covered those artifacts.
  This run is not a rerun of the complete build-host Python suite below.
- **Eight Chromium journeys passed** against the installed wheel on CPython 3.12.3,
  using the test-only synthetic backend and fake provider. They covered the retained
  flow/report paths and all five demonstration journeys listed below, including Unknown
  instead of false removals, original-row links, eligible hosts, untimed rows, pins,
  persisted notes and selected-evidence reports.
- The Overview had three summary metrics. Its page width matched the viewport at 768,
  390 and 320 CSS pixels; desktop and narrow-layout captures were visually inspected.
  No page script errors or external HTTP resource requests were observed in that check.

These checks did not contact the owner's Velociraptor server or model. They do not
establish detection accuracy, real-source mapping coverage, or comparative usability.

## Operator-reported destination upgrade (2026-10-28, 0.5.0a1)

The owner supplied terminal output and confirmed that the new UI opened. The destination
was Ubuntu 24.04 with CPython 3.12.3, glibc 2.39 and SQLite 3.45.1. The assistant did not
operate the destination server.

- The archive checksum and preflight passed with no failures or warnings. The previous
  preview was 0.4.0a2 with six saved investigations.
- The new version was installed into a separate directory. Configuration was backed up,
  the preview was stopped for a consistent state archive/copy, and the existing systemd
  preview unit was switched through a release drop-in. The old installation and state
  were retained. The loopback status API reported `0.5.0a1`; the owner confirmed the UI
  change through the existing HTTPS gateway.
- `doctor --probe --probe-model` returned `Result: ok`: dependencies and built assets,
  writable candidate state, read-only backend access, a reachable Velociraptor API, and
  the existing local Ollama model `qwen2.5:1.5b` available.

This is installation, startup and connectivity evidence. A live 0.5.0a1 analysis,
model-generated explanation quality, real-source field mappings, full manual UI
acceptance and an executed destination rollback are not established by these outputs.
Private addresses, credentials, evidence and runtime logs are not included here.

## Release candidate 0.5.0a1 (2026-10-27, build host, synthetic data only)

This application-suite record predates the October 28 packaging-only update. The latter
delivery record reports 17 preflight/packaging tests passed against the final archive;
the full application suite was not rerun for that packaging update. Independent review
of the delivered archive is recorded above.

These checks ran on the build host against synthetic fixtures, fake providers and synthetic
demonstration bundles. No live Velociraptor server, Ollama model or real evidence was used.

- CPython 3.12.14: **235 passed, 1 skipped** (the skipped test needs a checkout readable by
  `nobody`). Ruff passed. CPython 3.10.22, 3.11.17 and 3.13.16 from separate local virtualenvs:
  **235 passed, 1 skipped** on each. At this checkpoint the GitHub CI matrix had not run;
  publication had not yet been requested.
- Frontend: TypeScript check and production Vite build passed; **37 component tests passed**.
- Playwright (headless Chromium, built UI, synthetic backend): **8 passed** — the three
  retained 0.4 journeys and the five brief journeys (review without AI; meaningful change
  with both original records; incomplete current capture gives Unknown, not Removed;
  eligible-host denominator, Where else seen, timeline bucket and untimed rows; pins and a
  note survive reload, a pinned change reopens that exact change, selected-evidence report).
- Independent code review of the new services and UI found 13 defects; all were fixed with
  regression tests (`tests/test_edge_cases.py`, additions to `test_views_api.py` and the
  frontend inspector test): previous row shown while another loads, nested summary fields,
  truncated comparison and entity keys merging distinct values, a normalizer exception on
  unusual numbers, Removed despite rows without key fields, timeline completeness ignoring
  failed time-mapped sources, partial edge buckets, cancelled index builds restarting on
  poll, pinned changes opening the wrong comparison, mapping changes not invalidating
  comparison caches, inexact large integers in change values, the host chart linking to
  all findings instead of those needing review, and repeated bundle opening in the
  selected report.
- 100k-row measurement (`tests/support/measure_large.py`, Xeon E5-2699 v4, 6 CPUs, 16 GB):
  two captures of 126,024 rows each. Import 47.2 s (106 MiB peak RSS). Cold index build
  16.0 s (52 MiB peak, 40.6 MB index). Warm views: overview 3 ms, a rows page 5 ms (first and
  last page), cited rows over all sources 118 ms, full-text search 378 ms, inspector 13 ms,
  timeline 127 ms, hosts 1.7 s, Where else seen 288 ms. A 100k-row comparison takes 17.0 s
  cold (runs in the background with progress; 20.5 MB result file) and 7 ms when cached;
  a change page loads in 280 ms. Peak RSS 159 MiB.
- Compatibility with 0.4.0a2 (`a2` virtualenv, synthetic fixture bundle): 0.5.0a1 reads the
  0.4.0a2 state (overview, hosts, pins, selected report) and leaves every original file
  byte-identical (only `derived/`, `selection.*` and `report/selected.*` are added); after
  that, 0.4.0a2 still lists the investigation, its findings, rebuilds its HTML report and
  verifies the evidence bundle (rollback).
- No model request is made while navigating views (API test with a counting provider);
  state-changing routes keep the CSRF, Host and Origin checks.

Not executed: live Velociraptor or Ollama checks, real evidence, the systemd promotion and
rollback procedure in INSTALL.md, the HTTPS gateway with dual cookies, and analyst
usability timing against 0.4.0a2 or the Velociraptor GUI. Real LTH column names are not
verified (documented candidates; operator mappings can correct them).

## Report revision 0.4.0a2 (2026-10-18)

- CPython 3.12.14: **157 passed, 1 skipped** after building the UI. The skipped test needs
  a checkout accessible to `nobody`; parent-directory permissions were not changed.
- New regression checks cover severity ordering and unchanged finding IDs, origin/severity
  counts, prepared views versus failed/skipped requests, missing metadata, embedded fonts
  with license text, and report rebuilding without model calls, backend reads or changes
  to saved evidence/analysis. Existing hostile-text escaping and report sandbox checks pass.
- Frontend: **30 tests passed**, TypeScript and production build passed. Ruff and installer
  shell syntax passed. Runtime dependencies are unchanged from a1.
- New Playwright coverage checks embedded font loading inside the report's actual sandbox.
  Its CI result and offline release checks are recorded in the deliverable verification file.
- These are synthetic checks; the a2 report has not yet been installed on the owner's server.
  The live results below concern a1 and are retained as historical evidence.

## Reported destination upgrade to 0.4.0a2 (2026-10-18)

The owner supplied terminal output and the regenerated HTML report. These are
operator-reported destination checks and a static inspection of the supplied export;
the assistant did not operate the destination server.

- The installed CLI reports 0.4.0a2. The preview service is active and its command points
  to the a2 virtualenv and configuration. The loopback status API reports 0.4.0a2,
  read-only backend access and the existing Ollama configuration.
- Rebuilding an existing saved investigation produced JSON, Markdown and HTML in the a2
  state copy. This was report regeneration, not a new analysis or inference benchmark.
- The supplied HTML identifies generator 0.4.0a2, summarizes three accepted findings
  (two model proposals and one deterministic signal), and places the medium rule finding
  before the two informational model proposals.
- Evidence coverage records ten sources and 2,114 saved rows. The recorded prepared
  model input is only two of 244 process rows, reduced for the request budget, with one
  recorded valid request. Other sources are marked not selected for that model view.
  These counts do not imply whole-bundle model analysis or useful threat discovery.
- Static inspection found three embedded WOFF2 fonts with their license text, no script
  elements or external resource references, no duplicate HTML IDs, and all local evidence
  links resolving. This is not a manual visual rendering assessment.
- The original larger-workload timeouts and model-quality limitations remain open.
  No real rows, host/account names, credentials or investigation identifiers are included
  in this validation record.

## Completed in this workspace (2026-10-17)

- Python 3.12.14: **149 passed, 1 skipped**. Coverage includes the shared existing-flow/hunt/bundle pipeline, API contracts, resume, coverage,
  evidence integrity and exact citations, report exports, configuration, MCP stdio and packaging.
- Policy tests: forged collection plans, unsupported persisted jobs, removed routes/tools,
  dangerous flags and hosted providers are refused below the UI.
- Analysis tests: malformed output, invented citations/verdicts/scores, outage/timeouts,
  no-model operation, request counts, full-message input limits and oversized repair refusal.
- Finding review/locate/explain API checks: exact source paging, CSRF, separate review state,
  user-triggered explanations, citation resolution, cache reuse and unchanged report bytes.
- Frontend: 30 component tests passed; TypeScript check and production Vite build passed.
  Tests cover read-only planning, late responses after scope changes, no-AI import defaults,
  explicit AI opt-in, full values, keyboard dialogs, hostile text and exact-row navigation.
- Ruff and shell syntax checks are run before packaging. Release installation and installed
  application smoke checks are recorded in the adjacent release verification file when run.

The final Python counts and package hashes are recorded in the deliverable verification
file rather than copied from the original project's 0.3.1 report. That report's much larger
workspace suite is not evidence that removed components were tested in this edition.

One Linux permission test is skipped here because this workspace cannot be imported by the
`nobody` account. Simulated inaccessible-directory tests pass; the original live PermissionError
regression handling is retained. No ownership or permissions on shared parent directories
were changed to force the skipped check to pass.

## Completed in GitHub Actions (2026-10-17)

[Successful CI run](https://github.com/mmrjb/velociraptor-dfir-agent/actions/runs/39016811714)
for source commit `58df886e7fbfc06fabce1af7892b93bc8cf118a8`, on Ubuntu 24.04 x86_64:

- CPython 3.10, 3.11, 3.12 and 3.13: **148 passed, 2 skipped** on each version; Ruff passed.
  The Python jobs lack built UI assets, so the wheel-content check is deferred to the
  packaging job. The real `nobody` permissions check needs root and is also skipped.
- Frontend: **30 component tests passed**; TypeScript and production build passed.
- Packaging: wheel/sdist built; installed-wheel doctor and evidence-only bundle analysis
  passed; both package checks passed, including UI assets and bundled license notices.
- Playwright Chromium: **2 browser journeys passed** against the installed wheel with a
  synthetic backend and fake provider. They exercise evidence-only analysis, finding-to-row
  navigation, reports navigation, explicit model provenance and rejection of collection plans.

This CI run does not use the owner's API credentials or a real model and is not an accuracy
or production-readiness claim. The earlier local browser download limitation is superseded
by the automated browser run above. Destination-server startup is recorded below;
detailed manual visual and broader real-analysis acceptance remain open.

## Reported on the destination server (2026-10-17)

The owner ran the supplied offline release on Ubuntu 24.04 with Python 3.12.3 and shared
terminal output. These are operator-reported checks, not commands executed by CI or by
the assistant against that server:

- The transferred archive passed `sha256sum -c`; the isolated installed command reports
  `vr-dfir 0.4.0a1`.
- `doctor --probe --probe-model` returned `Result: ok`: application dependencies, writable
  preview state and bundled UI assets passed. The configured Velociraptor API was reachable
  and Ollama reported the already installed `qwen2.5:1.5b` model available.
- The separate `vr-dfir-ai-preview` systemd service is enabled and active under `vrdfir`.
  Its startup log binds the UI to `127.0.0.1:8766`.
- `/api/v1/status` returned `ok: true` and version `0.4.0a1`. The owner confirmed the UI
  opened and was tested; detailed visual checks were not recorded.

This establishes installation, startup and connectivity. The later live retrieval and
bounded inference checks are recorded below. No destination-server evidence, credential
files, host inventory or screenshots are included.

## Reported live evidence and Ollama checks (2026-10-17; reviewed 2026-10-18)

The owner supplied CLI output from the installed release. Only aggregate outcomes and
limitations are recorded here; real rows, identifiers, account names and model responses
remain outside the repository.

- Existing-flow retrieval from the real Velociraptor server: all 10 sources read to
  completion, 2,114 rows persisted, evidence-only outcome, one deterministic review signal.
  An empty OS-specific package source was reported as a verified precondition skip.
- A larger Ollama bundle analysis failed: all five attempted requests exceeded the
  configured 120-second timeout. The investigation remained partial with its saved
  evidence and deterministic finding available; no model finding was produced.
- A direct, small JSON request to `qwen2.5:1.5b` completed in about 9.21 seconds, including
  6.28 seconds of model loading. The server reported Ollama 0.40.3, six vCPUs and about
  7.7 GiB RAM. This tiny request is not a throughput benchmark for longer prompts.
- A smaller analysis-only profile selected the Processes source, at most five rows,
  a 5,000-character input cap, a 512-token output cap and one model request. The CLI run
  used a 4,096-token context and a 300-second request timeout. The logged request contained
  3,783 characters; analysis completed with two accepted model proposals, one deterministic
  finding and zero rejected proposals. End-to-end duration was not recorded.
- The two model proposals were informational process descriptions. Their process-name
  observations matched the two cited rows subsequently displayed by the owner. Both
  proposals cited the same pair of rows, including a row unnecessary for the individual
  observation: citation existence checks do not establish precise or sufficient support.
  These outputs do not demonstrate useful threat discovery or establish that a host is safe.
- The deterministic finding concerned an additional UID 0 account in persisted Users
  evidence. The small model profile did not include Users, so that finding was not an
  AI detection and its omission from model proposals is not a tested model miss.

All 2,114 rows remained in the saved bundle. That count is evidence storage coverage;
the small model test evaluated only its selected process view, not all saved rows.
Broader quality, benign/malicious adjudication and exact claim-to-row support remain open.

## Pending acceptance

- Real hunt retrieval, larger datasets and independent comparison of retrieved source
  counts/rows with the original Velociraptor GUI. One existing-flow read succeeded.
- Useful real-model analysis across broader evidence: latency, sampling coverage, precision
  of individual claim-to-row references and human adjudication. The small process-only
  smoke test succeeded; the earlier larger workload timed out on this CPU host.
- Retry after a model outage with evidence-ID/hash comparisons and manual report/export
  review. Successful source retrieval and model inference do not complete these checks.
- A real interactive MCP client connected to this installed preview.
- Detailed manual visual review and other CPU architectures.
- Independent code-provenance review, owner license selection, public publication.

## Destination-server checklist

Use the isolated installation described in INSTALL.md; keep internal 0.3.x running separately.

1. Check `--version`, run `doctor --probe --probe-model`, and confirm preview state and port 8766.
2. Read one approved existing lab flow with `--no-llm`. Compare source counts and a few exact
   records with Velociraptor. Record missing/failed sources; an empty table is not a safety verdict.
3. Enable Ollama and analyze the same existing evidence. Open each model citation, inspect
   rejections and missing context, and check input/request budgets in private runtime logs.
4. Retry analysis after a model outage and verify saved evidence IDs/hashes remain unchanged.
5. Follow GUI findings to exact rows, save a local review, request a finding explanation,
   and open exported Markdown/HTML/JSON reports. Test long paths and Persian/Latin values.
6. Connect the real MCP client; verify only the read/local-analysis tools are available.

Do not add real evidence, credentials, inventory, runtime logs or lab screenshots to git.
