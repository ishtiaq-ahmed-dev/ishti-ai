import { AnimatePresence, motion } from "motion/react";
import { Bell, Bot, Brain, Cpu, LayoutDashboard, ListChecks, MessageSquare, Settings as SettingsIcon, FolderKanban, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { del, getJSON } from "@/lib/api";
import { Chat } from "@/sections/Chat";
import { Memory, Settings, Skills, System, Tasks } from "@/sections/Pages";
import { Overview } from "@/sections/Overview";
import { NotificationBell } from "@/components/rare/notification-bell";
import { ThemeToggle } from "@/components/ThemeToggle";

const NAV = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "tasks", label: "Tasks", icon: ListChecks },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "skills", label: "Skills", icon: Bot },
  { key: "system", label: "System", icon: Cpu },
  { key: "settings", label: "Settings", icon: SettingsIcon },
] as const;
type Key = (typeof NAV)[number]["key"];

type Fact = { entity: string; attr: string; value: string };
type Session = { session_id: string; title: string; updated_at: string; message_count: number };

function ago(iso: string) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "now" : m < 60 ? `${m}m` : m < 1440 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`;
}

export default function App() {
  const [page, setPage] = useState<Key>("chat");
  const [owner, setOwner] = useState("there");
  const [projects, setProjects] = useState<Fact[]>([]);
  const [alerts, setAlerts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [openSession, setOpenSession] = useState<{ id: string; n: number } | null>(null);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const loadSessions = useCallback(() => getJSON<{ sessions: Session[] }>("/api/sessions")
    .then((d) => setSessions(d.sessions.filter((x) => x.message_count > 0).slice(0, 12))), []);
  const onBusy = useCallback((b: boolean) => setBusy(b), []);

  useEffect(() => {
    getJSON<{ facts: Fact[] }>("/api/facts").then(({ facts }) => {
      setOwner(facts.find((f) => f.entity === "owner" && f.attr === "name")?.value.split(" ")[0] ?? "there");
      setProjects(facts.filter((f) => f.attr === "location" && f.entity !== "owner").slice(0, 6));
    });
    // reminders and hands-free voice replies arrive over the websocket
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "assistant_message" && String(msg.data?.reply ?? "").startsWith("⏰")) {
        setAlerts((a) => [msg.data.reply, ...a].slice(0, 20));
        if (Notification?.permission === "granted") new Notification("ISHTI", { body: msg.data.reply });
      }
    };
    if (typeof Notification !== "undefined" && Notification.permission === "default") Notification.requestPermission();
    loadSessions();
    return () => ws.close();
  }, [busy, loadSessions]);

  return (
    <div className="flex h-full p-3 bg-canvas text-foreground transition-colors duration-200">
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-3xl border border-border bg-background shadow-[0_8px_40px_rgba(0,0,0,0.06)] dark:shadow-[0_8px_40px_rgba(0,0,0,0.8)]">
        <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-card/75 px-3 py-4">
          <div className="mb-5 flex items-center justify-between px-2">
            <div className="flex items-center gap-2.5">
              <div className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-violet-400 to-indigo-600 text-sm font-bold text-white">
                {owner[0]?.toUpperCase()}</div>
              <div><div className="text-sm font-semibold leading-tight">{owner === "there" ? "Owner" : owner}</div>
                <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className={`h-1.5 w-1.5 rounded-full ${busy ? "animate-pulse bg-accent" : "bg-emerald-500"}`} />
                  {busy ? "ISHTI is working" : "Online"}</div></div>
            </div>
            <div className="flex items-center gap-1.5">
              <ThemeToggle size={34} />
              <NotificationBell
                count={alerts.length}
                size={34}
                color="violet"
                onClick={() => { setPage("tasks"); setAlerts([]); }}
                title={alerts.length > 0 ? `${alerts.length} reminder${alerts.length > 1 ? "s" : ""}` : "Reminders"}
              />
            </div>
          </div>

          <div className="scroll-thin flex min-h-0 flex-1 flex-col overflow-y-auto pr-1">
            <nav className="space-y-0.5">
              {NAV.map(({ key, label, icon: Icon }) => (
                <button key={key} onClick={() => setPage(key)}
                  className={`relative flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-colors ${page === key ? "text-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                  {page === key && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-xl bg-accent-soft"
                    transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                  <Icon className={`relative h-4 w-4 ${page === key ? "text-accent" : ""}`} />
                  <span className="relative font-medium">{label}</span>
                </button>
              ))}
            </nav>

            <div className="mt-5 flex items-center justify-between px-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              <span>Recent</span>
              <button onClick={() => { setPage("chat"); setOpenSession({ id: "", n: Date.now() }); }}
                className="rounded-md px-1.5 py-0.5 normal-case tracking-normal text-accent hover:bg-accent-soft">+ New</button>
            </div>
            <div className="mt-1 space-y-0.5">
              {sessions.length ? sessions.map((x) => (
                <div key={x.session_id}
                  className={`group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-1.5 text-sm transition-colors ${activeSession === x.session_id && page === "chat" ? "bg-accent-soft text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}
                  onClick={() => { setPage("chat"); setOpenSession({ id: x.session_id, n: Date.now() }); }}>
                  <span className="min-w-0 flex-1 truncate">{x.title}</span>
                  <span className="text-[10px] opacity-60 group-hover:hidden">{ago(x.updated_at)}</span>
                  <button title="Delete chat" className="hidden rounded p-0.5 hover:bg-background group-hover:block"
                    onClick={(e) => { e.stopPropagation(); del(`/api/sessions/${x.session_id}`).then(loadSessions); }}>
                    <X className="h-3 w-3" /></button>
                </div>
              )) : <div className="px-3 py-1 text-xs text-muted-foreground">Your chats appear here.</div>}
            </div>

            <div className="mt-5 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">My projects</div>
            <div className="mt-1 space-y-0.5">
              {projects.length ? projects.map((p, i) => (
                <div key={p.entity} title={p.value} className="flex items-center gap-2 truncate rounded-lg px-3 py-1.5 text-sm text-muted-foreground">
                  <FolderKanban className="h-3.5 w-3.5 shrink-0" style={{ color: ["#6e56cf", "#2f6fdd", "#1f9d6b", "#d98a1c"][i % 4] }} />
                  <span className="truncate capitalize">{p.entity}</span></div>
              )) : <div className="px-3 py-1 text-xs text-muted-foreground">Tell me “Project X is at D:\…”</div>}
            </div>
          </div>

          <div className="mt-3 shrink-0 rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 p-3 text-white">
            <div className="flex items-center gap-1.5 text-sm font-semibold"><Bell className="h-3.5 w-3.5" /> Local-first</div>
            <p className="mt-1 text-[11px] leading-snug text-white/85">Models, memory and voice run on this PC. Nothing leaves it.</p>
            <a href="https://rareui.com" target="_blank" rel="noreferrer" className="mt-2 inline-block text-[10px] text-white/70 underline-offset-2 hover:underline">
              UI components: Rare UI</a>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <AnimatePresence mode="wait">
            <motion.div key={page} className="h-full" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}>
              {page === "chat" && <Chat owner={owner} onBusy={onBusy} open={openSession}
                onSession={(id) => { setActiveSession(id); loadSessions(); }} />}
              {page === "overview" && <Overview />}
              {page === "tasks" && <Tasks />}
              {page === "memory" && <Memory />}
              {page === "skills" && <Skills />}
              {page === "system" && <System />}
              {page === "settings" && <Settings />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
