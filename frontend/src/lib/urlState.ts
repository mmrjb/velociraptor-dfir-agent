// Filters, selections and paging live in the URL so Back, reload and shared links keep the
// exact context. Setting a parameter to null or "" removes it.
import { useCallback } from "react";
import { useSearchParams } from "react-router";

export type SetParams = (updates: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => void;

export function useUrlState(): [URLSearchParams, SetParams] {
  const [params, setParams] = useSearchParams();
  const set: SetParams = useCallback(
    (updates, opts) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(updates)) {
            if (v === null || v === undefined || v === "") next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: opts?.replace ?? false },
      );
    },
    [setParams],
  );
  return [params, set];
}

// Keys whose values are filters (cleared together by "Clear filters"; never pins, notes or reviews).
export const FILTER_KEYS = ["sev", "review", "origin", "host", "src", "from", "to", "untimed", "ek", "ekey", "q", "fq", "cited"] as const;
