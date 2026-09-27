import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, Cpu, Download, Loader2, Play, Square, Trash2 } from "lucide-react";
import { getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Model = { name: string; installed: boolean; size: number; loaded: boolean; roles: string[]; residency: string;
  shares_weights_with: string[]; frees: number };
type Voice = { name: string; engine: string; gender: string; installed: boolean; selected: boolean; default: boolean };
type Pkg = { key: string; label: string; installed: boolean; size: number; voices: string[]; selected: boolean; in_use: boolean;
  job?: Job };
type Whisper = { name: string; installed: boolean; size: number; selected: boolean };
type Job = { status: string; done: number; total: number; error?: string };
type State = { llm: { roles: Record<string, string>; models: Model[] }; whisper: Whisper[];
  voices: { voices: Voice[]; files: Pkg[] }; jobs: Record<string, Job> };

const gb = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
const ROLE_HELP: Record<string, string> = { planner: "the main brain", coder: "code questions", translator: "Urdu / Pashto",
  fast: "quick small tasks" };

/** Settings -> Models & voices: pick what ISHTI uses, delete what you don't (frees disk), download again anytime. */
export function ModelsVoices({ isOpen, onToggle }: { isOpen: boolean; onToggle: () => void }) {
  const [st, setSt] = useState<State>();
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const [playing, setPlaying] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(() => getJSON<State>("/api/manage").then(setSt).catch((e) => setMsg(String(e.message || e))), []);
  useEffect(() => { if (isOpen) load(); }, [isOpen, load]);
  const downloading = st && (Object.values(st.jobs).some((j) => !["done", "error"].includes(j.status)));
  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [downloading, load]);

  async function act(label: string, url: string, body: object, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(label); setMsg("");
    try {
      const r = await postJSON<{ ok?: boolean; error?: string }>(url, body);
      if (r?.error) setMsg(r.error);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    } finally {
      setBusy("");
      load();
    }
  }

  function preview(name: string) {
    audio.current?.pause();
    if (playing === name) { setPlaying(""); return; }
    const a = new Audio(`/api/manage/preview?name=${encodeURIComponent(name)}`);
    audio.current = a;
    setPlaying(name);
    a.onended = () => setPlaying("");
    a.play().catch(() => setPlaying(""));
  }

  const jobOf = (key: string) => st?.jobs[key];
  const Progress = ({ job }: { job?: Job }) => !job || job.status === "done" ? null : (
    <span className={cn("text-[10px]", job.status === "error" ? "text-danger" : "text-accent")}>
      {job.status === "error" ? `failed: ${job.error}` : job.total ? `${Math.round((100 * job.done) / job.total)}%` : job.status}
    </span>
  );
  const freeable = st?.llm.models.filter((m) => m.installed && !m.roles.length).reduce((n, m) => n + m.frees, 0) ?? 0;

  return (
    <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden">
      <div className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors" onClick={onToggle}>
        <div className="flex items-center gap-3 min-w-0">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-orange-500/15 text-orange-500"><Cpu className="h-4 w-4" /></div>
          <div className="min-w-0">
            <div className="font-semibold text-foreground text-sm">Models & voices</div>
            <div className="text-xs text-muted-foreground truncate">Choose what ISHTI uses, delete what you don't need, download again anytime.</div>
          </div>
        </div>
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-180 text-accent")} />
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }} className="overflow-hidden border-t border-border/60 bg-background/50">
            <div className="space-y-5 p-4 text-xs">
              {msg && <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-danger">{msg}</div>}
              {!st ? <div className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</div> : (
                <>
                  {/* language models */}
                  <section>
                    <div className="mb-2 flex items-baseline justify-between gap-2">
                      <h3 className="text-sm font-semibold">Language models</h3>
                      {freeable > 0 && <span className="text-[11px] text-muted-foreground">Unused models take {gb(freeable)} you can free</span>}
                    </div>
                    <div className="grid gap-2">
                      {Object.entries(st.llm.roles).map(([role, model]) => (
                        <label key={role} className="flex items-center justify-between gap-2 rounded-lg border border-border/50 bg-card/60 px-3 py-2">
                          <span><span className="font-medium capitalize text-accent">{role}</span>
                            <span className="text-muted-foreground"> · {ROLE_HELP[role] ?? ""}</span></span>
                          <select value={model} disabled={!!busy} aria-label={`Model for ${role}`}
                            onChange={(e) => act(role, "/api/manage/role", { role, model: e.target.value })}
                            className="max-w-[60%] shrink-0 rounded-md border border-border bg-background px-2 py-1 font-mono text-[11px]">
                            {st.llm.models.filter((m) => m.installed && !m.name.includes("embed")).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                          </select>
                        </label>
                      ))}
                    </div>
                    <ul className="mt-2 divide-y divide-border/50 rounded-lg border border-border/50">
                      {st.llm.models.map((m) => (
                        <li key={m.name} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                          <div className="min-w-0">
                            <span className="font-mono font-medium">{m.name}</span>
                            <span className="ml-2 text-muted-foreground">{gb(m.size)}</span>
                            {m.roles.length > 0 && <span className="ml-2 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent">{m.roles.join(", ")}</span>}
                            {m.loaded && <span className="ml-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600">in memory</span>}
                            {m.installed && m.shares_weights_with.length > 0 &&
                              <div className="text-[10px] text-muted-foreground">shares its weights with {m.shares_weights_with.join(", ")} - deleting frees almost nothing</div>}
                          </div>
                          <div className="flex items-center gap-2">
                            <Progress job={jobOf(m.name)} />
                            {m.installed ? (
                              <button type="button" disabled={!!busy || m.roles.length > 0} title={m.roles.length ? "In use - pick another model for that role first" : "Delete (frees disk space)"}
                                onClick={() => act(m.name, "/api/manage/model", { name: m.name, action: "delete" },
                                  `Delete ${m.name}? It frees about ${gb(m.frees)}. You can download it again here later.`)}
                                className="flex items-center gap-1 rounded-md px-2 py-1 text-danger hover:bg-danger/10 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed">
                                <Trash2 className="h-3.5 w-3.5" /> Delete
                              </button>
                            ) : (
                              <button type="button" disabled={!!busy || !!jobOf(m.name)} onClick={() => act(m.name, "/api/manage/model", { name: m.name, action: "download" })}
                                className="flex items-center gap-1 rounded-md px-2 py-1 text-accent hover:bg-accent/10 disabled:opacity-40 cursor-pointer">
                                <Download className="h-3.5 w-3.5" /> Download
                              </button>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>

                  {/* voices */}
                  <section>
                    <h3 className="mb-2 text-sm font-semibold">Voices</h3>
                    <ul className="grid gap-2">
                      {st.voices.voices.map((v) => (
                        <li key={v.name} className={cn("flex items-center justify-between gap-2 rounded-lg border px-3 py-2",
                          v.selected ? "border-accent bg-accent/10" : "border-border/50 bg-card/60", !v.installed && "opacity-50")}>
                          <div className="min-w-0">
                            <div className="font-medium truncate">{v.name}</div>
                            <div className="text-[10px] text-muted-foreground">{v.engine === "kokoro" ? "Kokoro" : "Piper"} · {v.gender}
                              {v.default && " · default"}{!v.installed && " · not downloaded"}</div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <button type="button" disabled={!v.installed} onClick={() => preview(v.name)} aria-label={`Preview ${v.name}`}
                              className="grid h-7 w-7 place-items-center rounded-md hover:bg-muted disabled:opacity-30 cursor-pointer">
                              {playing === v.name ? <Square className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                            </button>
                            {v.installed && !v.selected && (
                              <button type="button" disabled={!!busy} onClick={() => act(v.name, "/api/manage/voice", { name: v.name, default: true })}
                                className="rounded-md bg-accent px-2 py-1 text-[11px] font-medium text-white hover:opacity-90 cursor-pointer">Use</button>
                            )}
                            {v.selected && <span className="text-[11px] font-medium text-accent">In use</span>}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>

                  {/* engine files */}
                  <section>
                    <h3 className="mb-1 text-sm font-semibold">Voice engine files</h3>
                    <p className="mb-2 text-[11px] text-muted-foreground">Kokoro's voices share one model; each Classic (Piper) voice is its own file.</p>
                    <ul className="divide-y divide-border/50 rounded-lg border border-border/50">
                      {st.voices.files.map((f) => (
                        <li key={f.key} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                          <div className="min-w-0">
                            <div className="font-medium">{f.label}</div>
                            <div className="text-[10px] text-muted-foreground">
                              {f.installed ? gb(f.size) : "not downloaded"}{f.voices.length > 1 && ` · ${f.voices.length} voices`}
                              {f.in_use && " · in use"}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <Progress job={f.job} />
                            {f.installed ? (
                              <button type="button" disabled={!!busy || f.in_use} title={f.in_use ? "In use - pick a voice from another engine first" : "Delete (frees disk space)"}
                                onClick={() => act(f.key, "/api/manage/files", { key: f.key, action: "delete" },
                                  `Delete ${f.label}? It frees about ${gb(f.size)}. You can download it again here later.`)}
                                className="flex items-center gap-1 rounded-md px-2 py-1 text-danger hover:bg-danger/10 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed">
                                <Trash2 className="h-3.5 w-3.5" /> Delete
                              </button>
                            ) : (
                              <button type="button" disabled={!!busy || !!(f.job && !["done", "error"].includes(f.job.status))}
                                onClick={() => act(f.key, "/api/manage/files", { key: f.key, action: "download" })}
                                className="flex items-center gap-1 rounded-md px-2 py-1 text-accent hover:bg-accent/10 disabled:opacity-40 cursor-pointer">
                                <Download className="h-3.5 w-3.5" /> Download
                              </button>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>

                  {/* speech recognition */}
                  <section>
                    <h3 className="mb-2 text-sm font-semibold">Speech recognition (Whisper)</h3>
                    <ul className="divide-y divide-border/50 rounded-lg border border-border/50">
                      {st.whisper.map((w) => (
                        <li key={w.name} className="flex items-center justify-between gap-2 px-3 py-2">
                          <span><span className="font-mono font-medium">{w.name}</span>
                            <span className="ml-2 text-muted-foreground">{w.installed ? gb(w.size) : "not downloaded"}</span>
                            {w.selected && <span className="ml-2 text-[11px] font-medium text-accent">In use</span>}</span>
                          <div className="flex items-center gap-2">
                            <Progress job={jobOf(`whisper:${w.name}`)} />
                            {w.installed && !w.selected && (
                              <button type="button" disabled={!!busy} onClick={() => act(w.name, "/api/manage/whisper", { name: w.name, action: "select" })}
                                className="rounded-md bg-accent px-2 py-1 text-[11px] font-medium text-white cursor-pointer">Use</button>
                            )}
                            {w.installed ? (
                              <button type="button" disabled={!!busy || w.selected} onClick={() => act(w.name, "/api/manage/whisper", { name: w.name, action: "delete" },
                                `Delete Whisper ${w.name}? It frees ${gb(w.size)}.`)}
                                className="rounded-md p-1 text-danger hover:bg-danger/10 disabled:opacity-30 cursor-pointer" aria-label={`Delete Whisper ${w.name}`}>
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            ) : (
                              <button type="button" disabled={!!busy} onClick={() => act(w.name, "/api/manage/whisper", { name: w.name, action: "download" })}
                                className="flex items-center gap-1 rounded-md px-2 py-1 text-accent hover:bg-accent/10 cursor-pointer">
                                <Download className="h-3.5 w-3.5" /> Download
                              </button>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
