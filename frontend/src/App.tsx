import { BrowserRouter, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { AppProvider } from "./lib/app";
import { FindingsPage } from "./pages/FindingsPage";
import { InvestigationDetailPage } from "./pages/InvestigationDetailPage";
import { InvestigationsPage } from "./pages/InvestigationsPage";
import { NewInvestigationPage } from "./pages/NewInvestigationPage";
import { ReportsPage } from "./pages/ReportsPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<InvestigationsPage />} />
        <Route path="investigations" element={<InvestigationsPage />} />
        <Route path="investigations/new" element={<NewInvestigationPage />} />
        <Route path="investigations/:id" element={<InvestigationDetailPage />} />
        <Route path="findings" element={<FindingsPage />} />
        <Route path="findings/:id" element={<FindingsPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function NotFound() {
  return (
    <div className="card">
      <h1>Page not found</h1>
      <p>
        <a href="/">Go to Investigations</a> · <a href="/investigations">Investigations</a>
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
