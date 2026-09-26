import { motion } from "motion/react";
import { AudioLines, Brain, CheckCheck, GitBranch, MessageSquareText, Route, Sparkles, Wrench } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { getJSON } from "@/lib/api";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { Page } from "@/sections/Pages";
import { AgentOrb } from "@/components/AgentOrb";
import type { OrbState } from "@/lib/agent-state";

type Overview = {
  requests: number; by_source: Record<string, number>; by_skill: Record<string, number>;
  laya: { loaded: boolean; error: string; model: string }; models: Record<string, string>;
  voice: Record<string, string>; reminders: number; memories: number;
  skills: { name: string; version: string; route: string; status: string; uses: number; problem?: string }[];
  owner: { attr: string; value: string }[]; facts: { entity: string; attr: string; value: string }[];
  prefs: { audio_output: boolean; response_length: string };
};

function Stage({ icon, orbState, title, lines, count, label, i }:
  { icon: ReactNode; orbState?: OrbState; title: string; lines: string[]; count?: number; label?: string; i: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04, duration: 0.25 }}
      className="group relative flex min-w-0 flex-1 flex-col gap-2 rounded-2xl border border-border bg-card/40 p-4 shadow-xs hover:border-accent/40 transition-colors">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent overflow-hidden">
            {orbState ? <AgentOrb state={orbState} size={20} className="shrink-0" /> : icon}
          </span>
          <span className="text-sm font-semibold tracking-tight truncate">{title}</span>
        </div>
        <span className="rounded-md bg-muted px-2 py-0.5 text-[11px] font-mono font-medium text-muted-foreground shrink-0">
          0{i + 1}
        </span>
      </div>
      <div className="mt-0.5 space-y-1">
        {lines.map((l) => (
          <div key={l} className="text-xs text-muted-foreground leading-snug line-clamp-2" title={l}>
            {l}
          </div>
        ))}
      </div>
      {count !== undefined && (
        <div className="mt-auto pt-2 text-lg sm:text-xl font-semibold tabular-nums text-foreground">
          <AnimatedCounter value={count} /> <span className="text-xs font-normal text-muted-foreground">{label}</span>
        </div>
      )}
    </motion.div>
  );
}

export function Overview({ onToggleNav }: { onToggleNav?: () => void }) {
  const [o, setO] = useState<Overview>();
  useEffect(() => { const load = () => getJSON<Overview>("/api/overview").then(setO); load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  if (!o) return null;
  const src = (k: string) => o.by_source[k] ?? 0;
  const max = Math.max(1, ...o.skills.map((s) => s.uses));
  const stages = [
    { icon: <MessageSquareText className="h-4 w-4" />, orbState: "listening" as OrbState, title: "Input", lines: ["Text in this app", `Voice · whisper ${o.voice.whisper_model}`, `Hands-free · "${o.voice.wake_model.replace("_", " ")}"`], count: o.requests, label: "requests" },
    { icon: <Sparkles className="h-4 w-4" />, orbState: "solving" as OrbState, title: "Understand", lines: ["Follow-ups rewritten with the chat", "\"this year\" → real dates"], count: undefined },
    { icon: <Route className="h-4 w-4" />, orbState: "working" as OrbState, title: "Route", lines: ["Skill patterns first", `Laya ${o.laya.model} ${o.laya.loaded ? "· loaded" : ""}`], count: src("rule") + src("laya"), label: `${src("rule")} rule · ${src("laya")} Laya` },
    { icon: <GitBranch className="h-4 w-4" />, orbState: "weaving" as OrbState, title: "Plan", lines: ["Several tasks → steps", "Answers carried between steps"], count: src("plan"), label: "plan steps" },
    { icon: <Wrench className="h-4 w-4" />, orbState: "shaping" as OrbState, title: "Skills", lines: [`${o.skills.length} skills`, `${o.skills.filter((s) => s.status !== "broken").length} working`], count: o.skills.length, label: "skills" },
    { icon: <Brain className="h-4 w-4" />, orbState: "connecting" as OrbState, title: "Tools & memory", lines: [`LLM ${o.models.planner}`, "SearXNG · Playwright MCP · FFmpeg", `${o.facts.length + o.owner.length} facts · ${o.memories} memories`] },
    { icon: <CheckCheck className="h-4 w-4" />, orbState: "solving" as OrbState, title: "Verify", lines: ["Answer check by the local model", "Falls short → next skill"], count: src("escalation"), label: "escalations" },
    { icon: <AudioLines className="h-4 w-4" />, orbState: "composing" as OrbState, title: "Answer", lines: ["Streamed with its reasoning", `Voice ${o.prefs.audio_output ? "on" : "off"} · ${o.voice.piper_voice}`] },
  ];

  return (
    <Page title="Overview" subtitle="How ISHTI works, what it can do, and what it knows about you - live." onToggleNav={onToggleNav}>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Pipeline</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {stages.map((s, i) => (
          <div key={s.title} className="flex min-w-0">
            <Stage {...s} i={i} />
          </div>
        ))}
      </div>

      <div className="mt-7 grid gap-6 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Skills</div>
          <div className="overflow-hidden rounded-2xl border border-border bg-card/40 shadow-xs">
            {o.skills.sort((a, b) => b.uses - a.uses).map((s, i) => (
              <motion.div key={s.name} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.02 }}
                className="grid grid-cols-[130px_1fr_100px] sm:grid-cols-[150px_1fr_120px] items-center gap-3 border-b border-border px-4 py-2.5 last:border-0 hover:bg-muted/30 transition-colors">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${s.status === "broken" ? "bg-danger" : s.status === "working" ? "bg-success" : "bg-warning"}`} />
                  <span className="truncate">{s.name}</span>
                </div>
                <div className="truncate text-xs text-muted-foreground" title={s.problem || s.route}>
                  {s.problem || s.route}
                </div>
                <div className="flex items-center gap-2">
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <motion.div className="h-full rounded-full bg-accent" initial={{ width: 0 }} animate={{ width: `${(s.uses / max) * 100}%` }} transition={{ duration: 0.5 }} />
                  </div>
                  <span className="w-8 text-right text-xs tabular-nums text-muted-foreground">{s.uses}</span>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        <div className="space-y-4 lg:col-span-5">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">You</div>
            <div className="rounded-2xl border border-border bg-card/40 p-4 text-sm shadow-xs">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {o.owner.map((f) => (
                  <div key={f.attr} className="flex items-baseline gap-2">
                    <span className="w-16 shrink-0 capitalize text-xs text-muted-foreground">{f.attr}</span>
                    <span className="font-medium text-foreground truncate">{f.value}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2.5 border-t border-border pt-3 text-xs text-muted-foreground">
                <span><b className="text-foreground">{o.reminders}</b> reminders</span>
                <span>•</span>
                <span><b className="text-foreground">{o.memories}</b> memories</span>
                <span>•</span>
                <span>answers: <b className="text-foreground">{o.prefs.response_length}</b></span>
              </div>
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Saved facts & projects</div>
            <div className="scroll-thin max-h-72 space-y-2 overflow-y-auto rounded-2xl border border-border bg-card/40 p-4 text-sm shadow-xs">
              {o.facts.length ? o.facts.map((f, i) => (
                <div key={i} className="flex items-center gap-2.5 text-xs sm:text-sm py-0.5">
                  <span className="min-w-[110px] shrink-0 font-medium capitalize text-foreground truncate">{f.entity}</span>
                  <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 text-[11px] font-mono text-muted-foreground">{f.attr}</span>
                  <span className="truncate text-muted-foreground" title={f.value}>{f.value}</span>
                </div>
              )) : <div className="text-xs text-muted-foreground">Nothing saved yet.</div>}
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Models</div>
            <div className="rounded-2xl border border-border bg-card/40 p-4 text-sm shadow-xs">
              <div className="space-y-2">
                {Object.entries(o.models).map(([role, m]) => (
                  <div key={role} className="flex items-center justify-between text-xs sm:text-sm">
                    <span className="w-20 uppercase tracking-wider text-xs text-muted-foreground">{role}</span>
                    <span className="font-mono text-foreground font-medium truncate">{m}</span>
                  </div>
                ))}
                <div className="flex items-center justify-between border-t border-border pt-2 text-xs sm:text-sm">
                  <span className="w-20 uppercase tracking-wider text-xs text-muted-foreground">router</span>
                  <span className="font-mono text-foreground font-medium">Laya {o.laya.model}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Page>
  );
}

export default Overview;
