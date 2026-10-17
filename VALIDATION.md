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

## Pending acceptance

- This candidate against the real Velociraptor 0.76.5 API. The source retains the hunts()
  compatibility fix from the owner's internal release; local tests only emulate API shapes.
- Real Ollama inference on the owner's existing model, including quality, speed and citations.
  A successful `doctor --probe-model` establishes model availability, not usable analysis.
- A real interactive MCP client connected to this installed preview.
- Browser journeys in `frontend/e2e/`: the official Playwright Chromium download returned an
  invalid archive in this environment. No successful browser run or visual inspection is claimed.
- Python versions other than 3.12, Ubuntu-specific installation and other CPU architectures.
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
