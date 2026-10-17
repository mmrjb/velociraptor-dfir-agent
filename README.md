# Velociraptor DFIR Agent — AI preview

An analyst workspace for reviewing **existing Velociraptor evidence** with a local GUI,
CLI, and MCP server. Optional Ollama analysis proposes structured findings that link back
to the exact evidence rows used. Evidence-only review works without a model.

**0.4.0a1 · alpha · private review candidate.** This is a narrowed edition of the internal
0.3.1 project. Installation, API/model availability probes and UI startup have passed on
the owner's Ubuntu 24.04 server. Real-evidence retrieval and real-model analysis acceptance
checks remain open; see
[VALIDATION.md](VALIDATION.md). A project license has not been selected. Keep this candidate
private until the publication checks in [PUBLISHING.md](PUBLISHING.md) are resolved.

## What you can do

- Browse organizations, endpoints, existing flows and hunts using the Velociraptor API.
- Review an evidence scope before retrieving existing results, or import a versioned evidence bundle.
- Read findings beside their source rows, inspect full values, filter and page evidence,
  and save a separate analyst review status.
- Use optional, bounded Ollama requests; reject fabricated citations and keep evidence
  available when a model fails. Explanations remain separate from the original finding.
- Export JSON, Markdown, and self-contained HTML reports; resume interrupted retrieval.
- Use the same investigation pipeline through GUI, CLI, or 16 MCP tools.

This edition has **no collection launch, hunt creation, response actions, raw VQL interface,
Notebook viewer, or Master Triage dashboard**. It is a separate application using the
Velociraptor backend. There is no demo mode or bundled model. Test fixtures are synthetic
and live only in the source test directory.

## Start with a saved evidence bundle

The offline release includes a wheel with built UI assets and a CPython 3.12 Linux x86_64
wheelhouse. It runs beside an existing installation on port **8766**, with separate state.

```bash
sha256sum -c velociraptor-dfir-ai-preview-0.4.0a1.tar.gz.sha256
tar -xzf velociraptor-dfir-ai-preview-0.4.0a1.tar.gz
cd velociraptor-dfir-ai-preview-0.4.0a1
bash install-preview.sh
```

Run the commands printed by the installer, then open `http://127.0.0.1:8766/` and choose
**Import Evidence**. The supported import is this project's version 1 evidence bundle
(`manifest.json` plus `sources/*.jsonl`), rather than an arbitrary Velociraptor export ZIP.
See [INSTALL.md](INSTALL.md) for remote access and source installation.

## Connect your backend and model

Use an administrator-provided API credential scoped for reading existing results. Set
`VELOCIRAPTOR_API_CONFIG`, the organization, and the already installed Ollama model in your
private environment file; see [CONFIGURATION.md](CONFIGURATION.md). Credentials stay on the
application host. A remote Ollama address sends bounded evidence views to that address.

```bash
vr-dfir doctor --env-file /absolute/path/preview.env --probe --probe-model
vr-dfir hunts list --org-id root --env-file /absolute/path/preview.env
vr-dfir analyze flow --client-id C.EXAMPLE01 --flow-id F.EXAMPLE01 --org-id root \
  --env-file /absolute/path/preview.env
vr-dfir analyze bundle --input /absolute/path/evidence --no-llm \
  --env-file /absolute/path/preview.env
```

Use the executable inside the preview virtualenv to avoid selecting an older installed
`vr-dfir`. IDs above are placeholders. Findings prioritize human review; citation checks
establish row references, not the truth of a model's interpretation or a host's safety.

[CLI guide](docs/cli.md) · [GUI guide](docs/ui.md) · [MCP and operations](docs/operations.md) ·
[Evidence format](docs/evidence.md) · [Architecture](docs/architecture.md) ·
[Attribution](ACKNOWLEDGEMENTS.md)

## Project layout

| Path | Purpose |
|---|---|
| `frontend/src/` | React GUI, evidence views and component tests |
| `src/vr_dfir/` | Shared investigation pipeline, Velociraptor reads, analysis and reports; GUI API, CLI and MCP adapters |
| `schemas/` | Evidence and analysis contracts |
| `tests/` | Synthetic fixtures, API/CLI/MCP, analysis and packaging checks |
| `scripts/` | Offline release build, isolated installer and release verification |
| `docs/` | Architecture, usage, operations and optional systemd service template |
| `.github/workflows/` | Python matrix, frontend, packaging and Chromium CI |

For a persistent server installation, follow the optional
[systemd setup](INSTALL.md#optional-systemd-service) after installing the isolated preview.
