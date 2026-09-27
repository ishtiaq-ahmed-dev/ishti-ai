import { motion, AnimatePresence } from "motion/react";
import {
  AudioLines,
  Brain,
  CheckCheck,
  ChevronDown,
  Cpu,
  Database,
  GitBranch,
  MessageSquareText,
  Route,
  Search,
  Sparkles,
  User,
  Wrench,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { getJSON } from "@/lib/api";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { Page } from "@/sections/Pages";
import { AgentOrb } from "@/components/AgentOrb";
import { IshtiBrand } from "@/components/IshtiBrand";
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

function CollapsibleCard({
  title,
  icon,
  badge,
  preview,
  isOpen,
  onToggle,
  children,
  className = "",
}: {
  title: string;
  icon: ReactNode;
  badge?: ReactNode;
  preview?: string;
  isOpen: boolean;
  onToggle: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl border border-border bg-card/40 shadow-xs transition-colors hover:border-border/90 overflow-hidden ${className}`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 text-left hover:bg-muted/20 transition-colors cursor-pointer select-none group"
        aria-expanded={isOpen}
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent group-hover:scale-105 transition-transform">
            {icon}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-foreground tracking-tight">{title}</span>
              {badge}
            </div>
            {preview && !isOpen && (
              <p className="text-xs text-muted-foreground truncate mt-0.5" title={preview}>
                {preview}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-3">
          <span className="text-[11px] font-medium text-muted-foreground hidden sm:inline">
            {isOpen ? "Collapse" : "Expand"}
          </span>
          <span
            className={`grid h-7 w-7 place-items-center rounded-lg bg-muted text-muted-foreground transition-transform duration-200 ${
              isOpen ? "rotate-180" : ""
            }`}
          >
            <ChevronDown className="h-4 w-4" />
          </span>
        </div>
      </button>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeInOut" }}
            className="overflow-hidden border-t border-border"
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function Overview({ onToggleNav }: { onToggleNav?: () => void }) {
  const [o, setO] = useState<Overview>();
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    skills: false,
    you: false,
    facts: false,
    models: false,
  });
  const [skillSearch, setSkillSearch] = useState("");

  useEffect(() => {
    const load = () => getJSON<Overview>("/api/overview").then(setO);
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  const toggleSection = (key: string) => {
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleExpandAll = () => {
    setOpenSections({ skills: true, you: true, facts: true, models: true });
  };

  const handleCollapseAll = () => {
    setOpenSections({ skills: false, you: false, facts: false, models: false });
  };

  if (!o) {
    return (
      <Page
        title="Overview"
        subtitle="How ISHTI works, what it can do, and what it knows about you - live."
        onToggleNav={onToggleNav}
      >
        <div className="space-y-6">
          <div className="h-20 w-full rounded-2xl border border-border bg-card/40 animate-pulse" />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-36 rounded-2xl border border-border bg-card/40 p-4 animate-pulse flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <div className="h-8 w-8 rounded-xl bg-muted" />
                  <div className="h-4 w-6 rounded bg-muted" />
                </div>
                <div className="space-y-2">
                  <div className="h-3 w-3/4 rounded bg-muted" />
                  <div className="h-2.5 w-1/2 rounded bg-muted" />
                </div>
                <div className="h-4 w-1/3 rounded bg-muted" />
              </div>
            ))}
          </div>
        </div>
      </Page>
    );
  }

  const src = (k: string) => o.by_source[k] ?? 0;
  const max = Math.max(1, ...o.skills.map((s) => s.uses));
  const stages = [
    { icon: <MessageSquareText className="h-4 w-4" />, orbState: "listening" as OrbState, title: "Input", lines: ["Text in this app", `Voice · whisper ${o.voice.whisper_model}`, `Hands-free · "hey ishti"`], count: o.requests, label: "requests" },
    { icon: <Sparkles className="h-4 w-4" />, orbState: "solving" as OrbState, title: "Understand", lines: ["Follow-ups rewritten with the chat", "\"this year\" → real dates"], count: undefined },
    { icon: <Route className="h-4 w-4" />, orbState: "working" as OrbState, title: "Route", lines: ["Skill patterns first", `Laya ${o.laya.model} ${o.laya.loaded ? "· loaded" : ""}`], count: src("rule") + src("laya"), label: `${src("rule")} rule · ${src("laya")} Laya` },
    { icon: <GitBranch className="h-4 w-4" />, orbState: "weaving" as OrbState, title: "Plan", lines: ["Several tasks → steps", "Answers carried between steps"], count: src("plan"), label: "plan steps" },
    { icon: <Wrench className="h-4 w-4" />, orbState: "shaping" as OrbState, title: "Skills", lines: [`${o.skills.length} skills`, `${o.skills.filter((s) => s.status !== "broken").length} working`], count: o.skills.length, label: "skills" },
    { icon: <Brain className="h-4 w-4" />, orbState: "connecting" as OrbState, title: "Tools & memory", lines: [`LLM ${o.models.planner}`, "SearXNG · Playwright MCP · FFmpeg", `${o.facts.length + o.owner.length} facts · ${o.memories} memories`] },
    { icon: <CheckCheck className="h-4 w-4" />, orbState: "solving" as OrbState, title: "Verify", lines: ["Answer check by the local model", "Falls short → next skill"], count: src("escalation"), label: "escalations" },
    { icon: <AudioLines className="h-4 w-4" />, orbState: "composing" as OrbState, title: "Answer", lines: ["Streamed with its reasoning", `Voice ${o.prefs.audio_output ? "on" : "off"} · ${o.voice.piper_voice}`] },
  ];

  const handleOpenNewChat = () => {
    try {
      localStorage.setItem("ishti-page", "chat");
      window.location.hash = "#chat";
      window.dispatchEvent(new CustomEvent("ishti-start-new-chat"));
    } catch {}
  };

  const filteredSkills = o.skills
    .filter(
      (s) =>
        s.name.toLowerCase().includes(skillSearch.toLowerCase()) ||
        (s.route && s.route.toLowerCase().includes(skillSearch.toLowerCase())) ||
        (s.problem && s.problem.toLowerCase().includes(skillSearch.toLowerCase()))
    )
    .sort((a, b) => b.uses - a.uses);

  const ownerName = o.owner.find((f) => f.attr.toLowerCase().includes("name"))?.value || "Profile";

  return (
    <Page
      title="Overview"
      subtitle="How ISHTI works, what it can do, and what it knows about you - live."
      onToggleNav={onToggleNav}
      action={
        <button
          type="button"
          onClick={handleOpenNewChat}
          className="flex items-center gap-2 rounded-xl border border-border bg-card px-3.5 py-1.5 text-xs font-semibold text-foreground hover:border-accent hover:text-accent transition-all cursor-pointer shadow-xs"
        >
          <IshtiBrand variant="icon" size={18} />
          <span>New Chat</span>
        </button>
      }
    >
      {/* Brand Overview Banner - Clicking logo opens Chat and starts a new chat */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border bg-card/60 p-4 shadow-xs">
        <div
          role="button"
          tabIndex={0}
          onClick={handleOpenNewChat}
          title="Click to open Chat and start a new conversation"
          className="flex items-center gap-3 cursor-pointer group select-none"
        >
          <IshtiBrand
            variant="lockup"
            className="group-hover:opacity-90 active:scale-95 transition-all"
          />
          <div className="hidden sm:block border-l border-border/80 pl-3">
            <div className="text-xs font-semibold text-foreground group-hover:text-accent transition-colors">
              Personal AI Assistant Neural Core
            </div>
            <div className="text-[11px] text-muted-foreground">
              Click anywhere on this logo to open dashboard & start a new chat
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={handleOpenNewChat}
          className="flex items-center gap-1.5 rounded-xl bg-accent px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:opacity-90 transition-all cursor-pointer"
        >
          <span>Open Main Chat</span>
          <span className="font-mono text-[11px] opacity-80">→</span>
        </button>
      </div>

      {/* Pipeline - Always visible / uncollapsed as requested */}
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Pipeline</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {stages.map((s, i) => (
          <div key={s.title} className="flex min-w-0">
            <Stage {...s} i={i} />
          </div>
        ))}
      </div>

      {/* Collapsible Section Controls */}
      <div className="mt-8 mb-3 flex items-center justify-between flex-wrap gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          System Details & Status
        </div>
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={handleExpandAll}
            className="font-medium text-muted-foreground hover:text-foreground hover:underline transition-colors cursor-pointer"
          >
            Expand all
          </button>
          <span className="text-muted-foreground/40">•</span>
          <button
            type="button"
            onClick={handleCollapseAll}
            className="font-medium text-muted-foreground hover:text-foreground hover:underline transition-colors cursor-pointer"
          >
            Collapse all
          </button>
        </div>
      </div>

      {/* Collapsible Grid */}
      <div className="grid gap-5 lg:grid-cols-12 items-start">
        {/* Left Column: Skills */}
        <div className="lg:col-span-7">
          <CollapsibleCard
            title="Skills"
            icon={<Wrench className="h-4 w-4" />}
            isOpen={openSections.skills}
            onToggle={() => toggleSection("skills")}
            badge={
              <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-accent">
                {o.skills.length} skills · {o.skills.filter((s) => s.status !== "broken").length} working
              </span>
            }
            preview={`${o.skills.filter((s) => s.status !== "broken").length} working · Top: ${o.skills.slice(0, 3).map((s) => s.name).join(", ")}`}
          >
            <div className="p-3 border-b border-border bg-muted/20">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search skills by name, route..."
                  value={skillSearch}
                  onChange={(e) => setSkillSearch(e.target.value)}
                  className="w-full rounded-xl border border-border bg-background py-1.5 pl-8 pr-3 text-xs text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-hidden"
                />
              </div>
            </div>

            <div className="max-h-96 overflow-y-auto scroll-thin divide-y divide-border/60">
              {filteredSkills.map((s, i) => (
                <motion.div
                  key={s.name}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: i * 0.015 }}
                  className="grid grid-cols-[130px_1fr_100px] sm:grid-cols-[150px_1fr_120px] items-center gap-3 px-4 py-2.5 hover:bg-muted/30 transition-colors"
                >
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${
                        s.status === "broken"
                          ? "bg-danger"
                          : s.status === "working"
                          ? "bg-success"
                          : "bg-warning"
                      }`}
                    />
                    <span className="truncate">{s.name}</span>
                  </div>
                  <div className="truncate text-xs text-muted-foreground" title={s.problem || s.route}>
                    {s.problem || s.route}
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-accent"
                        style={{ width: `${(s.uses / max) * 100}%` }}
                      />
                    </div>
                    <span className="w-8 text-right text-xs tabular-nums text-muted-foreground">
                      {s.uses}
                    </span>
                  </div>
                </motion.div>
              ))}
              {filteredSkills.length === 0 && (
                <div className="p-6 text-center text-xs text-muted-foreground">
                  No skills matched "{skillSearch}".
                </div>
              )}
            </div>
          </CollapsibleCard>
        </div>

        {/* Right Column: You, Saved Facts, Models */}
        <div className="space-y-4 lg:col-span-5">
          {/* You */}
          <CollapsibleCard
            title="You"
            icon={<User className="h-4 w-4" />}
            isOpen={openSections.you}
            onToggle={() => toggleSection("you")}
            badge={
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {ownerName}
              </span>
            }
            preview={`${o.reminders} reminders · ${o.memories} memories · Length: ${o.prefs.response_length}`}
          >
            <div className="p-4 text-sm">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2.5">
                {o.owner.map((f) => (
                  <div key={f.attr} className="flex flex-col gap-0.5 min-w-0 overflow-hidden">
                    <span
                      className="capitalize text-[11px] font-medium text-muted-foreground truncate"
                      title={f.attr.replace(/_/g, " ")}
                    >
                      {f.attr.replace(/_/g, " ")}
                    </span>
                    <span className="font-semibold text-sm text-foreground truncate" title={f.value}>
                      {f.value}
                    </span>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2.5 border-t border-border pt-3 text-xs text-muted-foreground">
                <span>
                  <b className="text-foreground">{o.reminders}</b> reminders
                </span>
                <span>•</span>
                <span>
                  <b className="text-foreground">{o.memories}</b> memories
                </span>
                <span>•</span>
                <span>
                  answers: <b className="text-foreground">{o.prefs.response_length}</b>
                </span>
              </div>
            </div>
          </CollapsibleCard>

          {/* Saved Facts & Projects */}
          <CollapsibleCard
            title="Saved facts & projects"
            icon={<Database className="h-4 w-4" />}
            isOpen={openSections.facts}
            onToggle={() => toggleSection("facts")}
            badge={
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {o.facts.length} facts
              </span>
            }
            preview={
              o.facts.length
                ? `${o.facts.length} stored facts (${o.facts.slice(0, 2).map((f) => f.entity).join(", ")}...)`
                : "Nothing saved yet"
            }
          >
            <div className="scroll-thin max-h-72 space-y-2 overflow-y-auto p-4 text-sm">
              {o.facts.length ? (
                o.facts.map((f, i) => (
                  <div key={i} className="flex items-center gap-2.5 text-xs sm:text-sm py-0.5">
                    <span className="min-w-[110px] shrink-0 font-medium capitalize text-foreground truncate">
                      {f.entity}
                    </span>
                    <span className="shrink-0 rounded-md bg-muted px-2 py-0.5 text-[11px] font-mono text-muted-foreground">
                      {f.attr}
                    </span>
                    <span className="truncate text-muted-foreground" title={f.value}>
                      {f.value}
                    </span>
                  </div>
                ))
              ) : (
                <div className="text-xs text-muted-foreground">Nothing saved yet.</div>
              )}
            </div>
          </CollapsibleCard>

          {/* Models */}
          <CollapsibleCard
            title="Models"
            icon={<Cpu className="h-4 w-4" />}
            isOpen={openSections.models}
            onToggle={() => toggleSection("models")}
            badge={
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {Object.keys(o.models).length + 1} roles
              </span>
            }
            preview={`Planner: ${o.models.planner || "configured"} · Router: Laya ${o.laya.model}`}
          >
            <div className="p-4 text-sm">
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
          </CollapsibleCard>
        </div>
      </div>
    </Page>
  );
}

export default Overview;
