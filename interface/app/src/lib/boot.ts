// Live startup state of the backend: which services are pending / loading / ready (GET /api/ready, then the
// boot_stage events of WS /ws/events; docs/API_CONTRACT.md §2-3). The dashboard opens ~1 s after launch while the
// models keep loading, so everything that depends on the backend being ready reads this instead of assuming it.
import { createContext, useContext, useEffect, useState } from "react";

export type StageStatus = "pending" | "loading" | "done" | "error";
export type BootStage = { id: string; label: string; done: boolean; status?: StageStatus };
export type Boot = {
  ready: boolean;
  state: string;
  stage: string;
  progress: number;
  stages: BootStage[];
  error: string | null;
  warnings: string[];
  elapsed_s?: number;
  unreachable?: boolean;
};

export const BootContext = createContext<Boot | null>(null);
export const useBootState = () => useContext(BootContext);

const statusOf = (s: BootStage): StageStatus => s.status ?? (s.done ? "done" : "pending");

/** Polls once, then follows boot_stage events; falls back to polling while the socket is down. Stops once ready. */
export function useBoot(): Boot | null {
  const [boot, setBoot] = useState<Boot | null>(null);

  useEffect(() => {
    let alive = true;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let live = false;

    const load = async () => {
      try {
        const r = await fetch("/api/ready", { cache: "no-store" });
        const d = (await r.json()) as Boot;
        if (!alive) return;
        setBoot({ ...d, unreachable: false });
        if (d.ready) return; // done: no more traffic
      } catch {
        if (alive) setBoot((b) => (b ? { ...b, unreachable: true } : b));
      }
      if (alive) timer = setTimeout(load, live ? 4000 : 800);
    };

    const connect = () => {
      try {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/ws/events`);
        ws.onopen = () => (live = true);
        ws.onclose = () => {
          live = false;
          if (alive) setTimeout(connect, 2000);
        };
        ws.onmessage = (m) => {
          try {
            const msg = JSON.parse(m.data);
            if (msg.type !== "boot_stage" || !msg.data) return;
            const d = msg.data;
            setBoot((b) => {
              if (!b) return b;
              const stages = b.stages.map((s) =>
                s.id === d.id ? { ...s, done: !!d.done, status: (d.status as StageStatus) ?? (d.done ? "done" : s.status) } : s,
              );
              return { ...b, stages, stage: d.stage ?? b.stage, progress: d.progress ?? b.progress, error: d.error ?? null, ready: d.id === "ready" && !!d.done ? true : b.ready };
            });
            if (d.id === "ready" && d.done && timer) clearTimeout(timer);
          } catch {
            /* a malformed event is not worth a crash */
          }
        };
      } catch {
        /* polling covers it */
      }
    };

    load();
    connect();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return boot;
}

export const stageStatus = statusOf;

/** What each internal stage means to a person. */
export const SERVICE: Record<string, { name: string; hint: string }> = {
  backend_up: { name: "ISHTI server", hint: "Starting up" },
  ollama_ready: { name: "AI engine", hint: "Ollama, the program that runs the models" },
  memory_loaded: { name: "Memory", hint: "Your facts and the search index" },
  skills_loaded: { name: "Skills and router", hint: "Everything ISHTI can do, and the model that picks the right skill" },
  planner_warm: { name: "Main model", hint: "The brain, loaded onto the graphics card" },
  voice_ready: { name: "Voice", hint: "Speaking (ISHTI works without it)" },
  ready: { name: "Ready", hint: "" },
};
