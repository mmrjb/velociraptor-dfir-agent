import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { AppRoutes } from "../App";
import { AppProvider } from "../lib/app";
import { AiPanel } from "../components/AiPanel";
import { DataTable, inferColumns } from "../components/DataTable";
import { Markdown } from "../components/Markdown";
import { StructuredFields, ValueCell, exactText } from "../components/values";
import { baseRoutes, mockApi } from "./mockApi";

function renderAt(path: string) {
  return render(
    <AppProvider>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProvider>,
  );
}

const HOSTILE = "<script>alert('xss')</script><img src=x onerror=alert(1)>";
const PERSIAN = "مدیر سیستم ‮gnp.exe";

describe("shared value rendering", () => {
  it("distinguishes null, empty, absent, booleans and nested values", () => {
    render(
      <div>
        <ValueCell value={null} label="a" />
        <ValueCell value="" label="b" />
        <ValueCell value={undefined} label="c" />
        <ValueCell value={false} label="d" />
        <ValueCell value={[1, 2, 3]} label="e" />
        <ValueCell value={{ Pid: 1, Name: "x", User: "y", Exe: "z" }} label="f" />
      </div>,
    );
    expect(screen.getByText("null")).toHaveClass("v-null");
    expect(screen.getByText("(empty string)")).toBeInTheDocument();
    expect(screen.getByLabelText("absent")).toBeInTheDocument();
    expect(screen.getByText("false")).toHaveClass("v-bool");
    expect(screen.getByText("[3 items]")).toBeInTheDocument();
    expect(screen.getByText("{Pid, Name, User, +1}")).toBeInTheDocument();
  });

  it("opens the full value and copies it exactly (Unicode and bidi controls preserved)", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const long = `${PERSIAN} ${"/very/long/path".repeat(10)}\nsecond line`;
    render(<ValueCell value={long} label="CommandLine" />);
    await userEvent.click(screen.getByRole("button", { name: "Show full value of CommandLine" }));
    const dialog = screen.getByRole("dialog", { name: "Full value: CommandLine" });
    expect(within(dialog).getByText(/2 lines/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Copy CommandLine (exact value)" }));
    expect(writeText).toHaveBeenCalledWith(long);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders hostile evidence as text with automatic direction", () => {
    const { container } = render(<StructuredFields record={{ CommandLine: HOSTILE, User: PERSIAN }} preferred={["User"]} />);
    expect(container.querySelector("script, img")).toBeNull();
    expect(screen.getByText(HOSTILE)).toHaveAttribute("dir", "auto");
    expect(screen.getAllByText(PERSIAN)[0]).toHaveAttribute("dir", "auto");
    expect(container.querySelectorAll("dt")[0]).toHaveTextContent("User"); // preferred field first
    expect(exactText({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
});

describe("shared data table", () => {
  const rows = Array.from({ length: 3 }, (_, i) => ({ Pid: i, CommandLine: `/usr/bin/x --flag ${i} ${"long ".repeat(30)}`, Hash: null }));

  it("pages, toggles columns and wrapping, and highlights the referenced row", async () => {
    const onPage = vi.fn();
    const { container } = render(
      <DataTable id="t1" label="Rows" rows={rows} columns={inferColumns(rows)} rowKey={(r) => String(r.Pid)} total={120} offset={50} limit={50} onPageChange={onPage} highlightKey="1" />,
    );
    expect(screen.getByText("Rows 51–53 of 120")).toBeInTheDocument();
    expect(container.querySelector("tr.highlight")).toHaveAttribute("data-row-key", "1");
    expect(container.querySelector("th.sticky-col, th")).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onPage).toHaveBeenCalledWith(100);
    await userEvent.click(screen.getByText(/Columns \(3 of 3\)/));
    await userEvent.click(screen.getByRole("checkbox", { name: "Hash" }));
    expect(screen.getByText(/Columns \(2 of 3\)/)).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Hash" })).toBeNull();
    const wrap = screen.getByRole("button", { name: /Wrap text/ });
    expect(wrap).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(wrap);
    expect(container.querySelector(".dt")).toHaveClass("wrap-mode");
    expect(screen.getAllByRole("button", { name: "Show full value of CommandLine" })).toHaveLength(3);
    expect(screen.getByRole("region", { name: /scrollable table/ })).toHaveAttribute("tabindex", "0");
  });
});

describe("safe Markdown", () => {
  it("never renders HTML, scripts, images or unsafe links", () => {
    const md = "# Title\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1)) [ok](https://docs.example/a)\n\n![img](https://img.example/x.png)\n\n{{ Query \"SELECT 1\" | Table }}\n\n- one\n  - nested `code`\n\n| a | b |\n|---|---|\n| <b>x</b> | 2 |";
    const { container } = render(<Markdown source={md} />);
    expect(container.querySelector("script, img, b, iframe")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(screen.getByText(/link removed/)).toBeInTheDocument();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
    const ok = screen.getByRole("link", { name: "ok" });
    expect(ok).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText(/image not displayed/)).toBeInTheDocument();
    expect(screen.getByText(/\{\{ Query "SELECT 1" \| Table \}\}/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Title" }).tagName).toBe("H2");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getByText("<b>x</b>")).toBeInTheDocument();
  });
});

describe("Findings workspace", () => {
  const f = (id: string, sev: string, title: string) => ({ finding_id: id, title, observation: "obs", interpretation: "int", evidence_ids: ["ev-aaaaaaaaaaaaaaaaaaaa"], severity: sev, confidence: "low", confidence_rationale: "r", benign_explanations: [], missing_context: [], next_steps: [], origin: "deterministic_rule", client_ids: ["C.1"], sources: ["X/Y"], evidence_refs: [{ evidence_id: "ev-aaaaaaaaaaaaaaaaaaaa", client_id: "C.1", hostname: "h", flow_id: "F.1", artifact: "X", source: "Y", row_index: 60 }] });
  const routes = (extra = {}) => ({
    ...baseRoutes,
    "GET /api/v1/investigations/inv-20260101-000000-abc123": () => ({ investigation: { title: "t" } }),
    "GET /api/v1/investigations/inv-20260101-000000-abc123/findings": () => ({ findings: [f("D-01", "high", "First high"), f("D-02", "low", "A low one"), f("D-03", "high", "Second high")], rejected_findings: [], coverage: { sources: [], counts: {}, gaps: [] }, report_ready: true }),
    "GET /api/v1/investigations/inv-20260101-000000-abc123/reviews": () => ({ "D-03": { state: "needs_follow_up", label: "Needs follow-up", note: "x", updated_at: "2026-01-01T00:00:00Z", by: "a" } }),
    ...extra,
  });

  it("keeps filters through previous/next and shows severity, review and confidence separately", async () => {
    mockApi(routes());
    renderAt("/findings/inv-20260101-000000-abc123?sev=high&finding=D-01");
    expect(await screen.findByText("Finding 1 of 2")).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Severity, review status and confidence" });
    expect(within(group).getByText("Severity: High")).toHaveClass("badge");
    expect(within(group).getByText("Review: Unreviewed")).toHaveClass("review-chip");
    expect(within(group).getByLabelText("Confidence low")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Next finding/ }));
    expect(await screen.findByRole("heading", { name: "D-03: Second high" })).toBeInTheDocument();
    expect(screen.getByText("Finding 2 of 2")).toBeInTheDocument(); // the low finding was skipped
    expect(screen.getAllByText("Review: Needs follow-up").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Next finding/ })).toBeDisabled();
  });

  it("saves a review status without touching the finding", async () => {
    const { calls } = mockApi(routes({ "PUT /api/v1/investigations/inv-20260101-000000-abc123/findings/D-01/review": () => ({ state: "benign", label: "Benign / expected", note: "admin", updated_at: "2026-01-01T00:00:00Z", by: "local UI" }) }));
    renderAt("/findings/inv-20260101-000000-abc123?finding=D-01");
    await userEvent.click(await screen.findByText("Analyst review status"));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "benign");
    await userEvent.type(screen.getByLabelText("Note (optional)"), "admin");
    await userEvent.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ state: "benign", note: "admin" });
    expect(await screen.findAllByText("Review: Benign / expected")).not.toHaveLength(0);
  });

  it("opens the exact evidence row in its source table and highlights it", async () => {
    const records = Array.from({ length: 50 }, (_, i) => ({ evidence_id: i === 10 ? "ev-aaaaaaaaaaaaaaaaaaaa" : `ev-${String(i).padStart(20, "0")}`, row_index: 50 + i, raw_sha256: "h", retrieved_at: "t", event_time: null, row: { Name: `p${50 + i}` } }));
    const { calls, fetchMock } = mockApi(routes({
      "GET /api/v1/investigations/inv-20260101-000000-abc123/evidence/ev-aaaaaaaaaaaaaaaaaaaa/locate": () => ({ evidence_id: "ev-aaaaaaaaaaaaaaaaaaaa", source_key: "k1", row_index: 60, full_name: "X/Y", offset: 50, limit: 50 }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123/sources": () => ({ bundle_id: "b", synthetic: true, sources: [{ source_key: "k0", full_name: "X/Z", client_id: "C.1", hostname: "h", flow_id: "F.1", hunt_id: null, counts_only: false, coverage: { persisted_rows: 3 } }, { source_key: "k1", full_name: "X/Y", client_id: "C.1", hostname: "h", flow_id: "F.1", hunt_id: null, counts_only: false, coverage: { persisted_rows: 120 } }] }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123/evidence": () => ({ records, total_matched: 120, offset: 50, limit: 50, search_scope: null, scan_truncated: false, source: { source_key: "k1", full_name: "X/Y", client_id: "C.1", hostname: "h", flow_id: "F.1", org_id: "O", coverage: { result_status: "rows", read_status: "complete", persisted_rows: 120 } } }),
    }));
    const { container } = renderAt("/findings/inv-20260101-000000-abc123?finding=D-01");
    await userEvent.click(await screen.findByRole("button", { name: "Open evidence ev-aaaaaaaaaaaaaaaaaaaa in its source table" }));
    await waitFor(() => expect(container.querySelector("tr.highlight")).not.toBeNull());
    expect(container.querySelector("tr.highlight")).toHaveAttribute("data-row-key", "ev-aaaaaaaaaaaaaaaaaaaa");
    const evidenceCall = calls.find((c) => c.path.startsWith("/api/v1/investigations/inv-20260101-000000-abc123/evidence?"))!;
    expect(evidenceCall.path).toContain("source_key=k1");
    expect(evidenceCall.path).toContain("offset=50");
    expect(await screen.findByRole("region", { name: "Selected evidence row" })).toHaveTextContent("p60");
    expect(fetchMock).toHaveBeenCalled();
  });
});

describe("review regressions", () => {
  it("removes backslash links and survives deeply nested quotes", () => {
    const { container } = render(<Markdown source={"[home](/\\evil.example/x) [ok](/investigations/a)\n\n" + ">".repeat(5000) + " deep"} />);
    expect(container.querySelector('a[href^="/\\\\"]')).toBeNull();
    expect(screen.getByRole("link", { name: "ok" })).toHaveAttribute("href", "/investigations/a");
    expect(container.querySelectorAll("blockquote").length).toBeLessThanOrEqual(17);
  });

  it("clears an AI result when the subject changes", async () => {
    mockApi({ "POST /api/v1/x/a/explain": () => ({ action: "finding_summary", status: "ok", label: "AI interpretation", provider: "ollama", model: "m", prompt_version: "v1", summary: "About A.", points: [], gaps: [], next_steps: [], rejected: [], records_supplied: 1, records_omitted: 0 }) });
    const actions = [{ id: "finding_summary", label: "Summarize this finding", body: {} }];
    const { rerender } = render(<AiPanel path="/x/a/explain" actions={actions} />);
    await userEvent.click(screen.getByRole("button", { name: "Summarize this finding" }));
    expect(await screen.findByText("About A.")).toBeInTheDocument();
    rerender(<AiPanel path="/x/b/explain" actions={actions} />);
    expect(screen.queryByText("About A.")).toBeNull();
  });

  it("keeps viewer clicks away from the row and closes only the top viewer on Escape", async () => {
    const onRow = vi.fn();
    const rows = [{ Id: 1, Evidence: { CommandLine: "/usr/bin/x " + "y".repeat(80), User: "root" } }];
    render(<DataTable id="t-click" label="Rows" rows={rows} columns={inferColumns(rows)} rowKey={(r) => String(r.Id)} onRowClick={onRow} />);
    await userEvent.click(screen.getByRole("button", { name: "Show full value of Evidence" }));
    const outer = screen.getByRole("dialog", { name: "Full value: Evidence" });
    await userEvent.click(within(outer).getAllByText(/usr\/bin\/x/)[0]);
    await userEvent.click(within(outer).getByRole("button", { name: "Show full value of CommandLine" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    await userEvent.keyboard("{Escape}");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    await userEvent.click(document.querySelector(".overlay")!);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onRow).not.toHaveBeenCalled();
  });

  it("offers the full value when a short value is cut off", () => {
    const sw = vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(270);
    const cw = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(232);
    render(<ValueCell value="0123456789abcdef0123456789abcdef" label="MachineId" />);
    expect(screen.getByRole("button", { name: "Show full value of MachineId" })).toBeInTheDocument();
    sw.mockRestore();
    cw.mockRestore();
  });

  it("returns focus to the opener and keeps Tab inside a dialog", async () => {
    render(<ValueCell value={"x".repeat(80)} label="CommandLine" />);
    const opener = screen.getByRole("button", { name: "Show full value of CommandLine" });
    await userEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Full value: CommandLine" });
    for (let i = 0; i < 4; i++) {
      await userEvent.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

});
