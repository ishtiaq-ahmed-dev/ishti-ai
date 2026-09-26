import { motion } from "motion/react";
import { Play, RefreshCw } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { TaskList, type Task } from "@/components/rare/task-list";
import { DeleteButton } from "@/components/rare/delete-button";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { ThemeSwitch } from "@/components/ThemeToggle";

export function Page({
  title,
  subtitle,
  action,
  children,
  className = "max-w-[1560px]",
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className={`mx-auto w-full px-4 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-7 ${className}`}>
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
            {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
          </div>
          {action}
        </div>
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
          {children}
        </motion.div>
      </div>
    </div>
  );
}

const Card = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`rounded-2xl border border-border bg-background p-4 ${className}`}>{children}</div>
);

// ---------------------------------------------------------------- Tasks (reminders & timers)
export function Tasks() {
  const [items, setItems] = useState<{ id: number; due: number; text: string }[]>([]);
  const load = () => getJSON<{ reminders: typeof items }>("/api/reminders").then((d) => setItems(d.reminders));
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  const tasks: Task[] = items.map((r) => ({
    id: String(r.id),
    label: `${new Date(r.due * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })} · ${r.text}`,
  }));
  return (
    <Page title="Tasks" subtitle="Reminders and timers. Tick one to cancel it. Add more from chat: “remind me at 5pm to …”.">
      <Card>
        {tasks.length ? (
          <TaskList tasks={tasks} accent="#6e56cf" onTasksChange={(next) => {
            next.filter((t) => t.done).forEach((t) => postJSON(`/api/reminders/${t.id}/done`).then(load));
          }} />
        ) : <p className="text-sm text-muted-foreground">Nothing scheduled.</p>}
      </Card>
    </Page>
  );
}

// ---------------------------------------------------------------- Memory (exact facts)
type Fact = { entity: string; attr: string; value: string; previous?: string; updated_at: number };
export function Memory() {
  const [facts, setFacts] = useState<Fact[]>([]);
  const load = () => getJSON<{ facts: Fact[] }>("/api/facts").then((d) => setFacts(d.facts));
  useEffect(() => { load(); }, []);
  const groups = facts.reduce<Record<string, Fact[]>>((acc, f) => ((acc[f.entity] ??= []).push(f), acc), {});
  return (
    <Page title="Memory" subtitle="Exact facts ISHTI looks up before anything else. Updated in place, never duplicated.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Object.entries(groups).map(([entity, rows], i) => (
          <motion.div key={entity} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}>
            <Card>
              <div className="mb-2 flex items-center justify-between">
                <span className="font-semibold capitalize">{entity}</span>
                {entity !== "owner" && <DeleteButton onConfirm={() => del(`/api/facts/${encodeURIComponent(entity)}`).then(load)} />}
              </div>
              <dl className="space-y-1 text-sm">
                {rows.map((f) => (
                  <div key={f.attr} className="flex items-start gap-2 min-w-0">
                    <dt className="w-24 shrink-0 text-muted-foreground">{f.attr}</dt>
                    <dd className="min-w-0 flex-1 break-words">{f.value}
                      {f.previous && <span className="ml-1 text-[11px] text-muted-foreground line-through">{f.previous}</span>}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          </motion.div>
        ))}
      </div>
    </Page>
  );
}

// ---------------------------------------------------------------- Skills
type SkillInfo = { name: string; version: string; description: string; route: string; status: string; problem?: string };
export function Skills() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  useEffect(() => { getJSON<{ skills: SkillInfo[] }>("/api/skills").then((d) => setSkills(d.skills)); }, []);
  const tone = (s: string) => s === "working" ? "bg-emerald-50 text-emerald-700" : s === "broken" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700";
  return (
    <Page title="Skills" subtitle="Each skill is a folder with a manifest; a broken one is skipped without affecting the rest.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {skills.map((s, i) => (
          <motion.div key={s.name} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.025 }}>
            <Card className="h-full flex flex-col justify-between">
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2 min-w-0">
                  <span className="font-semibold truncate">{s.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0 ${tone(s.status)}`}>{s.status} · v{s.version}</span>
                </div>
                <p className="text-sm text-muted-foreground break-words">{s.description}</p>
              </div>
              {s.problem && <p className="mt-2 text-xs text-danger break-words">{s.problem}</p>}
            </Card>
          </motion.div>
        ))}
      </div>
    </Page>
  );
}

// ---------------------------------------------------------------- System
type Telemetry = { cpu_percent: number; ram_percent: number; ram_used_gb: number; ram_total_gb: number;
                   gpu?: { name?: string; used_vram_mb?: number; total_vram_mb?: number; temp_c?: number } | string;
                   disks?: Record<string, { free_gb: number; total_gb: number }> };
type Check = { test: string; status: string; details: string };

function Stat({ label, value, unit, sub }: { label: string; value: number; unit: string; sub?: string }) {
  return (
    <Card>
      <div className="text-xs text-muted-foreground truncate">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums truncate">
        <AnimatedCounter value={Math.round(value)} />{unit}
      </div>
      {sub && <div className="text-[11px] text-muted-foreground truncate" title={sub}>{sub}</div>}
    </Card>
  );
}

export function System() {
  const [t, setT] = useState<Telemetry | null>(null);
  const [activity, setActivity] = useState<{ days: { date: string; count: number }[]; skills: { skill: string; count: number }[] }>();
  const [doctor, setDoctor] = useState<Record<string, Check[]> | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    const load = () => getJSON<Telemetry>("/api/system").then(setT);
    load(); const id = setInterval(load, 3000);
    getJSON<typeof activity>("/api/activity").then(setActivity);
    return () => clearInterval(id);
  }, []);
  const gpu = typeof t?.gpu === "object" ? t.gpu : undefined;
  const max = Math.max(1, ...(activity?.skills.map((s) => s.count) ?? [1]));
  return (
    <Page title="System" subtitle="This PC, live, and what ISHTI has been doing."
      action={<button disabled={running} onClick={async () => { setRunning(true); setDoctor((await getJSON<{ checks: Record<string, Check[]> }>("/api/doctor")).checks); setRunning(false); }}
        className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
        {running ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run diagnostics</button>}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="CPU" value={t?.cpu_percent ?? 0} unit="%" />
        <Stat label="RAM" value={t?.ram_percent ?? 0} unit="%" sub={t ? `${t.ram_used_gb} / ${t.ram_total_gb} GB` : ""} />
        <Stat label="VRAM" value={gpu?.used_vram_mb ? (gpu.used_vram_mb / (gpu.total_vram_mb || 1)) * 100 : 0} unit="%"
          sub={gpu ? `${gpu.used_vram_mb} / ${gpu.total_vram_mb} MB` : ""} />
        <Stat label="GPU temp" value={gpu?.temp_c ?? 0} unit="°C" sub={gpu?.name} />
      </div>
      <Card className="mt-3">
        <div className="mb-2 text-sm font-semibold">Requests by skill</div>
        <div className="space-y-1.5">
          {activity?.skills.map((s) => (
            <div key={s.skill} className="flex items-center gap-2 text-xs">
              <span className="w-24 truncate text-muted-foreground">{s.skill}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                <motion.div className="h-full rounded-full bg-accent" initial={{ width: 0 }} animate={{ width: `${(s.count / max) * 100}%` }} transition={{ duration: 0.5 }} />
              </div>
              <span className="w-10 text-right tabular-nums">{s.count}</span>
            </div>
          ))}
        </div>
      </Card>
      {doctor && (
        <Card className="mt-3">
          <div className="mb-2 text-sm font-semibold">Diagnostics</div>
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {Object.values(doctor).flat().map((c, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className={c.status === "PASS" ? "text-success" : c.status === "WARN" ? "text-warning" : "text-danger"}>●</span>
                <span className="font-medium">{c.test}</span><span className="truncate text-muted-foreground">{c.details}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- Settings
type Profile = { key: string; profile: string; purpose: string; connected: boolean };

/** The owner's own Chrome profiles ISHTI may drive (via the Playwright Extension). Tokens stay on this PC. */
function ChromeProfiles() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => { getJSON<{ profiles: Profile[] }>("/api/chrome_profiles").then((d) => setProfiles(d.profiles)); }, []);
  const save = (key: string, token: string) =>
    postJSON<{ profiles: Profile[] }>("/api/chrome_profiles", { key, token }).then((d) => { setProfiles(d.profiles); setDraft({}); });
  return (
    <Card>
      <div className="font-medium">Chrome profiles</div>
      <div className="mb-3 text-xs text-muted-foreground">
        ISHTI only ever uses these two profiles of your Chrome. In each one, install the{" "}
        <a className="text-accent" href="https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"
          target="_blank" rel="noreferrer">Playwright Extension</a>, open it, copy its token and paste it here.
        Until then ISHTI uses its own separate browser (no logins). Sending messages or submitting forms always asks you first.
      </div>
      <div className="space-y-2">
        {profiles.map((p) => (
          <div key={p.key} className="flex flex-wrap items-center gap-2 rounded-xl border border-border p-2.5">
            <span className={`h-2 w-2 rounded-full ${p.connected ? "bg-success" : "bg-muted-foreground/40"}`} />
            <div className="min-w-[160px] flex-1">
              <div className="text-sm font-medium">{p.profile} · {p.key === "whatsapp" ? "WhatsApp" : "Search & logins"}</div>
              <div className="text-[11px] text-muted-foreground">{p.connected ? "Connected" : "Not connected"}</div>
            </div>
            <input type="password" placeholder={p.connected ? "Replace token…" : "Paste token…"} value={draft[p.key] ?? ""}
              onChange={(e) => setDraft({ ...draft, [p.key]: e.target.value })}
              className="w-56 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs outline-none focus:border-accent" />
            <button disabled={!draft[p.key]} onClick={() => save(p.key, draft[p.key])}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40">Save</button>
            {p.connected && (
              <button onClick={() => save(p.key, "")} className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted">
                Disconnect</button>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

export function Settings() {
  const [p, setP] = useState<{ audio_output: boolean; wake_enabled: boolean; wake_model: string; models: Record<string, string> }>();
  useEffect(() => { getJSON<typeof p>("/api/prefs").then(setP); }, []);
  if (!p) return null;
  return (
    <Page title="Settings" subtitle="Everything runs on this PC. Models are set in ishti/config.json." className="max-w-4xl">
      <div className="space-y-3">
        <Card className="flex items-center justify-between">
          <div><div className="font-medium">Appearance</div><div className="text-xs text-muted-foreground">Switch between pure white light mode and pure black dark mode.</div></div>
          <ThemeSwitch />
        </Card>
        <Card className="flex items-center justify-between">
          <div><div className="font-medium">Read answers aloud</div><div className="text-xs text-muted-foreground">Piper voice, local. Same as saying “mute” / “read that”.</div></div>
          <button onClick={() => postJSON<typeof p>("/api/prefs", { audio_output: !p.audio_output }).then(setP)}
            className={`relative h-6 w-11 rounded-full transition-colors ${p.audio_output ? "bg-accent" : "bg-muted"}`} aria-pressed={p.audio_output}>
            <motion.span layout transition={{ type: "spring", stiffness: 600, damping: 32 }}
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow ${p.audio_output ? "right-0.5" : "left-0.5"}`} />
          </button>
        </Card>
        <ChromeProfiles />
        <Card><div className="font-medium">Hands-free</div>
          <div className="text-xs text-muted-foreground">Wake word “{p.wake_model.replace("_", " ")}” is {p.wake_enabled ? "on" : "off"} (needs a microphone).</div></Card>
        <Card><div className="mb-1 font-medium">Models</div>
          {Object.entries(p.models).map(([role, m]) => <div key={role} className="text-sm"><span className="inline-block w-20 text-muted-foreground">{role}</span>{m}</div>)}</Card>
        <Card><div className="font-medium">Credits</div>
          <div className="text-xs text-muted-foreground">Animated components from <a className="text-accent" href="https://rareui.com" target="_blank" rel="noreferrer">Rare UI</a>. See THIRD_PARTY_NOTICES.md.</div></Card>
      </div>
    </Page>
  );
}
