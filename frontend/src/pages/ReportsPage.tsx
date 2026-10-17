import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Download, Eye } from "lucide-react";
import { apiGet, errorMessage } from "../api/client";
import type { ApiError, InvestigationSummary } from "../api/types";
import { formatTime, MODE_LABELS } from "../lib/format";
import { Banner, SyntheticBadge, EmptyState, ErrorBanner, Spinner, StatusBadge } from "../components/ui";

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<InvestigationSummary[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [q, setQ] = useState("");
  const selected = params.get("investigation");
  const format = params.get("format") ?? "html";

  useEffect(() => {
    apiGet<InvestigationSummary[]>("/reports", { q })
      .then(setItems)
      .catch((e) => setError(errorMessage(e)));
  }, [q]);

  const current = items?.find((i) => i.investigation_id === selected) ?? null;
  const base = selected ? `/api/v1/investigations/${selected}/report` : "";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <p className="muted">Preview or export the canonical report (JSON) and its Markdown and HTML renderings. Partial runs include their errors and coverage.</p>
        </div>
      </div>
      <ErrorBanner error={error} />
      <div className="split">
        <div className="stack">
          <input type="search" aria-label="Search reports" placeholder="Search reports" value={q} onChange={(e) => setQ(e.target.value)} />
          {items === null ? (
            <Spinner />
          ) : items.length === 0 ? (
            <EmptyState title="No reports yet." />
          ) : (
            <div className="list">
              {items.map((i) => (
                <button key={i.investigation_id} className={`list-item ${selected === i.investigation_id ? "selected" : ""}`} onClick={() => setParams({ investigation: i.investigation_id, format })} aria-current={selected === i.investigation_id}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <strong dir="auto">{i.title}</strong>
                    <StatusBadge value={i.outcome} />
                  </div>
                  <div className="small muted">
                    {MODE_LABELS[i.mode] ?? i.mode} · {formatTime(i.created_at)} {i.synthetic && <SyntheticBadge />}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="stack">
          {!current ? (
            <div className="card">
              <p className="muted">Select a report to preview or export it.</p>
            </div>
          ) : (
            <div className="card">
              <div className="card-head">
                <h2 dir="auto">{current.title}</h2>
                <Link to={`/investigations/${current.investigation_id}`}>Open investigation</Link>
              </div>
              {current.synthetic && <Banner tone="synthetic">Synthetic test data; not collected from a real endpoint.</Banner>}
              <div className="row" style={{ marginBottom: 10 }}>
                <span className="muted">
                  <Eye size={14} aria-hidden /> Preview:
                </span>
                {["html", "md", "json"].map((f) => (
                  <button key={f} className={`btn small ${format === f ? "primary" : ""}`} aria-pressed={format === f} onClick={() => setParams({ investigation: current.investigation_id, format: f })}>
                    {f.toUpperCase()}
                  </button>
                ))}
                <span className="header-spacer" />
                {["json", "md", "html"].map((f) => (
                  <a key={f} className="btn small" href={`${base}?format=${f}&download=1`} download>
                    <Download size={14} aria-hidden /> Export {f.toUpperCase()}
                  </a>
                ))}
              </div>
              {format === "html" ? (
                <iframe title="HTML report preview" src={`${base}?format=html`} sandbox="" style={{ width: "100%", height: "70vh", border: "1px solid var(--border)", borderRadius: 6, background: "#fff" }} />
              ) : (
                <TextPreview url={`${base}?format=${format}`} />
              )}
            </div>
          )}
        </div>
      </div>
    </>
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
  return text === null ? <Spinner /> : <pre className="pre" style={{ maxHeight: "70vh" }} dir="auto">{text}</pre>;
}
