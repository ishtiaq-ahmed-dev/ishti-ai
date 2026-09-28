import { AnimatePresence, motion } from "motion/react";
import {
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  Download,
  Pencil,
  RefreshCw,
  Search,
  Sliders,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Users,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { Page } from "@/sections/Pages";
import { cn } from "@/lib/utils";

// Contract types: docs/LEARNING_INTERFACE.md section 4 (keys and value types are frozen).

type Lesson = {
  lesson_id: string;
  created?: number;
  updated?: number;
  kind?: string;
  scope?: string;
  context?: string;
  statement?: string;
  incorrect?: string;
  correct?: string;
  evidence?: number;
  positive?: number;
  negative?: number;
  confidence?: number;
  bucket?: string;
  status?: string;
  source?: string;
  priority?: number;
  action?: string;
  expires?: number | null;
  entity?: string;
  occasions?: number;
  confirmed?: number | boolean;
  note?: string;
};

type Candidate = {
  id: number;
  ts?: number;
  kind?: string;
  signature?: string;
  question?: string;
  status?: string;
  source?: string;
  payload?: Record<string, unknown>;
};

type Feedback = {
  id: number;
  ts?: number;
  kind?: string;
  polarity?: string;
  error_type?: string;
  target?: string;
  incorrect?: string;
  correct?: string;
  message?: string;
  source?: string;
  confidence?: number;
  applied?: string | number | boolean;
};

type Mistake = {
  error_type?: string;
  wrong?: string;
  right?: string;
  count?: number;
  signature?: string;
  domain?: string;
  last_ts?: number;
};

type Interaction = {
  id?: string;
  ts?: number;
  request?: string;
  skill?: string;
  status?: string;
  reward?: number;
  domain?: string;
  task_id?: string;
  step_no?: number;
};

type WorkflowRow = {
  id: number;
  name?: string;
  steps?: (string | Record<string, unknown>)[];
  count?: number;
  confidence?: number;
  status?: string;
  last_seen?: number;
};

type Person = {
  entity?: string;
  org?: string;
  relationship?: string;
  email?: string;
  phone?: string;
  confidence?: number;
  mentions?: number;
  last_seen?: number;
  aliases?: string[];
};

type DatasetRow = {
  id: number;
  ts?: number;
  instruction?: string;
  correct?: string;
  incorrect?: string;
  reason?: string;
  correction?: string;
  quality?: number;
  skill?: string;
  source?: string;
  exported?: number;
};

type DatasetStats = { total?: number; by_quality?: Record<string, number>; by_skill?: Record<string, number>; exported?: number };

type Overview = {
  enabled?: boolean;
  db?: string;
  counts?: Record<string, number>;
  loops?: Record<string, unknown>;
  confidence_buckets?: Record<string, number>;
  thresholds?: Record<string, number>;
  settings?: { enabled?: boolean; proactive?: boolean; [key: string]: unknown };
  recent?: Interaction[];
};

type LearningSettings = {
  settings?: Record<string, unknown>;
  thresholds?: Record<string, number>;
  evidence?: Record<string, number>;
};

type AskSlot = { question?: string | null; candidate_id?: number | null };
type Notice = { kind: "ok" | "error"; text: string };
type TabKey = "overview" | "lessons" | "reflections" | "candidates" | "feedback" | "mistakes" | "people" | "workflows" | "training" | "settings";

// Reflections (Reflexion 2303.11366) and mined insights (ExpeL 2308.10144). Both are stored as
// lessons at the weak band, so `bucket`/`confidence` behave like any other row in this file.
type Reflection = {
  lesson_id: string;
  statement: string;
  context: string;
  note?: string;
  confidence: number;
  bucket?: string;
  status?: string;
  updated?: number;
  kind_of?: "reflection" | "insight";
};
type MinedInsight = {
  statement: string;
  context?: string;
  provenance?: { interaction_id?: string; request?: string; skill?: string; ts?: number };
};
type ReflectionsPayload = {
  reflections?: Reflection[];
  mined?: MinedInsight[];
  counts?: { reflections?: number; insights?: number; pending?: number };
};

const THRESHOLD_KEYS = ["weak", "possible", "probable", "strong"] as const;
const EVIDENCE_KEYS = ["similar_request", "widen_scope", "candidate_rule", "workflow"] as const;
const THRESHOLD_HINTS: Record<string, string> = {
  weak: "confidence where a lesson stops being weak",
  possible: "start of the possible band",
  probable: "start of the probable band",
  strong: "start of the strong band",
};
const EVIDENCE_HINTS: Record<string, string> = {
  similar_request: "occasions before a similar request widens a lesson",
  widen_scope: "occasions before a lesson widens its scope",
  candidate_rule: "repeats before a pattern becomes a candidate rule",
  workflow: "runs before a procedure is offered as a workflow",
};

// ---- defensive data helpers (a missing backend must never white-screen the page) ----------------

const safeGet = <T,>(url: string): Promise<T | null> => getJSON<T>(url).catch(() => null);

const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

const str = (v: unknown, fallback = ""): string => (v === null || v === undefined ? fallback : String(v));

function fmtTs(ts: unknown): string {
  const n = typeof ts === "number" ? ts : Number(ts);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return new Date(n * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function loopValue(v: unknown): string {
  if (typeof v === "number") return v.toLocaleString();
  if (v && typeof v === "object") {
    const d = v as Record<string, unknown>;
    for (const k of ["count", "total", "active", "lessons"]) if (typeof d[k] === "number") return (d[k] as number).toLocaleString();
  }
  return "—";
}

// ---- small local UI helpers (Page/Card conventions copied from Pages.tsx) ----------------------

const Card = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`rounded-2xl border border-border bg-background p-4 ${className}`}>{children}</div>
);

function Stat({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <Card className="min-w-0 p-3">
      <p className="truncate text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-foreground">
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
      {sub && <p className="mt-0.5 truncate text-[10px] text-muted-foreground">{sub}</p>}
    </Card>
  );
}

function Pill({ children, tone = "muted" }: { children: ReactNode; tone?: "accent" | "muted" | "success" | "danger" | "warn" | "outline" }) {
  const tones = {
    accent: "border-accent/15 bg-accent/10 text-accent",
    muted: "border-transparent bg-muted/60 text-muted-foreground",
    success: "border-emerald-500/20 bg-emerald-500/10 text-emerald-500",
    danger: "border-red-500/20 bg-red-500/10 text-red-500",
    warn: "border-amber-500/20 bg-amber-500/10 text-amber-500",
    outline: "border-border bg-transparent text-muted-foreground",
  } as const;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide", tones[tone])}>
      {children}
    </span>
  );
}

const BUCKETS = ["weak", "possible", "probable", "strong", "confirmed"] as const;

const bucketFill = (bucket: unknown): string => {
  switch (bucket) {
    case "confirmed":
      return "bg-emerald-500";
    case "strong":
      return "bg-teal-500";
    case "probable":
      return "bg-accent";
    case "possible":
      return "bg-amber-500";
    default:
      return "bg-muted-foreground/40";
  }
};

const statusTone = (status: unknown): string => {
  const s = str(status);
  if (["ok", "done", "active", "confirmed", "working"].includes(s)) return "border-emerald-500/20 bg-emerald-500/10 text-emerald-500";
  if (["error", "failed", "rejected", "broken", "retired"].includes(s)) return "border-red-500/20 bg-red-500/10 text-red-500";
  if (["paused", "open", "waiting", "expired"].includes(s)) return "border-amber-500/20 bg-amber-500/10 text-amber-500";
  return "border-border bg-muted/60 text-muted-foreground";
};

function ConfBar({ confidence, bucket }: { confidence: number; bucket?: unknown }) {
  const pct = Math.max(0, Math.min(1, confidence)) * 100;
  return (
    <span className="inline-flex items-center gap-2" title={`confidence ${pct.toFixed(0)}%`}>
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        <span className={cn("block h-full rounded-full", bucketFill(bucket))} style={{ width: `${pct}%` }} />
      </span>
      <span className="text-[11px] font-medium tabular-nums text-foreground">{pct.toFixed(0)}%</span>
      <span className="rounded-md bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {bucket ? str(bucket) : "—"}
      </span>
    </span>
  );
}

function Panel({ title, hint, action, children }: { title: string; hint?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

const Empty = ({ children }: { children: ReactNode }) => (
  <Card className="py-6 text-center text-xs text-muted-foreground">{children}</Card>
);

const btn = "inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default";
const btnQuiet = cn(btn, "border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground");
const btnPrimary = cn(btn, "border-transparent bg-accent text-white shadow-xs hover:opacity-90");
const btnDanger = cn(btn, "border-red-500/30 bg-red-500/10 text-red-500 hover:bg-red-500/20");
const inputCls =
  "w-full rounded-xl border border-border bg-background px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent";

const TABS: { key: TabKey; label: string; icon: LucideIcon }[] = [
  { key: "overview", label: "Overview", icon: Sparkles },
  { key: "lessons", label: "Lessons", icon: Brain },
  { key: "reflections", label: "Reflections", icon: RefreshCw },
  { key: "candidates", label: "Candidates", icon: ThumbsUp },
  { key: "feedback", label: "Feedback", icon: ThumbsDown },
  { key: "mistakes", label: "Mistakes", icon: X },
  { key: "people", label: "People", icon: Users },
  { key: "workflows", label: "Workflows", icon: Workflow },
  { key: "training", label: "Training data", icon: Database },
  { key: "settings", label: "Settings", icon: Sliders },
];

type SettingsDraft = {
  enabled: boolean;
  proactive: boolean;
  thresholds: Record<string, string>;
  evidence: Record<string, string>;
};

const THRESHOLD_DEFAULTS: Record<string, number> = { weak: 0.3, possible: 0.6, probable: 0.8, strong: 0.95 };
const EVIDENCE_DEFAULTS: Record<string, number> = { similar_request: 2, widen_scope: 3, candidate_rule: 3, workflow: 3 };

const draftFrom = (st: LearningSettings | null): SettingsDraft => {
  const th = st?.thresholds ?? {};
  const ev = st?.evidence ?? {};
  return {
    enabled: st?.settings?.enabled !== false,
    proactive: st?.settings?.proactive === true,
    thresholds: Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, String(th[k] ?? THRESHOLD_DEFAULTS[k])])),
    evidence: Object.fromEntries(EVIDENCE_KEYS.map((k) => [k, String(ev[k] ?? EVIDENCE_DEFAULTS[k])])),
  };
};

export function Learning({ onToggleNav }: { onToggleNav?: () => void }) {
  const [tab, setTab] = useState<TabKey>("overview");
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const [mistakes, setMistakes] = useState<Mistake[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowRow[]>([]);
  const [dataset, setDataset] = useState<DatasetRow[]>([]);
  const [dstats, setDstats] = useState<DatasetStats>({});
  const [settings, setSettings] = useState<LearningSettings | null>(null);
  const [activity, setActivity] = useState<Interaction[]>([]);
  const [ask, setAsk] = useState<AskSlot | null>(null);
  const [refl, setRefl] = useState<{ list: Reflection[]; mined: MinedInsight[] }>({ list: [], mined: [] });
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [lessonStatus, setLessonStatus] = useState("");
  const [lessonKind, setLessonKind] = useState("");
  const [lessonQuery, setLessonQuery] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<{ id: string; statement: string; scope: string; confidence: string } | null>(null);
  const [confirmKey, setConfirmKey] = useState("");
  const [showAllActivity, setShowAllActivity] = useState(false);
  const [form, setForm] = useState({ instruction: "", correct: "", skill: "", quality: "" });

  const load = useCallback(async (spinner = false) => {
    if (spinner) setLoading(true);
    const [ov, ls, cd, fb, mk, pp, wf, ds, st, ac, aq, rf] = await Promise.all([
      safeGet<Overview>("/api/learning/overview"),
      safeGet<{ lessons?: Lesson[] }>("/api/learning/lessons"),
      safeGet<{ candidates?: Candidate[] }>("/api/learning/candidates"),
      safeGet<{ feedback?: Feedback[] }>("/api/learning/feedback"),
      safeGet<{ mistakes?: Mistake[] }>("/api/learning/mistakes"),
      safeGet<{ people?: Person[] }>("/api/learning/people"),
      safeGet<{ workflows?: WorkflowRow[] }>("/api/learning/workflows"),
      safeGet<{ dataset?: DatasetRow[]; stats?: DatasetStats }>("/api/learning/dataset"),
      safeGet<LearningSettings>("/api/learning/settings"),
      safeGet<{ interactions?: Interaction[] }>("/api/learning/interactions"),
      safeGet<AskSlot>("/api/learning/ask"),
      safeGet<ReflectionsPayload>("/api/learning/reflections"),
    ]);
    if (ov) setOverview(ov);
    if (ls) setLessons(ls.lessons ?? []);
    if (cd) setCandidates(cd.candidates ?? []);
    if (fb) setFeedback(fb.feedback ?? []);
    if (mk) setMistakes(mk.mistakes ?? []);
    if (pp) setPeople(pp.people ?? []);
    if (wf) setWorkflows(wf.workflows ?? []);
    if (ds) {
      setDataset(ds.dataset ?? []);
      setDstats(ds.stats ?? {});
    }
    if (st) {
      setSettings(st);
      setDraft((prev) => prev ?? draftFrom(st));
    }
    if (ac) setActivity(ac.interactions ?? []);
    setAsk(aq);
    if (rf) setRefl({ list: rf.reflections ?? [], mined: rf.mined ?? [] });
    setLoaded(true);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load(true);
  }, [load]);

  const run = useCallback(async (key: string, action: () => Promise<unknown>, okText?: string): Promise<boolean> => {
    setBusy(key);
    setNotice(null);
    try {
      await action();
      if (okText) setNotice({ kind: "ok", text: okText });
      await load();
      return true;
    } catch (exc) {
      setNotice({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
      return false;
    } finally {
      setBusy("");
    }
  }, [load]);

  const lessonAction = (id: string, action: "confirm" | "reject" | "pause" | "resume") => {
    const base = `/api/learning/lessons/${encodeURIComponent(id)}`;
    const target = action === "confirm" ? `${base}/confirm` : action === "reject" ? `${base}/reject` : base;
    const body = action === "pause" ? { status: "paused" } : action === "resume" ? { status: "active" } : {};
    const verb = action === "confirm" ? "confirmed" : action === "reject" ? "rejected" : action === "pause" ? "paused" : "resumed";
    void run(`lesson:${action}:${id}`, () => postJSON(target, body), `Lesson ${verb}.`);
  };

  const deleteLesson = (id: string) => {
    setConfirmKey("");
    void run(`lesson:delete:${id}`, () => del(`/api/learning/lessons/${encodeURIComponent(id)}?forget=1`),
      "Lesson deleted (memory forgotten where it had written back).");
  };

  const saveLesson = () => {
    if (!editing) return;
    const c = Number(editing.confidence);
    const payload = {
      statement: editing.statement,
      scope: editing.scope,
      confidence: Number.isFinite(c) ? Math.max(0, Math.min(1, c)) : 0,
    };
    void run(`lesson:save:${editing.id}`, () => postJSON(`/api/learning/lessons/${encodeURIComponent(editing.id)}`, payload), "Lesson updated.")
      .then((ok) => { if (ok) setEditing(null); });
  };

  const answerCandidate = (id: number, keep: boolean) => {
    if (!Number.isFinite(id) || id < 0) return;
    void run(`candidate:${id}`, () => postJSON(`/api/learning/candidates/${id}`, { keep }),
      keep ? "Candidate kept as a lesson." : "Candidate discarded.");
  };

  const workflowStatus = (id: number, status: "active" | "retired") => {
    void run(`workflow:${id}`, () => postJSON(`/api/learning/workflows/${id}`, { status }), `Workflow ${status}.`);
  };

  const exportDataset = () => {
    void run("dataset:export", async () => {
      const res = await postJSON<{ path?: string; count?: number }>("/api/learning/dataset/export");
      setNotice({ kind: "ok", text: `Exported ${num(res?.count)} example(s) to ${res?.path || "the export directory"}.` });
    });
  };

  const deleteExample = (id: number) => {
    setConfirmKey("");
    void run(`dataset:delete:${id}`, () => del(`/api/learning/dataset/${id}`), "Example deleted.");
  };

  const addExample = () => {
    if (!form.instruction.trim()) return;
    const body: Record<string, unknown> = { instruction: form.instruction.trim() };
    if (form.correct.trim()) body.correct = form.correct.trim();
    if (form.skill.trim()) body.skill = form.skill.trim();
    if (form.quality.trim() && Number.isFinite(Number(form.quality))) body.quality = Number(form.quality);
    void run("dataset:add", () => postJSON("/api/learning/dataset", body), "Example added to the offline dataset.")
      .then((ok) => { if (ok) setForm({ instruction: "", correct: "", skill: "", quality: "" }); });
  };

  const saveSettings = () => {
    if (!draft) return;
    const thresholds = Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, Number(draft.thresholds[k] || 0)]));
    const evidence = Object.fromEntries(EVIDENCE_KEYS.map((k) => [k, Math.max(1, Math.round(Number(draft.evidence[k] || 1)))]));
    void run("settings:save", () => postJSON("/api/learning/settings", {
      enabled: draft.enabled,
      proactive: draft.proactive,
      thresholds,
      evidence,
    }), "Settings saved to the learning DB (runtime override - config.json untouched).");
  };

  const counts = overview?.counts ?? {};
  const buckets = overview?.confidence_buckets ?? {};
  const bucketTotal = BUCKETS.reduce((sum, b) => sum + num(buckets[b]), 0);
  const scored = activity.filter((a) => typeof a.reward === "number" && Number.isFinite(a.reward));
  const avgReward = scored.length ? scored.reduce((sum, a) => sum + num(a.reward), 0) / scored.length : 0;
  const ruleMin = num(settings?.evidence?.candidate_rule, 3);
  const lessonStatuses = Array.from(new Set(lessons.map((l) => str(l.status)).filter(Boolean)));
  const lessonKinds = Array.from(new Set(lessons.map((l) => str(l.kind)).filter(Boolean)));
  const filteredLessons = lessons.filter((l) => {
    if (lessonStatus && str(l.status) !== lessonStatus) return false;
    if (lessonKind && str(l.kind) !== lessonKind) return false;
    if (lessonQuery.trim()) {
      const q = lessonQuery.trim().toLowerCase();
      const hay = [l.statement, l.scope, l.context, l.source, l.entity, l.incorrect, l.correct, l.note]
        .map((v) => str(v).toLowerCase()).join(" ");
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  return (
    <Page
      title="Learning"
      subtitle="Three store-based feedback loops - memory (what I know about you), skills (which capability to use) and learning (whether decisions worked). Everything is reversible from here; model weights never change at runtime."
      className="max-w-7xl"
      onToggleNav={onToggleNav}
    >
      <div className="space-y-4">
        {notice && (
          <div className={cn("flex items-start justify-between gap-3 rounded-2xl border px-3.5 py-2.5 text-xs",
            notice.kind === "error" ? "border-red-500/30 bg-red-500/10 text-red-500" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-500")}>
            <span className="min-w-0 break-words">{notice.text}</span>
            <button type="button" onClick={() => setNotice(null)} className="shrink-0 cursor-pointer opacity-70 hover:opacity-100">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {ask?.question && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-accent/30 bg-accent-soft/50 p-3.5">
            <div className="flex min-w-0 items-center gap-2 text-xs text-foreground">
              <Sparkles className="h-4 w-4 shrink-0 text-accent" />
              <span className="min-w-0">{ask.question}</span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button type="button" className={btnPrimary} onClick={() => answerCandidate(num(ask.candidate_id, -1), true)}>
                <Check className="h-3 w-3" /> Yes, keep
              </button>
              <button type="button" className={btnQuiet} onClick={() => answerCandidate(num(ask.candidate_id, -1), false)}>
                <X className="h-3 w-3" /> No
              </button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1.5 rounded-2xl border border-border bg-card/50 p-1.5 shadow-xs">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cn("inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-xs font-medium transition-colors cursor-pointer",
                tab === key ? "bg-accent text-white shadow-xs" : "text-muted-foreground hover:bg-muted hover:text-foreground")}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
          <span className="ml-auto flex items-center gap-2 pr-1">
            {loading && !loaded && <span className="text-[11px] text-muted-foreground">loading…</span>}
            <button type="button" onClick={() => void load()} className={btnQuiet} title="Refetch every panel">
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> Refresh
            </button>
          </span>
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }}
            className="space-y-4"
          >
            {tab === "overview" && (
              <Panel title="Overview" hint="Live counts straight from the learning DB. Every row here is something you can delete or veto.">
                <div className="grid gap-3 md:grid-cols-3">
                  {[
                    { key: "memory", title: "Memory loop", hint: "what do I know about my user", icon: Brain },
                    { key: "skill", title: "Skill loop", hint: "which capability should I use", icon: Sparkles },
                    { key: "learning", title: "Learning loop", hint: "did my decision work", icon: ThumbsUp },
                  ].map(({ key, title, hint, icon: Icon }) => (
                    <Card key={key}>
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="text-xs font-semibold text-foreground">{title}</p>
                          <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>
                        </div>
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
                          <Icon className="h-4 w-4" />
                        </span>
                      </div>
                      <p className="mt-3 text-2xl font-semibold tabular-nums text-foreground">{loopValue(overview?.loops?.[key])}</p>
                      <p className="mt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">entries</p>
                    </Card>
                  ))}
                </div>

                <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <Pill tone={overview?.enabled === false ? "danger" : "success"}>
                    learning {overview?.enabled === false ? "disabled" : "enabled"}
                  </Pill>
                  <Pill tone={overview?.settings?.proactive === true ? "accent" : "muted"}>
                    proactive {overview?.settings?.proactive === true ? "on" : "off"}
                  </Pill>
                  {overview?.db && <span className="truncate font-mono">db: {str(overview.db)}</span>}
                </div>

                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                  {[
                    ["lessons", "Lessons"],
                    ["active", "Active lessons"],
                    ["candidates_open", "Open candidates"],
                    ["feedback", "Feedback rows"],
                    ["mistakes", "Mistake groups"],
                    ["interactions", "Interactions"],
                    ["tasks", "Tasks"],
                    ["workflows", "Workflows"],
                    ["dataset", "Dataset rows"],
                    ["confirmed", "Confirmed lessons"],
                  ].map(([key, label]) => <Stat key={key} label={label} value={num(counts[key])} />)}
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                  <Card>
                    <p className="text-xs font-semibold text-foreground">Confidence buckets</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">Lessons per band (weak → confirmed). Boundaries are runtime settings, not config edits.</p>
                    <div className="mt-3 flex h-2.5 w-full overflow-hidden rounded-full bg-muted">
                      {bucketTotal > 0 ? (
                        BUCKETS.map((b) => {
                          const w = (num(buckets[b]) / bucketTotal) * 100;
                          return w > 0 ? (
                            <span key={b} className={cn("h-full", bucketFill(b))} style={{ width: `${w}%` }} title={`${b}: ${num(buckets[b])}`} />
                          ) : null;
                        })
                      ) : (
                        <span className="h-full w-full bg-muted-foreground/20" />
                      )}
                    </div>
                    <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1">
                      {BUCKETS.map((b) => (
                        <span key={b} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                          <span className={cn("h-2 w-2 rounded-full", bucketFill(b))} />
                          {b} <span className="font-semibold tabular-nums text-foreground">{num(buckets[b])}</span>
                        </span>
                      ))}
                    </div>
                  </Card>
                  <Card>
                    <p className="text-xs font-semibold text-foreground">Reward summary</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">Computed from the newest {activity.length} recorded turn(s).</p>
                    <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                      {[
                        ["scored turns", String(scored.length)],
                        ["avg reward", scored.length ? avgReward.toFixed(2) : "—"],
                        ["best", scored.length ? Math.max(...scored.map((a) => num(a.reward))).toFixed(2) : "—"],
                        ["lowest", scored.length ? Math.min(...scored.map((a) => num(a.reward))).toFixed(2) : "—"],
                      ].map(([label, value]) => (
                        <div key={label}>
                          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
                          <p className="text-lg font-semibold tabular-nums text-foreground">{value}</p>
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>

                <Card className="p-0">
                  <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                    <div>
                      <p className="text-xs font-semibold text-foreground">Recent activity</p>
                      <p className="text-[11px] text-muted-foreground">Newest agent turns first.</p>
                    </div>
                    <button type="button" className={btnQuiet} onClick={() => setShowAllActivity((v) => !v)}>
                      {showAllActivity ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                      {showAllActivity ? "Show fewer" : `Show all (${activity.length})`}
                    </button>
                  </div>
                  {activity.length === 0 ? (
                    <p className="px-4 pb-4 text-xs text-muted-foreground">No interactions recorded yet.</p>
                  ) : (
                    activity.slice(0, showAllActivity ? 20 : 6).map((a, i) => (
                      <div key={a.id ?? i} className="flex items-center gap-3 border-t border-border/60 px-4 py-2 text-xs first:border-t-0">
                        <span className="w-24 shrink-0 tabular-nums text-[11px] text-muted-foreground">{fmtTs(a.ts)}</span>
                        <span className="min-w-0 flex-1 truncate text-foreground/90">{a.request || "(no request)"}</span>
                        {a.skill && <Pill tone="accent">{a.skill}</Pill>}
                        {a.status && <span className={cn("shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] uppercase", statusTone(a.status))}>{a.status}</span>}
                        <span className="w-12 shrink-0 text-right tabular-nums text-[11px] text-muted-foreground">
                          {typeof a.reward === "number" ? a.reward.toFixed(2) : "—"}
                        </span>
                      </div>
                    ))
                  )}
                </Card>
              </Panel>
            )}
            {tab === "lessons" && (
              <Panel
                title="Lessons"
                hint="What ISHTI learned from your feedback: rules, preferences and corrections with confidence and evidence."
                action={
                  <span className="text-[11px] text-muted-foreground">
                    {filteredLessons.length} of {lessons.length} shown
                  </span>
                }
              >
                <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-border bg-card/50 p-3 shadow-xs">
                  <select
                    value={lessonStatus}
                    onChange={(e) => setLessonStatus(e.target.value)}
                    className="cursor-pointer rounded-xl border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                  >
                    <option value="">All statuses</option>
                    {lessonStatuses.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <select
                    value={lessonKind}
                    onChange={(e) => setLessonKind(e.target.value)}
                    className="cursor-pointer rounded-xl border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                  >
                    <option value="">All kinds</option>
                    {lessonKinds.map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                  <div className="relative min-w-[220px] flex-1">
                    <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <input
                      type="text"
                      value={lessonQuery}
                      onChange={(e) => setLessonQuery(e.target.value)}
                      placeholder="Search statements, scope, source, corrections..."
                      className={cn(inputCls, "pl-9")}
                    />
                  </div>
                </div>

                {filteredLessons.length === 0 ? (
                  <Empty>
                    {lessons.length === 0
                      ? "No lessons recorded yet - they appear here as soon as ISHTI gets feedback and confirms patterns."
                      : "No lessons match the current filters."}
                  </Empty>
                ) : (
                  <div className="space-y-3">
                    {filteredLessons.map((l) => {
                      const id = l.lesson_id;
                      const isEditing = editing?.id === id;
                      const isOpen = !!expanded[id];
                      const armed = confirmKey === `lesson:${id}`;
                      const paused = str(l.status) === "paused";
                      return (
                        <Card key={id} className="space-y-2.5 p-3.5">
                          <div className="flex items-start gap-2.5">
                            <button
                              type="button"
                              onClick={() => setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))}
                              className="mt-0.5 shrink-0 cursor-pointer rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                              title={isOpen ? "Collapse details" : "Expand details"}
                            >
                              {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            </button>
                            <div className="min-w-0 flex-1">
                              {isEditing ? (
                                <div className="space-y-2">
                                  <textarea
                                    rows={2}
                                    value={editing.statement}
                                    onChange={(e) => setEditing({ ...editing, statement: e.target.value })}
                                    className={inputCls}
                                    placeholder="statement"
                                  />
                                  <div className="flex flex-wrap gap-2">
                                    <input
                                      value={editing.scope}
                                      onChange={(e) => setEditing({ ...editing, scope: e.target.value })}
                                      className={cn(inputCls, "w-44")}
                                      placeholder="scope"
                                    />
                                    <input
                                      type="number"
                                      min="0"
                                      max="1"
                                      step="0.05"
                                      value={editing.confidence}
                                      onChange={(e) => setEditing({ ...editing, confidence: e.target.value })}
                                      className={cn(inputCls, "w-32")}
                                      placeholder="confidence"
                                    />
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <button type="button" className={btnPrimary} onClick={saveLesson} disabled={busy === `lesson:save:${id}`}>
                                      <Check className="h-3 w-3" /> Save
                                    </button>
                                    <button type="button" className={btnQuiet} onClick={() => setEditing(null)}>
                                      <X className="h-3 w-3" /> Cancel
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <>
                                  <p className="text-sm text-foreground">{l.statement || "(no statement)"}</p>
                                  {(l.incorrect || l.correct) && (
                                    <p className="mt-1 text-xs">
                                      {l.incorrect && <span className="text-red-500 line-through decoration-red-500/40">{l.incorrect}</span>}
                                      {l.incorrect && l.correct && <span className="mx-1.5 text-muted-foreground">→</span>}
                                      {l.correct && <span className="text-emerald-500">{l.correct}</span>}
                                    </p>
                                  )}
                                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                    {l.kind && <Pill tone="accent">{l.kind}</Pill>}
                                    {l.scope && <Pill tone="muted">scope: {l.scope}</Pill>}
                                    {l.context && <Pill tone="muted">{l.context}</Pill>}
                                    {l.status && (
                                      <span className={cn("rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide", statusTone(l.status))}>
                                        {l.status}
                                      </span>
                                    )}
                                    {l.source && <Pill tone="outline">{l.source}</Pill>}
                                  </div>
                                </>
                              )}
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center justify-between gap-2 pl-7">
                            <ConfBar confidence={num(l.confidence)} bucket={l.bucket} />
                            <div className="flex items-center gap-1">
                              <button type="button" className={btnQuiet} disabled={busy !== ""} onClick={() => lessonAction(id, "confirm")} title="Confirm this lesson">
                                <Check className="h-3 w-3" /> Confirm
                              </button>
                              <button type="button" className={btnQuiet} disabled={busy !== ""} onClick={() => lessonAction(id, "reject")} title="Reject this lesson">
                                <X className="h-3 w-3" /> Reject
                              </button>
                              <button
                                type="button"
                                className={btnQuiet}
                                disabled={busy !== ""}
                                onClick={() => lessonAction(id, paused ? "resume" : "pause")}
                                title={paused ? "Resume (status=active)" : "Pause (status=paused)"}
                              >
                                <RefreshCw className="h-3 w-3" /> {paused ? "Resume" : "Pause"}
                              </button>
                              <button
                                type="button"
                                className={btnQuiet}
                                disabled={busy !== ""}
                                onClick={() => setEditing({ id, statement: str(l.statement), scope: str(l.scope), confidence: String(num(l.confidence)) })}
                                title="Edit statement, scope and confidence"
                              >
                                <Pencil className="h-3 w-3" /> Edit
                              </button>
                              <button
                                type="button"
                                className={armed ? btnDanger : btnQuiet}
                                disabled={busy !== ""}
                                onClick={() => (armed ? deleteLesson(id) : setConfirmKey(`lesson:${id}`))}
                                title="Delete lesson (also forgets its memory write-back)"
                              >
                                <Trash2 className="h-3 w-3" /> {armed ? "Sure?" : "Delete"}
                              </button>
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pl-7 text-[11px] text-muted-foreground">
                            <span className="inline-flex items-center gap-1">
                              <Database className="h-3 w-3" /> evidence {num(l.evidence)} (+{num(l.positive)} / −{num(l.negative)})
                            </span>
                            <span>occasions {num(l.occasions, 1)}</span>
                            <span>updated {fmtTs(l.updated ?? l.created)}</span>
                            {l.entity && <span>entity: {str(l.entity)}</span>}
                          </div>
                          {isOpen && (
                            <div className="grid gap-1 rounded-xl border border-border/60 bg-muted/30 p-2.5 text-[11px] text-muted-foreground sm:grid-cols-2">
                              <span>lesson_id: <span className="font-mono text-foreground/80">{id}</span></span>
                              <span>created: {fmtTs(l.created)}</span>
                              <span>priority: {num(l.priority)}</span>
                              <span>action: {str(l.action, "—")}</span>
                              <span>confirmed: {l.confirmed ? "yes" : "no"}</span>
                              <span>expires: {l.expires ? fmtTs(l.expires) : "never"}</span>
                              {l.note && <span className="sm:col-span-2">note: {str(l.note)}</span>}
                            </div>
                          )}
                        </Card>
                      );
                    })}
                  </div>
                )}
              </Panel>
            )}
            {tab === "reflections" && (
              <>
                <Panel
                  title="Reflections"
                  hint="One sentence per turn that carried a signal - what went wrong, and the correction (Reflexion)."
                  action={<span className="text-[11px] text-muted-foreground">{refl.list.filter((r) => (r.kind_of ?? "reflection") === "reflection").length} written</span>}
                >
                  {refl.list.filter((r) => (r.kind_of ?? "reflection") === "reflection").length === 0 ? (
                    <Empty>No reflections yet - they appear once a turn is corrected or gets a clear thumbs up/down.</Empty>
                  ) : (
                    <div className="grid gap-2">
                      {refl.list
                        .filter((r) => (r.kind_of ?? "reflection") === "reflection")
                        .map((r) => (
                          <Card key={r.lesson_id} className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-xs text-foreground">{r.statement}</p>
                              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                                <span>{fmtTs(r.updated)}</span>
                                <span>{str(r.context, "general")}</span>
                                <span>{str(r.status, "active")}</span>
                              </div>
                            </div>
                            <ConfBar confidence={num(r.confidence)} bucket={r.bucket} />
                          </Card>
                        ))}
                    </div>
                  )}
                </Panel>
                <Panel
                  title="Mined insights"
                  hint="Rules pulled out of past interactions (ExpeL) - kept weak until evidence or you confirm them."
                  action={
                    <button
                      type="button"
                      className={btnPrimary}
                      disabled={busy === "insights:mine"}
                      onClick={() =>
                        void run(
                          "insights:mine",
                          async () => {
                            const res = await postJSON<{ promoted?: Reflection[] }>("/api/learning/reflections/mine", {});
                            await load(false);
                            return res;
                          },
                          "Insights promoted to lessons.",
                        )
                      }
                    >
                      <RefreshCw className={cn("h-3 w-3", busy === "insights:mine" && "animate-spin")} />
                      Mine now
                    </button>
                  }
                >
                  {refl.list.filter((r) => r.kind_of === "insight").length === 0 && refl.mined.length === 0 ? (
                    <Empty>Nothing to promote yet - ISHTI needs a few runs that read as a rule, not a log line.</Empty>
                  ) : (
                    <div className="grid gap-2">
                      {refl.list
                        .filter((r) => r.kind_of === "insight")
                        .map((r) => (
                          <Card key={r.lesson_id} className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-xs text-foreground">{r.statement}</p>
                              <p className="mt-1 truncate text-[10px] text-muted-foreground">{str(r.note, "mined insight")}</p>
                            </div>
                            <ConfBar confidence={num(r.confidence)} bucket={r.bucket} />
                          </Card>
                        ))}
                      {refl.mined.map((m, i) => (
                        <Card key={`${m.provenance?.interaction_id ?? "pending"}-${i}`} className="border-dashed">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-xs text-muted-foreground">{m.statement}</p>
                              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                                <span className="text-amber-500">pending · not promoted</span>
                                {m.context && <span>{m.context}</span>}
                                {m.provenance?.request && <span className="truncate">from: {m.provenance.request}</span>}
                              </div>
                            </div>
                          </div>
                        </Card>
                      ))}
                    </div>
                  )}
                </Panel>
              </>
            )}
            {tab === "candidates" && (
              <Panel
                title="Candidates"
                hint="Patterns ISHTI noticed and wants your yes/no on before they become lessons."
                action={<span className="text-[11px] text-muted-foreground">{candidates.length} open</span>}
              >
                {candidates.length === 0 ? (
                  <Empty>No open candidates - repeated patterns show up here once they cross the evidence minimum.</Empty>
                ) : (
                  <div className="space-y-3">
                    {candidates.map((c) => (
                      <Card key={c.id} className="space-y-2 p-3.5">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-sm text-foreground">{c.question || c.signature || `Candidate #${c.id}`}</p>
                            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                              {c.kind && <Pill tone="accent">{c.kind}</Pill>}
                              {c.status && (
                                <span className={cn("rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide", statusTone(c.status))}>
                                  {c.status}
                                </span>
                              )}
                              {c.source && <Pill tone="outline">{c.source}</Pill>}
                              <Pill tone="muted">{fmtTs(c.ts)}</Pill>
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <button type="button" className={btnPrimary} disabled={busy !== ""} onClick={() => answerCandidate(c.id, true)}>
                              <Check className="h-3 w-3" /> Keep
                            </button>
                            <button type="button" className={btnQuiet} disabled={busy !== ""} onClick={() => answerCandidate(c.id, false)}>
                              <X className="h-3 w-3" /> Discard
                            </button>
                          </div>
                        </div>
                        {c.payload && Object.keys(c.payload).length > 0 && (
                          <pre className="scroll-thin max-h-28 overflow-auto rounded-lg bg-muted/40 p-2 text-[10px] leading-relaxed text-muted-foreground">
                            {JSON.stringify(c.payload, null, 1)}
                          </pre>
                        )}
                      </Card>
                    ))}
                  </div>
                )}
              </Panel>
            )}
            {tab === "feedback" && (
              <Panel
                title="Corrections & feedback"
                hint="Every correction, praise or error class ISHTI picked up - newest first."
                action={<span className="text-[11px] text-muted-foreground">{feedback.length} rows</span>}
              >
                {feedback.length === 0 ? (
                  <Empty>No feedback recorded yet.</Empty>
                ) : (
                  <Card className="overflow-hidden p-0">
                    <div className="scroll-thin max-h-[560px] overflow-y-auto">
                      <table className="w-full text-left text-xs">
                        <thead className="sticky top-0 bg-background">
                          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                            <th className="px-3 py-2 font-medium">Time</th>
                            <th className="px-3 py-2 font-medium">Kind</th>
                            <th className="px-3 py-2 font-medium">Error</th>
                            <th className="px-3 py-2 font-medium">Incorrect → correct</th>
                            <th className="px-3 py-2 font-medium">Source</th>
                          </tr>
                        </thead>
                        <tbody>
                          {feedback.map((f) => (
                            <tr key={f.id} className="border-t border-border/60 align-top">
                              <td className="whitespace-nowrap px-3 py-2 tabular-nums text-muted-foreground">{fmtTs(f.ts)}</td>
                              <td className="px-3 py-2">
                                <span className="inline-flex items-center gap-1.5">
                                  {str(f.polarity) === "negative"
                                    ? <ThumbsDown className="h-3 w-3 text-red-500" />
                                    : <ThumbsUp className="h-3 w-3 text-emerald-500" />}
                                  <Pill tone={str(f.polarity) === "negative" ? "danger" : "success"}>{f.kind || f.polarity || "feedback"}</Pill>
                                </span>
                              </td>
                              <td className="px-3 py-2 text-muted-foreground">{str(f.error_type, "—")}</td>
                              <td className="px-3 py-2">
                                {f.incorrect && <span className="text-red-500 line-through decoration-red-500/40">{f.incorrect}</span>}
                                {f.incorrect && f.correct && <span className="mx-1 text-muted-foreground">→</span>}
                                {f.correct && <span className="text-emerald-500">{f.correct}</span>}
                                {!f.incorrect && !f.correct && <span className="text-foreground/80">{f.message || f.target || "—"}</span>}
                              </td>
                              <td className="px-3 py-2 text-muted-foreground">
                                {str(f.source, "—")}{f.applied ? " · applied" : ""}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                )}
              </Panel>
            )}
            {tab === "mistakes" && (
              <Panel
                title="Repeated mistakes"
                hint={`Failures grouped by signature. Rows with count ≥ ${ruleMin} (the candidate_rule evidence minimum) are rule candidates.`}
                action={<span className="text-[11px] text-muted-foreground">{mistakes.length} groups</span>}
              >
                {mistakes.length === 0 ? (
                  <Empty>No repeated mistakes recorded.</Empty>
                ) : (
                  <Card className="overflow-hidden p-0">
                    <div className="scroll-thin max-h-[560px] overflow-y-auto">
                      <table className="w-full text-left text-xs">
                        <thead className="sticky top-0 bg-background">
                          <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                            <th className="px-3 py-2 font-medium">Error type</th>
                            <th className="px-3 py-2 font-medium">Wrong → right</th>
                            <th className="px-3 py-2 font-medium">Count</th>
                            <th className="px-3 py-2 font-medium">Domain</th>
                            <th className="px-3 py-2 font-medium">Last seen</th>
                          </tr>
                        </thead>
                        <tbody>
                          {mistakes.map((m, i) => {
                            const hot = num(m.count) >= ruleMin;
                            return (
                              <tr key={m.signature ?? i} className={cn("border-t border-border/60 align-top", hot && "bg-accent-soft/50")}>
                                <td className="px-3 py-2">
                                  <span className="inline-flex flex-wrap items-center gap-1.5">
                                    <Pill tone={hot ? "accent" : "muted"}>{str(m.error_type, "unclassified")}</Pill>
                                    {hot && <Pill tone="accent">rule candidate</Pill>}
                                  </span>
                                </td>
                                <td className="px-3 py-2">
                                  {m.wrong && <span className="text-red-500 line-through decoration-red-500/40">{m.wrong}</span>}
                                  {m.wrong && m.right && <span className="mx-1 text-muted-foreground">→</span>}
                                  {m.right && <span className="text-emerald-500">{m.right}</span>}
                                  {!m.wrong && !m.right && <span className="text-muted-foreground">—</span>}
                                </td>
                                <td className="px-3 py-2 font-semibold tabular-nums text-foreground">{num(m.count)}</td>
                                <td className="px-3 py-2 text-muted-foreground">{str(m.domain, "—")}</td>
                                <td className="whitespace-nowrap px-3 py-2 tabular-nums text-muted-foreground">{fmtTs(m.last_ts)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                )}
              </Panel>
            )}
            {tab === "people" && (
              <Panel
                title="People & Entities"
                hint="Disambiguated people and contacts. Entities with matching names remain distinct rows."
                action={<span className="text-[11px] text-muted-foreground">{people.length} entities</span>}
              >
                {people.length === 0 ? (
                  <Empty>No entities tracked yet.</Empty>
                ) : (
                  <Card className="overflow-hidden p-0">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-background">
                        <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          <th className="px-3 py-2 font-medium">Name</th>
                          <th className="px-3 py-2 font-medium">Org / Rel</th>
                          <th className="px-3 py-2 font-medium">Contact</th>
                          <th className="px-3 py-2 font-medium">Mentions</th>
                          <th className="px-3 py-2 font-medium">Last seen</th>
                        </tr>
                      </thead>
                      <tbody>
                        {people.map((p, i) => (
                          <tr key={p.entity ?? i} className="border-t border-border/60">
                            <td className="px-3 py-2 font-medium text-foreground">{p.entity}</td>
                            <td className="px-3 py-2 text-muted-foreground">
                              {[p.org, p.relationship].filter(Boolean).join(" Â· ") || "â€”"}
                            </td>
                            <td className="px-3 py-2 text-muted-foreground">
                              {[p.email, p.phone].filter(Boolean).join(" / ") || "â€”"}
                            </td>
                            <td className="px-3 py-2 tabular-nums text-foreground">{num(p.mentions, 1)}</td>
                            <td className="px-3 py-2 tabular-nums text-muted-foreground">{fmtTs(p.last_seen)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Card>
                )}
              </Panel>
            )}
            {tab === "workflows" && (
              <Panel
                title="Automated Workflows"
                hint="Mined multi-step procedures from repeated successful task executions."
                action={<span className="text-[11px] text-muted-foreground">{workflows.length} workflows</span>}
              >
                {workflows.length === 0 ? (
                  <Empty>No automated workflows detected yet.</Empty>
                ) : (
                  <div className="grid gap-3">
                    {workflows.map((wf) => (
                      <Card key={wf.id} className="flex items-center justify-between gap-3 p-3">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-foreground text-xs">{wf.name || "Procedure"}</span>
                            <Pill tone={wf.status === "active" ? "success" : "muted"}>{wf.status || "active"}</Pill>
                            <span className="text-[11px] text-muted-foreground">Ran {num(wf.count)} times</span>
                          </div>
                          <p className="text-[11px] text-muted-foreground font-mono">
                            {Array.isArray(wf.steps) ? wf.steps.join(" -> ") : "â€”"}
                          </p>
                        </div>
                        <button
                          type="button"
                          className={wf.status === "active" ? btnDanger : btnQuiet}
                          onClick={() => workflowStatus(wf.id, wf.status === "active" ? "retired" : "active")}
                        >
                          {wf.status === "active" ? "Retire" : "Activate"}
                        </button>
                      </Card>
                    ))}
                  </div>
                )}
              </Panel>
            )}

            {tab === "training" && (
              <Panel
                title="Offline Fine-Tuning Dataset"
                hint="Curated instruction pairs for offline LoRA/QLoRA jobs. Separate from working memory."
                action={
                  <div className="flex items-center gap-2">
                    <button type="button" className={btnPrimary} onClick={exportDataset}>
                      <Download className="h-3 w-3" /> Export JSONL
                    </button>
                  </div>
                }
              >
                <div className="grid gap-3 sm:grid-cols-3">
                  <Stat label="Total Examples" value={num(dstats.total)} />
                  <Stat label="High Quality" value={num(dstats.by_quality?.high)} />
                  <Stat label="Exported" value={num(dstats.exported)} />
                </div>
                <Card className="space-y-3">
                  <p className="text-xs font-semibold text-foreground">Add Training Example</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <input
                      className={inputCls}
                      placeholder="Instruction"
                      value={form.instruction}
                      onChange={(e) => setForm((f) => ({ ...f, instruction: e.target.value }))}
                    />
                    <input
                      className={inputCls}
                      placeholder="Correct response"
                      value={form.correct}
                      onChange={(e) => setForm((f) => ({ ...f, correct: e.target.value }))}
                    />
                  </div>
                  <div className="flex justify-end">
                    <button type="button" className={btnPrimary} onClick={addExample}>
                      Add example
                    </button>
                  </div>
                </Card>
                {dataset.length === 0 ? (
                  <Empty>No training examples in database.</Empty>
                ) : (
                  <Card className="overflow-hidden p-0">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-background">
                        <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                          <th className="px-3 py-2 font-medium">Instruction</th>
                          <th className="px-3 py-2 font-medium">Correct response</th>
                          <th className="px-3 py-2 font-medium">Quality</th>
                          <th className="px-3 py-2 font-medium">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dataset.map((d) => (
                          <tr key={d.id} className="border-t border-border/60">
                            <td className="px-3 py-2 text-foreground">{d.instruction}</td>
                            <td className="px-3 py-2 text-muted-foreground">{d.correct || "â€”"}</td>
                            <td className="px-3 py-2 tabular-nums text-foreground">{num(d.quality).toFixed(2)}</td>
                            <td className="px-3 py-2">
                              <button type="button" className={btnDanger} onClick={() => deleteExample(d.id)}>
                                <Trash2 className="h-3 w-3" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Card>
                )}
              </Panel>
            )}

            {tab === "settings" && draft && (
              <Panel
                title="Learning Subsystem Settings"
                hint="Runtime overrides saved in SQLite. System config.json remains untouched."
                action={
                  <button type="button" className={btnPrimary} onClick={saveSettings}>
                    Save overrides
                  </button>
                }
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Card className="space-y-3">
                    <p className="text-xs font-semibold text-foreground">Switches</p>
                    <label className="flex items-center gap-2 text-xs text-foreground">
                      <input
                        type="checkbox"
                        checked={draft.enabled}
                        onChange={(e) => setDraft((d) => d && ({ ...d, enabled: e.target.checked }))}
                      />
                      Learning subsystem enabled
                    </label>
                    <label className="flex items-center gap-2 text-xs text-foreground">
                      <input
                        type="checkbox"
                        checked={draft.proactive}
                        onChange={(e) => setDraft((d) => d && ({ ...d, proactive: e.target.checked }))}
                      />
                      Proactive suggestions & candidate questions enabled
                    </label>
                  </Card>
                  <Card className="space-y-3">
                    <p className="text-xs font-semibold text-foreground">Confidence Thresholds</p>
                    {THRESHOLD_KEYS.map((k) => (
                      <div key={k} className="space-y-1">
                        <label className="flex justify-between text-[11px] text-muted-foreground">
                          <span className="capitalize">{k}</span>
                          <span className="text-[10px]">{THRESHOLD_HINTS[k]}</span>
                        </label>
                        <input
                          className={inputCls}
                          type="number"
                          step="0.05"
                          value={draft.thresholds[k] ?? ""}
                          onChange={(e) => {
                            const val = e.target.value;
                            setDraft((d) => d && ({ ...d, thresholds: { ...d.thresholds, [k]: val } }));
                          }}
                        />
                      </div>
                    ))}
                  </Card>
                  <Card className="space-y-3 sm:col-span-2">
                    <p className="text-xs font-semibold text-foreground">Evidence Minimum Counts</p>
                    <div className="grid gap-3 sm:grid-cols-2">
                      {EVIDENCE_KEYS.map((k) => (
                        <div key={k} className="space-y-1">
                          <label className="flex justify-between text-[11px] text-muted-foreground">
                            <span>{k}</span>
                            <span className="text-[10px]">{EVIDENCE_HINTS[k]}</span>
                          </label>
                          <input
                            className={inputCls}
                            type="number"
                            step="1"
                            value={draft.evidence[k] ?? ""}
                            onChange={(e) => {
                              const val = e.target.value;
                              setDraft((d) => d && ({ ...d, evidence: { ...d.evidence, [k]: val } }));
                            }}
                          />
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>
              </Panel>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </Page>
  );
}




