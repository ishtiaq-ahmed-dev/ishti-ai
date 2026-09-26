import { motion } from "motion/react";
import { Folder as FolderIcon } from "lucide-react";
import type { TraceEvent } from "@/lib/api";
import Folder from "@/components/rare/folder-component";
import { AnimatedCounter } from "@/components/rare/animated-counter";

/** Shown when the folders skill opens a folder: the folder, then its file types fanning out with counts. */
export function FolderScan({ scan, plan }: { scan: TraceEvent; plan?: TraceEvent }) {
  const kinds = Object.entries((plan?.groups ?? scan.kinds ?? {}) as Record<string, number>).sort((a, b) => b[1] - a[1]);
  const sample = (scan.sample as string[] | undefined) ?? [];
  const folderPath = String(scan.folder ?? "");
  const folderName = folderPath.split(/[\\/]/).filter(Boolean).pop() || folderPath || "Folder";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="my-3 flex flex-col sm:flex-row items-stretch sm:items-center gap-3.5 rounded-2xl border border-border bg-card/75 p-3 max-w-full min-w-0 overflow-hidden shadow-xs backdrop-blur-xs"
    >
      {/* 3D Folder Preview Showcase */}
      <div
        className="relative shrink-0 flex items-center justify-center rounded-xl bg-gradient-to-b from-accent-soft/30 to-accent-soft/10 border border-border/50 overflow-hidden self-center sm:self-auto"
        style={{ width: 104, height: 86 }}
      >
        <div
          style={{
            width: 209,
            height: 176,
            transform: "scale(0.40)",
            transformOrigin: "center center",
          }}
          className="flex items-center justify-center shrink-0 select-none"
        >
          <Folder color="blue" size="sm" allowOpen={false} />
        </div>
      </div>

      {/* Details: path, file count, type badges, file sample */}
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center justify-between gap-2 min-w-0">
          <div className="flex items-center gap-1.5 min-w-0 flex-1" title={folderPath}>
            <FolderIcon className="h-4 w-4 shrink-0 text-accent" />
            <span className="truncate text-sm font-semibold text-foreground">
              {folderName}
            </span>
            {folderPath !== folderName && (
              <span className="hidden md:inline truncate text-[11px] text-muted-foreground">
                ({folderPath})
              </span>
            )}
          </div>
          <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground tabular-nums border border-border/40">
            <AnimatedCounter value={Number(scan.count ?? 0)} /> files{plan ? " · planned" : ""}
          </span>
        </div>

        {kinds.length > 0 && (
          <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto scroll-thin py-0.5">
            {kinds.map(([k, v], i) => (
              <motion.span
                key={k}
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.03 * i }}
                className="shrink-0 rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-accent border border-accent/20"
              >
                {k} · <AnimatedCounter value={v} />
              </motion.span>
            ))}
          </div>
        )}

        {!plan && sample.length > 0 && (
          <div
            className="truncate text-[11px] text-muted-foreground min-w-0 font-mono"
            title={sample.slice(0, 5).join(" · ")}
          >
            {sample.slice(0, 5).join(" · ")}
          </div>
        )}
      </div>
    </motion.div>
  );
}

export default FolderScan;
