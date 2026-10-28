// Investigation workspace: a visible scope bar (organization, hosts, capture time) and four
// local views. Hosts & outliers and Run details are contextual sub-views, not extra tabs. The
// report selection tray (pins and notes) is reachable from every view.
import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useParams } from "react-router";
import { Bookmark, FileText } from "lucide-react";
import { apiDelete, apiGet, apiPost, errorMessage } from "../../api/client";
import type { ApiError, InvestigationDetail, Pin, SourcesView } from "../../api/types";
import { useApp, usePolling } from "../../lib/app";
import { formatTime, MODE_LABELS } from "../../lib/format";
import { useView } from "../../lib/useView";
import { Badge, ErrorBanner, Spinner, StatusBadge, SyntheticBadge } from "../../components/ui";
import { PinTray } from "./PinTray";
import type { PinsApi, WorkspaceCtx } from "./shared";

const TABS = [
  { to: "", label: "Overview", end: true },
  { to: "evidence", label: "Evidence" },
  { to: "changes", label: "Changes" },
  { to: "timeline", label: "Timeline" },
];

export function Workspace() {
  const { id = "" } = useParams();
  const app = useApp();
  const location = useLocation();
  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [trayOpen, setTrayOpen] = useState(false);

  const loadDetail = useCallback(async () => {
    try {
      const d = await apiGet<InvestigationDetail>(`/investigations/${id}`);
      setDetail(d);
      setError(null);
      app.setInvestigationContext({ id, title: d.investigation.title });
      return d.job_active || d.investigation.job?.state === "queued";
    } catch (e) {
      setError(errorMessage(e));
      return false;
    }
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadPins = useCallback(async () => {
    try {
      setPins((await apiGet<{ pins: Pin[] }>(`/investigations/${id}/selection`)).pins);
    } catch {
      setPins([]);
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    loadDetail();
    loadPins();
  }, [loadDetail, loadPins]);

  const active = !!detail && (detail.job_active || detail.investigation.job?.state === "queued");
  usePolling(async () => {
    const keep = await loadDetail();
    return keep ? undefined : false;
  }, 1500, active);

  const sourcesView = useView<SourcesView>(detail && !active ? `/investigations/${id}/views/sources` : null, { v: detail?.investigation.updated_at });

  const pinsApi: PinsApi = useMemo(
    () => ({
      count: pins.length,
      isPinned: (kind, ref) => pins.find((p) => p.kind === kind && p.ref === ref)?.pin_id ?? null,
      pin: async (body) => {
        await apiPost(`/investigations/${id}/selection`, body);
        await loadPins();
        app.toast("Pinned to the report selection");
      },
      unpin: async (pinId) => {
        await apiDelete(`/investigations/${id}/selection/${encodeURIComponent(pinId)}`);
        await loadPins();
        app.toast("Removed from the report selection (evidence and reviews are unchanged)");
      },
      open: () => setTrayOpen(true),
      reload: loadPins,
    }),
    [pins, id, loadPins, app],
  );

  if (!detail) return error ? <ErrorBanner error={error} onRetry={loadDetail} /> : <Spinner />;
  const inv = detail.investigation;
  const sources = sourcesView.data?.sources ?? [];
  const hosts = [...new Map(sources.map((s) => [s.client_id, s.hostname || s.client_id])).values()];
  const captured = sources.map((s) => s.collected_at).filter(Boolean).sort() as string[];
  const orgs = [...new Set(sources.map((s) => s.org_id).filter(Boolean))];
  const org = orgs.length ? orgs.join(", ") : inv.scope?.org_id || "unknown";
  const ctx: WorkspaceCtx = { invId: id, detail, reloadDetail: loadDetail, sources: sourcesView.data, aiEnabled: !!app.status && app.status.model.provider !== "none" && app.status.model.configured, pins: pinsApi };
  const sub = location.pathname.split("/")[3] ?? "";

  return (
    <div className="ws">
      <header className="scope-bar" aria-label="Investigation scope">
        <div className="scope-main">
          <h1 dir="auto">{inv.title}</h1>
          <dl className="scope-facts">
            <div>
              <dt>Organization</dt>
              <dd>
                <code>{org}</code>
              </dd>
            </div>
            <div>
              <dt>{hosts.length === 1 ? "Host" : "Hosts"}</dt>
              <dd dir="auto">{hosts.length === 0 ? inv.scope?.hostname || inv.scope?.client_id || inv.scope?.hunt_id || "-" : hosts.length === 1 ? hosts[0] : `${hosts.length} hosts`}</dd>
            </div>
            <div>
              <dt>Captured</dt>
              <dd>{captured.length ? (captured[0] === captured[captured.length - 1] ? formatTime(captured[0]) : `${formatTime(captured[0])} – ${formatTime(captured[captured.length - 1])}`) : "unknown"}</dd>
            </div>
            <div>
              <dt>Evidence</dt>
              <dd>{MODE_LABELS[inv.mode] ?? inv.mode}</dd>
            </div>
          </dl>
        </div>
        <div className="scope-actions">
          {inv.synthetic && <SyntheticBadge />}
          {(active || ["partial", "failed", "interrupted"].includes(inv.outcome)) && (
            <NavLink className="btn small" to={`/investigations/${id}/run`}>
              {active ? <Spinner label={inv.phase === "analyzing" ? "Analyzing" : "Reading"} /> : <StatusBadge value={inv.outcome} />} Run details
            </NavLink>
          )}
          <button className="btn small" onClick={() => setTrayOpen(true)} aria-haspopup="dialog">
            <Bookmark size={15} aria-hidden /> Report selection <Badge tone={pins.length ? "info" : "neutral"}>{pins.length}</Badge>
          </button>
          {detail.actions.export_report && (
            <NavLink className="btn small" to={`/reports?investigation=${id}`}>
              <FileText size={15} aria-hidden /> Reports
            </NavLink>
          )}
        </div>
      </header>
      <nav className="ws-tabs" aria-label="Investigation views">
        {TABS.map((t) => (
          <NavLink key={t.label} to={t.to ? `/investigations/${id}/${t.to}` : `/investigations/${id}`} end={t.end} className={({ isActive }) => `ws-tab ${isActive || (t.to === "" && sub === "hosts") ? "active" : ""}`}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <ErrorBanner error={error} />
      <Outlet context={ctx} />
      {trayOpen && <PinTray invId={id} pins={pins} onClose={() => setTrayOpen(false)} pinsApi={pinsApi} />}
    </div>
  );
}
