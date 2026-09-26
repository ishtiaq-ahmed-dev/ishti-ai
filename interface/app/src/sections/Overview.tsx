import { motion } from "motion/react";
import { ArrowRight, AudioLines, Brain, CheckCheck, GitBranch, MessageSquareText, Route, Sparkles, Wrench } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { getJSON } from "@/lib/api";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { Page } from "@/sections/Pages";

type Overview = {
  requests: number; by_source: Record<string, number>; by_skill: Record<string, number>;
  laya: { loaded: boolean; error: string; model: string }; models: Record<string, string>;
  voice: Record<string, string>; reminders: number; memories: number;
  skills: { name: string; version: string; route: string; status: string; uses: number; problem?: string }[];
  owner: { attr: string; value: string }[]; facts: { entity: string; attr: string; value: string }[];
  prefs: { audio_output: boolean; response_length: string };
};

function Stage({ icon, title, lines, count, label, i }:
  { icon: ReactNode; title: string; lines: string[]; count?: number; label?: string; i: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05, duration: 0.25 }}
      className="relative flex min-w-0 flex-col gap-1.5 rounded-2xl border border-border bg-background p-3">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent-soft text-accent">{icon}</span>
        <span className="text-sm font-semibold">{title}</span>
      </div>
      {lines.map((l) => <div key={l} className="truncate text-[11px] text-muted-foreground">{l}</div>)}
      {count !== undefined && (
        <div className="mt-auto pt-1 text-lg font-semibold tabular-nums">
          <AnimatedCounter value={count} /> <span className="text-[11px] font-normal text-muted-foreground">{label}</span>
        </div>
      )}
    </motion.div>
  );
}

export function Overview() {
  const [o, setO] = useState<Overview>();
  useEffect(() => { const load = () => getJSON<Overview>("/api/overview").then(setO); load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  if (!o) return null;
  const src = (k: string) => o.by_source[k] ?? 0;
  const max = Math.max(1, ...o.skills.map((s) => s.uses));
  const stages = [
    { icon: <MessageSquareText className="h-4 w-4" />, title: "Input", lines: ["Text in this app", `Voice · whisper ${o.voice.whisper_model}`, `Hands-free · "${o.voice.wake_model.replace("_", " ")}"`], count: o.requests, label: "requests" },
    { icon: <Sparkles className="h-4 w-4" />, title: "Understand", lines: ["Follow-ups rewritten with the chat", "\"this year\" → real dates"], count: undefined },
    { icon: <Route className="h-4 w-4" />, title: "Route", lines: ["Skill patterns first", `Laya ${o.laya.model} ${o.laya.loaded ? "· loaded" : ""}`], count: src("rule") + src("laya"), label: `${src("rule")} rule · ${src("laya")} Laya` },
    { icon: <GitBranch className="h-4 w-4" />, title: "Plan", lines: ["Several tasks → steps", "Answers carried between steps"], count: src("plan"), label: "plan steps" },
    { icon: <Wrench className="h-4 w-4" />, title: "Skills", lines: [`${o.skills.length} skills`, `${o.skills.filter((s) => s.status !== "broken").length} working`], count: o.skills.length, label: "skills" },
    { icon: <Brain className="h-4 w-4" />, title: "Tools & memory", lines: [`LLM ${o.models.planner}`, "SearXNG · Playwright MCP · FFmpeg", `${o.facts.length + o.owner.length} facts · ${o.memories} memories`] },
    { icon: <CheckCheck className="h-4 w-4" />, title: "Verify", lines: ["Answer check by the local model", "Falls short → next skill"], count: src("escalation"), label: "escalations" },
    { icon: <AudioLines className="h-4 w-4" />, title: "Answer", lines: ["Streamed with its reasoning", `Voice ${o.prefs.audio_output ? "on" : "off"} · ${o.voice.piper_voice}`] },
  ];
  return (
    <Page title="Overview" subtitle="How ISHTI works, what it can do, and what it knows about you - live.">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Pipeline</div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {stages.map((s, i) => (
          <div key={s.title} className="relative flex">
            <Stage {...s} i={i} />
            {i % 4 !== 3 && <ArrowRight className="absolute -right-2.5 top-1/2 z-10 hidden h-4 w-4 -translate-y-1/2 text-accent md:block" />}
          </div>
        ))}
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Skills</div>
          <div className="overflow-hidden rounded-2xl border border-border">
            {o.skills.sort((a, b) => b.uses - a.uses).map((s, i) => (
              <motion.div key={s.name} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.02 }}
                className="grid grid-cols-[110px_1fr_110px] items-center gap-3 border-b border-border px-3 py-2 last:border-0">
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  <span className={`h-1.5 w-1.5 rounded-full ${s.status === "broken" ? "bg-danger" : s.status === "working" ? "bg-success" : "bg-warning"}`} />
                  {s.name}</div>
                <div className="truncate text-xs text-muted-foreground" title={s.problem || s.route}>{s.problem || s.route}</div>
                <div className="flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                    <motion.div className="h-full rounded-full bg-accent" initial={{ width: 0 }} animate={{ width: `${(s.uses / max) * 100}%` }} transition={{ duration: 0.5 }} />
                  </div>
                  <span className="w-8 text-right text-xs tabular-nums text-muted-foreground">{s.uses}</span>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        <div className="space-y-4 lg:col-span-2">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">You</div>
            <div className="rounded-2xl border border-border p-3 text-sm">
              {o.owner.map((f) => <div key={f.attr} className="flex gap-2"><span className="w-20 capitalize text-muted-foreground">{f.attr}</span>{f.value}</div>)}
              <div className="mt-2 flex gap-3 border-t border-border pt-2 text-xs text-muted-foreground">
                <span><b className="text-foreground">{o.reminders}</b> reminders</span>
                <span><b className="text-foreground">{o.memories}</b> memories</span>
                <span>answers: <b className="text-foreground">{o.prefs.response_length}</b></span>
              </div>
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Saved facts & projects</div>
            <div className="scroll-thin max-h-64 space-y-1 overflow-y-auto rounded-2xl border border-border p-3 text-sm">
              {o.facts.length ? o.facts.map((f, i) => (
                <div key={i} className="flex gap-2"><span className="w-28 shrink-0 truncate font-medium capitalize">{f.entity}</span>
                  <span className="shrink-0 text-muted-foreground">{f.attr}</span><span className="truncate">{f.value}</span></div>
              )) : <div className="text-xs text-muted-foreground">Nothing saved yet.</div>}
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Models</div>
            <div className="rounded-2xl border border-border p-3 text-sm">
              {Object.entries(o.models).map(([role, m]) => <div key={role} className="flex gap-2"><span className="w-20 text-muted-foreground">{role}</span>{m}</div>)}
              <div className="flex gap-2"><span className="w-20 text-muted-foreground">router</span>Laya {o.laya.model}</div>
            </div>
          </div>
        </div>
      </div>
    </Page>
  );
}
