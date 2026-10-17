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

const finding = {
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
  it("offers New Investigation and Import Evidence with an empty history, and no demo", async () => {
    mockApi({ ...baseRoutes, "GET /api/v1/investigations": () => [] });
    renderAt("/investigations");
    expect(await screen.findByText("No investigations yet.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /New Investigation/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /Import Evidence/ }).length).toBeGreaterThan(0);
    expect(screen.queryByText(/demo/i)).toBeNull();
    expect(screen.getByRole("navigation")).toHaveTextContent("Findings & Evidence");
    expect(within(screen.getByRole("navigation")).getAllByRole("link")).toHaveLength(3);
    expect(within(screen.getByRole("navigation")).getAllByRole("link")[0]).toHaveTextContent("Investigations");
  });

  it("shows a configuration hint when Velociraptor is not configured", async () => {
    mockApi({ ...baseRoutes, "GET /api/v1/status": notConfigured, "GET /api/v1/investigations": () => [] });
    renderAt("/investigations");
    expect(await screen.findByText(/Velociraptor access is not configured on this server/)).toBeInTheDocument();
    expect(screen.getByText(/Velociraptor: Not configured/)).toBeInTheDocument();
  });
});

describe("Findings & Evidence", () => {
  it("links a finding to its exact evidence row and flags invalid citations", async () => {
    mockApi({
      ...baseRoutes,
      "GET /api/v1/investigations/inv-20260101-000000-abc123/findings": () => ({
        findings: [finding], rejected_findings: [{ reasons: ["cites evidence IDs that do not exist"], request_id: "r", title: "bogus" }],
        coverage: { sources: [], counts: { rows: 1 }, gaps: ["X: counts only"] }, report_ready: true, synthetic: true,
        provider: { name: "fake", model: "fake-test-model", fake: true }, citation_note: "Citation validation confirms ...",
      }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123": () => ({ investigation: { title: "Synthetic review" } }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123/evidence/ev-aaaaaaaaaaaaaaaaaaaa": () => ({
        evidence_id: "ev-aaaaaaaaaaaaaaaaaaaa", org_id: "OSYNTHLAB", client_id: "C.SYNTH0000000002", hostname: "lab-app-02", flow_id: "F.SYNTH0002",
        hunt_id: null, artifact: "LTH.SystemBaseline", source: "Processes", full_name: "LTH.SystemBaseline/Processes", source_key: "k",
        collected_at: null, retrieved_at: "2026-01-01T00:00:00Z", event_time: null, row_index: 141, raw_sha256: "abc", file: "f", line: 142,
        synthetic: true, investigation_id: "inv", row: { Name: "<script>alert(1)</script>", IsDeleted: false, Pid: 0, Note: "" },
      }),
    });
    const { container } = renderAt("/findings/inv-20260101-000000-abc123?finding=M-001");
    expect(await screen.findByRole("heading", { name: /M-001: Process executable/ })).toBeInTheDocument();
    expect(screen.getByText("Admin test binary")).toBeInTheDocument();
    expect(screen.getByText(/citation could not be resolved/)).toBeInTheDocument();
    expect(screen.getByText(/1 rejected model proposal/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open evidence ev-aaaaaaaaaaaaaaaaaaaa" }));
    expect(await screen.findByText(/zero-based position/)).toBeInTheDocument();
    expect(screen.getAllByText("<script>alert(1)</script>").length).toBeGreaterThan(0);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getAllByText("false").length).toBeGreaterThan(0);
  });

  it("moves between findings in the current filter", async () => {
    const second = { ...finding, finding_id: "D-001", title: "Second finding", origin: "deterministic_rule" };
    mockApi({
      ...baseRoutes,
      "GET /api/v1/investigations/inv-20260101-000000-abc123/findings": () => ({ findings: [finding, second], rejected_findings: [], coverage: { sources: [], counts: {}, gaps: [] }, report_ready: true }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123": () => ({ investigation: { title: "t" } }),
    });
    renderAt("/findings/inv-20260101-000000-abc123?finding=M-001");
    expect(await screen.findByText("Finding 1 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous finding" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next finding" }));
    expect(await screen.findByRole("heading", { name: /D-001: Second finding/ })).toBeInTheDocument();
    expect(screen.getByText("Finding 2 of 2")).toBeInTheDocument();
  });

  it("shows a missing citation as an explicit error, not a nearby row", async () => {
    mockApi({
      ...baseRoutes,
      "GET /api/v1/investigations/inv-20260101-000000-abc123/findings": () => ({ findings: [finding], rejected_findings: [], coverage: { sources: [], counts: {}, gaps: [] }, report_ready: true }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123": () => ({ investigation: { title: "t" } }),
      "GET /api/v1/investigations/inv-20260101-000000-abc123/evidence/ev-cccccccccccccccccccc": () => ({ __status: 404, __error: { code: "not_found", message: "Evidence ID ev-cccccccccccccccccccc does not exist in this bundle." } }),
    });
    renderAt("/findings/inv-20260101-000000-abc123?finding=M-001&evidence=ev-cccccccccccccccccccc");
    expect(await screen.findByText(/Invalid or missing citation/)).toBeInTheDocument();
    expect(screen.getByText(/No nearby row is substituted/)).toBeInTheDocument();
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
