// WP3 research progress UX + T2 unified thinking panel: ONE component (Thinking) serves both the
// live run (expanded by default, steps stream in, cancel) and finished/historic runs (auto-collapsed
// to "Thought for 12.4 s · used web_search, browser"). The user's own toggle always wins over the
// automatic expand/collapse, including during a live run. Historic messages whose run is not in the
// store lazily fetch GET /api/runs/{id} via chatStore.ensureRun: skeleton while loading, nothing (not
// an error) when the run is unknown. Expand/collapse-all reaches every panel through thinkingBus.
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, CircleStop, ExternalLink, FlaskConical, Loader2, XCircle } from "lucide-react";
import { chatStore, type StoreRun } from "@/lib/chat-store";
import { cn } from "@/lib/utils";

function fmtSeconds(seconds: number): string {
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`;
}

function runDuration(run: StoreRun): string {
  const start = run.started_at || run.steps.find((step) => step.ts)?.ts || null;
  const end = run.ended_at || [...run.steps].reverse().find((step) => step.ts)?.ts || null;
  return fmtSeconds(start && end ? Math.max(0, end - start) : 0);
}

function usedTools(run: StoreRun): string[] {
  const names: string[] = [];
  for (const step of run.steps) {
    const match = step.label.match(/^(?:Using|used)\s+([^:·—]+)/i);
    if (match?.[1]) names.push(match[1].trim());
  }
  return [...new Set(names)].filter(Boolean);
}

// Per-message toggle memory + "Expand all / Collapse all" fan-out (T2). A Thinking panel keeps its
// own open state, remembered here keyed by run_id so a user's toggle survives the live→done remount
// (the panel unmounts from the pending slot and re-mounts with the finished message).
const openOverride = new Map<string, boolean>();
const thinkingBus = {
  listeners: new Set<(expand: boolean) => void>(),
  emit(expand: boolean) {
    for (const fn of [...this.listeners]) {
      try { fn(expand); } catch {}
    }
  },
  subscribe(fn: (expand: boolean) => void): () => void {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  },
};

export function toggleAllThinking(expand: boolean) {
  thinkingBus.emit(expand);
}

const TERMINAL = ["done", "failed", "cancelled"] as const;

/** One step row: status icon, label (clipped to 3 lines with Show more), duration, sources. */
function StepRow({ step, status, duration, errorText }: {
  step: StoreRun["steps"][number]; status: "running" | "done" | "failed"; duration?: string; errorText?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [clipped, setClipped] = useState(false);
  const textRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = textRef.current;
    if (el) setClipped(el.scrollHeight > el.clientHeight + 1);
  }, [step.label, expanded]);
  return (
    <li className="flex items-start gap-1.5">
      {status === "running" ? (
        <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-sky-500" aria-hidden />
      ) : status === "failed" ? (
        <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-destructive" aria-hidden />
      ) : (
        <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" aria-hidden />
      )}
      <span className="min-w-0 flex-1">
        <span ref={textRef}
          className={cn("block break-words", !expanded && "line-clamp-3", status === "failed" && "text-destructive")}>
          {step.label}
        </span>
        {clipped && (
          <button type="button" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}
            className="mt-0.5 rounded text-accent hover:underline">
            {expanded ? "Show less" : "Show more"}
          </button>
        )}
        {errorText && <span className="mt-0.5 block break-words text-destructive">{errorText}</span>}
        {duration && <span className="ml-1 text-[10px] text-muted-foreground/70">{duration}</span>}
      </span>
      {step.sources.slice(0, 3).map((u) => (
        <a key={u} href={u} target="_blank" rel="noreferrer"
          className="inline-flex max-w-[180px] shrink-0 items-center gap-0.5 truncate text-accent hover:underline">
          <ExternalLink className="h-3 w-3" aria-hidden />
          {u.replace(/^https?:\/\//, "").split("/")[0]}
        </a>
      ))}
    </li>
  );
}

/** Skeleton shown while a historic run is being fetched. */
export function ThinkingSkeleton() {
  return (
    <div className="mb-2 flex items-center gap-2 text-[12px] text-muted-foreground" data-testid="thinking-skeleton"
      aria-label="Loading run trace">
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      <span className="animate-pulse">Loading trace…</span>
    </div>
  );
}

/** The one thinking panel: live streams expanded, finished auto-collapses, toggle always wins. */
export function Thinking({ run, runId, onCancel }: {
  run?: StoreRun; runId?: string; onCancel?: (runId: string) => void;
}) {
  // null = follow the run (live: expanded, finished: collapsed); true/false = the user's choice.
  const [open, setOpen] = useState<boolean | null>(() => (runId ? openOverride.get(runId) ?? null : null));
  const setOpenAndRemember = (value: boolean) => {
    if (runId) openOverride.set(runId, value);
    setOpen(value);
  };
  useEffect(() => thinkingBus.subscribe((expand) => setOpenAndRemember(expand)), [runId]);

  // Historic message: fetch the run once on first render (cached in the store; silent on 404).
  const missing = !run && !!runId && !chatStore.runMissed(runId);
  useEffect(() => {
    if (missing && runId) void chatStore.ensureRun(runId);
  }, [missing, runId]);

  if (!run) {
    if (missing) return <ThinkingSkeleton />;
    return null;
  }

  const live = run.state === "running" || run.state === "queued";
  const expanded = open ?? live; // auto behaviour until the user toggles
  const hasContent = live || (run.steps.length > 0 && TERMINAL.includes(run.state as (typeof TERMINAL)[number]));
  if (!hasContent) return null;

  const detailsId = `thinking-details-${run.run_id}`;
  const current = run.steps[run.steps.length - 1];
  const tools = usedTools(run);
  const summary = tools.length ? `used ${tools.join(", ")}` : run.steps.length ? `used ${[...new Set(run.steps.map((s) => s.kind))].join(", ")}` : "direct answer";
  const verifyCount = run.steps.filter((s) => s.kind === "verify").length;

  let headline: string;
  if (live) headline = current ? current.label : run.state === "queued" ? "Queued…" : "Thinking…";
  else if (run.state === "failed") headline = `Thought for ${runDuration(run)} · failed`;
  else if (run.state === "cancelled") headline = `Thought for ${runDuration(run)} · stopped`;
  else headline = `Thought for ${runDuration(run)} · ${summary}`;

  const stepStatus = (index: number): "running" | "done" | "failed" => {
    if (run.state === "failed" && index === run.steps.length - 1) return "failed";
    return live && index === run.steps.length - 1 ? "running" : "done";
  };
  const stepDuration = (index: number): string | undefined => {
    const s = run.steps[index];
    if (!s?.ts) return undefined;
    const end = run.steps[index + 1]?.ts ?? (live ? undefined : run.ended_at);
    if (!end) return undefined;
    return fmtSeconds(Math.max(0, end - s.ts));
  };

  return (
    <div className="mb-2 text-[12px]" data-testid={live ? "run-status" : "persisted-thinking"}>
      <button type="button" onClick={() => setOpenAndRemember(!expanded)} aria-expanded={expanded} aria-controls={detailsId}
        data-testid="thinking-toggle"
        className="flex w-full items-center gap-1.5 rounded-md py-0.5 text-left text-muted-foreground transition-colors hover:text-foreground">
        {live ? (
          run.steps.some((s) => s.kind === "research")
            ? <FlaskConical className="h-3.5 w-3.5 shrink-0 animate-pulse text-accent" aria-hidden />
            : <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-500" aria-hidden />
        ) : run.state === "failed" ? (
          <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" aria-hidden />
        )}
        <span className={cn("min-w-0 flex-1 truncate", live && "font-medium text-foreground")}>{headline}</span>
        {live && onCancel && (
          <span role="button" tabIndex={0} data-testid="run-cancel"
            onClick={(e) => { e.stopPropagation(); onCancel(run.run_id); }}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); e.preventDefault(); onCancel(run.run_id); } }}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:border-destructive hover:text-destructive cursor-pointer">
            <CircleStop className="h-3.5 w-3.5" aria-hidden /> Stop
          </span>
        )}
        <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 transition-transform", expanded && "rotate-180")} aria-hidden />
      </button>
      {expanded && (
        <ol id={detailsId} className="mt-1.5 space-y-1 border-l-2 border-sky-500/25 pl-3 text-muted-foreground">
          {run.steps.map((step, index) => (
            <StepRow key={`${step.n ?? index}-${index}`} step={step} status={stepStatus(index)}
              duration={stepDuration(index)}
              errorText={run.state === "failed" && index === run.steps.length - 1 ? run.error || undefined : undefined} />
          ))}
          {verifyCount > 0 && (
            <li className="text-[11px]">{verifyCount} verification pass{verifyCount > 1 ? "es" : ""} done</li>
          )}
          {run.state === "failed" && (
            <li className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive" data-testid="run-failed">
              {run.error || "This run failed."} You can retry — nothing was invented in the meantime.
            </li>
          )}
          {run.state === "cancelled" && <li className="text-[11px]" data-testid="run-cancelled">Stopped by you.</li>}
        </ol>
      )}
    </div>
  );
}


// Banner above a STREAMED partial answer (real backend): tokens are arriving live.
export function StreamedDraftBanner() {
  return (
    <div className="mb-1 inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground" data-testid="draft-banner">
      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      Streaming — the final answer lands when the run completes
    </div>
  );
}

// Banner shown when the backend finished a run whose research never reached COMPLET:
// the answer is rendered, but is explicitly marked unverified (honest, not fabricated).
export function UnverifiedBanner({ detail }: { detail: string }) {
  return (
    <div className="mt-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-foreground" data-testid="exhaustion-banner">
      Not fully verified — {detail || "research did not reach a verified completion."}
    </div>
  );
}

// Exhaustion / unverified state: renders the backend's honesty text verbatim.
export function ExhaustionBanner({ text }: { text: string }) {
  return (
    <div className="mt-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-foreground" data-testid="exhaustion-banner">
      {text}
    </div>
  );
}

/** True when a run's verify steps say the answer was marked unverified (WP2 exhaustion marker). */
export function runUnverified(run: StoreRun | null): string {
  if (!run || run.state !== "done") return "";
  const v = run.steps.find((s) => s.kind === "verify" && /unverified/i.test(s.label));
  return v ? v.label : "";
}
