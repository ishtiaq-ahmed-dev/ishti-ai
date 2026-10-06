// WP3 research progress UX: while run_step kind=research events arrive the run shows a
// "Researching..." card (step n, current action, sources, elapsed, cancel). Partial text is
// explicitly marked as working draft; final bubble only on run_finished; exhaustion states show
// backend text verbatim (what was tried, what is missing) - never a fabricated answer.
import { useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, CircleStop, ExternalLink, FlaskConical, Loader2 } from "lucide-react";
import type { StoreRun } from "@/lib/chat-store";

type Props = {
  run: StoreRun;
  onCancel: (runId: string) => void;
};

function runDuration(run: StoreRun): string {
  const start = run.started_at || run.steps.find((step) => step.ts)?.ts || null;
  const end = run.ended_at || [...run.steps].reverse().find((step) => step.ts)?.ts || null;
  const seconds = start && end ? Math.max(0, end - start) : 0;
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`;
}

function usedSkills(run: StoreRun): string[] {
  const names: string[] = [];
  for (const step of run.steps) {
    const match = step.label.match(/^Using\s+([^:·—]+)/i);
    if (match?.[1]) names.push(match[1].trim());
  }
  return [...new Set(names)].filter(Boolean);
}

/** Collapsed, replay-safe trace shown with a final assistant message. */
export function RunThoughts({ run }: { run: StoreRun }) {
  const [open, setOpen] = useState(false);
  if (!run.steps.length || !["done", "failed", "cancelled"].includes(run.state)) return null;
  const skills = usedSkills(run);
  const summary = skills.length ? `used ${skills.join(", ")}` : `used ${[...new Set(run.steps.map((s) => s.kind))].join(", ")}`;
  return (
    <div className="mb-2 text-[12px]" data-testid="persisted-thinking">
      <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md py-0.5 text-muted-foreground transition-colors hover:text-foreground">
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
        <span>Thought for {runDuration(run)} · {summary}</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && (
        <ol className="mt-1.5 space-y-1 border-l-2 border-emerald-500/25 pl-3 text-muted-foreground">
          {run.steps.map((step, index) => (
            <li key={`${step.n}-${index}`} className="flex items-start gap-1.5">
              <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" aria-hidden />
              <span className="break-words">{step.label}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function RunStatusCard({ run, onCancel }: Props) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const verifySteps = run.steps.filter((s) => s.kind === "verify");
  const current = run.steps[run.steps.length - 1];
  const researching = run.steps.some((s) => s.kind === "research");
  const live = run.state === "running" || run.state === "queued";
  const headline = current ? current.label : run.state === "queued" ? "Queued…" : "Thinking…";

  return (
    <div className="rounded-xl border border-border/70 bg-card/60 p-3 text-sm" data-testid="run-status">
      <div className="flex items-center gap-2">
        {researching
          ? <FlaskConical className="h-4 w-4 text-accent" aria-hidden />
          : <Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden />}
        <span className="min-w-0 truncate font-medium text-foreground">{headline}</span>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
        </span>
        {live && (
          <button
            onClick={() => onCancel(run.run_id)}
            data-testid="run-cancel"
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-destructive hover:text-destructive"
          >
            <CircleStop className="h-3.5 w-3.5" aria-hidden /> Stop
          </button>
        )}
      </div>

      {run.steps.length > 0 && (
        <ol className="mt-2 space-y-1 border-l-2 border-sky-500/30 pl-3 text-xs text-muted-foreground">
          {run.steps.map((s, i) => (
            <li key={i} className="flex items-center gap-1.5">
              {live && i === run.steps.length - 1
                ? <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-500" aria-hidden />
                : <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />}
              <span className="min-w-0 break-words">{s.label}</span>
              {s.sources.slice(0, 3).map((u) => (
                <a key={u} href={u} target="_blank" rel="noreferrer"
                   className="inline-flex max-w-[180px] items-center gap-0.5 truncate text-accent hover:underline">
                  <ExternalLink className="h-3 w-3" aria-hidden />
                  {u.replace(/^https?:\/\//, "").split("/")[0]}
                </a>
              ))}
            </li>
          ))}
        </ol>
      )}

      {verifySteps.length > 0 && (
        <div className="mt-1.5 text-xs text-muted-foreground">
          {verifySteps.length} verification pass{verifySteps.length > 1 ? "es" : ""} done
        </div>
      )}

      {run.state === "failed" && (
        <div className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive" data-testid="run-failed">
          {run.error || "This run failed."} You can retry — nothing was invented in the meantime.
        </div>
      )}
      {run.state === "cancelled" && (
        <div className="mt-2 text-xs text-muted-foreground" data-testid="run-cancelled">Stopped by you.</div>
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
