import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router";
import { Layout } from "./components/Layout";
import { AppProvider } from "./lib/app";
import { InvestigationsPage } from "./pages/InvestigationsPage";
import { NewInvestigationPage } from "./pages/NewInvestigationPage";
import { ReportsPage } from "./pages/ReportsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ChangesView } from "./pages/workspace/ChangesView";
import { EvidenceView } from "./pages/workspace/EvidenceView";
import { HostsView } from "./pages/workspace/HostsView";
import { OverviewView } from "./pages/workspace/OverviewView";
import { RunView } from "./pages/workspace/RunView";
import { TimelineView } from "./pages/workspace/TimelineView";
import { Workspace } from "./pages/workspace/Workspace";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<InvestigationsPage />} />
        <Route path="investigations" element={<InvestigationsPage />} />
        <Route path="investigations/new" element={<NewInvestigationPage />} />
        <Route path="investigations/:id" element={<Workspace />}>
          <Route index element={<OverviewView />} />
          <Route path="evidence" element={<EvidenceView />} />
          <Route path="changes" element={<ChangesView />} />
          <Route path="timeline" element={<TimelineView />} />
          <Route path="hosts" element={<HostsView />} />
          <Route path="run" element={<RunView />} />
        </Route>
        <Route path="findings" element={<Navigate to="/investigations" replace />} />
        <Route path="findings/:id" element={<LegacyFindings />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

/** Bookmarks of the earlier Findings & Evidence page open the same place in the workspace. */
function LegacyFindings() {
  const { id = "" } = useParams();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  if (params.get("tab") === "evidence") params.set("mode", "rows");
  if (params.get("evidence")) params.set("ev", params.get("evidence") as string);
  if (params.get("source")) params.set("src", params.get("source") as string);
  for (const k of ["tab", "evidence", "source", "hl", "eq"]) params.delete(k);
  const qs = params.toString();
  return <Navigate to={`/investigations/${id}/evidence${qs ? `?${qs}` : ""}`} replace />;
}

function NotFound() {
  return (
    <div className="card">
      <h1>Page not found</h1>
      <p>
        <a href="/investigations">Go to Investigations</a>
      </p>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AppProvider>
  );
}
