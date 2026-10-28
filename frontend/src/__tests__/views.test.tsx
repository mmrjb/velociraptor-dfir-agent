import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { AppRoutes } from "../App";
import { AppProvider } from "../lib/app";
import { Histogram, PartBar } from "../components/charts";
import { EvidenceInspector } from "../components/Inspector";
import { CommandValue, executableEnd } from "../components/renderers";
import { exactText, valueKind } from "../components/values";
import { baseRoutes, mockApi } from "./mockApi";

const INV = "inv-20260101-000000-abc123";
const B = `/api/v1/investigations/${INV}`;
const EID = "ev-aaaaaaaaaaaaaaaaaaaa";

function renderAt(path: string) {
  return render(
    <AppProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProvider>,
  );
}

const source = { source_key: "k1", label: "Processes — lab-app-02", full_name: "LTH.SystemBaseline/Processes", domain: "process", client_id: "C.SYNTH0000000002", hostname: "lab-app-02", org_id: "OSYNTHLAB", flow_id: "F.SYNTH0002", hunt_id: null, rows: 160, state: "complete", state_label: "Read completely", read_status: "complete", result_status: "rows", stop_reason: null, collected_at: "2026-01-01T00:00:00Z", age_days: 2, stale: false, timed_rows: 160, untimed_rows: 0, counts_only: false };

const finding = (id: string, sev: string, title: string) => ({
  finding_id: id, title, observation: "obs", interpretation: "int", evidence_ids: [EID], severity: sev, confidence: "low", confidence_rationale: "r",
  benign_explanations: ["Admin test"], missing_context: [], next_steps: [], origin: "deterministic_rule", client_ids: ["C.SYNTH0000000002"], sources: ["LTH.SystemBaseline/Processes"],
  evidence_refs: [{ evidence_id: EID, client_id: "C.SYNTH0000000002", hostname: "lab-app-02", flow_id: "F.SYNTH0002", artifact: "LTH.SystemBaseline", source: "Processes", row_index: 141 }],
});

const inspect = (row: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  state: "ready",
  record: { evidence_id: EID, investigation_id: INV, org_id: "OSYNTHLAB", client_id: "C.SYNTH0000000002", hostname: "lab-app-02", flow_id: "F.SYNTH0002", hunt_id: null, artifact: "LTH.SystemBaseline", source: "Processes", full_name: "LTH.SystemBaseline/Processes", source_key: "k1", collected_at: null, retrieved_at: null, row_index: 141, row_index_base: 0, raw_sha256: "abc", file: "f", line_offset_bytes: 10, recorded_event_time: null, recorded_event_time_field: null, recorded_event_time_note: "" },
  row, raw_text: JSON.stringify(row, null, 2), title: "kworkerd (PID 31337)",
  source: { ...source, mapping: { id: "process", label: "Processes", basis: "source name", configured: false, version: "v1" }, columns: { default: [], other: [] }, fields: {}, available_rows: 160 },
  presentation: { mapping: { id: "process", label: "Processes", basis: "source name 'Processes'", configured: false, version: "presentation-v1" }, domain: "process", summary: [{ field: "Name", role: "name", label: "Name", kind: "text" }, { field: "CommandLine", role: "cmdline", label: "Command line", kind: "command" }, { field: "Big", role: null, label: "Big", kind: "number" }], kinds: { CommandLine: "command" }, addresses: [], event_time: { status: "ok", utc: "2026-01-01T01:23:10Z", reason: null, detail: "", basis: { field: "StartTime", format: "iso8601", meaning: "process start time" } } },
  highlights: [{ rule_id: "suspicious_token", field: "CommandLine", start: 2, end: 6 }],
  findings: [{ finding_id: "R-001", title: "Tool name", severity: "low", confidence: "medium", origin: "deterministic_rule", rule_id: "suspicious_token", review_state: "unreviewed", review_label: "Not reviewed" }],
  entities: [], relations: { parent: null, parent_status: "No row with PID 1 was saved in this capture; the parent is not shown.", children: [], children_status: null, connections: [], process: null, process_status: null },
  related_by_finding: [], actions: { where_else: { available: false }, compare: { available: false }, pin: { pinned: false } },
  ...extra,
});

function workspaceRoutes(extra: Record<string, (url: URL, init?: RequestInit) => unknown> = {}) {
  return {
    ...baseRoutes,
    [`GET ${B}`]: () => ({ investigation: { investigation_id: INV, title: "Synthetic review", mode: "existing_flow", scope: { org_id: "OSYNTHLAB" }, outcome: "evidence_only", phase: "report_ready", job: { state: "finished" }, counts: {}, updated_at: "t", created_at: "t" }, job_active: false, actions: { export_report: true }, collections: [], existing: {} }),
    [`GET ${B}/selection`]: () => ({ pins: [] }),
    [`GET ${B}/views/sources`]: () => ({ state: "ready", sources: [source], state_labels: {} }),
    [`GET ${B}/findings`]: () => ({ findings: [finding("R-001", "high", "First high"), finding("R-002", "low", "A low one")], rejected_findings: [], coverage: null, report_ready: true }),
    [`GET ${B}/reviews`]: () => ({}),
    ...extra,
  };
}

describe("investigation workspace", () => {
  it("shows the scope and at most three metrics and two charts, with not-ready instead of zero", async () => {
    mockApi(workspaceRoutes({
      [`GET ${B}/views/overview`]: () => ({
        state: "ready", scope: { investigation_id: INV, title: "t", mode: "bundle", org_ids: ["OSYNTHLAB"], hosts: [], synthetic: false, note: "" }, status: {}, findings_ready: false,
        metrics: [
          { id: "needs_review", label: "Needs review", unit: "findings", value: null, total: null, detail: "not available", target: { view: "evidence" } },
          { id: "hosts", label: "Represented hosts", unit: "hosts", value: 1, detail: "d", target: { view: "hosts" } },
          { id: "sources", label: "Sources read", unit: "sources", value: 8, total: 10, detail: "d", breakdown: { complete: 8, failed: 1, incomplete: 1 }, target: { view: "coverage" } },
        ],
        charts: [{ id: "severity", kind: "donut", title: "Findings by severity", unit: "unique findings", state: "not_ready", total: null, segments: [] }],
        attention: [], coverage: { sources_total: 10, states: { complete: 8, failed: 1, incomplete: 1 }, state_labels: {}, captured_from: null, captured_to: null, stale_sources: 0, stale_after_days: 7, rows: 100, sources: [], retrieval_status: "incomplete" },
        next_action: { label: "Browse the saved source rows", target: { view: "evidence", mode: "rows" } }, index: { built_at: "t", normalizer_version: "v" },
      }),
    }));
    renderAt(`/investigations/${INV}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Synthetic review" })).toBeInTheDocument();
    const scope = screen.getByRole("banner", { name: "Investigation scope" });
    expect(within(scope).getByText("OSYNTHLAB")).toBeInTheDocument();
    const metrics = await screen.findByRole("list", { name: "Summary" });
    expect(within(metrics).getAllByRole("listitem")).toHaveLength(3);
    expect(within(metrics).getByText("not ready")).toBeInTheDocument();
    expect(screen.getByText(/8 of 10 sources read completely · 1 failed or unverified · 1 partial or not read/)).toBeInTheDocument();
    expect(screen.getAllByRole("region").filter((r) => r.classList.contains("chart-card")).length).toBeLessThanOrEqual(2);
    expect(within(screen.getByRole("navigation", { name: "Investigation views" })).getAllByRole("link").map((l) => l.textContent)).toEqual(["Overview", "Evidence", "Changes", "Timeline"]);
  });

  it("filters findings from the URL, keeps severity/review/confidence separate and saves a decision", async () => {
    const { calls } = mockApi(workspaceRoutes({ [`PUT ${B}/findings/R-001/review`]: () => ({ state: "benign", label: "Benign / expected", note: "admin", updated_at: "t", by: "local UI" }) }));
    renderAt(`/investigations/${INV}/evidence?sev=high&finding=R-001`);
    expect(await screen.findByRole("heading", { name: "First high" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Findings" });
    expect(within(list).queryByText("A low one")).toBeNull();
    const panel = screen.getByRole("region", { name: "Finding R-001" });
    expect(within(panel).getAllByText(/Severity:/)[0].textContent).toContain("High");
    expect(within(panel).getByLabelText("Confidence low")).toBeInTheDocument();
    expect(within(panel).getAllByText("Not reviewed").some((el) => el.classList.contains("review-chip"))).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Review decision"), "benign");
    await userEvent.type(screen.getByLabelText("Note (optional)"), "admin");
    await userEvent.click(screen.getByRole("button", { name: "Save decision" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ state: "benign", note: "admin" }));
    expect(calls.some((c) => c.path.includes("/explain"))).toBe(false);
  });

  it("clear filters keeps pins and never asks a model", async () => {
    const { calls } = mockApi(workspaceRoutes({ [`GET ${B}/selection`]: () => ({ pins: [{ pin_id: "pin-0000000000000001", kind: "finding", ref: "R-001", title: "R-001", note: "keep", created_at: "t", updated_at: "t", snapshot: {} }] }) }));
    renderAt(`/investigations/${INV}/evidence?sev=high&review=open`);
    expect(await screen.findByText(/Pins, notes and review decisions are kept/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("A low one")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Report selection/ })).toHaveTextContent("1");
    expect(calls.some((c) => c.method !== "GET")).toBe(false);
  });

  it("opens one inspector with exact text, rule spans only, and Escape closes it", async () => {
    const big = { __vr_bigint__: "18446744073709551615" };
    mockApi(workspaceRoutes({ [`GET ${B}/views/inspect/${EID}`]: () => inspect({ Name: "<script>alert(1)</script>", CommandLine: "./ncat -l 4444", Big: big }) }));
    const { container } = renderAt(`/investigations/${INV}/evidence?finding=R-001&ev=${EID}`);
    const insp = await screen.findByRole("region", { name: "Evidence inspector" });
    expect(await within(insp).findByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(within(insp).getByText("18446744073709551615")).toBeInTheDocument();
    const marks = insp.querySelectorAll("mark.hl");
    expect([...marks].map((m) => m.textContent)).toEqual(["ncat"]);
    await userEvent.click(within(insp).getByRole("button", { name: "Show related rows" }));
    expect(within(insp).getByText(/the parent is not shown/)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("region", { name: "Evidence inspector" })).toBeNull());
  });

  it("never shows the previous row while another row loads, and reads nested summary fields", async () => {
    const EID2 = "ev-bbbbbbbbbbbbbbbbbbbb";
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    mockApi(workspaceRoutes({
      [`GET ${B}/views/inspect/${EID}`]: () => inspect({ Name: "first-row", CommandLine: "a" }),
      [`GET ${B}/views/inspect/${EID2}`]: async () => {
        await gate;
        const d = inspect({ Name: "x", CommandLine: "b", Proc: { Exe: "/nested/value" } });
        return { ...d, record: { ...d.record, evidence_id: EID2 }, presentation: { ...d.presentation, summary: [{ field: "Proc.Exe", role: "exe", label: "Executable", kind: "path" }] } };
      },
    }));
    const props = { invId: INV, levels: { allFields: false, advanced: false, related: false }, onLevels: () => undefined, onClose: () => undefined, onOpenEvidence: () => undefined };
    const ui = (eid: string) => (
      <AppProvider>
        <MemoryRouter>
          <EvidenceInspector {...props} evidenceId={eid} />
        </MemoryRouter>
      </AppProvider>
    );
    const { rerender, container } = render(ui(EID));
    expect(await screen.findByText("first-row")).toBeInTheDocument();
    rerender(ui(EID2));
    await waitFor(() => expect(screen.queryByText("first-row")).toBeNull());
    release();
    await waitFor(() => expect(container.querySelector(".summary-fields")?.textContent).toContain("/nested/value"));
  });

  it("reports a missing row instead of showing a nearby one", async () => {
    mockApi(workspaceRoutes({ [`GET ${B}/views/inspect/${EID}`]: () => ({ __status: 404, __error: { code: "not_found", message: "Evidence ID does not exist in this investigation." } }) }));
    renderAt(`/investigations/${INV}/evidence?mode=rows&ev=${EID}`);
    expect(await screen.findByText(/No other row is shown in its place/)).toBeInTheDocument();
  });

  it("redirects old Findings & Evidence links into the workspace", async () => {
    mockApi(workspaceRoutes());
    renderAt(`/findings/${INV}?finding=R-002`);
    expect(await screen.findByRole("heading", { name: "A low one" })).toBeInTheDocument();
  });

  it("explains when no earlier capture exists to compare with", async () => {
    mockApi(workspaceRoutes({ [`GET ${B}/changes/sources`]: () => ({ sources: [{ source_key: "k1", full_name: "X/Users", label: "User accounts — h", adapter: "users", adapter_label: "User accounts", current: {}, baselines: [], default_baseline: null, cached: [], reason: "No earlier saved capture" }], not_comparable: [], note: "" }) }));
    renderAt(`/investigations/${INV}/changes`);
    expect(await screen.findByText("No earlier capture to compare with")).toBeInTheDocument();
    expect(screen.getByText(/nothing is collected for this/)).toBeInTheDocument();
  });
});

describe("renderers and charts", () => {
  it("keeps the executable and arguments readable without changing the text", () => {
    expect(executableEnd('"C:\\Program Files\\x.exe" -a')).toBe(24);
    const { container } = render(<CommandValue text="/usr/bin/curl -fsSL http://x | sh" marks={[]} />);
    expect(container.textContent).toBe("/usr/bin/curl -fsSL http://x | sh");
    expect(container.querySelector(".cmd-exe")?.textContent).toBe("/usr/bin/curl");
  });

  it("treats wrapped big integers as exact numbers", () => {
    const v = { __vr_bigint__: "9007199254740993" };
    expect(valueKind(v)).toBe("number");
    expect(exactText(v)).toBe("9007199254740993");
    expect(exactText({ a: v, b: [1] })).toBe('{\n  "a": 9007199254740993,\n  "b": [\n    1\n  ]\n}');
  });

  it("gives every chart a text equivalent that is also the control", async () => {
    const onSelect = vi.fn();
    render(<PartBar segments={[{ key: "high", label: "High", count: 2, filter: { severity: "high" } }, { key: "low", label: "Low", count: 6, filter: { severity: "low" } }]} total={8} unit="unique findings" palette="sev" onSelect={onSelect} label="Findings by severity" />);
    const legend = screen.getByRole("list", { name: "Findings by severity" });
    expect(within(legend).getAllByRole("button").map((b) => b.textContent)).toEqual(["High225%", "Low675%"]);
    await userEvent.click(within(legend).getByRole("button", { name: /High/ }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: "high" }));
  });

  it("marks possible gaps apart from measured zeros and lists buckets in a table", async () => {
    const b = (i: number, count: number, measured: boolean) => ({ start: new Date(Date.UTC(2026, 0, 1, i)).toISOString(), end: new Date(Date.UTC(2026, 0, 1, i + 1)).toISOString(), from_us: i * 3600e6, to_us: (i + 1) * 3600e6, count, measured });
    const onSelect = vi.fn();
    const { container } = render(<Histogram buckets={[b(0, 3, true), b(1, 0, false), b(2, 0, true), b(3, 1, true)]} unit="rows" zone="UTC" onSelect={onSelect} label="Events" />);
    expect(container.querySelectorAll("rect.gap")).toHaveLength(1);
    await userEvent.click(screen.getByText("Show as a table"));
    expect(screen.getByText("possible gap")).toBeInTheDocument();
    const plot = screen.getByRole("group", { name: /Use the arrow keys/ });
    plot.focus();
    await userEvent.keyboard("{ArrowRight}{ArrowLeft}{Enter}");
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ count: 3 }));
  });
});
