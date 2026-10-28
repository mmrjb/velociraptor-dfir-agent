// Application frame: three main destinations (Investigations, Reports, Settings) and a concise
// connection state. Investigation-specific views live inside the investigation workspace.
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { Activity, FileText, FolderSearch, Menu, Settings } from "lucide-react";
import { useApp } from "../lib/app";
import { Badge, ErrorBoundary } from "./ui";

const NAV = [
  { to: "/investigations", label: "Investigations", icon: FolderSearch },
  { to: "/reports", label: "Reports", icon: FileText },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Layout() {
  const app = useApp();
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const st = app.status;
  const vrTone = !st ? "neutral" : !st.velociraptor.configured ? "warn" : st.velociraptor.last_test?.reachable === false ? "bad" : st.velociraptor.last_test?.reachable ? "ok" : "neutral";
  const vrText = !st ? "Checking…" : !st.velociraptor.configured ? "Velociraptor not connected" : st.velociraptor.last_test?.reachable === false ? "Velociraptor unreachable" : st.velociraptor.last_test?.reachable ? "Velociraptor connected" : "Velociraptor configured";
  const aiOn = !!st && st.model.provider !== "none" && st.model.configured;
  const inInvestigations = location.pathname === "/" || location.pathname.startsWith("/investigations");

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
        <div className="header-spacer" />
        <NavLink to="/settings" className="status-chip" title="Connection and AI status (open Settings)">
          <span className={`dot ${vrTone}`} aria-hidden /> {vrText}
          <span className="sep" aria-hidden>
            ·
          </span>
          {aiOn ? "AI on" : "AI off"}
        </NavLink>
      </header>
      <aside className="sidebar" aria-label="Primary">
        <nav>
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `nav-link ${isActive || (to === "/investigations" && inInvestigations) ? "active" : ""}`} onClick={() => setNavOpen(false)}>
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
