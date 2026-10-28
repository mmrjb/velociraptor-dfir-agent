// Per-view scroll restoration: the position is kept for the current history entry and
// restored after the view's data has rendered (for example after Back).
import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router";

const KEY = "vrdfir.scroll";

function readAll(): Record<string, number> {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

export function useScrollRestore(ready: boolean) {
  const location = useLocation();
  const key = `${location.pathname}${location.search}`;
  const restored = useRef<string | null>(null);
  useEffect(() => {
    const save = () => {
      try {
        const all = readAll();
        all[key] = window.scrollY;
        const entries = Object.entries(all).slice(-50);
        sessionStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries)));
      } catch {
        /* storage unavailable: scroll is not restored */
      }
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => {
      save();
      window.removeEventListener("scroll", save);
    };
  }, [key]);
  useLayoutEffect(() => {
    if (!ready || restored.current === key) return;
    restored.current = key;
    const y = readAll()[key];
    if (typeof y === "number" && y > 0) window.scrollTo(0, y);
  }, [ready, key]);
}
