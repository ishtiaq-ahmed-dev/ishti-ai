import { AnimatePresence, motion } from "motion/react";
import { Minus, Plus, RotateCcw } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import type { TraceEvent } from "@/lib/api";

type Status = "active" | "done" | "error" | "waiting" | "skipped";
type GNode = { id: string; depth: number; parent?: string; label: string; sub?: string; status: Status; group?: string };

export const COLOR: Record<Status, string> = {
  active: "#38bdf8", // Electric Sky Blue
  done: "#10b981",   // Vibrant Emerald Green
  error: "#ef4444",  // Coral Red
  waiting: "#f59e0b",// Amber Gold
  skipped: "#94a3b8",// Cool Slate
};

function getNodeColor(n: GNode): string {
  if (n.status === "error") return COLOR.error;
  if (n.status === "waiting") return COLOR.waiting;
  if (n.status === "skipped") return COLOR.skipped;

  // Depth 0: You -> Electric Azure Blue
  if (n.depth === 0) return "#3b82f6";

  // Depth 1: Understand / Router / Planner
  if (n.id === "understand") return "#06b6d4"; // Cyan
  if (n.id === "router") {
    return n.label === "Planner" ? COLOR.waiting : "#6366f1"; // Amber for planner, Indigo for router
  }

  // Depth 2: Skills (Groups)
  if (n.depth === 2) {
    if (n.status === "active") return COLOR.active; // Electric Blue when running
    return COLOR.done; // Emerald Green when completed
  }

  // Depth 3: Tool calls
  if (n.depth === 3) {
    if (n.status === "active") return "#0ea5e9"; // Sky Cyan active
    return "#22c55e"; // Fresh Green
  }

  // Depth 4: Answer
  if (n.id === "answer") return COLOR.done; // Emerald Green

  return n.status === "active" ? COLOR.active : COLOR.done;
}

/** Trace -> tree: You -> (understood as) -> Router/Planner -> skills (groups) -> their tool calls -> Answer. */
export function buildGraph(events: TraceEvent[], request: string): GNode[] {
  const nodes: GNode[] = [{ id: "req", depth: 0, label: "You", sub: request, status: "done" }];
  let router: GNode | undefined;
  let current: GNode | undefined;
  let skillCount = 0;
  for (const e of events) {
    if (e.type === "tool" && !current && e.name === "understand") {
      const open = nodes.find((n) => n.id === "understand");
      if (open) { open.label = e.label ?? open.label; open.status = e.status === "start" ? "active" : "done"; }
      else nodes.push({ id: "understand", depth: 1, parent: "req", label: e.label ?? "understand", status: "active" });
    } else if (e.type === "route" && !router) {
      const planned = e.source === "planner";
      const how = e.source === "laya" ? `Laya ${Math.round((e.confidence ?? 0) * 100)}%` : e.source ?? "";
      router = { id: "router", depth: 1, parent: "req", label: planned ? "Planner" : "Router", sub: how, status: "done" };
      nodes.push(router);
    } else if (e.type === "step" && e.status === "start") {
      current = { id: `skill${skillCount++}`, depth: 2, parent: "router", label: e.skill ?? "skill",
                  sub: (e.why as string | undefined)?.slice(0, 70), status: "active" };
      nodes.push(current);
    } else if (e.type === "step" && current) {
      current.status = e.status === "skip" ? "skipped" : e.status === "error" ? "error" : "done";
    } else if (e.type === "tool" && current) {
      const cur = current;
      const open = [...nodes].reverse().find((n) => n.group === cur.id && n.id.includes(`:${e.name}:`) && n.status === "active");
      const status: Status = e.status === "start" ? "active" : e.status === "error" ? "error"
        : e.status === "waiting" ? "waiting" : "done";
      if (open) { open.label = e.label ?? open.label; open.status = status; }
      else nodes.push({ id: `${cur.id}:${e.name}:${nodes.length}`, depth: 3, parent: cur.id, group: cur.id,
                        label: e.label ?? String(e.name), status });
    } else if (e.type === "done" && current) {
      nodes.push({ id: "answer", depth: 4, parent: current.id, label: "Answer", sub: `${e.ms} ms`, status: "done" });
    }
  }
  if (!router) nodes[0].status = "active";
  return nodes;
}

/** Pan (drag) + zoom (wheel / buttons) around SVG content, for the full-screen view. */
function Zoomable({ children, height }: { children: ReactNode; height: number }) {
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const zoom = (f: number) => setView((v) => ({ ...v, k: Math.min(3, Math.max(0.35, v.k * f)) }));
  return (
    <div className="relative h-full w-full touch-none select-none"
      onWheel={(e) => zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12)}
      onPointerDown={(e) => { drag.current = { x: e.clientX - view.x, y: e.clientY - view.y }; }}
      onPointerMove={(e) => { if (drag.current) setView((v) => ({ ...v, x: e.clientX - drag.current!.x, y: e.clientY - drag.current!.y })); }}
      onPointerUp={() => { drag.current = null; }} onPointerLeave={() => { drag.current = null; }}>
      <svg viewBox={`0 0 760 ${height}`} className="h-full w-full cursor-grab active:cursor-grabbing">
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>{children}</g>
      </svg>
      <div className="absolute bottom-3 right-3 flex gap-1">
        {[{ i: <Plus className="h-3.5 w-3.5" />, f: () => zoom(1.25), t: "Zoom in" },
          { i: <Minus className="h-3.5 w-3.5" />, f: () => zoom(0.8), t: "Zoom out" },
          { i: <RotateCcw className="h-3.5 w-3.5" />, f: () => setView({ k: 1, x: 0, y: 0 }), t: "Reset view" }].map((b) => (
          <button key={b.t} title={b.t} onPointerDown={(e) => e.stopPropagation()} onClick={b.f}
            className="grid h-7 w-7 place-items-center rounded-lg border border-border bg-background text-muted-foreground shadow-sm hover:text-foreground">{b.i}</button>
        ))}
      </div>
    </div>
  );
}

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

export function ActivityGraph({ events, request, live, full = false }:
  { events: TraceEvent[]; request: string; live: boolean; full?: boolean }) {
  const all = useMemo(() => buildGraph(events, request), [events, request]);
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  // a skill's tool calls show while it runs (and in full screen); otherwise they fold into a badge
  const isOpen = (n: GNode) => toggled[n.id] ?? (full || n.status === "active");
  const nodes = all.filter((n) => !n.group || isOpen(all.find((s) => s.id === n.group)!));
  const hidden = (id: string) => all.filter((n) => n.group === id).length;

  const cols = [0, 1, 2, 3, 4].map((d) => nodes.filter((n) => n.depth === d));
  const rowGap = 50;
  const H = Math.max(240, Math.max(...cols.map((c) => c.length)) * rowGap + 50);
  const X = [44, 150, 270, 420, 640];
  const pos = new Map<string, { x: number; y: number }>();
  cols.forEach((col, d) => col.forEach((n, i) => pos.set(n.id, { x: X[d], y: H / 2 + (i - (col.length - 1) / 2) * rowGap })));

  const content = (
    <>
      {nodes.filter((n) => n.parent && pos.get(n.parent)).map((n) => {
        const a = pos.get(n.parent!)!, b = pos.get(n.id)!;
        const d = `M${a.x + 8} ${a.y} C${a.x + 60} ${a.y}, ${b.x - 60} ${b.y}, ${b.x - 8} ${b.y}`;
        const strokeColor = getNodeColor(n);
        return (
          <g key={`e-${n.id}`}>
            <motion.path d={d} fill="none" stroke={strokeColor} strokeOpacity={0.4} strokeWidth={1.4}
              initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.35, ease: "easeOut" }} />
            {live && n.status === "active" && <path d={d} fill="none" stroke="#38bdf8" strokeWidth={1.8} className="edge-flow" />}
          </g>
        );
      })}
      <AnimatePresence>
        {nodes.map((n) => {
          const p = pos.get(n.id)!;
          const big = n.depth === 0;
          const count = n.depth === 2 ? hidden(n.id) : 0;
          const color = getNodeColor(n);
          return (
            <motion.g key={n.id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              onPointerDown={(e) => count && e.stopPropagation()}
              onClick={() => count && setToggled((t) => ({ ...t, [n.id]: !isOpen(n) }))}
              style={{ cursor: count ? "pointer" : "default" }}>
              {n.status === "active" && live && (
                <motion.circle cx={p.x} cy={p.y} r={big ? 18 : 9} fill="none" stroke="#38bdf8"
                  initial={{ opacity: 0.7, scale: 1 }} animate={{ opacity: 0, scale: 2.3 }}
                  transition={{ duration: 1.2, repeat: Infinity }} style={{ transformOrigin: `${p.x}px ${p.y}px` }} />
              )}
              {n.depth === 2 && (  // skills are groups: colorful soft container behind the node and its label
                <rect x={p.x - 12} y={p.y - 17} width={140} height={34} rx={12}
                  className={n.status === "active" ? "fill-sky-500/10 stroke-sky-500/35" : "fill-emerald-500/10 stroke-emerald-500/25"}
                  strokeWidth={1} />
              )}
              <circle cx={p.x} cy={p.y} r={big ? 14 : 6} fill={color}
                style={{ filter: `drop-shadow(0 0 ${big ? 10 : 5}px ${color})` }} />
              <text x={p.x + (big ? 0 : 12)} y={big ? p.y + 30 : p.y - 2} textAnchor={big ? "middle" : "start"}
                className="fill-foreground text-[12px] font-medium">{trunc(n.label, n.depth === 3 ? 34 : 18)}</text>
              {(n.sub || count > 0) && !big && (
                <text x={p.x + 12} y={p.y + 12} className="fill-muted-foreground text-[10px]">
                  {count > 0 && !isOpen(n) ? `${count} step${count > 1 ? "s" : ""} · click to open` : trunc(n.sub ?? "", 30)}
                </text>
              )}
            </motion.g>
          );
        })}
      </AnimatePresence>
    </>
  );

  return (
    <div className={`relative overflow-hidden rounded-2xl border border-border bg-graph ${full ? "h-full" : ""}`}
      style={{
        backgroundImage:
          "radial-gradient(circle at 10% 45%, rgba(56,189,248,0.14), transparent 45%), radial-gradient(circle at 85% 55%, rgba(16,185,129,0.12), transparent 45%)",
      }}>
      {full ? <Zoomable height={H}>{content}</Zoomable>
            : <svg viewBox={`0 0 760 ${H}`} className="block max-h-[60vh] w-full" role="img" aria-label="Live activity graph">{content}</svg>}
      <div className="pointer-events-none absolute left-3 top-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <span className={`h-1.5 w-1.5 rounded-full ${live ? "animate-pulse bg-sky-400" : "bg-emerald-400"}`} />
        <span className="font-semibold">{live ? "Live Pipeline Activity" : "Completed"}</span>
      </div>
    </div>
  );
}

export type Turn = { question: string; skill: string | null; ms?: number };

/** The whole conversation at a glance: every question, and the skills that handled it. */
export function ConversationGraph({ turns, full = false }: { turns: Turn[]; full?: boolean }) {
  const skills = [...new Set(turns.map((t) => t.skill ?? "none"))];
  const H = Math.max(260, Math.max(turns.length * 46, skills.length * 60) + 60);
  const q = turns.map((t, i) => ({ ...t, x: 230, y: H / 2 + (i - (turns.length - 1) / 2) * 46 }));
  const s = new Map(skills.map((k, i) => [k, { x: 560, y: H / 2 + (i - (skills.length - 1) / 2) * 60 }]));
  const content = (
    <>
      {q.map((t, i) => {
        const b = s.get(t.skill ?? "none")!;
        return (
          <g key={`e${i}`}>
            <motion.path d={`M76 ${H / 2} C150 ${H / 2}, 150 ${t.y}, ${t.x - 8} ${t.y}`} fill="none" stroke="#3b82f6"
              strokeOpacity={0.4} initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: i * 0.04 }} />
            <motion.path d={`M${t.x + 8} ${t.y} C${t.x + 150} ${t.y}, ${b.x - 150} ${b.y}, ${b.x - 8} ${b.y}`} fill="none"
              stroke="#10b981" strokeOpacity={0.4} initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.1 + i * 0.04 }} />
          </g>
        );
      })}
      <circle cx={60} cy={H / 2} r={16} fill="#3b82f6" style={{ filter: "drop-shadow(0 0 12px rgba(59,130,246,0.6))" }} />
      <text x={60} y={H / 2 + 32} textAnchor="middle" className="fill-foreground text-[12px] font-medium">This chat</text>
      {q.map((t, i) => (
        <g key={`q${i}`}>
          <circle cx={t.x} cy={t.y} r={6} fill="#38bdf8" />
          <text x={t.x + 12} y={t.y + 4} className="fill-foreground text-[11px]">{i + 1}. {trunc(t.question, 34)}</text>
        </g>
      ))}
      {[...s.entries()].map(([k, p]) => (
        <g key={`s${k}`}>
          <rect x={p.x - 12} y={p.y - 17} width={150} height={34} rx={12} className="fill-emerald-500/10 stroke-emerald-500/25" />
          <circle cx={p.x} cy={p.y} r={7} fill="#10b981" />
          <text x={p.x + 14} y={p.y + 4} className="fill-foreground text-[12px] font-medium">
            {k} · {turns.filter((t) => (t.skill ?? "none") === k).length}×</text>
        </g>
      ))}
    </>
  );
  return (
    <div className={`relative overflow-hidden rounded-2xl border border-border bg-graph ${full ? "h-full" : ""}`}>
      {full ? <Zoomable height={H}>{content}</Zoomable>
            : <svg viewBox={`0 0 760 ${H}`} className="block max-h-[60vh] w-full">{content}</svg>}
    </div>
  );
}
