import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, ChevronUp, Cpu, Database, Layers, Loader2 } from "lucide-react";
import { getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type ArchBackend = { id: string; description: string; selected: boolean };
type ArchState = {
  active: string;
  backends: ArchBackend[];
  switched_at: number;
  switched_by: string;
  memory?: {
    memories?: Record<string, number>;
    entities?: number;
    db?: string;
    thresholds?: Record<string, number | string>;
    error?: string;
  };
  models?: { main?: string; validation?: string };
  tool_rounds?: number;
};

const LABELS: Record<string, string> = {
  arch1: "ARCH 1 · Dispatcher",
  arch2: "ARCH 2 · Two-pass brain",
};

function MemoryStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-border/70 bg-muted/30 px-3 py-2">
      <div className="text-sm font-semibold tabular-nums">{value}</div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
    </div>
  );
}

/** Settings -> Architecture: click to choose which brain answers (arch1 dispatcher / arch2
 *  two-pass Qwen3 pipeline) and watch the four-layer memory store fill up. */
export function ArchitectureCard() {
  const [state, setState] = useState<ArchState | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    getJSON<ArchState>("/api/arch")
      .then((d) => {
        setState(d);
        setError("");
      })
      .catch((e) => setError(String(e?.message ?? e)));
  }, []);

  useEffect(() => {
    load();
    const handleArchEvent = (e: Event) => {
      const custom = e as CustomEvent<ArchState>;
      if (custom.detail) {
        setState((s) => (s ? { ...s, ...custom.detail } : custom.detail));
      } else {
        load();
      }
    };
    window.addEventListener("ishti-arch-state", handleArchEvent);
    const t = setInterval(load, 20000); // memory stats refresh quietly
    return () => {
      window.removeEventListener("ishti-arch-state", handleArchEvent);
      clearInterval(t);
    };
  }, [load]);

  const switchTo = useCallback(
    (backend: string) => {
      setBusy(backend);
      postJSON<ArchState>("/api/arch/switch", { backend })
        .then((d) => {
          setState((s) => (s ? { ...s, ...d } : d));
          setError("");
        })
        .catch((e) => setError(String(e?.message ?? e)))
        .finally(() => {
          setBusy(null);
          load();
        });
    },
    [load]
  );

  const runJob = useCallback(
    (job: "promote" | "decay") => {
      setBusy(job);
      postJSON(`/api/arch/memory/${job}`)
        .then(load)
        .catch((e) => setError(String(e?.message ?? e)))
        .finally(() => setBusy(null));
    },
    [load]
  );

  if (!state) return null;
  const mem = state.memory ?? {};
  const counts = mem.memories ?? {};
  const total =
    Object.values(counts).reduce<number>((a, b) => a + Number(b ?? 0), 0) + Number(mem.entities ?? 0);

  return (
    <div className="rounded-2xl border border-border bg-card/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left cursor-pointer"
      >
        <div className="flex items-center gap-2.5">
          <Layers className="h-4 w-4 text-accent" />
          <div>
            <div className="text-sm font-semibold">Architecture</div>
            <div className="text-xs text-muted-foreground">
              Active: {LABELS[state.active] ?? state.active}
              {state.models?.main ? ` · main model ${state.models.main}` : ""}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
            {total} memory items
          </span>
          {open ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </div>
      </button>

      {open && (
        <div className="space-y-4 border-t border-border/70 px-4 py-4">
          <div className="grid gap-3 sm:grid-cols-2">
            {state.backends.map((b) => (
              <button
                key={b.id}
                type="button"
                disabled={busy !== null}
                onClick={() => switchTo(b.id)}
                className={cn(
                  "rounded-2xl border p-3.5 text-left transition-all cursor-pointer disabled:opacity-60",
                  b.selected
                    ? "border-accent bg-accent-soft/60 shadow-xs"
                    : "border-border bg-background hover:border-accent/60 hover:bg-muted/30"
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-sm font-semibold">
                    {b.selected && <Check className="h-3.5 w-3.5 text-accent" />}
                    {LABELS[b.id] ?? b.id}
                  </span>
                  {busy === b.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
                  ) : b.selected ? (
                    <span className="rounded-md bg-accent px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white">
                      Active
                    </span>
                  ) : null}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{b.description}</p>
              </button>
            ))}
          </div>

          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Switching takes effect on your next message — nothing restarts. ARCH2 keeps the same tools,
            safety gates and history; its router reuses ARCH1's rules + Laya, then Qwen3-14B thinks, calls
            tools, a validation pass checks them, and Qwen3-14B writes the final answer.
            {state.tool_rounds ? ` Up to ${state.tool_rounds} tool rounds per request.` : ""}
          </p>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Database className="h-3.5 w-3.5 text-accent" />
                Four-layer memory store
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => runJob("promote")}
                  className="rounded-lg border border-border bg-background px-2.5 py-1 text-[11px] font-medium text-muted-foreground hover:text-accent hover:border-accent transition-colors cursor-pointer disabled:opacity-60"
                  title="Promote repeated observations into stable memories (runs automatically too)"
                >
                  Promote now
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => runJob("decay")}
                  className="rounded-lg border border-border bg-background px-2.5 py-1 text-[11px] font-medium text-muted-foreground hover:text-accent hover:border-accent transition-colors cursor-pointer disabled:opacity-60"
                  title="Flag stale low-importance memories as needs-confirmation (never deletes)"
                >
                  Decay check
                </button>
              </div>
            </div>
            {mem.error ? (
              <div className="rounded-xl border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-500">
                {mem.error}
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                <MemoryStat label="Semantic" value={counts.semantic ?? 0} />
                <MemoryStat label="Episodic" value={counts.episodic ?? 0} />
                <MemoryStat label="Procedures" value={counts.procedural ?? 0} />
                <MemoryStat label="Observations" value={counts.observation ?? 0} />
                <MemoryStat label="People" value={mem.entities ?? 0} />
                <MemoryStat label="Total" value={total} />
              </div>
            )}
          </div>

          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Cpu className="h-3 w-3" />
            {state.models?.main
              ? `ARCH2 models: main ${state.models.main}, validation ${state.models.validation ?? state.models.main}.`
              : "ARCH2 model names come from ishti/config.json (arch.arch2.models)."}
            {mem.thresholds && typeof mem.thresholds.temporary === "number" && (
              <> Observations promote past {String(mem.thresholds.temporary)} repeats (configurable).</>
            )}
          </div>
          {error && <div className="text-xs text-red-500">{error}</div>}
        </div>
      )}
    </div>
  );
}
