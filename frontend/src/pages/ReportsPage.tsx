// Reports: two clearly identified scopes per investigation. "Selected evidence" holds the
// pinned items, analyst notes, their exact provenance and the coverage limits of the involved
// sources; "Full investigation" is the existing comprehensive report. Opening or exporting a
// report never makes a model request.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Download, FileText, RefreshCw } from "lucide-react";
import { apiGet, apiPost, errorMessage } from "../api/client";
import type { ApiError, InvestigationSummary, Pin } from "../api/types";
import { formatTime, MODE_LABELS } from "../lib/format";
import { Banner, EmptyState, ErrorBanner, Spinner, StatusBadge, SyntheticBadge } from "../components/ui";

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const selected = params.get("investigation");
  const scope = params.get("scope") === "selected" ? "selected" : "full";
  const format = params.get("format") ?? "html";
  useEffect(() => {
    apiGet<InvestigationSummary[]>("/reports")
      .then(setItems)
      .catch((e) => setError(errorMessage(e)));
  }, []);
  const current = items?.find((i) => i.investigation_id === selected) ?? null;
  const go = (u: Record<string, string>) => setParams({ investigation: selected ?? "", scope, format, ...u });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <p className="muted">Build a report from the evidence you selected, or export the full investigation report.</p>
        </div>
      </div>
      <ErrorBanner error={error} />
      <div className="split">
        <div className="stack">
          {items === null ? (
            <Spinner />
          ) : items.length === 0 ? (
            <EmptyState title="No reports yet.">
              <p className="small">Reports appear when an investigation has been analyzed.</p>
            </EmptyState>
          ) : (
            <ul className="plain list" aria-label="Investigations with reports">
              {items.map((i) => (
                <li key={i.investigation_id}>
                  <button className={`list-item ${selected === i.investigation_id ? "selected" : ""}`} onClick={() => setParams({ investigation: i.investigation_id, scope, format })} aria-current={selected === i.investigation_id ? "true" : undefined}>
                    <div className="row between">
                      <strong dir="auto">{i.title}</strong>
                      <StatusBadge value={i.outcome} />
                    </div>
                    <div className="small muted">
                      {MODE_LABELS[i.mode] ?? i.mode} · {formatTime(i.created_at)} {i.synthetic && <SyntheticBadge />}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="stack">
          {!current ? (
            <div className="card">
              <p className="muted">Select an investigation to build or export its report.</p>
            </div>
          ) : (
            <div className="card">
              <div className="card-head">
                <h2 dir="auto">{current.title}</h2>
                <Link to={`/investigations/${current.investigation_id}`}>Open investigation</Link>
              </div>
              <div className="seg" role="tablist" aria-label="Report scope">
                <button role="tab" aria-selected={scope === "selected"} className={`seg-btn ${scope === "selected" ? "on" : ""}`} onClick={() => go({ scope: "selected" })}>
                  Selected evidence
                </button>
                <button role="tab" aria-selected={scope === "full"} className={`seg-btn ${scope === "full" ? "on" : ""}`} onClick={() => go({ scope: "full" })}>
                  Full investigation
                </button>
              </div>
              {scope === "selected" ? <SelectedScope inv={current} format={format} onFormat={(f) => go({ format: f })} /> : <ReportPreview inv={current.investigation_id} scope="full" format={format} onFormat={(f) => go({ format: f })} />}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function SelectedScope({ inv, format, onFormat }: { inv: InvestigationSummary; format: string; onFormat: (f: string) => void }) {
  const id = inv.investigation_id;
  const [pins, setPins] = useState<Pin[] | null>(null);
  const [built, setBuilt] = useState<{ generated_at: string; items: number; limitations: string[] } | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    setBuilt(null);
    apiGet<{ pins: Pin[] }>(`/investigations/${id}/selection`)
      .then((d) => setPins(d.pins))
      .catch((e) => setError(errorMessage(e)));
  }, [id]);
  const build = async () => {
    setBusy(true);
    setError(null);
    try {
      setBuilt(await apiPost(`/investigations/${id}/selected-report`, {}));
      setVersion((v) => v + 1);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  if (pins === null) return error ? <ErrorBanner error={error} /> : <Spinner />;
  return (
    <div className="stack">
      <p className="small">
        {pins.length} pinned item(s) with analyst notes, their exact source rows and capture times, the comparison policy for pinned changes, and the coverage limits of the involved sources.
      </p>
      {pins.length === 0 ? (
        <Banner tone="info" title="Nothing is pinned yet">
          Use “Pin to report” on a finding, an evidence row or a change in the <Link to={`/investigations/${id}/evidence`}>investigation</Link>.
        </Banner>
      ) : (
        <div className="row">
          <button className="btn primary" onClick={build} disabled={busy}>
            {built ? <RefreshCw size={16} aria-hidden /> : <FileText size={16} aria-hidden />} {busy ? "Building…" : built ? "Rebuild from current pins" : "Build selected-evidence report"}
          </button>
          {built && <span className="small muted">Built {formatTime(built.generated_at)} with {built.items} item(s).</span>}
        </div>
      )}
      <ErrorBanner error={error} />
      {built?.limitations.length ? (
        <Banner tone="warn" title="Coverage limits of the selected evidence">
          <ul className="small">
            {built.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
      {pins.length > 0 && <ReportPreview inv={id} scope="selected" format={format} onFormat={onFormat} version={version} />}
    </div>
  );
}

function ReportPreview({ inv, scope, format, onFormat, version = 0 }: { inv: string; scope: "full" | "selected"; format: string; onFormat: (f: string) => void; version?: number }) {
  const base = `/api/v1/investigations/${inv}/report`;
  const [exists, setExists] = useState<boolean | null>(null);
  useEffect(() => {
    setExists(null);
    fetch(`${base}?format=json&scope=${scope}`, { credentials: "same-origin" })
      .then((r) => setExists(r.ok))
      .catch(() => setExists(false));
  }, [base, scope, version]);
  if (exists === null) return <Spinner />;
  if (!exists) return <p className="small muted">{scope === "selected" ? "Build the report to preview and export it." : "The full report has not been generated yet."}</p>;
  return (
    <div className="stack">
      <div className="row">
        <span className="small muted">Preview:</span>
        {["html", "md", "json"].map((f) => (
          <button key={f} className={`btn small ${format === f ? "primary" : ""}`} aria-pressed={format === f} onClick={() => onFormat(f)}>
            {f.toUpperCase()}
          </button>
        ))}
        <span className="header-spacer" />
        {["html", "md", "json"].map((f) => (
          <a key={f} className="btn small" href={`${base}?format=${f}&scope=${scope}&download=1`} download>
            <Download size={14} aria-hidden /> {f.toUpperCase()}
          </a>
        ))}
      </div>
      {format === "html" ? (
        <iframe key={version} title="HTML report preview" src={`${base}?format=html&scope=${scope}&v=${version}`} sandbox="" className="report-frame" />
      ) : (
        <TextPreview url={`${base}?format=${format}&scope=${scope}&v=${version}`} />
      )}
    </div>
  );
}

function TextPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    setText(null);
    fetch(url, { credentials: "same-origin" })
      .then((r) => r.text())
      .then((t) => setText(t.length > 400000 ? `${t.slice(0, 400000)}\n… (preview truncated; export for the full file)` : t))
      .catch(() => setText("Could not load the preview."));
  }, [url]);
  return text === null ? <Spinner /> : <pre className="pre report-text" dir="auto">{text}</pre>;
}
