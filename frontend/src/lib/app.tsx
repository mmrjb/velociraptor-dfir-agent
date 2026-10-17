import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiGet, apiPost, apiPut, ensureSession, errorMessage } from "../api/client";
import type { ApiError, Org, Preferences, SessionInfo, StatusInfo } from "../api/types";

interface AppState {
  session: SessionInfo | null;
  status: StatusInfo | null;
  statusError: ApiError | null;
  orgs: Org[];
  orgsError: ApiError | null;
  selectedOrg: string;
  setSelectedOrg: (org: string) => void;
  preferences: Preferences | null;
  savePreferences: (p: Partial<Preferences>) => Promise<void>;
  refreshStatus: () => Promise<void>;
  testConnection: () => Promise<void>;
  testing: boolean;
  toast: (message: string) => void;
  toasts: { id: number; message: string }[];
  investigationContext: { id: string; title: string } | null;
  setInvestigationContext: (ctx: { id: string; title: string } | null) => void;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [status, setStatus] = useState<StatusInfo | null>(null);
  const [statusError, setStatusError] = useState<ApiError | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [orgsError, setOrgsError] = useState<ApiError | null>(null);
  const [selectedOrg, setSelectedOrgState] = useState<string>("");
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [testing, setTesting] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; message: string }[]>([]);
  const [investigationContext, setInvestigationContext] = useState<{ id: string; title: string } | null>(null);
  const toastId = useRef(0);

  const toast = useCallback((message: string) => {
    const id = ++toastId.current;
    setToasts((t) => [...t.slice(-2), { id, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500);
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await apiGet<StatusInfo>("/status");
      setStatus(s);
      setStatusError(null);
    } catch (e) {
      setStatusError(errorMessage(e));
    }
  }, []);

  const loadOrgs = useCallback(async (defaultOrg: string, prefOrg: string | null) => {
    try {
      const list = await apiGet<Org[]>("/orgs");
      setOrgs(list);
      setOrgsError(null);
      const wanted = prefOrg && list.some((o) => (o.OrgId || "root") === prefOrg) ? prefOrg : defaultOrg;
      setSelectedOrgState(wanted || "root");
    } catch (e) {
      setOrgs([]);
      setOrgsError(errorMessage(e));
      setSelectedOrgState(prefOrg || defaultOrg || "root");
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const s = await ensureSession();
        setSession(s);
      } catch (e) {
        setStatusError(errorMessage(e));
        return;
      }
      let prefs: Preferences | null = null;
      try {
        prefs = (await apiGet<{ preferences: Preferences }>("/settings")).preferences;
        setPreferences(prefs);
      } catch {
        /* preferences are optional */
      }
      try {
        const st = await apiGet<StatusInfo>("/status");
        setStatus(st);
        if (st.velociraptor.configured) {
          await loadOrgs(st.velociraptor.default_org, prefs?.selected_org ?? null);
        } else {
          setSelectedOrgState(prefs?.selected_org || st.velociraptor.default_org || "root");
        }
      } catch (e) {
        setStatusError(errorMessage(e));
      }
    })();
  }, [loadOrgs]);

  const savePreferences = useCallback(
    async (p: Partial<Preferences>) => {
      const merged: Preferences = { selected_org: null, page_size: 50, default_no_llm: false, analysis_budget: null, ...preferences, ...p };
      const saved = await apiPut<Preferences>("/settings/preferences", merged);
      setPreferences(saved);
    },
    [preferences],
  );

  const setSelectedOrg = useCallback(
    (org: string) => {
      setSelectedOrgState(org);
      // Changing the default org never rewrites stored investigations' scope.
      savePreferences({ selected_org: org }).catch(() => undefined);
    },
    [savePreferences],
  );

  const testConnection = useCallback(async () => {
    setTesting(true);
    try {
      const s = await apiPost<StatusInfo>("/status/test", {});
      setStatus(s);
      setStatusError(null);
      if (s.velociraptor.configured) await loadOrgs(s.velociraptor.default_org, preferences?.selected_org ?? null);
    } catch (e) {
      setStatusError(errorMessage(e));
    } finally {
      setTesting(false);
    }
  }, [loadOrgs, preferences]);

  const value = useMemo(
    () => ({ session, status, statusError, orgs, orgsError, selectedOrg, setSelectedOrg, preferences, savePreferences, refreshStatus, testConnection, testing, toast, toasts, investigationContext, setInvestigationContext }),
    [session, status, statusError, orgs, orgsError, selectedOrg, setSelectedOrg, preferences, savePreferences, refreshStatus, testConnection, testing, toast, toasts, investigationContext],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePolling(fn: () => Promise<boolean | void>, intervalMs: number, active: boolean) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    const tick = async () => {
      try {
        const keep = await saved.current();
        failures = 0;
        if (keep === false) return;
      } catch {
        failures += 1; // bounded backoff on reconnect; never creates work server-side
      }
      if (!stopped) timer = setTimeout(tick, Math.min(intervalMs * 2 ** failures, 15000));
    };
    timer = setTimeout(tick, intervalMs);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [active, intervalMs]);
}
