import { AnimatePresence, motion } from "motion/react";
import { Mic } from "lucide-react";

export type Live = { text: string; step: string; done: boolean } | null;

/** What ISHTI is doing right now for a voice/wake-word request (typed chats narrate in their own Thinking panel). */
export function LiveNarration({ live }: { live: Live }) {
  return (
    <AnimatePresence>
      {live && (
        <motion.div role="status" aria-live="polite" initial={{ opacity: 0, y: -12, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -12 }} transition={{ duration: 0.2 }}
          className="pointer-events-none fixed top-5 left-1/2 z-50 flex max-w-[min(92vw,560px)] -translate-x-1/2 items-center gap-3 rounded-2xl border border-border bg-background/95 px-4 py-2.5 shadow-lg backdrop-blur">
          <span className="relative grid h-7 w-7 shrink-0 place-items-center rounded-full bg-accent-soft text-accent">
            {!live.done && <span className="absolute inset-0 animate-ping rounded-full bg-accent/25" />}
            <Mic className="relative h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="truncate text-xs text-muted-foreground">“{live.text}”</div>
            <AnimatePresence mode="wait">
              <motion.div key={live.step} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }} className="truncate text-sm font-medium">{live.step}</motion.div>
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
