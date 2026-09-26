import { motion } from "motion/react";
import type { TraceEvent } from "@/lib/api";
import Folder from "@/components/rare/folder-component";
import { AnimatedCounter } from "@/components/rare/animated-counter";

/** Shown when the folders skill opens a folder: the folder, then its file types fanning out with counts. */
export function FolderScan({ scan, plan }: { scan: TraceEvent; plan?: TraceEvent }) {
  const kinds = Object.entries((plan?.groups ?? scan.kinds ?? {}) as Record<string, number>).sort((a, b) => b[1] - a[1]);
  const sample = (scan.sample as string[] | undefined) ?? [];
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
      className="my-2 flex items-center gap-4 rounded-2xl border border-border bg-card p-3">
      <div className="shrink-0" style={{ width: 104, height: 88 }}>
        <div style={{ transform: "scale(0.32)", transformOrigin: "top left" }}><Folder color="blue" size="sm" /></div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex items-baseline gap-2">
          <span className="truncate text-sm font-semibold">{String(scan.folder ?? "")}</span>
          <span className="text-xs text-muted-foreground">
            <AnimatedCounter value={Number(scan.count ?? 0)} /> files{plan ? " · planned moves" : ""}
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {kinds.map(([k, v], i) => (
            <motion.span key={k} initial={{ opacity: 0, scale: 0.8, x: -12 }} animate={{ opacity: 1, scale: 1, x: 0 }}
              transition={{ delay: 0.05 * i, type: "spring", stiffness: 420, damping: 26 }}
              className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
              {k} · <AnimatedCounter value={v} />
            </motion.span>
          ))}
        </div>
        {!plan && sample.length > 0 && (
          <div className="mt-1.5 truncate text-[11px] text-muted-foreground">{sample.slice(0, 4).join("  ·  ")}</div>
        )}
      </div>
    </motion.div>
  );
}
