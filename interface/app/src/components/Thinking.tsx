import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, ChevronDown, CircleDashed, Loader2, PauseCircle, XCircle } from "lucide-react";
import { useState } from "react";
import type { TraceEvent } from "@/lib/api";
import StepPlayer from "@/components/rare/step-player";

const ICON = {
  start: <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />,
  done: <CheckCircle2 className="h-3.5 w-3.5 text-success" />,
  error: <XCircle className="h-3.5 w-3.5 text-danger" />,
  waiting: <PauseCircle className="h-3.5 w-3.5 text-warning" />,
  skip: <CircleDashed className="h-3.5 w-3.5 text-muted-foreground" />,
};

type Row = { key: string; status: keyof typeof ICON; text: string; indent: boolean };

function rows(events: TraceEvent[]): Row[] {
  const out: Row[] = [];
  for (const e of events) {
    if (e.type === "step") {
      const i = [...out].reverse().findIndex((r) => !r.indent && r.text.startsWith(`Using ${e.skill}`));
      if (e.status === "start") out.push({ key: `s${out.length}`, status: "start", text: `Using ${e.skill}`, indent: false });
      else if (i >= 0) out[out.length - 1 - i].status = (e.status as Row["status"]) ?? "done";
    } else if (e.type === "escalate") {
      out.push({ key: `e${out.length}`, status: "skip", text: String(e.why ?? "Trying another way"), indent: false });
    } else if (e.type === "tool") {
      const open = [...out].reverse().find((r) => r.indent && r.status === "start" && r.key.includes(`:${e.name}:`));
      if (open) { open.status = (e.status as Row["status"]) ?? "done"; open.text = e.label ?? open.text; }
      else out.push({ key: `t:${e.name}:${out.length}`, status: (e.status as Row["status"]) ?? "done",
                      text: e.label ?? String(e.name), indent: true });
    }
  }
  return out;
}

export function Thinking({ events, live }: { events: TraceEvent[]; live: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? live; // follows the run until the owner toggles it
  const route = events.find((e) => e.type === "route");
  const done = events.find((e) => e.type === "done");
  const steps = route?.steps ?? [];
  const startedSteps = events.filter((e) => e.type === "step" && e.status === "start").length;
  const list = rows(events);

  return (
    <div className="mb-2 text-[13px]">
      <button onClick={() => setOpen(!expanded)}
        className="flex items-center gap-1.5 rounded-md py-0.5 text-muted-foreground transition-colors hover:text-foreground">
        {live ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" /> : <CheckCircle2 className="h-3.5 w-3.5 text-success" />}
        <span>{live ? (route ? `Working · ${route.skill}` : "Thinking…")
                    : `Thought for ${(done?.ms ?? 0) < 1000 ? `${done?.ms ?? 0} ms` : `${((done?.ms ?? 0) / 1000).toFixed(1)} s`} · ${done?.skill ?? route?.skill ?? ""}`}</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`} />
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.25, ease: "easeOut" }} className="overflow-hidden">
            <div className="mt-1.5 space-y-2 border-l-2 border-accent-soft pl-3">
              {route?.why && <p className="text-muted-foreground break-words">{route.why}</p>}
              {route?.scores && route.scores.length > 0 && (
                <div className="space-y-1">
                  {route.scores.map(([name, v]) => (
                    <div key={name} className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span className="w-20 truncate">{name}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                        <motion.div className="h-full rounded-full bg-accent" initial={{ width: 0 }}
                          animate={{ width: `${Math.round(v * 100)}%` }} transition={{ duration: 0.4, ease: "easeOut" }} />
                      </div>
                      <span className="w-8 text-right tabular-nums">{Math.round(v * 100)}%</span>
                    </div>
                  ))}
                </div>
              )}
              {steps.length > 1 && (
                <div className="space-y-1">
                  <StepPlayer steps={steps.map((s) => ({ label: s.skill }))} value={Math.max(0, startedSteps - 1)}
                    playing={false} showControl={false} size={14} />
                  <ol className="list-decimal pl-4 text-muted-foreground break-words">
                    {steps.map((s, i) => <li key={i} className="break-words"><b className="text-foreground">{s.skill}</b>: {s.request}</li>)}
                  </ol>
                </div>
              )}
              <ul className="space-y-1">
                <AnimatePresence initial={false}>
                  {list.map((r) => (
                    <motion.li key={r.key} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.18 }} className={`flex items-start gap-1.5 ${r.indent ? "pl-4" : ""}`}>
                      <span className="mt-0.5 shrink-0">{ICON[r.status]}</span>
                      <span className={r.indent ? "min-w-0 flex-1 break-words text-muted-foreground" : "min-w-0 flex-1 break-words font-medium"}>{r.text}</span>
                    </motion.li>
                  ))}
                </AnimatePresence>
              </ul>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
