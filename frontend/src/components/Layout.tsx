import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { Activity, FileText, FolderSearch, Menu, Search, Settings } from "lucide-react";
import { useApp } from "../lib/app";
import { Badge, Drawer, ErrorBoundary } from "./ui";
import { formatTime } from "../lib/format";

const NAV = [
  { to: "/investigations", label: "Investigations", icon: FolderSearch, end: false },
  { to: "/findings", label: "Findings & Evidence", icon: Search },
  { to: "/reports", label: "Reports", icon: FileText },
];

export function Layout() {
  const app = useApp();
  const [navOpen, setNavOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const location = useLocation();
  const st = app.status;

  const vrTone = !st ? "neutral" : !st.velociraptor.configured ? "warn" : st.velociraptor.last_test?.reachable === false ? "bad" : st.velociraptor.last_test?.reachable ? "ok" : "neutral";
  const vrText = !st
    ? "Checking…"
    : !st.velociraptor.configured
      ? "Not configured"
      : st.velociraptor.last_test?.reachable === false
        ? "Disconnected"
        : st.velociraptor.last_test?.reachable
          ? "Connected"
          : "Configured (not tested)";
  const modelTone = !st ? "neutral" : st.model.provider === "none" ? "neutral" : !st.model.configured ? "warn" : st.model.last_test?.reachable === false ? "bad" : st.model.last_test?.reachable ? "ok" : "neutral";
  const modelText = !st
    ? "…"
    : st.model.provider === "none"
      ? "AI disabled"
      : `${st.model.model ?? "model not set"}${st.model.last_test?.reachable === false ? " (unavailable)" : ""}`;

  return (
    <div className={`app ${navOpen ? "nav-open" : ""}`}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="header">
        <button className="btn ghost menu-button" aria-label="Toggle navigation" aria-expanded={navOpen} onClick={() => setNavOpen((v) => !v)}>
          <Menu size={18} aria-hidden />
        </button>
        <a className="brand" href="/" aria-label="Velociraptor DFIR Agent home">
          <span className="brand-mark" aria-hidden>
            <Activity size={16} />
          </span>
          <span className="brand-text">Velociraptor DFIR Agent</span>
        </a>
        {st?.velociraptor.synthetic && <Badge tone="synthetic">Synthetic test server</Badge>}
        <div className="header-context" aria-label="Current context">
          <label className="row small">
            <span className="muted">Org</span>
            {app.orgs.length > 0 ? (
              <select aria-label="Selected organization" value={app.selectedOrg} onChange={(e) => app.setSelectedOrg(e.target.value)}>
                {app.orgs.map((o) => (
                  <option key={o.OrgId || "root"} value={o.OrgId || "root"}>
                    {o.Name ? `${o.Name} (${o.OrgId || "root"})` : o.OrgId || "root"}
                  </option>
                ))}
              </select>
            ) : (
              <code aria-label="Selected organization">{app.selectedOrg || "-"}</code>
            )}
          </label>
          {app.investigationContext && location.pathname !== "/" && (
            <span className="crumb small" title={app.investigationContext.title}>
              Investigation: {app.investigationContext.title}
            </span>
          )}
        </div>
        <div className="header-spacer" />
        <span className="status-chip" title="Velociraptor API connection">
          <span className={`dot ${vrTone}`} aria-hidden /> Velociraptor: {vrText}
        </span>
        <span className="status-chip" title="Analysis model">
          <span className={`dot ${modelTone}`} aria-hidden /> Model: {modelText}
        </span>
        <button className="btn" onClick={() => setSettingsOpen(true)} aria-haspopup="dialog" aria-label="Settings & help">
          <Settings size={16} aria-hidden /> <span className="btn-text">Settings &amp; help</span>
        </button>
      </header>
      <aside className="sidebar" aria-label="Primary">
        <nav>
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`} onClick={() => setNavOpen(false)}>
              <Icon size={18} aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">AI preview · Read-only backend · {app.session?.version ?? "…"}</div>
      </aside>
      <main className="main" id="main" tabIndex={-1}>
        <ErrorBoundary label="This page" resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
      <div className="toasts" aria-live="polite">
        {app.toasts.map((t) => (
          <div className="toast" key={t.id}>
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

function SettingsPanel({ onClose }: { onClose: () => void }) {
  const app = useApp();
  const st = app.status;
  return (
    <Drawer title="Settings & help" onClose={onClose}>
      <section className="stack">
        <h3>Connection</h3>
        <dl className="kv">
          <dt>Velociraptor API</dt>
          <dd>{st?.velociraptor.configured ? "Configured on the server" : "Not configured (saved and imported evidence still work)"}</dd>
          <dt>Default org</dt>
          <dd>
            <code>{st?.velociraptor.default_org ?? "-"}</code>
          </dd>
          <dt>Selected org</dt>
          <dd>
            <code>{app.selectedOrg}</code> <span className="muted small">(applies to new work only)</span>
          </dd>
          <dt>Analysis provider</dt>
          <dd>
            {st?.model.provider ?? "-"} {st?.model.model ? <code>{st.model.model}</code> : null} {st?.model.hosted ? <strong>(hosted service)</strong> : null}
          </dd>
          <dt>Last test</dt>
          <dd>{formatTime(st?.tested_at)}</dd>
          {st?.velociraptor.last_test && (
            <>
              <dt>Velociraptor test</dt>
              <dd>{st.velociraptor.last_test.reachable ? "Reachable" : `Unavailable: ${st.velociraptor.last_test.error ?? "unknown error"}`}</dd>
            </>
          )}
          {st?.model.last_test && (
            <>
              <dt>Model test</dt>
              <dd>
                {st.model.last_test.reachable === null
                  ? "Not probed (hosted service)"
                  : st.model.last_test.reachable
                    ? st.model.last_test.model_available === false
                      ? "Server reachable, model not available"
                      : "Reachable"
                    : `Unavailable: ${st.model.last_test.error ?? "unknown error"}`}
              </dd>
            </>
          )}
        </dl>
        <div className="row">
          <button className="btn primary" onClick={() => app.testConnection()} disabled={app.testing}>
            {app.testing ? "Testing…" : "Test Connection"}
          </button>
          <span className="muted small">Bounded read-only checks. Nothing is collected and no model is downloaded.</span>
        </div>
        {st?.setup_hint && <p className="small">{st.setup_hint}</p>}
        <div className="divider" />
        <h3>Preferences</h3>
        <label className="switch">
          <input type="checkbox" checked={app.preferences?.default_no_llm ?? false} onChange={(e) => app.savePreferences({ default_no_llm: e.target.checked }).then(() => app.toast("Preference saved"))} />
          Default to "Evidence only / no AI"
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
        <div className="divider" />
        <h3>Help</h3>
        <ul className="small">
          <li>Credentials and the model endpoint are configured on the server (environment file). They are never sent to the browser.</li>
          <li>Remote access: <code>ssh -N -L 8766:127.0.0.1:8766 user@server</code>, then open <code>http://127.0.0.1:8766/</code>.</li>
          <li>Analysis reads existing evidence. This edition cannot launch collections or change the Velociraptor server.</li>
          <li>Findings are review aids. A report without findings is not a statement that a host is clean.</li>
        </ul>
        <details className="advanced">
          <summary>Advanced diagnostics</summary>
          <pre className="pre">{JSON.stringify({ status: st, statusError: app.statusError, orgsError: app.orgsError }, null, 2)}</pre>
        </details>
      </section>
    </Drawer>
  );
}
