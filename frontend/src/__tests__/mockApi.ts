import { vi } from "vitest";

type Handler = (url: URL, init?: RequestInit) => unknown | Promise<unknown>;

// Routes are matched on "METHOD /api/v1/path" (query ignored). Handlers return the `data`
// payload, or an object with __status/__error to simulate an API error.
export function mockApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://127.0.0.1:8766");
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${url.pathname}`;
    calls.push({ method, path: url.pathname + url.search, body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const handler = routes[key] ?? Object.entries(routes).find(([k]) => k.endsWith("*") && key.startsWith(k.slice(0, -1)))?.[1];
    if (!handler) return new Response(JSON.stringify({ ok: false, error: { code: "not_found", message: `no mock for ${key}` } }), { status: 404 });
    const out = (await handler(url, init)) as { __status?: number; __error?: unknown } | unknown;
    if (out && typeof out === "object" && "__status" in (out as object)) {
      const o = out as { __status: number; __error: unknown };
      return new Response(JSON.stringify({ ok: false, error: o.__error }), { status: o.__status });
    }
    return new Response(JSON.stringify({ ok: true, data: out }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export const baseRoutes = {
  "GET /api/v1/session": () => ({ csrf_token: "t", version: "0.4.0a1", recovered_jobs: [] }),
  "GET /api/v1/settings": () => ({ preferences: { selected_org: null, page_size: 50, default_no_llm: false, analysis_budget: null }, server: {} }),
  "GET /api/v1/status": () => ({
    version: "0.4.0a1",
    edition: "AI public preview", backend_access: "read_only",
    velociraptor: { configured: true, default_org: "OSYNTHLAB", last_test: null, synthetic: false },
    model: { provider: "ollama", model: "test-model", hosted: false, configured: true, last_test: null, budgets: {} },
    allowlist: { configured: true, artifacts: [] }, tested_at: null, setup_hint: null,
  }),
  "GET /api/v1/orgs": () => [{ OrgId: "OSYNTHLAB", Name: "Synthetic lab org" }],
};
