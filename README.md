# Velociraptor DFIR Agent

**Review existing endpoint evidence through a GUI, CLI, or MCP interface.**

[![CI](https://github.com/mmrjb/velociraptor-dfir-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/mmrjb/velociraptor-dfir-agent/actions/workflows/ci.yml)
[Installation](INSTALL.md) · [LAN HTTPS access](docs/lan-access.md) · [CLI](docs/cli.md) · [MCP](docs/operations.md#mcp) · [Validation](VALIDATION.md) · [Architecture](docs/architecture.md)

An independent analyst workspace that uses Velociraptor as its backend. Browse existing
results, inspect findings beside their source rows, and export a report. Optional local
Ollama analysis adds structured proposals; evidence-only review works without a model.

**Version 0.4.0a2 · Experimental project.** Installation, existing-flow retrieval and report
generation have been exercised on Ubuntu 24.04. Model quality, citation precision and
performance on larger inputs are still being evaluated.

## Why this project

An investigation needs a clear path from an observation to the evidence behind it.
This workspace keeps source rows, coverage, provenance and findings together, and makes
the same investigation pipeline available through three interfaces.

- **GUI:** browse existing results, review evidence scope, inspect full row values and
  record analyst review status.
- **CLI:** retrieve and review existing flows or hunts, import saved bundles, resume
  retrieval and export reports.
- **MCP:** expose the shared investigation service through 16 tools.
- **Evidence:** preserve row references, hashes, source coverage and collection provenance.
- **Analysis:** combine deterministic review signals with optional bounded Ollama requests.
  Saved evidence remains available when the model fails.
- **Reports:** export JSON, Markdown and standalone HTML with embedded fonts. Reports
  separate retrieved evidence from prepared model input and show findings by severity.

## Typical workflow

1. Select an existing Velociraptor flow or hunt, or import a saved evidence bundle.
2. Review the scope and retrieve the existing results.
3. Examine rule signals and optional model proposals alongside their cited rows.
4. Save analyst review decisions and export the report.

This edition reads existing backend evidence. Collection launching, hunt creation, endpoint
response, arbitrary VQL, notebook viewing and Master Triage are outside its scope.
There is no demo mode or bundled model. Synthetic fixtures live under `tests/` only.

## Install from source

Use Python 3.12 with venv support and Node.js 22.12+ (or a compatible version listed in
`frontend/package.json`). The source package supports Python 3.10–3.13 in CI.

```bash
git clone https://github.com/mmrjb/velociraptor-dfir-agent.git
cd velociraptor-dfir-agent

python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[ui,mcp]'

cd frontend
npm ci
npm run build
cd ..

cp .env.example .env
.venv/bin/vr-dfir ui --env-file "$PWD/.env" --host 127.0.0.1 --port 8766
```

Open **http://127.0.0.1:8766/**. The initial configuration has model analysis disabled and
no API credential configured. Configure an existing server using [CONFIGURATION.md](CONFIGURATION.md),
or import a version 1 bundle produced by this project (`manifest.json` and `sources/*.jsonl`).
An arbitrary Velociraptor export ZIP is not this bundle format.

Source installation requires access to Python and npm package registries. The complete
offline bundle used for lab validation has not been attached to a GitHub Release.
[INSTALL.md](INSTALL.md) covers offline bundles, SSH access, systemd and upgrades.
For direct access from another computer without an SSH tunnel, an administrator can set up
the optional [authenticated Nginx HTTPS gateway](docs/lan-access.md). The application stays
on loopback; the gateway can serve the preview on port 8766 and an existing internal UI on
port 8765 with separate browser session cookies.

## Connect Velociraptor and Ollama

Configure your private `.env` using [.env.example](.env.example):

- `VELOCIRAPTOR_API_CONFIG`: path to an administrator-provided API credential scoped for reads.
- `VELOCIRAPTOR_ORG_ID`: the organization to review.
- `VELOCIRAPTOR_MODEL_PROVIDER`: `none` or `ollama`.
- `OLLAMA_HOST` and `OLLAMA_MODEL`: an approved endpoint and an already installed model.

A remote Ollama endpoint receives the selected evidence view. Keep configuration, credentials,
investigation state and real evidence outside version control.

```bash
.venv/bin/vr-dfir doctor --env-file "$PWD/.env" --probe --probe-model
.venv/bin/vr-dfir hunts list --org-id root --env-file "$PWD/.env"

# Replace the example identifiers with an existing client and flow.
.venv/bin/vr-dfir analyze flow --env-file "$PWD/.env" \
  --org-id root --client-id C.EXAMPLE01 --flow-id F.EXAMPLE01 --no-llm
```

For local bundle review:

```bash
.venv/bin/vr-dfir analyze bundle --input /absolute/path/evidence \
  --env-file "$PWD/.env" --no-llm
```

Use the executable inside this installation's virtualenv to select the intended version.

## Validation and limits

[Validation record](VALIDATION.md) · [Successful CI run](https://github.com/mmrjb/velociraptor-dfir-agent/actions/runs/39107266776)

| Area | Evidence so far |
|---|---|
| Automated checks | Python 3.10–3.13, frontend tests, installed-wheel checks and three Chromium journeys in CI |
| Destination installation | Ubuntu 24.04, Python 3.12, loopback UI and a2 report regeneration |
| Live backend | Existing-flow retrieval from Velociraptor 0.76.5 |
| Live model | One small CPU-based Ollama analysis; larger requests timed out |
| MCP | Stdio protocol and tool checks; broader interactive-client acceptance remains open |

A saved bundle can contain many more rows than the model input. Review the report's model
input coverage and request outcomes. Citation checks establish row references; analysts
must assess whether those rows support the interpretation. Findings prioritize human review
and do not establish compromise or a clean host.

## Project layout

| Path | Responsibility |
|---|---|
| `src/vr_dfir/` | Shared pipeline, API reads, analysis, evidence and reporting |
| `frontend/src/` | React analyst workspace |
| `schemas/` | Evidence and analysis contracts |
| `tests/` | Synthetic fixtures and regression tests |
| `scripts/` | Offline build, installation and verification |
| `docs/` | Usage, architecture and operations |

## Attribution and licensing

Developed by [Mohammadmahdi Rajabzadeh](https://github.com/mmrjb).
Velociraptor provides the backend. Matthew Green's
[MCP bridge](https://github.com/mgreen27/mcp-velociraptor) inspired interface conventions
and parts of the analyst workflow. This is an independent project.

A project open-source license has not been selected. Public repository visibility does
not change the licensing status described in [NOTICE](NOTICE). Preserve the attribution in
[ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) and the bundled
[third-party notices](THIRD_PARTY_NOTICES.md).

Questions and feedback about installation, evidence review or reporting are welcome in
[Issues](https://github.com/mmrjb/velociraptor-dfir-agent/issues). Use synthetic or redacted
examples and keep credentials and real evidence private.
