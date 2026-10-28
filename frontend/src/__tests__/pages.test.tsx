import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { AppRoutes } from "../App";
import { AppProvider } from "../lib/app";
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

export const finding = {
  finding_id: "M-001", title: "Process executable in a temporary directory", observation: "kworkerd runs from /tmp/.cache",
  interpretation: "Review signal.", evidence_ids: ["ev-aaaaaaaaaaaaaaaaaaaa", "ev-bbbbbbbbbbbbbbbbbbbb"], severity: "medium", confidence: "low",
  confidence_rationale: "r", benign_explanations: ["Admin test binary"], missing_context: ["Parent process"], next_steps: ["Hash it"],
  origin: "model_proposal", client_ids: ["C.SYNTH0000000002"], sources: ["LTH.SystemBaseline/Processes"],
  evidence_refs: [
    { evidence_id: "ev-aaaaaaaaaaaaaaaaaaaa", client_id: "C.SYNTH0000000002", hostname: "lab-app-02", flow_id: "F.SYNTH0002", artifact: "LTH.SystemBaseline", source: "Processes", row_index: 141 },
    { evidence_id: "ev-bbbbbbbbbbbbbbbbbbbb", unresolved: true },
  ],
};

const notConfigured = () => ({ ...(baseRoutes["GET /api/v1/status"]() as object), velociraptor: { configured: false, default_org: "root", last_test: null, synthetic: false }, setup_hint: "set it" });

describe("Investigations (first launch)", () => {
  it("offers one primary action with an empty history, three main destinations and no demo", async () => {
    mockApi({ ...baseRoutes, "GET /api/v1/investigations": () => [] });
    renderAt("/investigations");
    expect(await screen.findByText("No investigations yet.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Start investigation/ })).toHaveLength(1);
    expect(screen.queryByText(/demo/i)).toBeNull();
    const nav = within(screen.getByRole("complementary", { name: "Primary" })).getAllByRole("link");
    expect(nav.map((a) => a.textContent)).toEqual(["Investigations", "Reports", "Settings"]);
  });

  it("keeps saved evidence and import available when Velociraptor is not configured", async () => {
    mockApi({ ...baseRoutes, "GET /api/v1/status": notConfigured, "GET /api/v1/investigations": () => [] });
    renderAt("/investigations");
    expect(await screen.findByText(/Velociraptor is not connected/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import evidence" })).toHaveAttribute("href", "/investigations/new?mode=import");
  });

  it("shows report readiness and review progress instead of a zero", async () => {
    mockApi({ ...baseRoutes, "GET /api/v1/investigations": () => [
      { investigation_id: "inv-20260101-000000-abc123", title: "Not analyzed", mode: "bundle", scope: { org_id: "O" }, outcome: "evidence_only", phase: "report_ready", counts: { findings: 0 }, report_ready: false, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
      { investigation_id: "inv-20260101-000000-abc124", title: "Reviewed", mode: "existing_flow", scope: { org_id: "O", hostname: "web" }, outcome: "complete", phase: "report_ready", counts: { findings: 4 }, report_ready: true, reviews_decided: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    ] });
    renderAt("/investigations");
    expect(await screen.findByText("findings not ready")).toBeInTheDocument();
    expect(screen.getByText("1 of 4 reviewed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue Reviewed" })).toBeInTheDocument();
  });
});

const flowPlan = (launches = 0) => ({
  plan: { mode: "existing_flow", org_id: "OSYNTHLAB", collections: [],
    errors: [], warnings: [], ok: true, launches }, plan_token: "pln-1.sig",
});
async function enterFlow() {
  await userEvent.type(await screen.findByRole("textbox", { name: "Client ID" }), "C.1a2b3c");
  await userEvent.type(screen.getByRole("textbox", { name: "Flow ID" }), "F.ABC");
}

describe("Read-only investigation scope", () => {
  it("plans existing evidence with the no-AI choice, then starts local analysis", async () => {
    const { calls } = mockApi({ ...baseRoutes, "GET /api/v1/flows": () => [],
      "POST /api/v1/plans": () => flowPlan(),
      "POST /api/v1/investigations": () => ({ investigation_id: "inv-20260101-000000-abc123" }),
    });
    renderAt("/investigations/new");
    await enterFlow();
    expect(screen.getAllByRole("option").some((x) => /Collect new/.test(x.textContent ?? ""))).toBe(false);
    await userEvent.click(screen.getByRole("checkbox", { name: "Evidence only / no AI" }));
    await userEvent.click(screen.getByRole("button", { name: "Review evidence scope" }));
    await userEvent.click(await screen.findByRole("button", { name: "Analyze Existing Evidence" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/api/v1/investigations")).toBeDefined());
    expect(calls.find((c) => c.path === "/api/v1/plans")!.body).toEqual({ mode: "existing_flow", org_id: "OSYNTHLAB", client_id: "C.1a2b3c", flow_id: "F.ABC", no_llm: true });
    expect(calls.find((c) => c.path === "/api/v1/investigations")!.body).toEqual({ plan_token: "pln-1.sig", title: null });
    expect(calls.some((c) => /profiles|collect|notebooks|triage/.test(c.path))).toBe(false);
  });
  it("rejects a late plan after the analyst changes the flow", async () => {
    let finish!: (value: unknown) => void;
    const { calls } = mockApi({ ...baseRoutes, "GET /api/v1/flows": () => [],
      "POST /api/v1/plans": () => new Promise((resolve) => { finish = resolve; }),
    });
    renderAt("/investigations/new"); await enterFlow();
    await userEvent.click(screen.getByRole("button", { name: "Review evidence scope" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/plans")).toBe(true));
    await userEvent.type(screen.getByRole("textbox", { name: "Flow ID" }), "D");
    finish(flowPlan());
    await waitFor(() => expect(screen.getByRole("button", { name: "Review evidence scope" })).toBeEnabled());
    expect(screen.queryByRole("button", { name: "Analyze Existing Evidence" })).toBeNull();
  });
  it("refuses a returned plan that claims to launch a collection", async () => {
    mockApi({ ...baseRoutes, "GET /api/v1/flows": () => [], "POST /api/v1/plans": () => flowPlan(1) });
    renderAt("/investigations/new"); await enterFlow();
    await userEvent.click(screen.getByRole("button", { name: "Review evidence scope" }));
    expect(await screen.findByRole("button", { name: "Analyze Existing Evidence" })).toBeDisabled();
  });
  it("imports offline with no AI by default", async () => {
    const { calls } = mockApi({ ...baseRoutes, "GET /api/v1/status": notConfigured,
      "GET /api/v1/bundles": () => [], "POST /api/v1/imports": () => ({ investigation_id: "inv-20260101-000000-abc123" }),
    });
    renderAt("/investigations/new?mode=import");
    expect(await screen.findByRole("checkbox", { name: /Evidence only \/ no AI/ })).toBeChecked();
    await userEvent.upload(screen.getByLabelText("Evidence bundle archive"), new File(["archive"], "bundle.tar.gz", { type: "application/gzip" }));
    await userEvent.click(screen.getByRole("button", { name: "Import evidence" }));
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/api/v1/imports?") && c.path.includes("no_llm=true"))).toBe(true));
  });
  it("honors explicit AI opt-in for a saved bundle", async () => {
    const { calls } = mockApi({ ...baseRoutes,
      "GET /api/v1/bundles": () => [{ investigation_id: "inv-20260101-000000-abc123", title: "Lab bundle", rows: 5 }],
      "POST /api/v1/bundles/inv-20260101-000000-abc123/analyze": () => ({ investigation_id: "inv-20260101-000000-abc123" }),
    });
    renderAt("/investigations/new?mode=import");
    await userEvent.click(await screen.findByRole("checkbox", { name: /Evidence only \/ no AI/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Analyze again" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/analyze"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/analyze"))!.body).toEqual({ no_llm: false });
  });
});
