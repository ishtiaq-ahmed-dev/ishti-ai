// WP2 feedback UI: per-message thumbs up/down + reason chips + optional comment + correction
// ("what should it have said"). Bound to message_id + run_id from the store (never the wrong
// version - regenerate produces a new run_id). State machine: idle -> sending -> recorded, with
// idempotent resend (same message_id+run_id+rating is deduped by the backend) and visible errors.
import { useState } from "react";
import { Check, RotateCcw, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

const REASONS = ["Wrong facts", "Wrong source", "Missed the point", "Bad format", "Too long", "Unsafe"];

type Props = {
  messageId: string | undefined;
  runId: string | undefined;
};

type Phase = "idle" | "sending" | "recorded" | "error";

export function FeedbackPanel({ messageId, runId }: Props) {
  const [choice, setChoice] = useState<-1 | 1 | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [reasons, setReasons] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [correction, setCorrection] = useState("");
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);

  if (!messageId) return null;

  const submit = async (rating: -1 | 1) => {
    setChoice(rating);
    setPhase("sending");
    setError("");
    try {
      await postJSON("/api/feedback", {
        message_id: messageId, run_id: runId || "", rating,
        reasons, comment: comment.trim(), correction: correction.trim(),
      });
      setPhase("recorded");
    } catch (e) {
      // offline queue: the rating stays selected; resending hits the idempotent endpoint
      setPhase("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const resubmit = () => void submit(choice === 1 ? 1 : -1);

  return (
    <div className="mt-1.5" data-testid={`feedback-${messageId}`}>
      <div className="flex items-center gap-1">
        <button
          aria-label="Good answer"
          aria-pressed={choice === 1}
          data-testid="thumb-up"
          disabled={phase === "sending"}
          onClick={() => (choice === 1 && phase === "recorded" ? setPhase("idle") : void submit(1))}
          className={cn("rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-accent",
            choice === 1 && "bg-accent/15 text-accent")}
        >
          {choice === 1 && phase === "recorded" ? <Check className="h-3.5 w-3.5" /> : <ThumbsUp className="h-3.5 w-3.5" />}
        </button>
        <button
          aria-label="Bad answer"
          aria-pressed={choice === -1}
          data-testid="thumb-down"
          disabled={phase === "sending"}
          onClick={() => { setExpanded(true); void submit(-1); }}
          className={cn("rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-accent",
            choice === -1 && "bg-accent/15 text-accent")}
        >
          {choice === -1 && phase === "recorded" ? <Check className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />}
        </button>
        {phase === "sending" && <span className="text-[11px] text-muted-foreground" role="status">Recording…</span>}
        {phase === "recorded" && <span className="text-[11px] text-muted-foreground" role="status">Recorded — this shapes future answers.</span>}
        {phase === "error" && (
          <span className="flex items-center gap-1 text-[11px] text-destructive" role="alert">
            {error || "Not saved yet."}
            <button onClick={resubmit} className="inline-flex items-center gap-0.5 underline" data-testid="feedback-retry">
              <RotateCcw className="h-3 w-3" /> Retry
            </button>
          </span>
        )}
      </div>

      {expanded && choice === -1 && phase !== "recorded" && (
        <div className="mt-2 rounded-lg border border-border bg-card/70 p-2.5" data-testid="feedback-details">
          <div className="flex flex-wrap gap-1.5">
            {REASONS.map((r) => (
              <button
                key={r}
                aria-pressed={reasons.includes(r)}
                onClick={() => setReasons((cur) => cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r])}
                className={cn("rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors",
                  reasons.includes(r) && "border-accent bg-accent/10 text-accent")}
              >
                {r}
              </button>
            ))}
          </div>
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Anything else? (optional)"
            className="mt-2 w-full rounded-md border border-border bg-background px-2 py-1 text-xs outline-none focus:border-accent"
          />
          <textarea
            value={correction}
            onChange={(e) => setCorrection(e.target.value)}
            placeholder="What should it have said?"
            rows={2}
            data-testid="feedback-correction"
            className="mt-1.5 w-full resize-y rounded-md border border-border bg-background px-2 py-1 text-xs outline-none focus:border-accent"
          />
          <div className="mt-1.5 flex items-center justify-between">
            <button onClick={() => setExpanded(false)} className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground">
              <X className="h-3 w-3" /> Close
            </button>
            <button
              onClick={resubmit}
              disabled={phase === "sending"}
              data-testid="feedback-save"
              className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground disabled:opacity-50"
            >
              {phase === "sending" ? "Saving…" : "Send feedback"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
