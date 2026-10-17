# Development

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cd frontend
npm ci
npm test -- --maxWorkers=1
npm run build
cd ..
.venv/bin/pytest
.venv/bin/ruff check src tests scripts
```

Tests use synthetic data and injected backends/providers. Do not run collections or download
models to test this edition. Keep the GUI, CLI and MCP as adapters over shared services;
fixed VQL templates must validate identifiers and be covered by the fake read backend.
Do not add a raw query or backend mutation interface. MCP stdout must be JSON-RPC only.

Browser journeys (requires an already installed Playwright Chromium browser):

```bash
export VR_DFIR_STATE_DIR="$(mktemp -d)"
export PYTHONPATH="$PWD/src:$PWD/tests"
export VR_DFIR_E2E_COMMAND="$PWD/.venv/bin/python $PWD/tests/support/e2e_server.py --port 8766"
cd frontend
npm run e2e
```

For noexec checkouts, `scripts/frontend.sh build|test` mirrors the frontend to an executable
work directory. Building a release requires a clean local git commit, preinstalled build/npm
dependencies and a supplied wheelhouse; `scripts/build_preview.py --help` gives its arguments.
The builder never resolves dependencies from a package index or downloads models.

Never commit secrets, real evidence, API configs, inventories, model requests or runtime output.
Preserve attribution. Public publication and license selection remain owner decisions.
