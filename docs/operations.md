# Operations and MCP

Use separate state and port 8766 for the preview. Keep the original 0.3.x service running
independently. Only configure approved API/model endpoints and retain private file permissions.
There is no automatic installation, collection, model download, or public exposure on launch.

## MCP

Point an MCP client to the absolute preview executable, with an absolute environment path:

```json
{
  "mcpServers": {
    "velociraptor-ai-preview": {
      "command": "/absolute/path/preview/venv/bin/vr-dfir-mcp",
      "env": { "VELOCIRAPTOR_ENV_FILE": "/absolute/path/preview/preview.env" }
    }
  }
}
```

The 16 tools list organizations/clients, read existing results/status/pages, start local
analysis of existing flows/hunts, list or inspect investigations, resume local retrieval,
retry analysis, and retrieve evidence/reports. Starting analysis can contact the configured
Ollama server and persist evidence locally. No tool launches a collection, creates a hunt,
changes an endpoint, or accepts arbitrary VQL. Standard output contains JSON-RPC only.

## Live lab acceptance

Start with one existing, approved lab flow and `--no-llm`. Compare source counts and selected
rows to Velociraptor. Repeat with Ollama, open every cited row, inspect rejected proposals,
and compare evidence IDs/hashes before and after retry. Confirm missing permissions and API
failures are visible errors. Run MCP from your real client. These checks are pending for
this packaged candidate; see VALIDATION.md. They require no new endpoint collection.

The default maximum is 6 model requests per analysis run, including repairs, with a bounded
input and output. Retrying or asking for another explanation is a separate action. Model
quality depends on its weights, context and evidence selection; no accuracy claim is made.
