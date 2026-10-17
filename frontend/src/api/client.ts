// Same-origin client for the local UI API. The CSRF token is held in memory only (never in
// localStorage); the session cookie is HttpOnly and set by the server.
import type { ApiError, SessionInfo } from "./types";

export class ApiRequestError extends Error {
  readonly status: number;
  readonly error: ApiError;
  constructor(status: number, error: ApiError) {
    super(error.message);
    this.status = status;
    this.error = error;
  }
}

let csrfToken: string | null = null;
let sessionPromise: Promise<SessionInfo> | null = null;

export function ensureSession(force = false): Promise<SessionInfo> {
  if (!sessionPromise || force) {
    sessionPromise = fetch("/api/v1/session", { credentials: "same-origin" })
      .then(parse<SessionInfo>)
      .then((info) => {
        csrfToken = info.csrf_token;
        return info;
      })
      .catch((err) => {
        sessionPromise = null;
        throw err;
      });
  }
  return sessionPromise;
}

async function parse<T>(res: Response): Promise<T> {
  let body: unknown = null;
  const text = await res.text();
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  const obj = body as { ok?: boolean; data?: T; error?: ApiError } | null;
  if (!res.ok || !obj || obj.ok === false) {
    const error: ApiError = obj?.error ?? {
      code: `http_${res.status}`,
      message: res.status === 503 ? "The server is not ready." : `Request failed (HTTP ${res.status}).`,
    };
    throw new ApiRequestError(res.status, error);
  }
  return obj.data as T;
}

function query(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export async function apiGet<T>(path: string, params?: Record<string, string | number | boolean | undefined | null>): Promise<T> {
  const res = await fetch(`/api/v1${path}${query(params)}`, { credentials: "same-origin" });
  return parse<T>(res);
}

async function mutate<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>, raw?: BodyInit): Promise<T> {
  await ensureSession();
  const doFetch = () =>
    fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        ...(raw ? { "Content-Type": "application/octet-stream" } : { "Content-Type": "application/json" }),
        "X-CSRF-Token": csrfToken ?? "",
        ...headers,
      },
      body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
  let res = await doFetch();
  if (res.status === 403) {
    // Session may have expired (server restart). Refresh once, then retry.
    const clone = res.clone();
    const err = await clone.json().catch(() => null);
    if (err?.error?.code === "csrf_failed") {
      await ensureSession(true);
      res = await doFetch();
    }
  }
  return parse<T>(res);
}

export const apiPost = <T>(path: string, body?: unknown, headers?: Record<string, string>) => mutate<T>("POST", path, body ?? {}, headers);
export const apiPut = <T>(path: string, body?: unknown) => mutate<T>("PUT", path, body ?? {});
export const apiDelete = <T>(path: string) => mutate<T>("DELETE", path);
export const apiUpload = <T>(path: string, data: Blob, params?: Record<string, string>) =>
  mutate<T>("POST", `${path}${query(params)}`, undefined, {}, data);

export function newIdempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function errorMessage(err: unknown): ApiError {
  if (err instanceof ApiRequestError) return err.error;
  if (err instanceof Error) return { code: "client_error", message: err.message };
  return { code: "client_error", message: "Unexpected error." };
}
