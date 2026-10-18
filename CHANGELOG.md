# Changelog

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
