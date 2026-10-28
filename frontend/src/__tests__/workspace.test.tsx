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

describe("review regressions", () => {
  it("removes backslash links and survives deeply nested quotes", () => {
    const { container } = render(<Markdown source={"[home](/\\evil.example/x) [ok](/investigations/a)\n\n" + ">".repeat(5000) + " deep"} />);
    expect(container.querySelector('a[href^="/\\\\"]')).toBeNull();
    expect(screen.getByRole("link", { name: "ok" })).toHaveAttribute("href", "/investigations/a");
    expect(container.querySelectorAll("blockquote").length).toBeLessThanOrEqual(17);
  });

  it("clears an AI result when the subject changes", async () => {
    mockApi({ "GET /api/v1/session": baseRoutes["GET /api/v1/session"], "POST /api/v1/x/a/explain": () => ({ action: "finding_summary", status: "ok", label: "AI interpretation", provider: "ollama", model: "m", prompt_version: "v1", summary: "About A.", points: [], gaps: [], next_steps: [], rejected: [], records_supplied: 1, records_omitted: 0 }) });
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
