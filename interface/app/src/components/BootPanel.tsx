import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, Check, ChevronDown, ChevronUp, Circle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { type Boot, SERVICE, stageStatus } from "@/lib/boot";

/** Floating startup checklist: shows each service as pending / loading / ready while ISHTI finishes loading in the
 *  background, so the dashboard is usable from the first second. Fades out a moment after everything is ready. */
export function BootPanel({ boot }: { boot: Boot | null }) {
  const [open, setOpen] = useState(true);
  const [lingering, setLingering] = useState(true);

  useEffect(() => {
    if (boot?.ready) {
      const t = setTimeout(() => setLingering(false), 2500);
      return () => clearTimeout(t);
    }
    setLingering(true);
  }, [boot?.ready]);

  if (!boot) return null;
  const show = !boot.ready || lingering;
  const rows = boot.stages.filter((s) => s.id !== "ready");
  const failed = rows.find((s) => stageStatus(s) === "error");

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          transition={{ duration: 0.2 }}
          role="status"
          aria-live="polite"
          data-testid="boot-panel"
          className="fixed bottom-24 right-5 z-50 w-[300px] rounded-2xl border border-border bg-card/95 p-3 shadow-xl backdrop-blur-md"
        >
          <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between gap-2 text-left cursor-pointer">
            <span className="text-xs font-semibold">
              {boot.ready ? "ISHTI is ready" : boot.unreachable ? "Waiting for the ISHTI server…" : failed ? "Something needs attention" : "ISHTI is starting up"}
            </span>
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              {boot.ready ? "100%" : `${boot.progress}%`}
              {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
            </span>
          </button>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${boot.ready ? 100 : boot.progress}%` }} />
          </div>
          {open && (
            <ul className="mt-2.5 space-y-1.5">
              {rows.map((s) => {
                const st = stageStatus(s);
                const svc = SERVICE[s.id] ?? { name: s.label, hint: "" };
                return (
                  <li key={s.id} className="flex items-start gap-2 text-xs" title={svc.hint}>
                    <span className="mt-0.5 shrink-0">
                      {st === "done" ? <Check className="h-3.5 w-3.5 text-emerald-500" /> :
                        st === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" /> :
                        st === "error" ? <AlertTriangle className="h-3.5 w-3.5 text-amber-500" /> :
                        <Circle className="h-3.5 w-3.5 text-muted-foreground/50" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className={st === "pending" ? "text-muted-foreground" : "font-medium"}>{svc.name}</span>
                      <span className="ml-1.5 text-[10.5px] text-muted-foreground">
                        {st === "done" ? "ready" : st === "loading" ? "loading" : st === "error" ? "retrying" : "waiting"}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          {open && !boot.ready && (
            <p className="mt-2.5 text-[10.5px] leading-snug text-muted-foreground">
              You can look around now. A message you send is queued and answered as soon as the main model is loaded.
            </p>
          )}
          {boot.error && !boot.ready && (
            <div className="mt-2 rounded-lg bg-amber-500/10 p-2 text-[10.5px] leading-snug text-amber-700 dark:text-amber-300">
              {boot.error}
              <button type="button" className="ml-2 underline cursor-pointer" onClick={() => fetch("/api/ready/retry", { method: "POST" }).catch(() => {})}>
                Try again
              </button>
            </div>
          )}
          {boot.warnings?.length > 0 && boot.ready && <p className="mt-2 text-[10.5px] text-amber-600 dark:text-amber-300">{boot.warnings[0]}</p>}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
