// The report selection: pinned evidence, findings and changes with optional analyst notes.
// Unpinning never deletes evidence or changes a review decision. Pins survive reload and
// service restart (stored with the investigation).
import { useState } from "react";
import { useNavigate } from "react-router";
import { FileText, Trash2 } from "lucide-react";
import { apiPut, errorMessage } from "../../api/client";
import type { ApiError, Pin } from "../../api/types";
import { formatTime } from "../../lib/format";
import { Drawer, EmptyState, ErrorBanner } from "../../components/ui";
import type { PinsApi } from "./shared";

const KIND_LABEL: Record<string, string> = { evidence: "Evidence row", finding: "Finding", change: "Change" };

export function PinTray({ invId, pins, onClose, pinsApi }: { invId: string; pins: Pin[]; onClose: () => void; pinsApi: PinsApi }) {
  const navigate = useNavigate();
  const openPin = (p: Pin) => {
    onClose();
    if (p.kind === "evidence") navigate(`/investigations/${invId}/evidence?mode=rows&ev=${encodeURIComponent(p.ref)}`);
    else if (p.kind === "finding") navigate(`/investigations/${invId}/evidence?finding=${encodeURIComponent(p.ref)}`);
    else {
      // The Changes view is addressed by the current source and the baseline capture.
      const cur = (p.snapshot.current ?? {}) as { source_key?: string };
      const base = (p.snapshot.baseline ?? {}) as { investigation_id?: string; source_key?: string };
      const qs = new URLSearchParams({ src: cur.source_key ?? "", base: `${base.investigation_id ?? ""}|${base.source_key ?? ""}`, item: String(p.snapshot.item_key ?? "") });
      navigate(`/investigations/${invId}/changes?${qs}`);
    }
  };
  return (
    <Drawer title={`Report selection (${pins.length})`} onClose={onClose}>
      <div className="stack">
        <p className="small muted">Pinned items and notes go into the "Selected evidence" report. The full investigation report remains available separately.</p>
        {pins.length === 0 ? (
          <EmptyState title="Nothing pinned yet.">
            <p className="small">Use “Pin to report” on a finding, an evidence row or a change.</p>
          </EmptyState>
        ) : (
          <ul className="plain stack">
            {pins.map((p) => (
              <PinItem key={p.pin_id} invId={invId} pin={p} onOpen={() => openPin(p)} pinsApi={pinsApi} />
            ))}
          </ul>
        )}
        <div className="row">
          <button
            className="btn primary"
            disabled={pins.length === 0}
            onClick={() => {
              onClose();
              navigate(`/reports?investigation=${invId}&scope=selected`);
            }}
          >
            <FileText size={16} aria-hidden /> Build selected-evidence report
          </button>
        </div>
      </div>
    </Drawer>
  );
}

function PinItem({ invId, pin, onOpen, pinsApi }: { invId: string; pin: Pin; onOpen: () => void; pinsApi: PinsApi }) {
  const [note, setNote] = useState(pin.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await apiPut(`/investigations/${invId}/selection/${encodeURIComponent(pin.pin_id)}`, { note: note.trim() || null });
      pinsApi.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="pin card tight">
      <div className="row between">
        <span className="xsmall muted">{KIND_LABEL[pin.kind]}</span>
        <button className="btn ghost small" aria-label={`Unpin ${pin.title}`} onClick={() => pinsApi.unpin(pin.pin_id).catch((e) => setError(errorMessage(e)))}>
          <Trash2 size={14} aria-hidden /> Unpin
        </button>
      </div>
      <button className="btn link" onClick={onOpen} dir="auto">
        {pin.title}
      </button>
      {pin.resolved === false && <p className="field-error">This reference no longer resolves to saved evidence.</p>}
      <label className="field small">
        Analyst note
        <textarea className="note-input" value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} rows={2} />
      </label>
      <div className="row">
        <button className="btn small" disabled={busy || (note.trim() || null) === (pin.note || null)} onClick={save}>
          {busy ? "Saving…" : "Save note"}
        </button>
        <span className="xsmall muted">Pinned {formatTime(pin.created_at)}</span>
      </div>
      <ErrorBanner error={error} />
    </li>
  );
}
