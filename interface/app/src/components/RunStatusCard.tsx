// WP3 research progress UX: while run_step kind=research events arrive the run shows a
// "Researching..." card (step n, current action, sources, elapsed, cancel). Partial text is
// explicitly marked as working draft; final bubble only on run_finished; exhaustion states show
// backend text verbatim (what was tried, what is missing) - never a fabricated answer.
import { useEffect, useState } from "react";
import { CircleStop, ExternalLink, FlaskConical, Loader2 } from "lucide-react";
import type { StoreRun } from "@/lib/chat-store";

type Props = {
  run: StoreRun;
  onCancel: (runId: string) => void;
};

export function RunStatusCard({ run, onCancel }: Props) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const researchSteps = run.steps.filter((s) => s.kind === "research");
  const verifySteps = run.steps.filter((s) => s.kind === "verify");
  const current = run.steps[run.steps.length - 1];
  const researching = run.steps.some((s) => s.kind === "research");
  const live = run.state === "running" || run.state === "queued";

  return (
    <div className="rounded-xl border border-border/70 bg-card/60 p-3 text-sm" data-testid="run-status">
      <div className="flex items-center gap-2">
        {researching
          ? <FlaskConical className="h-4 w-4 text-accent" aria-hidden />
          : <Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden />}
        <span className="font-medium text-foreground">
          {researching ? "Researching…" : run.state === "queued" ? "Queued…" : "Working…"}
        </span>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} elapsed
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

      {current && (
        <div className="mt-2 flex items-baseline gap-2 text-xs text-muted-foreground">
          <span className="rounded bg-muted px-1.5 py-0.5 font-mono">{current.kind}</span>
          <span>{current.label}</span>
        </div>
      )}

      {researchSteps.length > 0 && (
        <ol className="mt-2 space-y-1 text-xs text-muted-foreground">
          {run.steps.map((s, i) => (
            <li key={i} className="flex items-center gap-1.5">
              <span className="font-mono text-[10px] opacity-70">{i + 1}.</span>
              <span>{s.label}</span>
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
