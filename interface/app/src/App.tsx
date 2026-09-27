import { AnimatePresence, motion } from "motion/react";
import { Bot, Brain, CandlestickChart, UserRound, Cpu, FolderKanban, LayoutDashboard, ListChecks, MessageSquare, PanelLeftClose, Power, Settings as SettingsIcon, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getJSON } from "@/lib/api";
import { Chat } from "@/sections/Chat";
import { Memory, Settings, Skills, System, Tasks } from "@/sections/Pages";
import { Overview } from "@/sections/Overview";
import { AboutYou } from "@/sections/AboutYou";
import { Markets } from "@/sections/Markets";
import { NotificationBell } from "@/components/rare/notification-bell";
import { ThemeToggle } from "@/components/ThemeToggle";
import { IshtiBrand } from "@/components/IshtiBrand";
import { initChatFontSize } from "@/lib/font-size";

const NAV = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "tasks", label: "Tasks", icon: ListChecks },
  { key: "markets", label: "Markets", icon: CandlestickChart },
  { key: "about", label: "About you", icon: UserRound },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "skills", label: "Skills", icon: Bot },
  { key: "system", label: "System", icon: Cpu },
  { key: "settings", label: "Settings", icon: SettingsIcon },
] as const;
type Key = (typeof NAV)[number]["key"];

type Fact = { entity: string; attr: string; value: string };

function parseHash(raw: string): Key | null {
  const clean = raw.replace(/^#\/?/, "").toLowerCase().split("/")[0].split("?")[0] as Key;
  return NAV.some((n) => n.key === clean) ? clean : null;
}

function getInitialPage(): Key {
  if (typeof window !== "undefined") {
    const fromHash = parseHash(window.location.hash);
    if (fromHash) return fromHash;
    try {
      const saved = localStorage.getItem("ishti-page") as Key;
      if (NAV.some((n) => n.key === saved)) return saved;
    } catch {}
  }
  return "chat";
}

export default function App() {
  const [page, setPageState] = useState<Key>(getInitialPage);
  const [owner, setOwner] = useState("Ishtiaq Ahmed");
  const [projects, setProjects] = useState<Fact[]>([]);
  const [alerts, setAlerts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [showNav, setShowNav] = useState(() => {
    try {
      const saved = localStorage.getItem("ishti-show-nav");
      if (saved !== null) return saved === "true";
    } catch {}
    return true;
  });

  const [showExitModal, setShowExitModal] = useState(false);
  const [isExiting, setIsExiting] = useState(false);

  const toggleNav = useCallback(() => {
    setShowNav((prev) => {
      const next = !prev;
      try { localStorage.setItem("ishti-show-nav", String(next)); } catch {}
      return next;
    });
  }, []);

  const handleConfirmExit = useCallback(async () => {
    setIsExiting(true);
    try {
      await fetch("/api/shutdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
    } catch {}
    setTimeout(() => {
      window.close();
    }, 1200);
  }, []);

  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!isExiting) {
        e.preventDefault();
        e.returnValue = "Do you want to exit ISHTI?";
        return "Do you want to exit ISHTI?";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isExiting]);

  const onBusy = useCallback((b: boolean) => {
    setBusy(b);
  }, []);

  useEffect(() => {
    initChatFontSize();
  }, []);

  const setPage = useCallback((next: Key) => {
    setPageState(next);
    try {
      localStorage.setItem("ishti-page", next);
      let targetHash = `#${next}`;
      if (next === "chat") {
        const savedSession = localStorage.getItem("ishti-session-id");
        if (savedSession) targetHash = `#chat/${savedSession}`;
      }
      if (window.location.hash !== targetHash) {
        window.history.replaceState(null, "", targetHash);
      }
    } catch {}
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const matched = parseHash(window.location.hash);
      if (matched) {
        setPageState(matched);
        try { localStorage.setItem("ishti-page", matched); } catch {}
      }
    };
    window.addEventListener("hashchange", onHashChange);
    if (!window.location.hash) {
      let targetHash = `#${page}`;
      if (page === "chat") {
        try {
          const s = localStorage.getItem("ishti-session-id");
          if (s) targetHash = `#chat/${s}`;
        } catch {}
      }
      window.history.replaceState(null, "", targetHash);
    }
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [page]);

  useEffect(() => {
    getJSON<{ facts: Fact[] }>("/api/facts")
      .then((data) => {
        const facts = data?.facts ?? [];
        setOwner(facts.find((f) => f.entity === "owner" && f.attr === "name")?.value.trim() ?? "Ishtiaq Ahmed");
        setProjects(facts.filter((f) => f.attr === "location" && f.entity !== "owner").slice(0, 6));
      })
      .catch((err) => console.warn("[App] Failed to load initial facts:", err));

    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;
    let isMounted = true;

    function connect() {
      if (!isMounted) return;
      try {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/ws`);

        ws.onopen = () => {
          backoff = 1000;
        };

        ws.onmessage = (m) => {
          try {
            const msg = JSON.parse(m.data);
            if (msg.type === "assistant_message" && String(msg.data?.reply ?? "").startsWith("⏰")) {
              setAlerts((a) => [msg.data.reply, ...a].slice(0, 20));
              if (typeof Notification !== "undefined" && Notification?.permission === "granted") {
                new Notification("ISHTI", { body: msg.data.reply });
              }
            }
          } catch (err) {
            console.warn("[App WS] Error parsing message:", err);
          }
        };

        ws.onclose = () => {
          if (!isMounted) return;
          reconnectTimeout = setTimeout(() => {
            backoff = Math.min(backoff * 1.5, 15000);
            connect();
          }, backoff);
        };

        ws.onerror = () => {
          if (ws) ws.close();
        };
      } catch (e) {
        console.warn("[App WS] Connection attempt failed:", e);
        if (isMounted) {
          reconnectTimeout = setTimeout(connect, 3000);
        }
      }
    }

    connect();

    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission();
    }

    return () => {
      isMounted = false;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, []);

  const handleLogoClick = useCallback(() => {
    setPage("chat");
    try {
      window.dispatchEvent(new CustomEvent("ishti-start-new-chat"));
    } catch {}
  }, [setPage]);

  return (
    <div className="flex h-full p-3 bg-canvas text-foreground transition-colors duration-200">
      <div className="relative flex min-h-0 flex-1 overflow-hidden rounded-3xl border border-border bg-background shadow-[0_8px_40px_rgba(0,0,0,0.06)] dark:shadow-[0_8px_40px_rgba(0,0,0,0.8)]">
        <AnimatePresence initial={false}>
          {showNav && (
            <motion.aside
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 240, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="flex h-full shrink-0 flex-col overflow-hidden border-r border-border bg-card/75 max-sm:absolute max-sm:inset-y-0 max-sm:left-0 max-sm:z-40 max-sm:bg-background/95 max-sm:backdrop-blur-md max-sm:shadow-2xl"
            >
              <div className="flex h-full w-[240px] flex-col min-h-0 px-3 py-4">
                {/* Official Brand Header */}
                <div className="mb-4 flex items-center justify-between px-1 pt-0.5">
                  <IshtiBrand
                    variant="lockup"
                    className="cursor-pointer hover:opacity-90 active:scale-[0.98] transition-all"
                    onClick={handleLogoClick}
                    title="ISHTI - Start New Chat"
                  />
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={toggleNav}
                      title="Hide navigation bar"
                      className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
                    >
                      <PanelLeftClose className="h-4 w-4" />
                    </button>
                    <ThemeToggle size={28} />
                    <NotificationBell
                      count={alerts.length}
                      size={28}
                      color="violet"
                      onClick={() => { setPage("tasks"); setAlerts([]); }}
                      title={alerts.length > 0 ? `${alerts.length} reminder${alerts.length > 1 ? "s" : ""}` : "Reminders"}
                    />
                  </div>
                </div>

                {/* User profile row */}
                <div className="mb-4 flex items-center gap-2.5 rounded-xl bg-muted/30 border border-border/40 px-2.5 py-2">
                  <div className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-violet-400 to-indigo-600 text-xs font-bold text-white shadow-xs shrink-0">
                    {owner.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "IA"}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold leading-tight truncate">{owner === "there" ? "Ishtiaq Ahmed" : owner}</div>
                    <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                      <span className={`h-1.5 w-1.5 rounded-full ${busy ? "animate-pulse bg-accent" : "bg-emerald-500"}`} />
                      {busy ? "ISHTI is working" : "Online"}</div>
                  </div>
                </div>

                <div className="scroll-thin flex min-h-0 flex-1 flex-col overflow-y-auto pr-1">
                  <nav className="space-y-0.5">
                    {NAV.map(({ key, label, icon: Icon }) => (
                      <button key={key} onClick={() => setPage(key)}
                        className={`relative flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-colors cursor-pointer ${page === key ? "text-foreground font-semibold" : "text-muted-foreground hover:text-foreground"}`}>
                        {page === key && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-xl bg-accent-soft"
                          transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                        <Icon className={`relative h-4 w-4 ${page === key ? "text-accent" : ""}`} />
                        <span className="relative font-medium">{label}</span>
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => setShowExitModal(true)}
                      className="relative flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-destructive/80 hover:text-destructive hover:bg-destructive/10 transition-colors cursor-pointer mt-1"
                      title="Shut down ISHTI and release all resources"
                    >
                      <Power className="relative h-4 w-4 text-destructive" />
                      <span className="relative font-medium">Exit ISHTI</span>
                    </button>
                  </nav>

                  <div className="mt-5 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">My projects</div>
                  <div className="mt-1 space-y-0.5">
                    {projects.length ? projects.map((p, i) => (
                      <div key={p.entity} title={p.value} className="flex items-center gap-2 truncate rounded-lg px-3 py-1.5 text-sm text-muted-foreground">
                        <FolderKanban className="h-3.5 w-3.5 shrink-0" style={{ color: ["#6e56cf", "#2f6fdd", "#1f9d6b", "#d98a1c"][i % 4] }} />
                        <span className="truncate capitalize">{p.entity}</span></div>
                    )) : <div className="px-3 py-1 text-xs text-muted-foreground">Tell me “Project X is at D:\…”</div>}
                  </div>
                </div>

                <div className="mt-3 shrink-0 rounded-2xl bg-gradient-to-br from-indigo-600 via-blue-600 to-emerald-600 p-3.5 text-white shadow-sm">
                  <div className="flex items-center gap-1.5 text-xs font-bold leading-tight tracking-wide">
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-amber-300" />
                    <span>Built and designed by ishtiaq ahmed</span>
                  </div>
                  <p className="mt-1 text-[11px] leading-snug text-white/85">
                    Personal AI Assistant · 100% Local Inference & Neural Privacy
                  </p>
                </div>
              </div>
            </motion.aside>
          )}
        </AnimatePresence>

        <main className="min-w-0 flex-1 overflow-hidden relative">
          <AnimatePresence mode="wait">
            <motion.div key={page} className="h-full overflow-hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}>
              {page === "chat" && <Chat owner={owner} onBusy={onBusy} showNav={showNav} onToggleNav={toggleNav} />}
              {page === "overview" && <Overview onToggleNav={toggleNav} />}
              {page === "tasks" && <Tasks onToggleNav={toggleNav} />}
              {page === "markets" && <Markets onToggleNav={toggleNav} />}
              {page === "about" && <AboutYou onToggleNav={toggleNav} />}
              {page === "memory" && <Memory onToggleNav={toggleNav} />}
              {page === "skills" && <Skills onToggleNav={toggleNav} />}
              {page === "system" && <System onToggleNav={toggleNav} />}
              {page === "settings" && <Settings onToggleNav={toggleNav} />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      {showExitModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 animate-in fade-in duration-200">
          <div className="w-full max-w-md rounded-2xl border border-border/80 bg-background/95 p-6 shadow-2xl backdrop-blur-md">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-xl bg-destructive/15 text-destructive shrink-0">
                <Power className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-foreground">Do you want to exit ISHTI?</h3>
                <p className="text-xs text-muted-foreground mt-0.5">Clean shutdown & GPU memory release</p>
              </div>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
              Exiting will cleanly terminate all background server processes, unload models to release GPU VRAM (RTX 5070 Ti), and close active browser sessions.
            </p>
            {isExiting ? (
              <div className="mt-5 flex items-center justify-center gap-2.5 rounded-xl bg-muted/60 p-3 text-xs font-medium text-foreground">
                <span className="h-2 w-2 animate-ping rounded-full bg-destructive" />
                Shutting down ISHTI services...
              </div>
            ) : (
              <div className="mt-6 flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowExitModal(false)}
                  className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer transition-colors"
                >
                  No, keep running
                </button>
                <button
                  type="button"
                  onClick={handleConfirmExit}
                  className="rounded-xl bg-destructive px-4 py-2 text-xs font-medium text-white hover:bg-destructive/90 cursor-pointer transition-colors shadow-xs"
                >
                  Yes, exit ISHTI
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
