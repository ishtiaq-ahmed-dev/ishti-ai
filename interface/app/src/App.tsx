import { AnimatePresence, motion } from "motion/react";
import { Bot, Cpu, GraduationCap, LayoutDashboard, ListChecks, MessageSquare, PanelLeftClose, Power, Cable, Settings as SettingsIcon, Sparkles, Minimize2, X, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "@/lib/api";
import { Chat } from "@/sections/Chat";
import { Settings, Skills, Tasks } from "@/sections/Pages";
import { ConnectorsPage } from "@/sections/ConnectorsPage";
import { Learning } from "@/sections/Learning";
import { chatStore } from "@/lib/chat-store";
import { ProfileGate } from "@/components/ProfileGate";
import { ProfileSwitcher, PromptLayersViewer } from "@/components/ProfileSwitcher";
import { AgentsPanel } from "@/components/AgentsPanel";
import { LearningPanel } from "@/components/LearningPanel";
import { Overview } from "@/sections/Overview";
import { NotificationBell } from "@/components/rare/notification-bell";
import { NotificationPopover, type Notif } from "@/components/NotificationCenter";
import { ThemeToggle } from "@/components/ThemeToggle";
import { IshtiBrand } from "@/components/IshtiBrand";
import { initChatFontSize } from "@/lib/font-size";
import { BootContext, useBoot } from "@/lib/boot";
import { BootPanel } from "@/components/BootPanel";

type VramData = {
  device: string;
  used_mb: number;
  total_mb: number;
  percent: number;
};

type ModelSwitchToast = {
  role: string;
  from: string;
  to: string;
  reason?: string;
};

type ShutdownProgress = {
  stage: string;
  progress: number;
  message: string;
};

const NAV = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "connectors", label: "Connectors", icon: Cable },
  { key: "tasks", label: "Tasks", icon: ListChecks },
  { key: "learning", label: "Learning", icon: GraduationCap },
  { key: "skills", label: "Skills", icon: Bot },
  { key: "settings", label: "Settings", icon: SettingsIcon },
] as const;
// Inputs merged into Connectors (owner request): the Connectors page shows sensors, messaging,
// tools and agents in one section. #inputs still lands here (alias below).
type Key = (typeof NAV)[number]["key"];

type Fact = { entity: string; attr: string; value: string };

function parseHash(raw: string): Key | null {
  const clean = raw.replace(/^#\/?/, "").toLowerCase().split("/")[0].split("?")[0];
  // #notifications is the Notifications tab of Tasks; #about/#memory are tabs of Learning; #system is a card in Settings.
  // #inputs is now the merged Connectors page (Inputs + Connectors are one section).
  const alias = (clean === "notifications" ? "tasks"
    : clean === "about" || clean === "memory" ? "learning"
      : clean === "system" ? "settings" : clean === "markets" ? "skills" : clean === "inputs" ? "connectors" : clean) as Key;
  return NAV.some((n) => n.key === alias) ? alias : null;
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
  const boot = useBoot();
  // WP1: start the app-level chat store + one shared event stream BEFORE any page mounts.
  useEffect(() => { chatStore.start(); return () => chatStore.stop(); }, []);
  const [gated, setGated] = useState(() => { try { return !sessionStorage.getItem("ishti-profile-chosen"); } catch { return true; } });
  const [page, setPageState] = useState<Key>(getInitialPage);
  const [owner, setOwner] = useState("Ishtiaq Ahmed");
  const [notifs, setNotifs] = useState<Notif[]>([]);
  const [unread, setUnread] = useState(0);
  const [showNotifs, setShowNotifs] = useState(false);
  const [notificationError, setNotificationError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showNav, setShowNav] = useState(() => {
    try {
      const saved = localStorage.getItem("ishti-show-nav");
      if (saved !== null) return saved === "true";
    } catch {}
    return true;
  });

  const [showExitModal, setShowExitModal] = useState(false);
  const [isShuttingDown, setIsShuttingDown] = useState(false);
  const [shutdownProgress, setShutdownProgress] = useState<ShutdownProgress | null>(null);
  const [vram, setVram] = useState<VramData | null>(null);
  const [modelSwitchToast, setModelSwitchToast] = useState<ModelSwitchToast | null>(null);
  const [apiError, setApiError] = useState("");
  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const on = (e: Event) => {
      setApiError(String((e as CustomEvent).detail?.msg || "Request failed"));
      clearTimeout(t);
      t = setTimeout(() => setApiError(""), 6000);
    };
    window.addEventListener("ishti-api-error", on);
    return () => { window.removeEventListener("ishti-api-error", on); clearTimeout(t); };
  }, []);
  const [rememberExitChoice, setRememberExitChoice] = useState(false);

  const toggleNav = useCallback(() => {
    setShowNav((prev) => {
      const next = !prev;
      try { localStorage.setItem("ishti-show-nav", String(next)); } catch {}
      return next;
    });
  }, []);

  const executeShutdown = useCallback(() => {
    setIsShuttingDown(true);
    setShutdownProgress({
      stage: "Wrapping things up",
      progress: 0.25,
      message: "Saving everything and closing safely...",
    });
    const url = "/api/shutdown";
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob(["{}"], { type: "application/json" }));
    } else {
      fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } }).catch(() => {});
    }

    // Safety timeout: ensure window closes even if server hangs
    setTimeout(() => {
      if ((window as any).pywebview?.api?.force_close) {
        (window as any).pywebview.api.force_close();
      } else {
        window.close();
      }
    }, 3000);
  }, []);

  const handleMinimizeToTray = useCallback(() => {
    setShowExitModal(false);
    if (rememberExitChoice) {
      try { localStorage.setItem("ishti_exit_preference", "tray"); } catch {}
    }
    if ((window as any).pywebview?.api?.minimize_to_tray) {
      (window as any).pywebview.api.minimize_to_tray();
    }
  }, [rememberExitChoice]);

  const handleQuitConfirm = useCallback(() => {
    setShowExitModal(false);
    if (rememberExitChoice) {
      try { localStorage.setItem("ishti_exit_preference", "quit"); } catch {}
    }
    executeShutdown();
  }, [rememberExitChoice, executeShutdown]);

  const handleCloseTrigger = useCallback(() => {
    const pref = localStorage.getItem("ishti_exit_preference");
    if (pref === "quit") {
      executeShutdown();
    } else if (pref === "tray" && (window as any).pywebview?.api?.minimize_to_tray) {
      (window as any).pywebview.api.minimize_to_tray();
    } else {
      setShowExitModal(true);
    }
  }, [executeShutdown]);

  useEffect(() => {
    const handleCloseReq = () => handleCloseTrigger();
    window.addEventListener("ishti-window-close-requested", handleCloseReq);
    return () => {
      window.removeEventListener("ishti-window-close-requested", handleCloseReq);
    };
  }, [handleCloseTrigger]);

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
      if (next === "learning") {
        // clicking the nav item always lands on the Learning overview tab, not the last #about/#memory tab
        try { window.dispatchEvent(new CustomEvent("ishti-learning-tab", { detail: "overview" })); } catch {}
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
          try { window.dispatchEvent(new CustomEvent("ishti-ws-connected")); } catch {}
        };

        ws.onmessage = (m) => {
          try {
            const msg = JSON.parse(m.data);
            if (msg.type === "resync_required") {
              try { window.dispatchEvent(new CustomEvent("ishti-task-completed")); } catch {}
              try { window.dispatchEvent(new CustomEvent("ishti-state-resync")); } catch {}
            } else if (msg.type === "connectors_updated") {
              window.dispatchEvent(new CustomEvent("ishti-connectors-updated", { detail: msg.data }));
            } else if (msg.type === "task_completed_full" && msg.data) {
              try { window.dispatchEvent(new CustomEvent("ishti-task-completed", { detail: msg.data })); } catch {}
            } else if (msg.type === "assistant_message" && String(msg.data?.reply ?? "").startsWith("⏰")) {
              if (typeof Notification !== "undefined" && Notification?.permission === "granted") {
                new Notification("ISHTI", { body: msg.data.reply });
              }
            } else if (msg.type === "notification" && msg.data) {
              const n = msg.data as Notif;
              setNotifs((cur) => [n, ...cur.filter((x) => x.id !== n.id)].slice(0, 50));
              setUnread((u) => (n.read ? u : u + 1));
              try { window.dispatchEvent(new CustomEvent("ishti-notification")); } catch {}
            } else if (msg.type === "vram_status" && msg.data) {
              const d = msg.data;
              const used = d.gpu?.used_mb ?? d.used_mb ?? 0;
              const total = d.gpu?.total_mb ?? d.total_mb ?? 16303;
              const pct = d.gpu?.used_pct ?? d.percent ?? (total > 0 ? Math.round((used / total) * 100) : 0);
              const device = d.gpu?.name ?? d.device ?? "NVIDIA GeForce RTX 5070 Ti";
              setVram({
                device,
                used_mb: Math.round(used),
                total_mb: Math.round(total),
                percent: pct,
              });
            } else if ((msg.type === "model_switch" || msg.type === "model_loaded") && msg.data) {
              const d = msg.data;
              const ev: ModelSwitchToast = {
                role: d.role || "Model",
                from: d.from || d.from_model || "",
                to: d.to || d.to_model || "",
                reason: d.reason || "Switched to a lighter model to stay fast",
              };
              setModelSwitchToast(ev);
              setTimeout(() => {
                setModelSwitchToast((cur) => (cur === ev ? null : cur));
              }, 4500);
              try { window.dispatchEvent(new CustomEvent("ishti-models-updated", { detail: d })); } catch {}
            } else if (msg.type === "arch_state" && msg.data) {
              try { window.dispatchEvent(new CustomEvent("ishti-arch-state", { detail: msg.data })); } catch {}
            } else if (msg.type === "memory_updated" && msg.data) {
              if (msg.data?.entity === "owner" && msg.data?.attr === "name" && msg.data?.value) {
                setOwner(String(msg.data.value).trim());
              }
              try { window.dispatchEvent(new CustomEvent("ishti-memory-updated", { detail: msg.data })); } catch {}
            } else if (msg.type === "task_progress" && msg.data) {
              try { window.dispatchEvent(new CustomEvent("ishti-task-progress", { detail: msg.data })); } catch {}
            } else if (msg.type === "mic_state" && msg.data) {
              try { window.dispatchEvent(new CustomEvent("ishti-mic-state", { detail: msg.data })); } catch {}
            } else if (msg.type === "shutdown_progress" && msg.data) {
              const d = msg.data;
              const label = d.label || d.stage || d.step || "Shutting down";
              const prog = typeof d.progress === "number" ? d.progress : (d.index && d.total ? d.index / d.total : 0.5);
              setIsShuttingDown(true);
              setShutdownProgress({
                stage: label,
                progress: prog,
                message: d.message || d.error || "",
              });
              if (d.step === "exit" || d.stage === "Goodbye" || prog >= 1.0) {
                setTimeout(() => {
                  if ((window as any).pywebview?.api?.force_close) {
                    (window as any).pywebview.api.force_close();
                  } else {
                    window.close();
                  }
                }, 600);
              }
            } else if (msg.type === "show_window") {
              if ((window as any).pywebview?.api?.show_window) {
                (window as any).pywebview.api.show_window();
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

  useEffect(() => {  // bell popover: outside click or Escape closes it
    if (!showNotifs) return;
    const onDown = (ev: MouseEvent) => {
      const t = ev.target as HTMLElement | null;
      if (t && t.closest("[data-notif-root]")) return;
      setShowNotifs(false);
    };
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape") setShowNotifs(false); };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [showNotifs]);

  const loadNotifications = useCallback(() => {
    getJSON<{ notifications: Notif[]; unread: number }>("/api/notifications")
      .then((d) => { setNotifs(d.notifications ?? []); setUnread(d.unread ?? 0); setNotificationError(""); })
      .catch((err) => setNotificationError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    loadNotifications();
    const onSync = () => loadNotifications();
    window.addEventListener("ishti-notification", onSync);
    return () => window.removeEventListener("ishti-notification", onSync);
  }, [loadNotifications]);

  const markAllRead = useCallback(() => {
    setNotifs((cur) => cur.map((n) => ({ ...n, read: true })));
    setUnread(0);
    postJSON("/api/notifications/read", {})
      .catch((err) => setNotificationError(err instanceof Error ? err.message : String(err)))
      .finally(() => window.dispatchEvent(new CustomEvent("ishti-notification")));
  }, []);

  const markOneRead = useCallback((id: number) => {
    setNotifs((cur) => cur.map((n) => (n.id === id ? { ...n, read: true } : n)));
    setUnread((u) => Math.max(0, u - 1));
    postJSON("/api/notifications/read", { id })
      .catch((err) => setNotificationError(err instanceof Error ? err.message : String(err)))
      .finally(() => window.dispatchEvent(new CustomEvent("ishti-notification")));
  }, []);

  const viewAllNotifications = useCallback(() => {
    setShowNotifs(false);
    setPage("tasks");
    try { window.history.replaceState(null, "", "#notifications"); } catch {}
    try { window.dispatchEvent(new CustomEvent("ishti-tasks-tab", { detail: "notifications" })); } catch {}
  }, [setPage]);

  const handleLogoClick = useCallback(() => {
    setPage("chat");
    try {
      window.dispatchEvent(new CustomEvent("ishti-start-new-chat"));
    } catch {}
  }, [setPage]);

  return (
    <BootContext.Provider value={boot}>
    {gated && <ProfileGate onChosen={() => setGated(false)} />}
    <div className="flex h-full p-3 bg-canvas text-foreground transition-colors duration-200">
      <BootPanel boot={boot} />
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
                  <div className="flex items-center gap-1" data-notif-root>
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
                      count={unread}
                      size={28}
                      color="violet"
                      onClick={() => setShowNotifs((v) => !v)}
                      title={unread > 0 ? `${unread} unread notification${unread > 1 ? "s" : ""}` : "Notifications"}
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

                {/* WP4: Personal/Business mode switch */}
                <div className="mb-3 flex justify-center">
                  <ProfileSwitcher />
                </div>
                {notificationError && <p role="alert" className="mb-2 px-2 text-[10px] text-danger">Notifications: {notificationError}</p>}

                <div className="scroll-thin flex min-h-0 flex-1 flex-col overflow-y-auto pr-1">
                  <nav className="space-y-0.5">
                    {NAV.map(({ key, label, icon: Icon }) => (
                      <button key={key} onClick={() => setPage(key)}
                        className={`relative flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-colors cursor-pointer ${page === key ? "text-foreground font-semibold" : "text-muted-foreground hover:text-foreground"}`}>
                        {page === key && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-xl bg-accent-soft"
                          transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                        <Icon className={`relative h-4 w-4 ${page === key ? "text-accent" : ""}`} />
                        <span className="relative font-medium">{label}</span>
                        {key === "chat" && chatStore.unreadTotal() > 0 && (
                          <span data-testid="chat-unread-badge"
                                className="relative ml-auto rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-bold text-accent-foreground">
                            {chatStore.unreadTotal()}
                          </span>
                        )}
                      </button>
                    ))}
                  </nav>
                </div>

                <div className="mt-3 shrink-0 rounded-2xl bg-gradient-to-br from-indigo-600 via-blue-600 to-emerald-600 p-3 text-white shadow-sm">
                  <div className="flex items-center gap-1.5 text-xs font-bold leading-tight tracking-wide">
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-white" />
                    <span>Built and designed by ishtiaq ahmed</span>
                  </div>
                  <p className="mt-1 text-[10.5px] leading-snug text-white/85">
                    Personal AI Assistant · 100% Local Inference & Neural Privacy
                  </p>
                </div>

                {/* Bottom-left Power button & Live VRAM chip */}
                <div className="mt-2 pt-2 border-t border-border/40 flex items-center justify-between gap-1 shrink-0">
                  <button
                    type="button"
                    id="ishti-sidebar-power-btn"
                    onClick={() => setShowExitModal(true)}
                    className="flex items-center gap-2 rounded-xl px-2.5 py-1.5 text-xs font-semibold text-danger/90 hover:text-danger hover:bg-danger/10 active:scale-95 transition-all cursor-pointer"
                    title="Shut down ISHTI (Quit and release all GPU memory)"
                  >
                    <Power className="h-4 w-4 text-danger" />
                    <span>Shut down</span>
                  </button>

                  {vram && (
                    <div
                      className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-muted/40 border border-border/40 text-[10px] text-muted-foreground font-mono"
                      title={`${vram.device}: ${vram.used_mb}MB / ${vram.total_mb}MB (${vram.percent}%)`}
                    >
                      <Cpu className={`h-3 w-3 shrink-0 ${vram.percent > 85 ? "text-amber-400" : "text-emerald-400"}`} />
                      <span>{(vram.used_mb / 1024).toFixed(1)}GB</span>
                    </div>
                  )}
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
              {page === "connectors" && <ConnectorsPage onToggleNav={toggleNav} />}
              {page === "tasks" && <Tasks onToggleNav={toggleNav} />}
              {page === "learning" && <Learning onToggleNav={toggleNav} />}
              {page === "skills" && <Skills onToggleNav={toggleNav} />}
              {page === "settings" && (
                <Settings onToggleNav={toggleNav}>
                  {/* WP4 read-only prompt layers + WP7 agents live as Settings cards */}
                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    <div className="rounded-xl border border-border bg-card p-3">
                      <PromptLayersViewer />
                    </div>
                    <div className="rounded-xl border border-border bg-card p-3">
                      <AgentsPanel />
                    </div>
                    <div className="rounded-xl border border-border bg-card p-3 md:col-span-2">
                      <LearningPanel />
                    </div>
                  </div>
                </Settings>
              )}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      {/* Notification popover (the bell): the mini inbox, newest first. Closing on outside click/Escape is a
          document listener rather than a full-screen catcher, so a stuck exit can never block the app. */}
      <AnimatePresence>
        {showNotifs && (
          <>
            <motion.div
              data-notif-root
              initial={{ opacity: 0, y: -6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6, scale: 0.98 }}
              transition={{ duration: 0.15 }}
              className="fixed left-3 top-14 z-50 w-[340px] max-w-[calc(100vw-24px)]"
            >
              <NotificationPopover
                items={notifs}
                unread={unread}
                onMarkAll={markAllRead}
                onMarkOne={markOneRead}
                onViewAll={viewAllNotifications}
                onClose={() => setShowNotifs(false)}
              />
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Exit confirmation dialog: Quit / Minimize to tray / Cancel + Remember choice */}
      {showExitModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 animate-in fade-in duration-200">
          <div className="w-full max-w-md rounded-2xl border border-border/80 bg-background/95 p-6 shadow-2xl backdrop-blur-md">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-xl bg-danger/15 text-danger shrink-0">
                <Power className="h-5 w-5" />
              </div>            <div>
              <h3 className="text-base font-semibold text-foreground">Do you want to exit ISHTI?</h3>
              <p className="text-xs text-emerald-500 font-medium mt-0.5">All data is auto-saved • Closes safely</p>
            </div>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
              All your conversations, memories, and settings are saved automatically. When you exit, ISHTI finishes what it is doing, saves everything, and closes safely. See you next time!
            </p>

            <div className="mt-4 pt-3 border-t border-border/40 flex items-center justify-between">
              <label className="flex items-center gap-2 cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                <input
                  type="checkbox"
                  checked={rememberExitChoice}
                  onChange={(e) => setRememberExitChoice(e.target.checked)}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                <span>Remember my choice</span>
              </label>

              <button
                type="button"
                onClick={() => setShowExitModal(false)}
                className="rounded-xl border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer transition-colors"
              >
                Cancel
              </button>
            </div>

            <div className="mt-4 flex items-center justify-end gap-2.5">
              <button
                type="button"
                onClick={handleMinimizeToTray}
                className="flex items-center gap-1.5 rounded-xl border border-border/80 bg-muted/30 px-3.5 py-2 text-xs font-medium text-foreground hover:bg-muted cursor-pointer transition-colors"
              >
                <Minimize2 className="h-3.5 w-3.5" />
                Minimize to tray
              </button>
              <button
                type="button"
                onClick={handleQuitConfirm}
                className="flex items-center gap-1.5 rounded-xl bg-danger px-4 py-2 text-xs font-semibold text-white hover:bg-danger/90 active:scale-95 cursor-pointer transition-all shadow-sm"
              >
                <Power className="h-3.5 w-3.5" />
                Quit ISHTI
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Unified Shutdown Progress Overlay */}
      {isShuttingDown && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 animate-in fade-in duration-300">
          <div className="w-full max-w-sm rounded-2xl border border-accent/30 bg-background/95 p-6 shadow-2xl backdrop-blur-md flex flex-col items-center text-center">
            <div className="relative mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-accent/10 text-accent border border-accent/20">
              <Sparkles className="h-7 w-7 animate-pulse text-accent" />
            </div>
            <h3 className="text-base font-bold text-foreground">Shutting Down ISHTI</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {shutdownProgress?.message || "Saving your work and closing safely..."}
            </p>

            <div className="mt-5 w-full">
              <div className="flex justify-between text-[11px] font-semibold text-accent mb-1.5">
                <span>{shutdownProgress?.stage || "Wrapping things up"}</span>
                <span>{Math.round((shutdownProgress?.progress || 0.25) * 100)}%</span>
              </div>
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <motion.div
                  className="h-full bg-gradient-to-r from-accent to-accent/70 rounded-full"
                  initial={{ width: "25%" }}
                  animate={{ width: `${Math.min(100, Math.max(10, Math.round((shutdownProgress?.progress || 0.25) * 100)))}%` }}
                  transition={{ duration: 0.3 }}
                />
              </div>
            </div>
            <div className="mt-4 text-[10px] text-muted-foreground/75">
              Everything is saved — see you soon!
            </div>
          </div>
        </div>
      )}

      {apiError && (
        <div role="alert" data-testid="api-error-toast"
          className="fixed bottom-5 right-5 z-50 flex max-w-sm items-start gap-2 rounded-2xl border border-destructive/40 bg-background/95 px-4 py-3 text-xs shadow-xl backdrop-blur-md">
          <span className="min-w-0 flex-1 break-words text-foreground">{apiError}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setApiError("")}
            className="rounded-lg p-0.5 text-muted-foreground hover:text-foreground cursor-pointer"><X className="h-3.5 w-3.5" /></button>
        </div>
      )}

      {/* Model Switch Notification Toast */}
      <AnimatePresence>
        {modelSwitchToast && (
          <motion.div
            initial={{ opacity: 0, y: -20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -20, scale: 0.95 }}
            transition={{ duration: 0.2 }}
            className="fixed top-5 right-5 z-50 flex items-center gap-3 rounded-2xl border border-accent/30 bg-background/95 px-4 py-3 shadow-xl backdrop-blur-md max-w-sm"
          >
            <div className="grid h-8 w-8 place-items-center rounded-xl bg-accent/15 text-accent shrink-0">
              <Zap className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold text-foreground">Switched to a lighter model to stay fast</div>
              <div className="text-[11px] text-muted-foreground truncate">
                <span className="capitalize font-medium text-accent/90">{modelSwitchToast.role}</span>: {modelSwitchToast.from} → {modelSwitchToast.to}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setModelSwitchToast(null)}
              className="rounded-lg p-1 text-muted-foreground hover:text-foreground hover:bg-muted/50 cursor-pointer"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
    </BootContext.Provider>
  );
}
