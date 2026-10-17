import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, errorMessage } from "../api/client";
import type { ApiError } from "../api/types";

type Params = Record<string, string | number | boolean | undefined | null>;

/** GET a resource; `path === null` skips loading. Reloads when the path or params change. */
export function useApi<T>(path: string | null, params?: Params) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const key = path === null ? null : `${path}?${JSON.stringify(params ?? {})}`;
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (path === null) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const d = await apiGet<T>(path, params);
      if (mine === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) {
        setError(errorMessage(e));
        setData(null);
      }
    } finally {
      if (mine === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    if (key === null) {
      setData(null);
      setError(null);
      return;
    }
    load();
  }, [key, load]);

  return { data, error, loading, reload: load, setData };
}
