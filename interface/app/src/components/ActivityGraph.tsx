import { AnimatePresence, motion } from "motion/react";
import { useMemo } from "react";
import type { TraceEvent } from "@/lib/api";

type Status = "active" | "done" | "error" | "waiting" | "skipped";
type GNode = { id: string; depth: number; parent?: string; label: string; sub?: string; status: Status };

const COLOR: Record<Status, string> = {
  active: "#a78bfa", done: "#34d399", error: "#f87171", waiting: "#fbbf24", skipped: "#64748b",
};

/** Turns the dispatcher's trace into a tree: You -> Router/Planner -> skills -> tool calls -> answer. */
export function buildGraph(events: TraceEvent[], request: string): GNode[] {
  const nodes: GNode[] = [{ id: "req", depth: 0, label: "You", sub: request, status: "done" }];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const add = (n: GNode) => { nodes.push(n); byId.set(n.id, n); return n; };
  let router: GNode | undefined;
  let current: GNode | undefined;
  let skillCount = 0;
  for (const e of events) {
    if (e.type === "route" && !router) {
      const planned = e.source === "planner";
      const how = e.source === "laya" ? `Laya ${Math.round((e.confidence ?? 0) * 100)}%` : e.source ?? "";
      router = add({ id: "router", depth: 1, parent: "req", label: planned ? "Planner" : "Router", sub: how, status: "done" });
    } else if (e.type === "step" && e.status === "start") {
      current = add({ id: `skill${skillCount++}`, depth: 2, parent: "router", label: e.skill ?? "skill",
                      sub: (e.why as string | undefined)?.slice(0, 60), status: "active" });
    } else if (e.type === "step" && current) {
      current.status = e.status === "skip" ? "skipped" : e.status === "error" ? "error" : "done";
    } else if (e.type === "tool" && current) {
      const open = [...nodes].reverse().find((n) => n.parent === current!.id && n.id.includes(`:${e.name}:`) && n.status === "active");
      const status: Status = e.status === "start" ? "active" : e.status === "error" ? "error"
        : e.status === "waiting" ? "waiting" : "done";
      if (open) { open.label = e.label ?? open.label; open.status = status; }
      else add({ id: `${current.id}:${e.name}:${nodes.length}`, depth: 3, parent: current.id,
                 label: e.label ?? String(e.name), status });
    } else if (e.type === "done" && current) {
      add({ id: "answer", depth: 4, parent: current.id, label: "Answer", sub: `${e.ms} ms`, status: "done" });
    }
  }
  if (!router) nodes[0].status = "active";
  return nodes;
}

export function ActivityGraph({ events, request, live }: { events: TraceEvent[]; request: string; live: boolean }) {
  const nodes = useMemo(() => buildGraph(events, request), [events, request]);
  const cols = [0, 1, 2, 3, 4].map((d) => nodes.filter((n) => n.depth === d));
  const rowGap = 54;
  const H = Math.max(260, Math.max(...cols.map((c) => c.length)) * rowGap + 60);
  const X = [40, 130, 230, 330, 470];
  const pos = new Map<string, { x: number; y: number }>();
  cols.forEach((col, d) => col.forEach((n, i) => pos.set(n.id, { x: X[d], y: H / 2 + (i - (col.length - 1) / 2) * rowGap })));

  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-graph"
         style={{ backgroundImage: "radial-gradient(circle at 12% 50%, rgba(139,117,255,0.18), transparent 55%)" }}>
      <svg viewBox={`0 0 560 ${H}`} className="block w-full" role="img" aria-label="Live activity graph">
        {nodes.filter((n) => n.parent).map((n) => {
          const a = pos.get(n.parent!)!, b = pos.get(n.id)!;
          const d = `M${a.x + 8} ${a.y} C${a.x + 70} ${a.y}, ${b.x - 70} ${b.y}, ${b.x - 8} ${b.y}`;
          const flowing = live && n.status === "active";
          return (
            <g key={`e-${n.id}`}>
              <motion.path d={d} fill="none" stroke={COLOR[n.status]} strokeOpacity={0.35} strokeWidth={1.4}
                initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.35, ease: "easeOut" }} />
              {flowing && <path d={d} fill="none" stroke={COLOR.active} strokeWidth={1.6} className="edge-flow" />}
            </g>
          );
        })}
        <AnimatePresence>
          {nodes.map((n) => {
            const p = pos.get(n.id)!;
            const big = n.depth === 0;
            return (
              <motion.g key={n.id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}>
                {n.status === "active" && live && (
                  <motion.circle cx={p.x} cy={p.y} r={big ? 18 : 9} fill="none" stroke={COLOR.active}
                    initial={{ opacity: 0.6, scale: 1 }} animate={{ opacity: 0, scale: 2.2 }}
                    transition={{ duration: 1.2, repeat: Infinity }} style={{ transformOrigin: `${p.x}px ${p.y}px` }} />
                )}
                <circle cx={p.x} cy={p.y} r={big ? 14 : 6} fill={big ? "#8b75ff" : COLOR[n.status]}
                  style={{ filter: `drop-shadow(0 0 ${big ? 10 : 5}px ${big ? "#8b75ff" : COLOR[n.status]})` }} />
                <text x={p.x + (big ? 0 : 12)} y={big ? p.y + 30 : p.y - 2} textAnchor={big ? "middle" : "start"}
                  className="fill-foreground text-[12px] font-medium">{n.label.length > 26 ? n.label.slice(0, 25) + "…" : n.label}</text>
                {n.sub && !big && (
                  <text x={p.x + 12} y={p.y + 12} className="fill-muted-foreground text-[10px]">
                    {n.sub.length > 30 ? n.sub.slice(0, 29) + "…" : n.sub}</text>
                )}
              </motion.g>
            );
          })}
        </AnimatePresence>
      </svg>
      <div className="absolute left-3 top-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <span className={`h-1.5 w-1.5 rounded-full ${live ? "animate-pulse bg-violet-400" : "bg-emerald-400"}`} />
        {live ? "Live activity" : "Completed"}
      </div>
    </div>
  );
}
