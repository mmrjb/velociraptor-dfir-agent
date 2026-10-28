# AI preview configuration

Configuration precedence: process environment, the file passed with `--env-file` (or
`VELOCIRAPTOR_ENV_FILE`), `./.env`, then defaults. Existing process variables win. Use a
separate working directory, environment file and state directory for this preview.

```dotenv
# Absolute path to an administrator-provided read-only API credential. Empty = offline only.
VELOCIRAPTOR_API_CONFIG=
VELOCIRAPTOR_ORG_ID=root
VR_DFIR_GRPC_TIMEOUT_SECONDS=60
VR_DFIR_STATE_DIR=/absolute/private/preview/state
VR_DFIR_UI_HOST=127.0.0.1
VR_DFIR_UI_PORT=8766
# none: evidence and deterministic signals; ollama: optional model proposals.
VELOCIRAPTOR_MODEL_PROVIDER=none
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=
VR_DFIR_MODEL_TIMEOUT_SECONDS=120
VR_DFIR_MODEL_NUM_CTX=8192
VR_DFIR_MODEL_MAX_OUTPUT_TOKENS=1024
VR_DFIR_MODEL_TEMPERATURE=0
VR_DFIR_MAX_MODEL_INPUT_CHARS=12000
VR_DFIR_MAX_MODEL_REQUESTS=6
VR_DFIR_IMPORT_MAX_BYTES=52428800
# Optional presentation settings (0.5.0a1):
# JSON file mapping custom artifacts to presentation domains (see docs/presentation.md).
VR_DFIR_SOURCE_MAPPINGS=
# Comma-separated CIDR ranges labelled "Configured internal network" (display only).
VR_DFIR_INTERNAL_NETWORKS=
# Capture age after which a source is shown as older than N days (default 7).
VR_DFIR_STALE_AFTER_DAYS=7
```

To enable AI set the provider to `ollama` and `OLLAMA_MODEL` to a model already installed
on that server. No model is pulled automatically. `--no-llm` overrides analysis for an
individual run. The GUI import defaults to evidence only; uncheck it to explicitly use AI.
Requests contain bounded selected evidence, not the entire collection. Request budgets
limit work per analysis run; explicit retries/explanations can make additional requests.
Do not send private evidence to a remote Ollama server unless that destination is approved.
Hosted provider adapters are absent from this edition.

`doctor` reads local configuration. `doctor --probe --probe-model` additionally queries
backend capability metadata and the model server's availability endpoint; it does not run a
model analysis or start collections. Missing credentials leave bundle import usable.

Keep the API client and environment file private (for example mode 0600 for their owner,
or 0640 with a restricted application group). Do not copy the internal 0.3.x environment file
wholesale: its state path and port may point to the live application. The preview installer
creates fresh paths. Workspace configuration and dangerous-tool flags cannot enable removed
features. Advanced analysis-only profile files can still be supplied with CLI `--profile`;
there are no bundled collection profiles or profile management UI.
