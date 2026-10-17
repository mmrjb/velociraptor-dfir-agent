# GUI workflow

Open the loopback UI through the SSH tunnel described in INSTALL.md. The initial page is
Investigations, followed by Findings & Evidence and Reports. Pick the organization in the
header before reading live evidence.

1. Choose **New Investigation**. Select an existing endpoint collection or existing hunt.
   Search for the endpoint or paste its client/flow IDs. Review the source scope and analysis
   choice, then select **Analyze Existing Evidence**. There is no new-collection option.
2. Alternatively choose **Import Evidence**. Upload a version 1 bundle archive or reanalyze
   a saved bundle. Evidence-only mode is selected initially; uncheck it to use Ollama.
3. Inspect retrieval and analysis status independently. A failed model does not remove the
   retrieved evidence. Retry analysis only when you intend to make new model requests.
4. Open **Findings & Evidence**. Severity, confidence and analyst review state are separate.
   Each evidence ID opens its exact row or reports an unresolved reference; no substitute
   row is shown. Open the row in its source table to inspect surrounding evidence.
5. Use full-value dialogs for long commands/paths. Copy preserves exact values; null, empty,
   absent, false and zero are distinct. Filter, toggle columns, wrap text and page tables.
   UI text uses locally bundled body and code fonts, including Persian glyph support.
6. Save an analyst review note, request an optional cited explanation, or export a report.

Opening a report does not ask the model to produce a new analysis. Evidence and model text
are rendered as untrusted content. This is a local single-user analyst application; keep it
on loopback and use SSH, rather than exposing it directly on a public address.

Browser acceptance cases are in `frontend/e2e/`. Start the test-only server with the command
in CONTRIBUTING.md. They have not been run here because the browser download was unavailable;
component/API checks do not replace that acceptance test.
