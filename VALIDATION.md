# Validation — AI preview 0.4.0a1

This record concerns this narrowed candidate, not the internal 0.3.1 release. Tests use
synthetic fixtures and fake providers; they do not measure detection accuracy or model quality.

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
by the automated browser run above; manual visual and destination-server review remain open.

## Pending acceptance

- This candidate against the real Velociraptor 0.76.5 API. The source retains the hunts()
  compatibility fix from the owner's internal release; local tests only emulate API shapes.
- Real Ollama inference on the owner's existing model, including quality, speed and citations.
  A successful `doctor --probe-model` establishes model availability, not usable analysis.
- A real interactive MCP client connected to this installed preview.
- Manual visual review, installation on the owner's destination server and other CPU architectures.
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
