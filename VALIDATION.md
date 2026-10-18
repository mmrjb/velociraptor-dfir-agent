# Validation — AI preview 0.4.0a2 and earlier live checks

This record concerns this narrowed candidate, not the internal 0.3.1 release. Automated tests
use synthetic fixtures and fake providers. Operator-reported live lab checks are listed
separately below; none of these checks establishes detection accuracy or model quality.

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
