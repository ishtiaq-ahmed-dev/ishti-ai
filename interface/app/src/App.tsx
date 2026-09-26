import { AnimatePresence, motion } from "motion/react";
import { Bell, Bot, Brain, Cpu, ListChecks, MessageSquare, Settings as SettingsIcon, FolderKanban } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getJSON } from "@/lib/api";
import { Chat } from "@/sections/Chat";
import { Memory, Settings, Skills, System, Tasks } from "@/sections/Pages";
import { NotificationBell } from "@/components/rare/notification-bell";

const NAV = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "tasks", label: "Tasks", icon: ListChecks },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "skills", label: "Skills", icon: Bot },
  { key: "system", label: "System", icon: Cpu },
  { key: "settings", label: "Settings", icon: SettingsIcon },
] as const;
type Key = (typeof NAV)[number]["key"];

type Fact = { entity: string; attr: string; value: string };

export default function App() {
  const [page, setPage] = useState<Key>("chat");
  const [owner, setOwner] = useState("there");
  const [projects, setProjects] = useState<Fact[]>([]);
  const [alerts, setAlerts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
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
    return () => ws.close();
  }, [busy]);

  return (
    <div className="flex h-full p-3">
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-3xl border border-border bg-background shadow-[0_8px_40px_rgba(29,27,46,0.08)]">
        <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-card/60 px-3 py-4">
          <div className="mb-5 flex items-center justify-between px-2">
            <div className="flex items-center gap-2.5">
              <div className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-violet-400 to-indigo-600 text-sm font-bold text-white">
                {owner[0]?.toUpperCase()}</div>
              <div><div className="text-sm font-semibold leading-tight">{owner === "there" ? "Owner" : owner}</div>
                <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className={`h-1.5 w-1.5 rounded-full ${busy ? "animate-pulse bg-accent" : "bg-emerald-500"}`} />
                  {busy ? "ISHTI is working" : "Online"}</div></div>
            </div>
            <button onClick={() => { setPage("tasks"); setAlerts([]); }} title="Reminders">
              <NotificationBell count={alerts.length} size={18} color="violet" />
            </button>
          </div>

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

          <div className="mt-6 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">My projects</div>
          <div className="mt-1 space-y-0.5">
            {projects.length ? projects.map((p, i) => (
              <div key={p.entity} title={p.value} className="flex items-center gap-2 truncate rounded-lg px-3 py-1.5 text-sm text-muted-foreground">
                <FolderKanban className="h-3.5 w-3.5 shrink-0" style={{ color: ["#6e56cf", "#2f6fdd", "#1f9d6b", "#d98a1c"][i % 4] }} />
                <span className="truncate capitalize">{p.entity}</span></div>
            )) : <div className="px-3 py-1 text-xs text-muted-foreground">Tell me “Project X is at D:\…”</div>}
          </div>

          <div className="mt-auto rounded-2xl bg-gradient-to-br from-violet-500 to-indigo-600 p-3 text-white">
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
              {page === "chat" && <Chat owner={owner} onBusy={onBusy} />}
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
