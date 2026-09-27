import { useState, useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { AgentOrb, type OrbState } from "@/components/AgentOrb";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking";
export type Live = { text: string; step: string; done: boolean; voice?: VoiceState } | null;

const ORB: Record<VoiceState, OrbState> = { idle: "breathing", listening: "listening", thinking: "solving", speaking: "composing" };
const LABEL: Record<VoiceState, string> = { idle: "", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" };

/** A spoken request, live: what ISHTI hears (partial transcript), what it is doing, and whether it is listening,
 *  thinking or speaking. Automatically disappears as soon as response or chat is over. */
export function LiveNarration({ live, onDismiss }: { live: Live; onDismiss?: () => void }) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (live && !live.done && live.voice !== "idle") {
      setDismissed(false);
    }
  }, [live?.text, live?.step, live?.voice, live?.done]);

  const voice = live?.voice ?? (live && !live.done ? "thinking" : "idle");
  // Strictly hide if null, marked done, idle, or manually dismissed
  const isVisible = Boolean(live && !live.done && voice !== "idle" && !dismissed);

  return (
    <AnimatePresence>
      {isVisible && live && (
        <motion.div
          role="status"
          aria-live="polite"
          initial={{ opacity: 0, y: -12, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -12, scale: 0.97 }}
          transition={{ duration: 0.2 }}
          className="fixed top-5 left-1/2 z-50 flex w-[min(92vw,620px)] -translate-x-1/2 items-center gap-5 rounded-3xl border border-border/80 bg-background/98 px-6 py-5 shadow-2xl"
        >
          <AgentOrb state={ORB[voice]} size={100} className="shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.18em] text-accent">
                {LABEL[voice]}
                {voice === "listening" && (
                  <span className="flex h-4 items-end gap-0.5" aria-hidden>
                    {[0, 1, 2, 3].map((i) => (
                      <motion.span
                        key={i}
                        className="w-1 rounded-full bg-accent"
                        animate={{ height: ["30%", "100%", "40%"] }}
                        transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.12 }}
                      />
                    ))}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setDismissed(true);
                  onDismiss?.();
                }}
                className="rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
                title="Dismiss"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            {live.text && <div className="mt-1 line-clamp-2 text-sm font-medium text-muted-foreground">“{live.text}”</div>}
            <AnimatePresence mode="wait">
              {live.step && (
                <motion.div
                  key={live.step}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.15 }}
                  className="mt-1.5 truncate text-sm font-semibold text-foreground"
                >
                  {live.step}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
