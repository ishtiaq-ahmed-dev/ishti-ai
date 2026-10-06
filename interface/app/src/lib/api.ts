// Thin client for the ISHTI dispatcher API (same origin; `npm run dev` proxies /api).

export type TraceEvent = {
  t: number;
  type: "received" | "route" | "step" | "tool" | "escalate" | "done";
  skill?: string;
  status?: "start" | "done" | "error" | "skip" | "waiting";
  label?: string;
  name?: string;
  why?: string;
  source?: string;
  confidence?: number;
  scores?: [string, number][];
  steps?: { skill: string; request: string }[];
  summary?: string;
  ms?: number;
  [key: string]: unknown;
};

export type Source = { id?: number; title?: string; url?: string; domain?: string; snippet?: string; verified?: boolean };

export type Result = {
  final_answer: string;
  skill: string | null;
  session_id: string;
  message_id?: string | null;
  sources: Source[];
  latency_ms?: number;
};

/** Streams one request: trace events and reply tokens as they happen, then the final result. */
export async function streamChat(
  prompt: string,
  sessionId: string | null,
  on: { event: (e: TraceEvent) => void; token: (t: string) => void },
): Promise<Result> {
  const res = await fetch("/api/execute_stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, session_id: sessionId ?? "" }),
  });
  if (!res.ok || !res.body) throw new Error(`ISHTI server error ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: Result | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const msg = JSON.parse(line);
        if (msg.event) on.event(msg.event);
        else if (typeof msg.token === "string") on.token(msg.token);
        else if (msg.done) result = msg.result;
      } catch (parseErr) {
        console.warn("[streamChat] invalid JSON line:", line, parseErr);
      }
    }
  }
  if (!result) throw new Error("The stream ended without a result.");
  return result;
}

async function checkResponse(r: Response) {
  if (!r.ok) {
    let errorDetail = `Server error (${r.status} ${r.statusText})`;
    try {
      const data = await r.json();
      if (data?.error || data?.reply || typeof data?.detail === "string") {
        errorDetail = String(data.error || data.reply || data.detail);
      }
    } catch {}
    throw new Error(errorDetail);
  }
  return r.json();
}

// Every failed write is announced once (App.tsx renders the toast), so a rejected click never looks like nothing happened.
// Callers still receive the rejection and may handle it themselves too.
let lastToast = { msg: "", at: 0 };
const mutate = <T,>(url: string, init: RequestInit): Promise<T> =>
  fetch(url, init).then(checkResponse).catch((e: unknown) => {
    const msg = e instanceof TypeError ? "Can't reach the ISHTI backend." : e instanceof Error ? e.message : String(e);
    const now = Date.now();
    if (!/\/api\/(events|stt)/.test(url) && (msg !== lastToast.msg || now - lastToast.at > 3000)) {
      lastToast = { msg, at: now };
      window.dispatchEvent(new CustomEvent("ishti-api-error", { detail: { msg, url } }));
    }
    throw e;
  });
const json = (method: string, body?: unknown): RequestInit =>
  ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const getJSON = <T,>(url: string): Promise<T> => fetch(url).then(checkResponse);
export const postJSON = <T,>(url: string, body?: unknown): Promise<T> => mutate<T>(url, json("POST", body));
export const del = (url: string) => mutate<any>(url, { method: "DELETE" });
export const putJSON = <T,>(url: string, body?: unknown): Promise<T> => mutate<T>(url, json("PUT", body));
export const patchJSON = <T,>(url: string, body?: unknown): Promise<T> => mutate<T>(url, json("PATCH", body));
export const deleteJSON = <T,>(url: string): Promise<T> => mutate<T>(url, { method: "DELETE" });
