// Settings: connection state with one test action, the organization for new live
// investigations, viewer preferences and help. Credentials and the model endpoint are configured
// on the server; they are never sent to the browser.
import { useApp } from "../lib/app";
import { formatTime } from "../lib/format";
import { setDisplayZone, useDisplayZone } from "../lib/time";
import { Badge } from "../components/ui";

export function SettingsPage() {
  const app = useApp();
  const st = app.status;
  const zone = useDisplayZone();
  const aiOn = !!st && st.model.provider !== "none" && st.model.configured;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="muted">Connection, defaults for new work and display preferences.</p>
        </div>
      </div>
      <div className="grid cols-2">
        <section className="card stack" aria-label="Connection">
          <h2>Connection</h2>
          <dl className="kv">
            <dt>Velociraptor API</dt>
            <dd>
              {!st ? "…" : st.velociraptor.configured ? (st.velociraptor.last_test?.reachable === false ? `Unavailable: ${st.velociraptor.last_test.error ?? "unknown error"}` : st.velociraptor.last_test?.reachable ? "Connected" : "Configured (not tested)") : "Not configured: saved and imported evidence still work"}
            </dd>
            <dt>AI explanations</dt>
            <dd>
              {!st ? "…" : aiOn ? (
                <>
                  On: {st.model.provider} <code>{st.model.model}</code>
                  {st.model.last_test?.reachable === false ? ` (unavailable: ${st.model.last_test.error ?? "unknown"})` : ""}
                </>
              ) : (
                "Off. Review, charts, comparison and reports work without AI."
              )}
            </dd>
            <dt>Backend access</dt>
            <dd>Read-only (existing evidence; no collection or response actions)</dd>
            <dt>Last test</dt>
            <dd>{formatTime(st?.tested_at)}</dd>
            <dt>Version</dt>
            <dd>
              {st?.version ?? "…"} <Badge tone="neutral">{st?.edition ?? ""}</Badge>
            </dd>
          </dl>
          <div className="row">
            <button className="btn primary" onClick={() => app.testConnection()} disabled={app.testing}>
              {app.testing ? "Testing…" : "Test connection"}
            </button>
            <span className="small muted">Bounded read-only checks. Nothing is collected and no model is downloaded.</span>
          </div>
          {st?.setup_hint && <p className="small">{st.setup_hint}</p>}
        </section>
        <section className="card stack" aria-label="Preferences">
          <h2>Preferences</h2>
          {app.orgs.length > 0 && (
            <label className="field">
              Organization for new investigations
              <select value={app.selectedOrg} onChange={(e) => app.setSelectedOrg(e.target.value)}>
                {app.orgs.map((o) => (
                  <option key={o.OrgId || "root"} value={o.OrgId || "root"}>
                    {o.Name ? `${o.Name} (${o.OrgId || "root"})` : o.OrgId || "root"}
                  </option>
                ))}
              </select>
              <span className="hint">Saved investigations keep their own organization.</span>
            </label>
          )}
          <label className="switch">
            <input type="checkbox" checked={app.preferences?.default_no_llm ?? false} onChange={(e) => app.savePreferences({ default_no_llm: e.target.checked }).then(() => app.toast("Preference saved"))} />
            Start new investigations as “Evidence only / no AI”
          </label>
          <label className="field">
            Rows per page in evidence tables
            <select value={app.preferences?.page_size ?? 50} onChange={(e) => app.savePreferences({ page_size: Number(e.target.value) }).then(() => app.toast("Preference saved"))}>
              {[25, 50, 100, 200].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Time zone for displayed times
            <select value={zone} onChange={(e) => setDisplayZone(e.target.value as "UTC" | "local")}>
              <option value="UTC">UTC (default)</option>
              <option value="local">This browser's local time</option>
            </select>
            <span className="hint">Display only: stored times, filters and evidence are unchanged.</span>
          </label>
        </section>
      </div>
      <section className="card stack" aria-label="Help">
        <h2>Help</h2>
        <ul className="small">
          <li>Start from Investigations: open one, read the Overview, then follow “Next step”.</li>
          <li>Evidence shows findings beside their source rows; one inspector shows the selected row. Advanced holds the exact raw JSON and provenance.</li>
          <li>Changes compares two saved captures of the same host and source. Unknown means absence could not be established.</li>
          <li>Pin findings, rows and changes, add notes, and build the “Selected evidence” report under Reports.</li>
          <li>Remote access: <code>ssh -N -L PORT:127.0.0.1:PORT user@server</code>, then open <code>http://127.0.0.1:PORT/</code>, or use the administrator's authenticated HTTPS gateway.</li>
          <li>Findings are review aids. A report without findings is not a statement that a host is clean.</li>
        </ul>
        <details className="advanced">
          <summary>Advanced diagnostics</summary>
          <pre className="pre">{JSON.stringify({ status: st, statusError: app.statusError, orgsError: app.orgsError }, null, 2)}</pre>
        </details>
      </section>
    </>
  );
}
