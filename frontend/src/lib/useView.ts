// Loading of summary-first views. A view answers either { state: "ready", ... } or a pending
// status while the derived index is built; pending views are polled with a bounded backoff.
// Loading never triggers a model request (view endpoints only read saved data).
import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, errorMessage } from "../api/client";
import type { ApiError, ViewPending } from "../api/types";

type Params = Record<string, string | number | boolean | undefined | null>;

export function useView<T extends { state: "ready" }>(path: string | null, params?: Params) {
  const [data, setData] = useState<T | null>(null);
  const [dataKey, setDataKey] = useState<string | null>(null);
  const [pending, setPending] = useState<ViewPending | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const key = path === null ? null : `${path}?${JSON.stringify(params ?? {})}`;
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async (attempt = 0) => {
    if (path === null) return;
    const mine = ++seq.current;
    if (timer.current) clearTimeout(timer.current);
    setLoading(true);
    try {
      const d = await apiGet<T | ViewPending>(path, params);
      if (mine !== seq.current) return;
      if (d.state === "ready") {
        setData(d as T);
        setDataKey(key);
        setPending(null);
        setError(null);
      } else {
        setPending(d as ViewPending);
        if (d.state === "building" || d.state === "evidence_changing") {
          timer.current = setTimeout(() => load(attempt + 1), Math.min(700 * 1.4 ** attempt, 5000));
        }
      }
    } catch (e) {
      if (mine === seq.current) {
        setError(errorMessage(e));
        setPending(null);
      }
    } finally {
      if (mine === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    if (key === null) {
      setData(null);
      setPending(null);
      setError(null);
      return;
    }
    load();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [key, load]);

  // `fresh` is false while `data` still belongs to an earlier path or parameters.
  return { data, fresh: dataKey === key, pending, error, loading, reload: () => load(0) };
}
