import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, ChevronDown, Loader2, Minus, Plus, RotateCcw, XCircle } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import type { StoreRun } from "@/lib/chat-store";

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

/** One persisted run step (CONTRACT v2 run_step). */
export type RunStep = { kind: string; label: string; ts?: number; sources?: string[]; n?: number };

/**
 * A step's real kind. The executor records route/skill/tool/verify/research; rows written before those
 * kinds existed baked the meaning into the label ("Routing: ...", "Using x"), so read those labels too.
 */
function kindOf(s: RunStep): string {
  if (s.kind !== "tool") return s.kind;
  if (/^Routing:/i.test(s.label)) return "route";
  if (/^Using\s/i.test(s.label)) return "skill";
  return "tool";
}

/** Real run steps -> tree: You -> Router -> skills (groups) -> their tool calls -> Answer. */
export function buildGraph(steps: RunStep[], request: string, state?: string, ms?: number): GNode[] {
  const live = state === "running" || state === "queued";
  const nodes: GNode[] = [{ id: "req", depth: 0, label: "You", sub: request, status: "done" }];
  let router: GNode | undefined;
  let skill: GNode | undefined;
  let last: GNode | undefined;
  let n = 0;
  for (const s of steps) {
    const kind = kindOf(s);
    let node: GNode;
    if (kind === "route" && !router) {
      router = { id: "router", depth: 1, parent: "req", label: "Router",
                 sub: s.label.replace(/^Routing:\s*/i, "").slice(0, 70), status: "done" };
      node = router;
    } else if (kind === "skill") {
      // a skill is a group: its tool calls fold under it
      skill = { id: `skill${n++}`, depth: 2, parent: (router ?? nodes[0]).id,
                label: s.label.replace(/^Using\s+/i, ""), status: "done" };
      node = skill;
    } else {
      // a re-route after an escalation, a tool call, a verification, a research step
      const parent = skill ?? router ?? nodes[0];
      node = { id: `step${n++}`, depth: skill ? 3 : 2, parent: parent.id, group: skill?.id,
               label: s.label, status: "done",
               sub: s.sources?.length ? `${s.sources.length} source${s.sources.length > 1 ? "s" : ""}` : undefined };
    }
    nodes.push(node);
    last = node;
  }
  if (last) last.status = state === "failed" ? "error" : live ? "active" : "done";
  else nodes[0].status = live ? "active" : "done";
  if (!live) {
    nodes.push({ id: "answer", depth: last ? 4 : 1, parent: (last ?? nodes[0]).id, label: "Answer",
                 sub: ms ? `${Math.round(ms)} ms` : undefined, status: state === "failed" ? "error" : "done" });
  }
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

export function ActivityGraph({ steps, request, state, ms, full = false }:
  { steps: RunStep[]; request: string; state?: string; ms?: number; full?: boolean }) {
  const live = state === "running" || state === "queued";
  const all = useMemo(() => buildGraph(steps, request, state, ms), [steps, request, state, ms]);
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

// ---- T3: the plain LIST view of the activity panel (default tab, graph stays the second tab) ----
// One row per step with kind (route/skill/tool/verify/research), label, duration and status; each row
// expands (aria-expanded/aria-controls) to its detail text and sources. It reads StoreRun state that
// the chat store keeps updating, so rows appear live on run_step events and after a reconnect resync.
const KIND_LABEL: Record<string, string> = {
  route: "route", skill: "skill", tool: "tool", verify: "verify", research: "research",
};

function isLiveRun(run: StoreRun): boolean {
  return run.state === "running" || run.state === "queued";
}

function stepStatusOf(run: StoreRun, index: number): "running" | "done" | "failed" {
  if (run.state === "failed" && index === run.steps.length - 1) return "failed";
  return isLiveRun(run) && index === run.steps.length - 1 ? "running" : "done";
}

function StepListRow({ run, index }: { run: StoreRun; index: number }) {
  const [open, setOpen] = useState(false);
  const step = run.steps[index];
  const status = stepStatusOf(run, index);
  const end = run.steps[index + 1]?.ts ?? (isLiveRun(run) ? undefined : run.ended_at);
  const ms = step.ts && end ? Math.max(0, Math.round((end - step.ts) * 1000)) : undefined;
  const detailId = `activity-detail-${run.run_id}-${index}`;
  const kind = KIND_LABEL[step.kind] || step.kind;
  return (
    <li className="rounded-lg border border-border/60 bg-card/40">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls={detailId}
        data-testid="activity-step"
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px] transition-colors hover:bg-muted/50">
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
        {status === "running" ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-500" aria-hidden />
        ) : status === "failed" ? (
          <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" aria-hidden />
        )}
        <span className="shrink-0 rounded border border-border/70 bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {kind}
        </span>
        <span className={`min-w-0 flex-1 truncate ${status === "failed" ? "text-destructive" : "text-foreground"}`}>{step.label}</span>
        <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{ms !== undefined ? `${ms} ms` : ""}</span>
      </button>
      {open && (
        <div id={detailId} className="border-t border-border/60 px-2.5 py-2 text-[11px] text-muted-foreground">
          <p className="break-words whitespace-pre-wrap text-foreground">{step.label}</p>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
            <span>step {step.n ?? index + 1}</span>
            <span>kind: {kind}</span>
            <span>status: {status}</span>
            {ms !== undefined && <span>{ms} ms</span>}
          </div>
          {step.sources?.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1.5">
              {step.sources.map((u) => (
                <a key={u} href={u} target="_blank" rel="noreferrer"
                  className="max-w-full truncate text-accent hover:underline">
                  {u.replace(/^https?:\/\//, "").split("/")[0]}
                </a>
              ))}
            </div>
          )}
          {status === "failed" && run.error && <p className="mt-1 text-destructive">{run.error}</p>}
        </div>
      )}
    </li>
  );
}

/** Plain list view: every run's steps as expandable rows (newest run first). */
export function ActivityList({ runs }: { runs: StoreRun[] }) {
  const ordered = [...runs].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
  if (!ordered.length) {
    return (
      <p className="rounded-2xl border border-dashed border-border p-4 text-center text-xs text-muted-foreground" data-testid="activity-empty">
        No activity yet — runs and their steps will appear here.
      </p>
    );
  }
  return (
    <div className="min-w-0 space-y-3" data-testid="activity-list">
      {ordered.map((run) => {
        const start = run.started_at;
        const end = run.ended_at;
        const dur = start && end ? `${Math.max(0, end - start).toFixed(1)} s` : isLiveRun(run) ? "running…" : "";
        return (
          <section key={run.run_id} className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <span className="font-mono">{run.run_id.slice(0, 10)}</span>
              <span className={`rounded-full border px-1.5 py-0.5 ${
                run.state === "failed" ? "border-destructive/40 text-destructive"
                : isLiveRun(run) ? "border-sky-500/40 text-sky-500" : "border-emerald-500/40 text-emerald-600"}`}>
                {run.state}
              </span>
              {dur && <span>{dur}</span>}
              <span>{run.steps.length} step{run.steps.length === 1 ? "" : "s"}</span>
            </div>
            {run.steps.length ? (
              <ol className="space-y-1">
                {run.steps.map((_s, i) => <StepListRow key={`${run.run_id}-${i}`} run={run} index={i} />)}
              </ol>
            ) : (
              <p className="text-[11px] text-muted-foreground">No steps recorded for this run yet.</p>
            )}
          </section>
        );
      })}
    </div>
  );
}
