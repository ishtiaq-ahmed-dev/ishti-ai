import { AnimatePresence, motion } from "motion/react";
import { AgentOrb, type OrbState } from "@/components/AgentOrb";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking";
export type Live = { text: string; step: string; done: boolean; voice?: VoiceState } | null;

// three clearly different looks: the orb's own listening / solving / composing animations
const ORB: Record<VoiceState, OrbState> = { idle: "breathing", listening: "listening", thinking: "solving", speaking: "composing" };
const LABEL: Record<VoiceState, string> = { idle: "", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" };

/** A spoken request, live: what ISHTI hears (partial transcript), what it is doing, and whether it is listening,
 *  thinking or speaking. Typed chats narrate in their own Thinking panel. */
export function LiveNarration({ live }: { live: Live }) {
  const voice = live?.voice ?? (live && !live.done ? "thinking" : "idle");
  return (
    <AnimatePresence>
      {live && (
        <motion.div role="status" aria-live="polite" initial={{ opacity: 0, y: -12, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -12 }} transition={{ duration: 0.2 }}
          className="pointer-events-none fixed top-5 left-1/2 z-50 flex w-[min(92vw,620px)] -translate-x-1/2 items-center gap-5 rounded-3xl border border-border bg-background/95 px-6 py-5 shadow-2xl backdrop-blur">
          <AgentOrb state={ORB[voice]} size={112} className="shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.18em] text-accent">
              {LABEL[voice]}
              {voice === "listening" && (
                <span className="flex h-4 items-end gap-0.5" aria-hidden>
                  {[0, 1, 2, 3].map((i) => (
                    <motion.span key={i} className="w-1 rounded-full bg-accent" animate={{ height: ["30%", "100%", "40%"] }}
                      transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.12 }} />
                  ))}
                </span>
              )}
            </div>
            {live.text && <div className="mt-1 line-clamp-2 text-base text-muted-foreground">“{live.text}”</div>}
            <AnimatePresence mode="wait">
              <motion.div key={live.step} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }} className="mt-1 truncate text-base font-medium">{live.step}</motion.div>
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
