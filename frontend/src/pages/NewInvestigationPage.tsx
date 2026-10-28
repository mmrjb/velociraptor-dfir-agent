import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { FolderOpen, Upload } from "lucide-react";
import { apiGet, apiPost, apiUpload, errorMessage, newIdempotencyKey } from "../api/client";
import type { ApiError, ClientInfo, FlowSummary, HuntSummary, PlanResponse } from "../api/types";
import { useApp } from "../lib/app";
import { formatTime } from "../lib/format";
import { Badge, Banner, EmptyState, ErrorBanner, Spinner, SyntheticBadge } from "../components/ui";

type Mode = "existing_flow" | "existing_hunt";

export function NewInvestigationPage() {
  const app = useApp();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [importing, setImporting] = useState(params.get("mode") === "import");
  const [mode, setMode] = useState<Mode>(params.get("mode") === "existing_hunt" ? "existing_hunt" : "existing_flow");
  const [query, setQuery] = useState("");
  const [clients, setClients] = useState<ClientInfo[]>([]);
  const [clientId, setClientId] = useState(params.get("client") ?? "");
  const [flowId, setFlowId] = useState(params.get("flow") ?? "");
  const [huntId, setHuntId] = useState(params.get("hunt") ?? "");
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [hunts, setHunts] = useState<HuntSummary[]>([]);
  const [title, setTitle] = useState("");
  const [noLlm, setNoLlm] = useState(app.preferences?.default_no_llm ?? false);
  const [selectedSources, setSelectedSources] = useState("");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const org = app.selectedOrg;
  const idempotency = useRef(newIdempotencyKey());
  const scopeGeneration = useRef(0);
  const searchGeneration = useRef(0);

  useEffect(() => {
    scopeGeneration.current += 1;
    setPlan(null);
    idempotency.current = newIdempotencyKey();
  }, [mode, org, clientId, flowId, huntId, noLlm, selectedSources]);

  useEffect(() => {
    searchGeneration.current += 1;
    setClients([]);
    setFlows([]);
    setHunts([]);
  }, [org]);

  useEffect(() => {
    if (!app.status?.velociraptor.configured || !org) return;
    let active = true;
    setLoadingEvidence(true);
    setError(null);
    const request = mode === "existing_hunt"
      ? apiGet<HuntSummary[]>("/hunts", { org_id: org }).then((rows) => { if (active) setHunts(rows); })
      : /^C\.[A-Za-z0-9]+$|^server$/.test(clientId)
        ? apiGet<FlowSummary[]>("/flows", { client_id: clientId, org_id: org }).then((rows) => { if (active) setFlows(rows); })
        : Promise.resolve();
    request.catch((e) => { if (active) setError(errorMessage(e)); })
      .finally(() => { if (active) setLoadingEvidence(false); });
    return () => { active = false; };
  }, [mode, org, clientId, app.status?.velociraptor.configured]);

  async function searchClients() {
    const generation = ++searchGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const result = await apiGet<{ clients: ClientInfo[] }>("/clients", { org_id: org, q: query });
      if (generation !== searchGeneration.current) return;
      setClients(result.clients);
      if (result.clients.length === 1) { setClientId(result.clients[0].client_id); setFlowId(""); }
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function review() {
    const generation = scopeGeneration.current;
    setBusy(true);
    setError(null);
    try {
      const response = await apiPost<PlanResponse>("/plans", {
        mode, org_id: org, no_llm: noLlm,
        ...(mode === "existing_flow" ? { client_id: clientId.trim(), flow_id: flowId.trim() } : { hunt_id: huntId.trim(), max_clients: 200 }),
        ...(selectedSources.trim() ? { selected_sources: selectedSources.split(/[,\n]+/).map((x) => x.trim()).filter(Boolean) } : {}),
      });
      if (generation === scopeGeneration.current) setPlan(response);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  async function start() {
    if (!plan?.plan_token || plan.plan.launches !== 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await apiPost<{ investigation_id: string }>("/investigations", {
        plan_token: plan.plan_token, title: title.trim() || null,
      }, { "Idempotency-Key": idempotency.current });
      navigate(`/investigations/${result.investigation_id}`);
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  const choices: [string, string, string][] = [
    ["existing_flow", "Existing collection on a host", "Results of a collection that already ran on one endpoint."],
    ["existing_hunt", "Existing hunt", "Results already returned by a hunt across several endpoints."],
    ["import", "Evidence bundle", "Import a saved bundle, or reopen evidence saved here."],
  ];
  const choose = (key: string) => {
    if (key === "import") setImporting(true);
    else {
      setImporting(false);
      setMode(key as Mode);
      setPlan(null);
    }
  };
  const chooser = (
    <div className="choices" role="group" aria-label="Where the evidence comes from">
      {choices.map(([key, label, text]) => {
        const disabled = key !== "import" && !!app.status && !app.status.velociraptor.configured;
        const selected = key === "import" ? importing : !importing && mode === key;
        return (
          <button key={key} className={`choice-card ${selected ? "selected" : ""}`} aria-pressed={selected} disabled={disabled} onClick={() => choose(key)}>
            <span className="choice-title">{key === "import" ? <Upload size={16} aria-hidden /> : <FolderOpen size={16} aria-hidden />} {label}</span>
            <span className="small muted">{disabled ? "Needs the Velociraptor API connection (Settings)." : text}</span>
          </button>
        );
      })}
    </div>
  );
  const head = (
    <div className="page-head"><div><h1>Start investigation</h1><p className="muted">Choose where the evidence comes from. Existing results are read; nothing new is collected from endpoints.</p></div></div>
  );
  if (importing || (app.status && !app.status.velociraptor.configured)) return <>{head}{chooser}<ImportEvidence onCancel={app.status?.velociraptor.configured ? () => setImporting(false) : undefined} /></>;
  return <>
    {head}
    {chooser}
    <ErrorBanner error={error} />
    {!app.status ? <Spinner label="Checking configuration" /> : <div className="stack">
      <div className="card stack">
        <h2>1. Select existing evidence</h2>
        {app.orgs.length > 1 ? (
          <label className="field small">Organization<select aria-label="Organization" value={org} onChange={(e) => app.setSelectedOrg(e.target.value)}>
            {app.orgs.map((o) => <option key={o.OrgId || "root"} value={o.OrgId || "root"}>{o.Name ? `${o.Name} (${o.OrgId || "root"})` : o.OrgId || "root"}</option>)}
          </select></label>
        ) : <p className="small muted">Organization: <code>{org}</code></p>}
        {mode === "existing_flow" ? <>
          <div className="row"><label className="field" style={{ flex: 1 }}>Find endpoint<input aria-label="Find endpoint" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Hostname, FQDN, or client ID" onKeyDown={(e) => { if (e.key === "Enter") searchClients(); }} /></label>
            <button className="btn" onClick={searchClients} disabled={busy}>Search</button></div>
          {clients.length > 0 && <label className="field">Search results<select aria-label="Search results" value={clientId} onChange={(e) => { setClientId(e.target.value); setFlowId(""); setFlows([]); }}><option value="">Choose an endpoint</option>{clients.map((c) => <option key={c.client_id} value={c.client_id}>{c.hostname || c.fqdn || c.client_id} · {c.client_id}</option>)}</select></label>}
          <label className="field">Client ID<input aria-label="Client ID" value={clientId} onChange={(e) => { setClientId(e.target.value); setFlowId(""); setFlows([]); }} placeholder="C.…" /></label>
          {loadingEvidence ? <Spinner label="Reading collections" /> : <label className="field">Existing collections<select aria-label="Existing collections" value={flowId} onChange={(e) => setFlowId(e.target.value)}><option value="">Choose a collection or enter its ID below</option>{flows.map((f) => <option key={f.flow_id} value={f.flow_id}>{f.artifacts.join(", ")} · {f.state} · {formatTime(f.created)} · {f.flow_id}</option>)}</select></label>}
          <label className="field">Flow ID<input aria-label="Flow ID" value={flowId} onChange={(e) => setFlowId(e.target.value)} placeholder="F.…" /></label>
        </> : <>
          {loadingEvidence ? <Spinner label="Reading hunts" /> : <label className="field">Existing hunts<select aria-label="Existing hunts" value={huntId} onChange={(e) => setHuntId(e.target.value)}><option value="">Choose a hunt or enter its ID below</option>{hunts.map((h) => <option key={h.hunt_id} value={h.hunt_id}>{h.description || h.artifacts.join(", ")} · {h.hunt_id}</option>)}</select></label>}
          <label className="field">Hunt ID<input aria-label="Hunt ID" value={huntId} onChange={(e) => setHuntId(e.target.value)} placeholder="H.…" /></label>
          <p className="small muted">Reads existing results for up to 200 clients. Use the CLI for a narrower client filter or a different bounded limit.</p>
        </>}
      </div>
      <div className="card stack"><h2>2. Choose analysis</h2>
        <label className="field">Investigation title (optional)<input aria-label="Investigation title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} /></label>
        <label className="check"><input type="checkbox" checked={noLlm} onChange={(e) => setNoLlm(e.target.checked)} /> Evidence only / no AI</label>
        <p className="small muted">{noLlm ? "Save evidence, deterministic review signals, and a report without calling a model." : `Use the configured Ollama model (${app.status.model.model || "not set"}) with bounded, citation-checked requests. Evidence remains available if the model fails.`}</p>
        <details className="advanced"><summary>Limit evidence sources</summary><label className="field">Source names (optional, comma or newline separated)<textarea aria-label="Limit evidence sources" value={selectedSources} onChange={(e) => setSelectedSources(e.target.value)} placeholder="Leave empty to use all available sources" /></label></details>
        <button className="btn primary" disabled={busy || !org || (mode === "existing_flow" ? !clientId.trim() || !flowId.trim() : !huntId.trim())} onClick={review}>{busy ? "Checking…" : "Review evidence scope"}</button>
      </div>
      {plan && <div className="card stack"><div className="row"><h2>3. Review and analyze</h2><Badge tone={plan.plan.ok ? "ok" : "bad"}>{plan.plan.ok ? "Scope checked" : "Needs attention"}</Badge></div>
        <p>Org <code>{plan.plan.org_id}</code> · {noLlm ? "Evidence only" : "AI-assisted review"} · No new collections</p>
        {plan.plan.errors.map((x, i) => <Banner key={i} tone="bad">{x}</Banner>)}
        {plan.plan.warnings.map((x, i) => <Banner key={i} tone="warn">{x}</Banner>)}
        {plan.plan.collections.map((c, i) => <div key={i}><strong>{c.artifact}</strong><ul className="small">{c.sources.map((s) => <li key={s.full_name}><code>{s.full_name}</code></li>)}</ul></div>)}
        <button className="btn primary" onClick={start} disabled={busy || !plan.plan.ok || !plan.plan_token || plan.plan.launches !== 0}>{busy ? "Starting analysis…" : "Analyze Existing Evidence"}</button>
      </div>}
    </div>}
  </>;
}

function ImportEvidence({ onCancel }: { onCancel?: () => void }) {
  const navigate = useNavigate();
  const [noLlm, setNoLlm] = useState(true);
  const [bundles, setBundles] = useState<{ investigation_id: string; title: string; synthetic: boolean; rows: number }[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    apiGet<typeof bundles>("/bundles").then(setBundles).catch((e) => setError(errorMessage(e)));
  }, []);

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiUpload<{ investigation_id: string }>("/imports", file, { title: `Imported: ${file.name}`, no_llm: String(noLlm) });
      navigate(`/investigations/${r.investigation_id}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const reanalyze = async (id: string) => {
    setBusy(true);
    try {
      const r = await apiPost<{ investigation_id: string }>(`/bundles/${id}/analyze`, { no_llm: noLlm });
      navigate(`/investigations/${r.investigation_id}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <>
      {onCancel === undefined && <Banner tone="info" title="Velociraptor access is not configured on this server">Saved evidence and bundle import work without it. Configure the API in Settings to read existing collections and hunts.</Banner>}
      <ErrorBanner error={error} />
      <label className="check card">
        <input type="checkbox" checked={noLlm} onChange={(e) => setNoLlm(e.target.checked)} />
        Evidence only / no AI (uncheck to use the configured Ollama model)
      </label>
      <div className="grid cols-2">
        <div className="card">
          <h2>
            <Upload size={16} aria-hidden /> Upload a bundle
          </h2>
          <p className="small muted">A .tar.gz or .zip containing one evidence bundle (manifest.json + sources/*.jsonl). The server checks the manifest, the format version, file paths and row hashes, and rejects links and unsafe paths.</p>
          <input ref={fileRef} type="file" accept=".gz,.tgz,.zip,application/gzip,application/zip" aria-label="Evidence bundle archive" />
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" onClick={upload} disabled={busy}>
              {busy ? "Importing…" : (noLlm ? "Import evidence" : "Import and analyze with AI")}
            </button>
          </div>
        </div>
        <div className="card">
          <h2>
            <FolderOpen size={16} aria-hidden /> Saved evidence
          </h2>
          {bundles === null ? (
            <Spinner />
          ) : bundles.length === 0 ? (
            <EmptyState title="No saved evidence in this run store yet." />
          ) : (
            <div className="list">
              {bundles.map((b) => (
                <div key={b.investigation_id} className="list-item">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span>
                      {b.title} {b.synthetic && <SyntheticBadge />}
                      <div className="small muted mono">
                        {b.investigation_id} · {b.rows ?? 0} rows
                      </div>
                    </span>
                    <button className="btn small" onClick={() => reanalyze(b.investigation_id)} disabled={busy}>
                      Analyze again
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
