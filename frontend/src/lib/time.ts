// Display of event times. Stored times are UTC; the display zone is a per-viewer preference
// (UTC by default) that changes presentation only, never stored timestamps or filters.
import { useEffect, useState } from "react";

export type DisplayZone = "UTC" | "local";
const KEY = "vrdfir.displayZone";
const listeners = new Set<(z: DisplayZone) => void>();

export function getDisplayZone(): DisplayZone {
  try {
    return localStorage.getItem(KEY) === "local" ? "local" : "UTC";
  } catch {
    return "UTC";
  }
}

export function setDisplayZone(zone: DisplayZone) {
  try {
    localStorage.setItem(KEY, zone);
  } catch {
    /* preference lasts for this page only */
  }
  listeners.forEach((l) => l(zone));
}

export function useDisplayZone(): DisplayZone {
  const [zone, setZone] = useState<DisplayZone>(getDisplayZone);
  useEffect(() => {
    listeners.add(setZone);
    return () => {
      listeners.delete(setZone);
    };
  }, []);
  return zone;
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

/** "2026-01-01 01:23:10 UTC" or the local equivalent with its explicit offset. */
export function formatInstant(iso: string | null | undefined, zone: DisplayZone, withSeconds = true): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (zone === "UTC") {
    const s = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${withSeconds ? `:${pad(d.getUTCSeconds())}` : ""}`;
    return `${s} UTC`;
  }
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const s = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}${withSeconds ? `:${pad(d.getSeconds())}` : ""}`;
  return `${s} (UTC${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)})`;
}

export function ageText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "capture time unknown";
  if (days < 1 / 24) return "less than an hour ago";
  if (days < 1) return `${Math.round(days * 24)} h ago`;
  return `${Math.round(days)} day${Math.round(days) === 1 ? "" : "s"} ago`;
}
