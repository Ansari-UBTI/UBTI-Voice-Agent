const env = (import.meta as any).env || {};
// Dev: Vite on :5173 talks to FastAPI on :8000. Production: the dashboard is served by the same container.
export const API_BASE: string = env.VITE_API_URL || (env.DEV ? "http://localhost:8000" : window.location.origin);
export const WS_BASE = API_BASE.replace(/^http/, "ws");
const TOKEN = "ubti_token";

export const getToken = () => localStorage.getItem(TOKEN) || "";
export const setToken = (t: string) => (t ? localStorage.setItem(TOKEN, t) : localStorage.removeItem(TOKEN));

type Opts = { method?: string; body?: any; form?: FormData };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  let body: any;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(API_BASE + path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  if (res.status === 401 && t) {
    setToken("");
    window.location.href = "/login";
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(msg || "Request failed");
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : (res.blob() as any)) as Promise<T>;
}

export const money = (n?: number | null, d = 3) => `$${(n || 0).toFixed(d)}`;
export const REASONS: Record<string, string> = {
  caller_hangup: "Caller hung up", agent_hangup: "Agent ended", silence: "Silence", voicemail: "Voicemail", ivr: "IVR / menu",
  max_duration: "Max duration", transfer: "Transferred", operator: "Ended by operator", error: "Error", no_answer: "No answer",
  busy: "Busy", failed: "Failed", dial_error: "Dial error", no_media: "No audio stream", server_restart: "Server restart", canceled: "Cancelled",
};
export const reason = (r?: string | null) => (r ? REASONS[r] || r.replace(/_/g, " ") : "-");

export const fmtDur = (s?: number | null) => {
  if (!s) return "0:00";
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
export const fmtDate = (d?: string | null) =>
  d ? new Date(d).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "-";
export const ago = (d?: string | null) => {
  if (!d) return "-";
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
