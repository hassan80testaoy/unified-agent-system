/** API & WebSocket helper client for Unified Agent System */

export class ApiError extends Error {
  status: number;
  code: string;
  message_ar: string;
  message_en: string;

  constructor(status: number, detail: { code?: string; message_ar?: string; message_en?: string } | string) {
    const obj = typeof detail === "string" ? { message_en: detail, message_ar: detail } : detail || {};
    super(obj.message_ar || obj.message_en || `HTTP ${status}`);
    this.status = status;
    this.code = obj.code || "api_error";
    this.message_ar = obj.message_ar || this.message;
    this.message_en = obj.message_en || this.message;
  }
}

export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = typeof window !== "undefined" ? localStorage.getItem("uas_token") : null;
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(path.startsWith("/api") ? path : `/api${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (!res.ok) {
    let payload: { detail?: { code?: string; message_ar?: string; message_en?: string } | string } = {};
    try {
      payload = await res.json();
    } catch {
      payload = { detail: res.statusText };
    }
    throw new ApiError(res.status, payload.detail || res.statusText);
  }

  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}

export function runWebSocketUrl(runId: string): string {
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  // In local dev without Caddy, FastAPI runs directly on NEXT_PUBLIC_WS_API_PORT (default 8010)
  const wsApiPort = process.env.NEXT_PUBLIC_WS_API_PORT || "8010";
  if (window.location.port === "3010" || window.location.port === "3000") {
    return `${proto}//${window.location.hostname}:${wsApiPort}/api/ws/runs/${runId}`;
  }
  return `${proto}//${window.location.host}/api/ws/runs/${runId}`;
}
