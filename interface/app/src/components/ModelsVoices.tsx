import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ChevronDown,
  Cpu,
  Download,
  Loader2,
  Play,
  Square,
  Trash2,
  Sparkles,
  Code2,
  Zap,
  Languages,
  HardDrive,
  Volume2,
  Mic,
  Check,
  CheckCircle2,
  Radio,
  FileAudio,
  UserRound,
} from "lucide-react";
import { del, getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Model = {
  name: string;
  installed: boolean;
  size: number;
  loaded: boolean;
  roles: string[];
  residency: string;
  shares_weights_with: string[];
  frees: number;
};

export type Voice = {
  name: string;
  engine: string;
  gender: string;
  installed: boolean;
  selected: boolean;
  default: boolean;
};

export type Pkg = {
  key: string;
  label: string;
  installed: boolean;
  size: number;
  voices: string[];
  selected: boolean;
  in_use: boolean;
  job?: Job;
};

export type Whisper = {
  name: string;
  installed: boolean;
  size: number;
  selected: boolean;
};

export type Job = {
  status: string;
  done: number;
  total: number;
  error?: string;
};

export type State = {
  llm: { roles: Record<string, string>; models: Model[] };
  whisper: Whisper[];
  voices: { voices: Voice[]; files: Pkg[] };
  jobs: Record<string, Job>;
};

const gb = (b: number) =>
  b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`;

const ROLE_METAS: Record<
  string,
  { label: string; desc: string; icon: typeof Sparkles; color: string; bg: string }
> = {
  planner: {
    label: "Planner",
    desc: "Primary reasoning & multi-step plans",
    icon: Sparkles,
    color: "text-purple-600 dark:text-purple-400",
    bg: "bg-purple-500/10 dark:bg-purple-500/20 border-purple-500/30",
  },
  coder: {
    label: "Coder",
    desc: "Code generation, debugging & architecture",
    icon: Code2,
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10 dark:bg-emerald-500/20 border-emerald-500/30",
  },
  fast: {
    label: "Fast Classifier",
    desc: "Intent classification & cheap fast tasks",
    icon: Zap,
    color: "text-amber-600 dark:text-amber-400",
    bg: "bg-amber-500/10 dark:bg-amber-500/20 border-amber-500/30",
  },
  translator: {
    label: "Translator",
    desc: "Urdu, Pashto & language conversion",
    icon: Languages,
    color: "text-sky-600 dark:text-sky-400",
    bg: "bg-sky-500/10 dark:bg-sky-500/20 border-sky-500/30",
  },
};

/** Shared hook for /api/manage state */
export function useManage() {
  const [st, setSt] = useState<State>();
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const [playing, setPlaying] = useState("");
  const audio = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(() => {
    return getJSON<State>("/api/manage")
      .then(setSt)
      .catch((e) => setMsg(String(e.message || e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const downloading =
    st && Object.values(st.jobs).some((j) => !["done", "error"].includes(j.status));

  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [downloading, load]);

  const act = async (label: string, url: string, body: object, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(label);
    setMsg("");
    try {
      const r = await postJSON<{ ok?: boolean; error?: string }>(url, body);
      if (r?.error) setMsg(r.error);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    } finally {
      setBusy("");
      load();
    }
  };

  const preview = (name: string) => {
    audio.current?.pause();
    if (playing === name) {
      setPlaying("");
      return;
    }
    const a = new Audio(`/api/manage/preview?name=${encodeURIComponent(name)}`);
    audio.current = a;
    setPlaying(name);
    a.onended = () => setPlaying("");
    a.play().catch(() => setPlaying(""));
  };

  return { st, msg, setMsg, busy, playing, act, preview, load };
}

type OwnVoiceState = { exists: boolean; seconds?: number; recorded?: string };
const READ_ALOUD =
  "Hi, this is me. I'm recording my voice so ISHTI can send voice notes that sound like me. " +
  "I'll be home around eight tonight, and we can talk about the plans for the weekend then.";

/** "Your own voice": a 10-20 s sample the owner records here; WhatsApp voice notes are then converted to his voice. */
function OwnVoice() {
  const [st, setSt] = useState<OwnVoiceState>();
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [secs, setSecs] = useState(0);
  const [status, setStatus] = useState("");
  const [err, setErr] = useState("");
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(() => getJSON<OwnVoiceState>("/api/manage/owner_voice").then(setSt).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!rec) return;
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [rec]);
  useEffect(() => {
    if (rec && secs >= 20) rec.stop(); // 20 s is plenty; the converter uses the first 10
  }, [rec, secs]);

  const start = async () => {
    setErr("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const r = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRec(null);
        setStatus("Saving your sample…");
        try {
          const res = await fetch("/api/manage/owner_voice", { method: "POST", body: new Blob(chunks, { type: r.mimeType }) });
          const body = await res.json();
          if (!res.ok || body.error) setErr(body.error || `HTTP ${res.status}`);
        } catch (e) {
          setErr(String((e as Error).message || e));
        }
        setStatus("");
        load();
      };
      setSecs(0);
      r.start();
      setRec(r);
    } catch {
      setErr("Couldn't use the microphone - allow it for this page, or connect one.");
    }
  };

  const hear = () => {
    if (playing) {
      audio.current?.pause();
      setPlaying(false);
      return;
    }
    setStatus("Making a sample voice note in your voice (the first one takes ~10 s)…");
    const a = new Audio("/api/manage/owner_voice/preview");
    audio.current = a;
    a.oncanplay = () => setStatus("");
    a.onended = () => setPlaying(false);
    a.onerror = () => {
      setStatus("");
      setPlaying(false);
      setErr("Couldn't make the sample - is the own-voice converter downloaded (engine files below)?");
    };
    setPlaying(true);
    a.play().catch(() => setPlaying(false));
  };

  const remove = async () => {
    if (!window.confirm("Delete your voice sample? Voice notes will use ISHTI's voice until you record a new one.")) return;
    await del("/api/manage/owner_voice").catch(() => {});
    load();
  };

  return (
    <div className="rounded-xl border border-border/70 bg-card/60 p-3 space-y-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
            <UserRound className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="text-xs font-semibold text-foreground">Your own voice</div>
            <div className="text-[10px] text-muted-foreground">
              {st?.exists
                ? `Recorded ${st.recorded} (${st.seconds}s). WhatsApp voice notes are sent in your voice.`
                : "Record 10-20 seconds once, and voice notes ISHTI sends for you will sound like you."}
            </div>
          </div>
        </div>
        {st?.exists && !rec && (
          <span className="rounded-md bg-success/15 px-2 py-0.5 text-[9px] font-mono font-semibold text-success shrink-0">READY</span>
        )}
      </div>

      {rec && (
        <div className="rounded-lg border border-accent/40 bg-accent/5 p-2.5 text-[11px] leading-relaxed text-foreground">
          <div className="mb-1 font-semibold text-accent">Read this aloud, in your normal voice ({secs}s / 10-20s):</div>
          {READ_ALOUD}
        </div>
      )}
      {(status || err) && (
        <div role={err ? "alert" : "status"} className={cn("text-[11px]", err ? "text-danger" : "text-muted-foreground")}>
          {err || status}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {rec ? (
          <button
            type="button"
            onClick={() => rec.stop()}
            disabled={secs < 10}
            className="flex items-center gap-1.5 rounded-lg bg-danger px-3 py-1.5 text-[11px] font-semibold text-white disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
          >
            <Square className="h-3 w-3" /> {secs < 10 ? `Keep reading… ${10 - secs}s` : "Stop and save"}
          </button>
        ) : (
          <button
            type="button"
            onClick={start}
            disabled={!!status}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[11px] font-semibold text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
          >
            <Mic className="h-3 w-3" /> {st?.exists ? "Record again" : "Record my voice"}
          </button>
        )}
        {st?.exists && !rec && (
          <>
            <button
              type="button"
              onClick={hear}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[11px] font-medium hover:bg-muted cursor-pointer"
            >
              {playing ? <Square className="h-3 w-3" /> : <Play className="h-3 w-3" />} Hear a voice note
            </button>
            <button
              type="button"
              onClick={remove}
              className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] text-danger hover:bg-danger/10 cursor-pointer"
              aria-label="Delete my voice sample"
            >
              <Trash2 className="h-3 w-3" /> Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Progress({ job }: { job?: Job }) {
  if (!job || job.status === "done") return null;
  return (
    <span
      className={cn(
        "text-[10px] font-mono font-medium",
        job.status === "error" ? "text-danger" : "text-accent animate-pulse"
      )}
    >
      {job.status === "error"
        ? `failed: ${job.error}`
        : job.total
        ? `${Math.round((100 * job.done) / job.total)}%`
        : job.status}
    </span>
  );
}

// ========================================================================
// SECTION 1: LANGUAGE MODELS (LLMs)
// ========================================================================
export function ModelsCard({
  isOpen,
  onToggle,
}: {
  isOpen: boolean;
  onToggle: () => void;
}) {
  const { st, msg, busy, act } = useManage();
  const [showStorage, setShowStorage] = useState(false);

  const activePlanner = st?.llm.roles.planner;
  const installedModels = st?.llm.models.filter((m) => m.installed) ?? [];
  const freeable =
    st?.llm.models
      .filter((m) => m.installed && !m.roles.length)
      .reduce((n, m) => n + m.frees, 0) ?? 0;

  const jobOf = (key: string) => st?.jobs[key];

  return (
    <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden transition-all duration-200">
      {/* Header */}
      <div
        className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-violet-500/15 text-violet-500">
            <Cpu className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-foreground text-sm">
                Language models
              </span>
              {activePlanner && (
                <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-accent font-mono truncate max-w-[140px]">
                  {activePlanner.split(":")[0]}
                </span>
              )}
            </div>
            <div className="text-xs text-muted-foreground truncate">
              Assign roles to local models & manage model storage.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[11px] font-medium text-muted-foreground hidden sm:inline">
            {isOpen ? "Collapse" : "Expand"}
          </span>
          <ChevronDown
            className={cn(
              "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
              isOpen && "rotate-180 text-accent"
            )}
          />
        </div>
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border/60 bg-background/50"
          >
            <div className="p-4 space-y-5 text-xs">
              {msg && (
                <div
                  role="alert"
                  className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-danger"
                >
                  {msg}
                </div>
              )}

              {!st ? (
                <div className="flex items-center gap-2 text-muted-foreground py-4 justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  <span>Loading model inventory…</span>
                </div>
              ) : (
                <>
                  {/* --- 1. Role Assignment Cards --- */}
                  <div>
                    <div className="mb-3 flex items-baseline justify-between">
                      <div>
                        <h4 className="font-semibold text-foreground text-xs uppercase tracking-wider text-muted-foreground">
                          Model Role Assignments
                        </h4>
                        <p className="text-[11px] text-muted-foreground">
                          Switch which local neural network handles each capability.
                        </p>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {Object.entries(st.llm.roles).map(([role, selectedModel]) => {
                        const meta = ROLE_METAS[role] ?? {
                          label: role,
                          desc: "Assigned capability",
                          icon: Sparkles,
                          color: "text-accent",
                          bg: "bg-accent-soft border-border",
                        };
                        const Icon = meta.icon;
                        const currentModelObj = st.llm.models.find(
                          (m) => m.name === selectedModel
                        );

                        return (
                          <div
                            key={role}
                            className="rounded-xl border border-border/70 bg-card/60 p-3.5 flex flex-col justify-between gap-2.5 transition-shadow hover:border-accent/40"
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <div
                                  className={cn(
                                    "grid h-6 w-6 place-items-center rounded-lg border",
                                    meta.bg,
                                    meta.color
                                  )}
                                >
                                  <Icon className="h-3.5 w-3.5" />
                                </div>
                                <div>
                                  <div className="font-semibold text-foreground text-xs">
                                    {meta.label}
                                  </div>
                                  <div className="text-[10px] text-muted-foreground leading-tight">
                                    {meta.desc}
                                  </div>
                                </div>
                              </div>
                              {currentModelObj?.loaded && (
                                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[9px] font-semibold text-emerald-600 dark:text-emerald-400">
                                  In VRAM
                                </span>
                              )}
                            </div>

                            {/* Dropdown selector */}
                            <div className="relative mt-1">
                              <select
                                value={selectedModel}
                                disabled={!!busy}
                                aria-label={`Model for ${role}`}
                                onChange={(e) =>
                                  act(role, "/api/manage/role", {
                                    role,
                                    model: e.target.value,
                                  })
                                }
                                className="w-full appearance-none rounded-lg border border-border bg-background px-3 py-1.5 pr-8 font-mono text-[11px] font-medium text-foreground outline-none transition-colors hover:border-accent focus:border-accent disabled:opacity-50 cursor-pointer"
                              >
                                {st.llm.models
                                  .filter(
                                    (m) => m.installed && !m.name.includes("embed")
                                  )
                                  .map((m) => (
                                    <option key={m.name} value={m.name}>
                                      {m.name} ({gb(m.size)}){m.loaded ? " · Loaded" : ""}
                                    </option>
                                  ))}
                              </select>
                              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* --- 2. Collapsible Model Storage & Downloads --- */}
                  <div className="rounded-xl border border-border/70 bg-card/30 overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setShowStorage((prev) => !prev)}
                      className="w-full flex items-center justify-between p-3 text-left hover:bg-muted/40 transition-colors cursor-pointer select-none"
                    >
                      <div className="flex items-center gap-2.5">
                        <HardDrive className="h-4 w-4 text-muted-foreground" />
                        <div>
                          <div className="font-semibold text-foreground text-xs">
                            Installed Model Storage
                          </div>
                          <div className="text-[11px] text-muted-foreground">
                            {installedModels.length} models installed
                            {freeable > 0 && (
                              <span className="ml-1 text-amber-600 dark:text-amber-400">
                                · {gb(freeable)} unused
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 text-xs text-accent font-medium">
                        <span>{showStorage ? "Hide Storage" : "Manage Storage"}</span>
                        <ChevronDown
                          className={cn(
                            "h-3.5 w-3.5 transition-transform",
                            showStorage && "rotate-180"
                          )}
                        />
                      </div>
                    </button>

                    <AnimatePresence initial={false}>
                      {showStorage && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="overflow-hidden border-t border-border/60"
                        >
                          <ul className="divide-y divide-border/50 max-h-72 overflow-y-auto scroll-thin">
                            {st.llm.models.map((m) => {
                              const inUse = m.roles.length > 0;
                              return (
                                <li
                                  key={m.name}
                                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 hover:bg-muted/20"
                                >
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <span className="font-mono font-medium text-xs text-foreground">
                                        {m.name}
                                      </span>
                                      <span className="text-[10px] text-muted-foreground">
                                        {gb(m.size)}
                                      </span>
                                      {inUse && (
                                        <span className="rounded bg-accent/15 px-1.5 py-0.2 text-[9px] font-semibold text-accent">
                                          {m.roles.join(", ")}
                                        </span>
                                      )}
                                      {m.loaded && (
                                        <span className="rounded bg-emerald-500/15 px-1.5 py-0.2 text-[9px] font-semibold text-emerald-600 dark:text-emerald-400">
                                          in memory
                                        </span>
                                      )}
                                    </div>
                                    {m.installed && m.shares_weights_with.length > 0 && (
                                      <div className="text-[10px] text-muted-foreground mt-0.5">
                                        shares weights with{" "}
                                        {m.shares_weights_with.join(", ")}
                                      </div>
                                    )}
                                  </div>

                                  <div className="flex items-center gap-2 shrink-0">
                                    <Progress job={jobOf(m.name)} />
                                    {m.installed ? (
                                      <button
                                        type="button"
                                        disabled={!!busy || inUse}
                                        title={
                                          inUse
                                            ? "In use - reassign role first"
                                            : "Delete to free disk space"
                                        }
                                        onClick={() =>
                                          act(
                                            m.name,
                                            "/api/manage/model",
                                            { name: m.name, action: "delete" },
                                            `Delete ${m.name}? It frees about ${gb(
                                              m.frees
                                            )}. You can download it again anytime.`
                                          )
                                        }
                                        className="flex items-center gap-1 rounded-md px-2 py-1 text-danger hover:bg-danger/10 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed transition-colors"
                                      >
                                        <Trash2 className="h-3.5 w-3.5" />
                                        <span>Delete</span>
                                      </button>
                                    ) : (
                                      <button
                                        type="button"
                                        disabled={!!busy || !!jobOf(m.name)}
                                        onClick={() =>
                                          act(m.name, "/api/manage/model", {
                                            name: m.name,
                                            action: "download",
                                          })
                                        }
                                        className="flex items-center gap-1 rounded-md px-2 py-1 text-accent hover:bg-accent/10 disabled:opacity-40 cursor-pointer transition-colors"
                                      >
                                        <Download className="h-3.5 w-3.5" />
                                        <span>Download</span>
                                      </button>
                                    )}
                                  </div>
                                </li>
                              );
                            })}
                          </ul>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ========================================================================
// SECTION 2: VOICE & SPEECH SYNTHESIS
// ========================================================================
export function VoiceCard({
  isOpen,
  onToggle,
  audioOutput,
  wakeEnabled,
  onToggleAudioOutput,
}: {
  isOpen: boolean;
  onToggle: () => void;
  audioOutput?: boolean;
  wakeEnabled?: boolean;
  onToggleAudioOutput?: () => void;
}) {
  const { st, msg, busy, playing, act, preview } = useManage();
  const [showEngines, setShowEngines] = useState(false);

  const selectedVoice = st?.voices.voices.find((v) => v.selected)?.name;

  return (
    <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden transition-all duration-200">
      {/* Header */}
      <div
        className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-purple-500/15 text-purple-500">
            <Volume2 className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-foreground text-sm">
                Voices
              </span>
              <span
                className={cn(
                  "rounded-md px-2 py-0.5 text-[10px] font-semibold font-mono",
                  audioOutput ? "bg-accent-soft text-accent" : "bg-muted text-muted-foreground"
                )}
              >
                {selectedVoice || (audioOutput ? "Active" : "Muted")}
              </span>
            </div>
            <div className="text-xs text-muted-foreground truncate">
              Neural voice roster, live audio preview & speech output settings.
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0" onClick={(e) => e.stopPropagation()}>
          {onToggleAudioOutput && (
            <button
              type="button"
              onClick={onToggleAudioOutput}
              className={cn(
                "relative h-6 w-11 rounded-full transition-colors cursor-pointer",
                audioOutput ? "bg-accent" : "bg-muted"
              )}
              aria-pressed={audioOutput}
              title="Toggle audio speech readout"
            >
              <motion.span
                layout
                transition={{ type: "spring", stiffness: 600, damping: 32 }}
                className={cn(
                  "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-xs",
                  audioOutput ? "right-0.5" : "left-0.5"
                )}
              />
            </button>
          )}
          <span className="text-[11px] font-medium text-muted-foreground hidden sm:inline ml-1">
            {isOpen ? "Collapse" : "Expand"}
          </span>
          <button
            type="button"
            onClick={onToggle}
            className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
          >
            <ChevronDown
              className={cn(
                "h-4 w-4 transition-transform duration-200",
                isOpen && "rotate-180 text-accent"
              )}
            />
          </button>
        </div>
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border/60 bg-background/50"
          >
            <div className="p-4 space-y-4 text-xs">
              {msg && (
                <div
                  role="alert"
                  className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-danger"
                >
                  {msg}
                </div>
              )}

              {/* Toggles Banner */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                <div className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-card/60 p-3">
                  <div>
                    <div className="text-xs font-semibold text-foreground">
                      Read answers aloud
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      Zero cloud API calls. Synthesized on this PC.
                    </div>
                  </div>
                  <span className="text-xs font-mono font-medium text-accent">
                    {audioOutput ? "Enabled" : "Muted"}
                  </span>
                </div>

                <div className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-card/60 p-3">
                  <div className="flex items-center gap-2">
                    <Mic className="h-3.5 w-3.5 text-accent shrink-0" />
                    <div>
                      <div className="text-xs font-semibold text-foreground">
                        Wake Word (“hey ishti”)
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        {wakeEnabled ? "Listening active" : "Disabled"}
                      </div>
                    </div>
                  </div>
                  <span
                    className={cn(
                      "rounded-md px-2 py-0.5 text-[9px] font-mono font-semibold",
                      wakeEnabled
                        ? "bg-success/15 text-success"
                        : "bg-muted text-muted-foreground"
                    )}
                  >
                    {wakeEnabled ? "ACTIVE" : "OFF"}
                  </span>
                </div>
              </div>

              {!st ? (
                <div className="flex items-center gap-2 text-muted-foreground py-4 justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  <span>Loading voice roster…</span>
                </div>
              ) : (
                <>
                  <OwnVoice />
                  {/* --- Voice Roster --- */}
                  <div>
                    <div className="mb-2.5 flex items-baseline justify-between">
                      <h4 className="font-semibold text-foreground text-xs uppercase tracking-wider text-muted-foreground">
                        Voice Personalities
                      </h4>
                      <span className="text-[11px] text-muted-foreground">
                        Preview speech sample or select default
                      </span>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      {st.voices.voices.map((v) => {
                        const isPlaying = playing === v.name;
                        return (
                          <div
                            key={v.name}
                            className={cn(
                              "flex items-center justify-between gap-2.5 rounded-xl border p-3 transition-all",
                              v.selected
                                ? "border-accent bg-accent/10 shadow-xs"
                                : "border-border/70 bg-card/60 hover:border-accent/40"
                            )}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="font-semibold text-foreground text-xs truncate">
                                  {v.name}
                                </span>
                                {v.default && (
                                  <span className="rounded bg-muted px-1.5 py-0.2 text-[9px] text-muted-foreground font-mono">
                                    default
                                  </span>
                                )}
                              </div>
                              <div className="text-[10px] text-muted-foreground mt-0.5 flex items-center gap-1.5">
                                <span>{v.engine === "kokoro" ? "Kokoro" : "Piper"}</span>
                                <span>·</span>
                                <span className="capitalize">{v.gender}</span>
                                {!v.installed && (
                                  <span className="text-amber-500">· not downloaded</span>
                                )}
                              </div>
                            </div>

                            <div className="flex items-center gap-1.5 shrink-0">
                              {/* Preview Audio Button */}
                              <button
                                type="button"
                                disabled={!v.installed}
                                onClick={() => preview(v.name)}
                                aria-label={`Preview voice ${v.name}`}
                                className={cn(
                                  "grid h-8 w-8 place-items-center rounded-lg border transition-colors cursor-pointer disabled:opacity-30",
                                  isPlaying
                                    ? "border-accent bg-accent text-white shadow-xs"
                                    : "border-border bg-background hover:bg-muted text-foreground"
                                )}
                              >
                                {isPlaying ? (
                                  <Square className="h-3.5 w-3.5 fill-current" />
                                ) : (
                                  <Play className="h-3.5 w-3.5 fill-current ml-0.5" />
                                )}
                              </button>

                              {/* Use Button / Active Badge */}
                              {v.selected ? (
                                <span className="flex items-center gap-1 rounded-lg bg-accent/20 px-2.5 py-1 text-[11px] font-semibold text-accent">
                                  <Check className="h-3 w-3" />
                                  <span>In Use</span>
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  disabled={!!busy || !v.installed}
                                  onClick={() =>
                                    act(v.name, "/api/manage/voice", {
                                      name: v.name,
                                      default: true,
                                    })
                                  }
                                  className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90 disabled:opacity-40 cursor-pointer transition-opacity"
                                >
                                  Use
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* --- Voice Engine Files (Collapsible Drawer) --- */}
                  <div className="rounded-xl border border-border/70 bg-card/30 overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setShowEngines((prev) => !prev)}
                      className="w-full flex items-center justify-between p-3 text-left hover:bg-muted/40 transition-colors cursor-pointer select-none"
                    >
                      <div className="flex items-center gap-2.5">
                        <FileAudio className="h-4 w-4 text-muted-foreground" />
                        <div>
                          <div className="font-semibold text-foreground text-xs">
                            Voice Engine Packages
                          </div>
                          <div className="text-[11px] text-muted-foreground">
                            Kokoro & Piper neural weight models on disk
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 text-xs text-accent font-medium">
                        <span>{showEngines ? "Hide Packages" : "Manage Packages"}</span>
                        <ChevronDown
                          className={cn(
                            "h-3.5 w-3.5 transition-transform",
                            showEngines && "rotate-180"
                          )}
                        />
                      </div>
                    </button>

                    <AnimatePresence initial={false}>
                      {showEngines && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="overflow-hidden border-t border-border/60"
                        >
                          <ul className="divide-y divide-border/50">
                            {st.voices.files.map((f) => (
                              <li
                                key={f.key}
                                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 hover:bg-muted/20"
                              >
                                <div className="min-w-0">
                                  <div className="font-medium text-foreground text-xs">
                                    {f.label}
                                  </div>
                                  <div className="text-[10px] text-muted-foreground">
                                    {f.installed ? gb(f.size) : "not downloaded"}
                                    {f.voices.length > 1 && ` · ${f.voices.length} voices`}
                                    {f.in_use && " · in use"}
                                  </div>
                                </div>
                                <div className="flex items-center gap-2">
                                  <Progress job={f.job} />
                                  {f.installed ? (
                                    <button
                                      type="button"
                                      disabled={!!busy || f.in_use}
                                      title={
                                        f.in_use
                                          ? "In use - select another voice first"
                                          : "Delete package"
                                      }
                                      onClick={() =>
                                        act(
                                          f.key,
                                          "/api/manage/files",
                                          { key: f.key, action: "delete" },
                                          `Delete ${f.label}? It frees ${gb(
                                            f.size
                                          )}. You can download it again anytime.`
                                        )
                                      }
                                      className="flex items-center gap-1 rounded-md px-2 py-1 text-danger hover:bg-danger/10 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed"
                                    >
                                      <Trash2 className="h-3.5 w-3.5" /> Delete
                                    </button>
                                  ) : (
                                    <button
                                      type="button"
                                      disabled={
                                        !!busy ||
                                        !!(
                                          f.job &&
                                          !["done", "error"].includes(f.job.status)
                                        )
                                      }
                                      onClick={() =>
                                        act(f.key, "/api/manage/files", {
                                          key: f.key,
                                          action: "download",
                                        })
                                      }
                                      className="flex items-center gap-1 rounded-md px-2 py-1 text-accent hover:bg-accent/10 disabled:opacity-40 cursor-pointer"
                                    >
                                      <Download className="h-3.5 w-3.5" /> Download
                                    </button>
                                  )}
                                </div>
                              </li>
                            ))}
                          </ul>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ========================================================================
// SECTION 3: SPEECH RECOGNITION (WHISPER STT)
// ========================================================================
export function WhisperCard({
  isOpen,
  onToggle,
}: {
  isOpen: boolean;
  onToggle: () => void;
}) {
  const { st, msg, busy, act } = useManage();
  const selectedWhisper = st?.whisper.find((w) => w.selected)?.name;
  const jobOf = (key: string) => st?.jobs[key];

  return (
    <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden transition-all duration-200">
      {/* Header */}
      <div
        className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-500/15 text-emerald-500">
            <Radio className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-foreground text-sm">
                Speech recognition (Whisper)
              </span>
              {selectedWhisper && (
                <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 font-mono">
                  {selectedWhisper}
                </span>
              )}
            </div>
            <div className="text-xs text-muted-foreground truncate">
              Local speech-to-text transcription model and accuracy presets.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[11px] font-medium text-muted-foreground hidden sm:inline">
            {isOpen ? "Collapse" : "Expand"}
          </span>
          <ChevronDown
            className={cn(
              "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
              isOpen && "rotate-180 text-accent"
            )}
          />
        </div>
      </div>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border/60 bg-background/50"
          >
            <div className="p-4 space-y-3 text-xs">
              {msg && (
                <div
                  role="alert"
                  className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-danger"
                >
                  {msg}
                </div>
              )}

              {!st ? (
                <div className="flex items-center gap-2 text-muted-foreground py-4 justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  <span>Loading Whisper models…</span>
                </div>
              ) : (
                <div className="space-y-2">
                  {st.whisper.map((w) => {
                    const isSelected = w.selected;
                    return (
                      <div
                        key={w.name}
                        className={cn(
                          "flex items-center justify-between gap-3 rounded-xl border p-3 transition-all",
                          isSelected
                            ? "border-accent bg-accent/10 shadow-xs"
                            : "border-border/70 bg-card/60 hover:border-accent/40"
                        )}
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-mono font-semibold text-foreground text-xs">
                              {w.name}
                            </span>
                            <span className="text-[10px] text-muted-foreground">
                              {w.installed ? gb(w.size) : "not downloaded"}
                            </span>
                            {w.name === "large-v3" && (
                              <span className="rounded bg-accent/15 px-1.5 py-0.2 text-[9px] font-semibold text-accent font-sans">
                                Recommended
                              </span>
                            )}
                          </div>
                          <div className="text-[10px] text-muted-foreground mt-0.5">
                            {w.name === "large-v3"
                              ? "Highest accuracy for English, Urdu & Pashto mixed speech"
                              : w.name === "medium"
                              ? "Balanced speed and accuracy (~1.5 GB VRAM)"
                              : "Ultra-fast low-resource transcription"}
                          </div>
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          <Progress job={jobOf(`whisper:${w.name}`)} />
                          {isSelected ? (
                            <span className="flex items-center gap-1 rounded-lg bg-accent/20 px-2.5 py-1 text-[11px] font-semibold text-accent">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              <span>Active</span>
                            </span>
                          ) : w.installed ? (
                            <button
                              type="button"
                              disabled={!!busy}
                              onClick={() =>
                                act(w.name, "/api/manage/whisper", {
                                  name: w.name,
                                  action: "select",
                                })
                              }
                              className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90 cursor-pointer"
                            >
                              Use
                            </button>
                          ) : null}

                          {w.installed ? (
                            <button
                              type="button"
                              disabled={!!busy || isSelected}
                              onClick={() =>
                                act(
                                  w.name,
                                  "/api/manage/whisper",
                                  { name: w.name, action: "delete" },
                                  `Delete Whisper ${w.name}? It frees ${gb(w.size)}.`
                                )
                              }
                              className="rounded-lg p-1.5 text-danger hover:bg-danger/10 disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed"
                              aria-label={`Delete Whisper ${w.name}`}
                              title={isSelected ? "In use" : "Delete"}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          ) : (
                            <button
                              type="button"
                              disabled={!!busy}
                              onClick={() =>
                                act(w.name, "/api/manage/whisper", {
                                  name: w.name,
                                  action: "download",
                                })
                              }
                              className="flex items-center gap-1 rounded-lg bg-accent/15 px-2.5 py-1 text-[11px] font-medium text-accent hover:bg-accent/25 cursor-pointer"
                            >
                              <Download className="h-3.5 w-3.5" />
                              <span>Download</span>
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Legacy composite wrapper if needed */
export function ModelsVoices({
  isOpen,
  onToggle,
}: {
  isOpen: boolean;
  onToggle: () => void;
}) {
  return <ModelsCard isOpen={isOpen} onToggle={onToggle} />;
}
