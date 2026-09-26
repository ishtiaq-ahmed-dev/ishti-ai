// Thin client for the ISHTI dispatcher API (same origin; `npm run dev` proxies /api).

export type TraceEvent = {
  t: number;
  type: "received" | "route" | "step" | "tool" | "done";
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
      const msg = JSON.parse(line);
      if (msg.event) on.event(msg.event);
      else if (typeof msg.token === "string") on.token(msg.token);
      else if (msg.done) result = msg.result;
    }
  }
  if (!result) throw new Error("The stream ended without a result.");
  return result;
}

export const getJSON = <T,>(url: string): Promise<T> => fetch(url).then((r) => r.json());
export const postJSON = <T,>(url: string, body?: unknown): Promise<T> =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) })
    .then((r) => r.json());
export const del = (url: string) => fetch(url, { method: "DELETE" }).then((r) => r.json());
