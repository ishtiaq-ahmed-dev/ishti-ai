import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, AudioLines, Maximize2, MessageSquare, Mic, Minimize2, Network, PanelLeft, PanelLeftClose, Plus, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { del, getJSON, postJSON, streamChat, type Result, type Source, type TraceEvent } from "@/lib/api";
import { ActivityGraph, ConversationGraph, type Turn } from "@/components/ActivityGraph";
import { Thinking } from "@/components/Thinking";
import { FolderScan } from "@/components/FolderScan";
import { AgentOrb } from "@/components/AgentOrb";
import { resolveAgentState } from "@/lib/agent-state";
import BorderBeam from "border-beam";
import VoiceBeam from "voice-glow";
import DeleteButton from "@/components/rare/delete-button";
import { IshtiBrand } from "@/components/IshtiBrand";
import { Markdown } from "@/components/Markdown";
import { MediaPreview } from "@/components/MediaPreview";
import { LiveMode } from "@/components/LiveMode";
import { cn } from "@/lib/utils";

type Msg = {
  id: number;
  role: "user" | "ishti";
  text: string;
  events: TraceEvent[];
  live: boolean;
  sources: Source[];
  request?: string;
};

type Session = {
  session_id: string;
  title: string;
  updated_at: string;
  message_count: number;
};

let nextId = 1;

function ago(iso: string) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "now" : m < 60 ? `${m}m` : m < 1440 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`;
}

function Sources({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.slice(0, 6).map((s, i) => (
        <a
          key={i}
          href={s.url}
          target="_blank"
          rel="noreferrer"
          className="max-w-[220px] truncate rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-accent hover:text-accent"
        >
          {s.verified ? "✓ " : ""}{s.domain || s.title || s.url}
        </a>
      ))}
    </div>
  );
}

type Props = {
  owner: string;
  onBusy: (busy: boolean) => void;
  showNav?: boolean;
  onToggleNav?: () => void;
};

export function Chat({ owner, onBusy, showNav = true, onToggleNav }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(() => {
    if (typeof window !== "undefined") {
      const match = window.location.hash.match(/^#chat\/(sess_[^/?#]+)/i);
      if (match) return match[1];
      try {
        return localStorage.getItem("ishti-session-id");
      } catch {}
    }
    return null;
  });
  const [sessions, setSessions] = useState<Session[]>([]);
  const [showHistory, setShowHistory] = useState(() => {
    try {
      const saved = localStorage.getItem("ishti-chat-show-history");
      if (saved !== null) return saved === "true";
    } catch {}
    return true;
  });
  const [historyWidth, setHistoryWidth] = useState(() => {
    try {
      const saved = localStorage.getItem("ishti-recent-chats-width");
      if (saved) {
        const parsed = parseInt(saved, 10);
        if (parsed >= 180 && parsed <= 550) return parsed;
      }
    } catch {}
    return 240;
  });
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    const handleWidthChange = (e: Event) => {
      const detail = (e as CustomEvent<number>).detail;
      if (typeof detail === "number" && detail >= 180 && detail <= 550) {
        setHistoryWidth(detail);
      }
    };
    window.addEventListener("ishti-side-chats-width-changed", handleWidthChange);
    return () => {
      window.removeEventListener("ishti-side-chats-width-changed", handleWidthChange);
    };
  }, []);

  const startResizing = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    const startX = e.clientX;
    const startWidth = historyWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    let currentW = historyWidth;
    const onPointerMove = (ev: PointerEvent) => {
      const delta = ev.clientX - startX;
      const maxWidth = Math.max(350, Math.min(550, window.innerWidth * 0.45));
      const nextWidth = Math.max(180, Math.min(Math.round(startWidth + delta), maxWidth));
      currentW = nextWidth;
      setHistoryWidth(nextWidth);
      try {
        localStorage.setItem("ishti-recent-chats-width", String(nextWidth));
      } catch {}
    };

    const onPointerUp = () => {
      setIsResizing(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      try {
        window.dispatchEvent(new CustomEvent("ishti-side-chats-width-changed", { detail: currentW }));
      } catch {}
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
  }, [historyWidth]);
  const [graphFor, setGraphFor] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [liveOpen, setLiveOpen] = useState(false);
  const [audioStream, setAudioStream] = useState<MediaStream | null>(null);
  const [graphView, setGraphView] = useState<"answer" | "chat">("answer");
  const [fullGraph, setFullGraph] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scrollContainer = useRef<HTMLDivElement>(null);
  const isInitialLoad = useRef(true);
  const running = msgs.filter((m) => m.live).length;
  const busy = running > 0;
  // Up to 10 messages run at once (the server runs independent ones side by side; a follow-up waits for the
  // answer it is about). More wait here in order. A new chat's first message runs alone so all share its session.
  const canStart = running < 10 && (!!sessionId || running === 0);
  const [queue, setQueue] = useState<string[]>([]);

  const toggleHistory = useCallback(() => {
    setShowHistory((h) => {
      const next = !h;
      try { localStorage.setItem("ishti-chat-show-history", String(next)); } catch {}
      return next;
    });
  }, []);

  const loadSessions = useCallback(() => {
    getJSON<{ sessions: Session[] }>("/api/sessions")
      .then((d) => setSessions(d.sessions.filter((x) => x.message_count > 0).slice(0, 30)));
  }, []);

  useEffect(() => { onBusy(busy); }, [busy, onBusy]);
  useEffect(() => { loadSessions(); }, [loadSessions]);

  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 220)}px`; }
  }, [input]);

  useEffect(() => {
    if (isInitialLoad.current) {
      if (msgs.length > 0) {
        if (scrollContainer.current) {
          scrollContainer.current.scrollTop = scrollContainer.current.scrollHeight;
        } else {
          bottom.current?.scrollIntoView({ behavior: "instant" });
        }
        requestAnimationFrame(() => {
          isInitialLoad.current = false;
        });
      }
      return;
    }
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs]);

  const selectSession = useCallback(async (id: string) => {
    setGraphFor(null);
    isInitialLoad.current = true;
    try {
      localStorage.setItem("ishti-session-id", id);
      if (window.location.hash.startsWith("#chat") || !window.location.hash) {
        window.history.replaceState(null, "", `#chat/${id}`);
      }
    } catch {}
    await postJSON(`/api/sessions/${id}/activate`);
    const { messages } = await getJSON<{ messages: { role: string; content: string; sources?: Source[]; metadata?: { events?: TraceEvent[] } }[] }>(`/api/sessions/${id}/messages`);
    let last = "";
    setMsgs(messages.map((m) => {
      if (m.role === "user") last = m.content;
      return m.role === "user"
        ? { id: nextId++, role: "user", text: m.content, events: [], live: false, sources: [] }
        : { id: nextId++, role: "ishti", text: m.content, events: m.metadata?.events ?? [], live: false,
            sources: m.sources ?? [], request: last };
    }));
    setSessionId(id);
    requestAnimationFrame(() => {
      if (scrollContainer.current) {
        scrollContainer.current.scrollTop = scrollContainer.current.scrollHeight;
      } else {
        bottom.current?.scrollIntoView({ behavior: "instant" });
      }
    });
  }, []);

  const newChat = useCallback(async () => {
    const d = await postJSON<{ session_id: string }>("/api/sessions/new", { title: "New Conversation" });
    try {
      localStorage.setItem("ishti-session-id", d.session_id);
      if (window.location.hash.startsWith("#chat") || !window.location.hash) {
        window.history.replaceState(null, "", `#chat/${d.session_id}`);
      }
    } catch {}
    setSessionId(d.session_id);
    setMsgs([]);
    setGraphFor(null);
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    const handleStartNewChat = () => {
      newChat();
    };
    window.addEventListener("ishti-start-new-chat", handleStartNewChat);
    return () => {
      window.removeEventListener("ishti-start-new-chat", handleStartNewChat);
    };
  }, [newChat]);

  useEffect(() => {
    let targetSession: string | null = null;
    if (typeof window !== "undefined") {
      const hashMatch = window.location.hash.match(/^#chat\/(sess_[^/?#]+)/i);
      if (hashMatch) {
        targetSession = hashMatch[1];
      } else {
        const querySession = new URLSearchParams(window.location.search).get("session");
        if (querySession && querySession.startsWith("sess_")) {
          targetSession = querySession;
        } else {
          try {
            targetSession = localStorage.getItem("ishti-session-id");
          } catch {}
        }
      }
    }

    getJSON<{ active_session_id: string }>("/api/sessions").then((d) => {
      const idToLoad = targetSession || d.active_session_id;
      if (idToLoad) {
        setSessionId(idToLoad);
        selectSession(idToLoad);
      }
    });
  }, [selectSession]);

  useEffect(() => {
    const onHashChange = () => {
      const hashMatch = window.location.hash.match(/^#chat\/(sess_[^/?#]+)/i);
      if (hashMatch && hashMatch[1] && hashMatch[1] !== sessionId) {
        selectSession(hashMatch[1]);
      }
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [sessionId, selectSession]);

  const deleteSession = async (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    await del(`/api/sessions/${id}`);
    loadSessions();
    if (sessionId === id) {
      try { localStorage.removeItem("ishti-session-id"); } catch {}
      newChat();
    }
  };

  const patch = (id: number, f: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === id ? f(m) : m)));

  const adjustTextareaHeight = useCallback(() => {
    if (box.current) {
      box.current.style.height = "auto";
      const scrollH = box.current.scrollHeight;
      box.current.style.height = `${Math.min(Math.max(scrollH, 40), 220)}px`;
    }
  }, []);

  useEffect(() => {
    adjustTextareaHeight();
  }, [input, adjustTextareaHeight]);

  async function send(text: string) {
    text = text.trim();
    if (!text) return;
    isInitialLoad.current = false;
    setInput("");
    if (box.current) {
      box.current.style.height = "40px";
    }
    if (!canStart) { setQueue((q) => [...q, text]); return; }
    const reply: Msg = { id: nextId++, role: "ishti", text: "", events: [], live: true, sources: [], request: text };
    setMsgs((all) => [...all, { id: nextId++, role: "user", text, events: [], live: false, sources: [] }, reply]);
    setGraphFor(reply.id);
    try {
      const res: Result = await streamChat(text, sessionId, {
        event: (e) => patch(reply.id, (m) => ({ ...m, events: [...m.events, e] })),
        token: (t) => patch(reply.id, (m) => ({ ...m, text: m.text + t })),
      });
      setSessionId(res.session_id);
      try {
        localStorage.setItem("ishti-session-id", res.session_id);
        if (window.location.hash.startsWith("#chat") || !window.location.hash) {
          window.history.replaceState(null, "", `#chat/${res.session_id}`);
        }
      } catch {}
      patch(reply.id, (m) => ({ ...m, text: res.final_answer, sources: res.sources ?? [], live: false }));
      loadSessions();
    } catch (err) {
      patch(reply.id, (m) => ({ ...m, text: `Couldn't reach ISHTI: ${(err as Error).message}`, live: false }));
    }
  }

  useEffect(() => {
    if (!canStart || !queue.length) return;
    const [next, ...rest] = queue;
    setQueue(rest);
    send(next);
  }, [canStart, queue]); // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleMic() {
    if (recording && recorder.current) { recorder.current.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setAudioStream(stream);
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(stream);
      let inflight = false;
      // every second: transcribe what has been said so far and show it in the input, live
      rec.ondataavailable = async (e) => {
        if (!e.data.size) return;
        chunks.push(e.data);
        if (rec.state !== "recording" || inflight) return;
        inflight = true;
        try {
          const form = new FormData();
          form.append("audio", new Blob(chunks, { type: rec.mimeType }), "partial.webm");
          const { text } = await fetch("/api/stt", { method: "POST", body: form }).then((r) => r.json());
          if (text && rec.state === "recording") setInput(text);
        } catch {} finally { inflight = false; }
      };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setAudioStream(null);
        setRecording(false);
        try {
          if (!chunks.length) return;
          const form = new FormData();
          form.append("audio", new Blob(chunks, { type: rec.mimeType }), "speech.webm");
          const res = await fetch("/api/stt", { method: "POST", body: form });
          if (!res.ok) throw new Error(`STT failed with HTTP ${res.status}`);
          const data = await res.json();
          if (data?.text && data.text.trim()) send(data.text.trim());
        } catch (sttErr) {
          console.warn("[Voice] STT transcription failed:", sttErr);
        }
      };
      recorder.current = rec;
      rec.start(1000); // 1 s chunks feed the live transcript
      setRecording(true);
    } catch (micErr) {
      console.warn("[Voice] Microphone access failed or denied:", micErr);
      setRecording(false);
      setAudioStream(null);
    }
  }

  const graphMsg = msgs.find((m) => m.id === graphFor);
  const turns: Turn[] = msgs.filter((m) => m.role === "ishti" && m.request).map((m) => ({
    question: m.request!, skill: (m.events.findLast((e) => e.type === "done")?.skill as string | undefined)
      ?? (m.events.find((e) => e.type === "route")?.skill as string | undefined) ?? null,
    ms: m.events.findLast((e) => e.type === "done")?.ms,
  }));
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="relative flex h-full min-h-0 w-full overflow-hidden">
      {/* Side Recent Chats panel for Chat Section */}
      <AnimatePresence initial={false}>
        {showHistory && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: historyWidth, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={isResizing ? { duration: 0 } : { duration: 0.2, ease: "easeOut" }}
            className="relative flex h-full shrink-0 flex-col border-r border-border bg-card/40 max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:bg-background/95 max-md:backdrop-blur-md max-md:shadow-2xl"
            style={{ width: historyWidth }}
          >
            <div className="flex h-full w-full flex-col min-h-0 overflow-hidden" style={{ width: historyWidth }}>
              <div className="flex items-center justify-between border-b border-border px-3.5 py-3 shrink-0">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground truncate">Recent</span>
                  {historyWidth !== 240 && (
                    <button
                      type="button"
                      onClick={() => {
                        setHistoryWidth(240);
                        try {
                          localStorage.setItem("ishti-recent-chats-width", "240");
                          window.dispatchEvent(new CustomEvent("ishti-side-chats-width-changed", { detail: 240 }));
                        } catch {}
                      }}
                      title="Reset width to default (240px)"
                      className="rounded-md bg-muted/60 hover:bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                    >
                      {historyWidth}px
                    </button>
                  )}
                </div>
                <button
                  type="button"
                  onClick={newChat}
                  className="flex items-center gap-1 rounded-lg bg-accent-soft px-2 py-1 text-xs font-medium text-accent transition-colors hover:bg-accent hover:text-white cursor-pointer"
                >
                  <Plus className="h-3.5 w-3.5" /> New
                </button>
              </div>
              <div className="scroll-thin flex-1 min-h-0 space-y-1.5 overflow-y-auto p-2.5">
                {sessions.length ? (
                  sessions.map((x) => {
                    const isSelected = sessionId === x.session_id;
                    return (
                      <div
                        key={x.session_id}
                        onClick={() => selectSession(x.session_id)}
                        className={cn(
                          "group relative flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-all duration-200 select-none",
                          isSelected
                            ? "border-accent/70 bg-gradient-to-r from-accent/15 via-accent/8 to-transparent text-foreground font-semibold shadow-[0_2px_12px_rgba(110,86,207,0.12)] dark:shadow-[0_2px_12px_rgba(139,117,255,0.18)]"
                            : "border-border/60 dark:border-white/10 bg-card/40 dark:bg-card/20 text-muted-foreground hover:border-accent/40 hover:bg-card/80 dark:hover:bg-card/45 hover:text-foreground hover:shadow-xs",
                        )}
                      >
                        {/* Active vertical indicator bar on the left */}
                        {isSelected && (
                          <div className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-accent shadow-[0_0_10px_var(--color-accent)]" />
                        )}

                        {/* Chat Icon */}
                        <MessageSquare
                          className={cn(
                            "h-4 w-4 shrink-0 transition-colors",
                            isSelected
                              ? "text-accent"
                              : "text-muted-foreground/60 group-hover:text-muted-foreground"
                          )}
                        />

                        {/* Topic Title */}
                        <span
                          className={cn(
                            "min-w-0 flex-1 truncate text-[13.5px] leading-snug",
                            isSelected
                              ? "text-foreground font-semibold"
                              : "text-foreground/80 group-hover:text-foreground font-medium"
                          )}
                        >
                          {x.title}
                        </span>

                        {/* Trailing Timestamp & Action */}
                        <div className="shrink-0 flex items-center ml-1">
                          <span
                            className={cn(
                              "text-[10.5px] tabular-nums transition-opacity",
                              "group-hover:hidden group-has-[[data-state=open]]:hidden",
                              isSelected
                                ? "rounded-md bg-accent/20 px-1.5 py-0.5 text-accent font-semibold text-[10px]"
                                : "text-muted-foreground/60"
                            )}
                          >
                            {ago(x.updated_at)}
                          </span>
                          <div className="hidden group-hover:flex group-has-[[data-state=open]]:flex items-center">
                            <DeleteButton
                              size="sm"
                              onConfirm={() => deleteSession(x.session_id)}
                            />
                          </div>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="rounded-xl border border-dashed border-border/70 p-6 text-center text-xs text-muted-foreground">
                    Your chats appear here.
                  </div>
                )}
              </div>
            </div>

            {/* Draggable resize handle */}
            <div
              role="separator"
              aria-orientation="vertical"
              title="Drag to resize side chats (double click to reset)"
              onPointerDown={startResizing}
              onDoubleClick={() => {
                setHistoryWidth(240);
                try {
                  localStorage.setItem("ishti-recent-chats-width", "240");
                  window.dispatchEvent(new CustomEvent("ishti-side-chats-width-changed", { detail: 240 }));
                } catch {}
              }}
              className={cn(
                "absolute -right-2 top-0 bottom-0 z-30 w-4 cursor-col-resize select-none",
                "flex items-center justify-center transition-colors group",
                isResizing ? "cursor-col-resize" : ""
              )}
            >
              <div
                className={cn(
                  "h-8 w-1 rounded-full bg-border/80 transition-all",
                  "group-hover:h-14 group-hover:bg-accent group-hover:w-1.5",
                  isResizing && "h-20 bg-accent w-1.5 shadow-[0_0_8px_var(--color-accent)]"
                )}
              />
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* Main chat conversation view */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-border px-4 sm:px-6 py-3 shrink-0">
          <div className="flex items-center gap-2.5">
            {!showNav && onToggleNav && (
              <button
                type="button"
                onClick={onToggleNav}
                title="Show navigation bar"
                className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
              >
                <PanelLeft className="h-4 w-4 text-accent" />
              </button>
            )}
            <button
              type="button"
              onClick={toggleHistory}
              title={showHistory ? "Hide recent chats" : "Show recent chats"}
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
            >
              {showHistory ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeft className="h-4 w-4" />}
            </button>
            <AgentOrb
              events={msgs.find((m) => m.live)?.events}
              live={busy}
              recording={recording}
              size={42}
              className="shrink-0"
            />
            <div>
              <IshtiBrand
                variant="wordmark"
                height={26}
                className="translate-y-0.5 cursor-pointer hover:opacity-80 transition-opacity"
                onClick={newChat}
                title="ISHTI - Start New Chat"
              />
              <div className="text-[11px] text-muted-foreground flex items-center gap-1.5 min-w-0">
                {recording ? (
                  <span className="text-amber-500 font-medium">Listening to voice…</span>
                ) : busy ? (
                  <span className="text-accent font-medium truncate">
                    {running > 1 ? `Working on ${running} tasks` : resolveAgentState(msgs.find((m) => m.live)?.events, true).label}
                  </span>
                ) : (
                  "Local · private · ready"
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => setGraphFor(graphFor ? null : msgs.filter((m) => m.role === "ishti").at(-1)?.id ?? null)}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
              <Network className="h-4 w-4" /> Activity
            </button>
            <button onClick={newChat}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
              <Plus className="h-4 w-4" /> New chat
            </button>
          </div>
        </header>

        <div ref={scrollContainer} className="scroll-thin min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          {msgs.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-6 px-6">
              <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ duration: 0.4 }} className="relative flex items-center justify-center">
                <div className="absolute inset-0 -m-8 rounded-full bg-accent/10 blur-3xl pointer-events-none" />
                <AgentOrb size={80} state="breathing" />
              </motion.div>
              <div className="text-center flex flex-col items-center">
                <IshtiBrand
                  variant="full"
                  width={220}
                  className="mb-4 drop-shadow-sm cursor-pointer hover:opacity-90 active:scale-95 transition-all"
                  onClick={newChat}
                  title="ISHTI - Start New Chat"
                />
                <h1 className="text-2xl font-semibold tracking-tight">{greeting}, {owner}</h1>
                <p className="mt-1 text-sm text-muted-foreground">Ask anything, or tell me what to do on this PC.</p>
              </div>

              {/* Actionable Prompt Chips */}
              <div className="flex flex-wrap items-center justify-center gap-2 max-w-xl">
                {[
                  { label: "Hardware specs & memory usage", icon: "⚡" },
                  { label: "What's the weather tomorrow?", icon: "🌤️" },
                  { label: "Search for latest AI news & models", icon: "🌐" },
                  { label: "Find large files in Downloads", icon: "📁" },
                  { label: "Remind me in 30 minutes to take a break", icon: "⏰" },
                  { label: "Analyze NVIDIA stock performance", icon: "📈" },
                ].map((chip) => (
                  <button
                    key={chip.label}
                    type="button"
                    onClick={() => {
                      send(chip.label);
                    }}
                    className="flex items-center gap-2 rounded-xl border border-border/80 bg-card/60 px-3.5 py-2 text-xs font-medium text-foreground hover:border-accent hover:bg-accent-soft/30 hover:text-accent transition-all cursor-pointer shadow-xs active:scale-[0.98]"
                  >
                    <span>{chip.icon}</span>
                    <span>{chip.label}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-4xl xl:max-w-5xl space-y-5 px-4 sm:px-6 py-6 min-w-0">
              <AnimatePresence initial={false}>
                {msgs.map((m) => (
                  <motion.div key={m.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, ease: "easeOut" }}
                    className={m.role === "user" ? "flex justify-end min-w-0" : "flex gap-3 min-w-0 max-w-full"}>
                    {m.role === "user" ? (
                      <div
                        className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-accent-foreground shadow-xs"
                        style={{ fontSize: "var(--chat-font-size)", lineHeight: "var(--chat-leading)" }}
                      >
                        {m.text}
                      </div>
                    ) : (
                      <>
                        <IshtiBrand variant="icon" size={32} className="mt-0.5 shrink-0 shadow-xs" />
                        <div
                          className="min-w-0 flex-1 max-w-full overflow-hidden"
                          style={{ fontSize: "var(--chat-font-size)", lineHeight: "var(--chat-leading)" }}
                        >
                          <Thinking events={m.events} live={m.live} />
                          {(() => {
                            const scan = m.events.findLast((e) => e.type === "tool" && e.name === "scan");
                            const plan = m.events.findLast((e) => e.type === "tool" && e.name === "plan");
                            return scan ? <FolderScan scan={scan} plan={plan} /> : null;
                          })()}
                          {m.text ? <Markdown text={m.text} />
                                  : m.live && <span className="inline-block h-4 w-2 animate-pulse rounded-sm bg-accent/60" />}
                          {!m.live && <MediaPreview text={m.text} onSend={(p) => { setInput(`send "${p}" on whatsapp to `); box.current?.focus(); }} />}
                          <Sources sources={m.sources} />
                          {!m.live && m.id === msgs.at(-1)?.id && /\(yes\/no\)\s*$/i.test(m.text) && (
                            <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}
                              className="mt-2.5 flex flex-wrap gap-2">
                              <button onClick={() => send("yes")}
                                className="rounded-xl bg-accent px-4 py-2 text-xs font-semibold text-white shadow-xs transition-transform active:scale-95 cursor-pointer hover:opacity-90">Go ahead</button>
                              <button onClick={() => send("no")}
                                className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted cursor-pointer">Cancel</button>
                            </motion.div>
                          )}
                          {!m.live && m.events.length > 0 && (
                            <button onClick={() => setGraphFor(m.id)}
                              className="mt-1.5 text-[11px] text-muted-foreground transition-colors hover:text-accent cursor-pointer">
                              View activity graph</button>
                          )}
                        </div>
                      </>
                    )}
                  </motion.div>
                ))}
              </AnimatePresence>
              <div ref={bottom} />
            </div>
          )}
        </div>

        <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="px-4 sm:px-6 pb-5 pt-1 shrink-0">
          <AnimatePresence initial={false}>
            {queue.length > 0 && (
              <motion.ul initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
                aria-label="Queued messages" className="mx-auto mb-2 flex w-full max-w-4xl xl:max-w-5xl flex-col gap-1">
                {queue.map((q, i) => (
                  <motion.li key={`${i}-${q}`} layout initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
                    className="flex items-center gap-2 rounded-xl border border-dashed border-border bg-muted/40 px-3 py-1.5 text-xs">
                    <span className="shrink-0 font-medium text-accent">{i === 0 ? "Next" : `#${i + 1}`}</span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">{q}</span>
                    <button type="button" aria-label="Remove from queue" title="Remove from queue"
                      onClick={() => setQueue((all) => all.filter((_, j) => j !== i))}
                      className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </motion.li>
                ))}
              </motion.ul>
            )}
          </AnimatePresence>
          {recording ? (
            <VoiceBeam
              active={recording}
              stream={audioStream}
              colorVariant="gold"
              theme="auto"
              className="mx-auto w-full max-w-4xl xl:max-w-5xl rounded-2xl"
            >
              <div className="flex w-full items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-[0_2px_12px_rgba(29,27,46,0.06)] transition-shadow focus-within:shadow-[0_2px_18px_rgba(110,86,207,0.18)]">
                <textarea ref={box} value={input} onChange={(e) => setInput(e.target.value)} rows={1}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
                  placeholder="Ask or tell ISHTI to do anything…"
                  style={{ fontSize: "var(--chat-input-size)" }}
                  className="scroll-thin max-h-[220px] min-h-[40px] flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2 leading-relaxed outline-none placeholder:text-muted-foreground" />
                <button type="button" onClick={toggleMic} title="Record a voice message (reply comes as text)"
                  className={`grid h-9 w-9 place-items-center rounded-xl transition-colors cursor-pointer ${recording ? "bg-danger text-white" : "text-muted-foreground hover:bg-muted"}`}>
                  {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </button>
                <button type="button" onClick={() => setLiveOpen(true)} title="Live: talk with ISHTI out loud (the only mode that speaks)"
                  className="flex h-9 items-center gap-1.5 rounded-xl border border-accent/40 px-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft/40 cursor-pointer">
                  <AudioLines className="h-4 w-4" /> Live
                </button>
                <button type="submit" disabled={!input.trim()} title={canStart ? "Send" : "Queue: runs when a slot is free"}
                  className="grid h-9 w-9 place-items-center rounded-xl bg-accent text-white transition-opacity disabled:opacity-40 cursor-pointer">
                  <ArrowUp className="h-4 w-4" />
                </button>
              </div>
            </VoiceBeam>
          ) : (
            <BorderBeam
              active={busy}
              colorVariant="gold"
              size="md"
              theme="auto"
              className="mx-auto w-full max-w-4xl xl:max-w-5xl rounded-2xl"
            >
              <div className="flex w-full items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-[0_2px_12px_rgba(29,27,46,0.06)] transition-shadow focus-within:shadow-[0_2px_18px_rgba(110,86,207,0.18)]">
                <textarea ref={box} value={input} onChange={(e) => setInput(e.target.value)} rows={1}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
                  placeholder="Ask or tell ISHTI to do anything…"
                  style={{ fontSize: "var(--chat-input-size)" }}
                  className="scroll-thin max-h-[220px] min-h-[40px] flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2 leading-relaxed outline-none placeholder:text-muted-foreground" />
                <button type="button" onClick={toggleMic} title="Record a voice message (reply comes as text)"
                  className={`grid h-9 w-9 place-items-center rounded-xl transition-colors cursor-pointer ${recording ? "bg-danger text-white" : "text-muted-foreground hover:bg-muted"}`}>
                  {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </button>
                <button type="button" onClick={() => setLiveOpen(true)} title="Live: talk with ISHTI out loud (the only mode that speaks)"
                  className="flex h-9 items-center gap-1.5 rounded-xl border border-accent/40 px-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft/40 cursor-pointer">
                  <AudioLines className="h-4 w-4" /> Live
                </button>
                <button type="submit" disabled={!input.trim()} title={canStart ? "Send" : "Queue: runs when a slot is free"}
                  className="grid h-9 w-9 place-items-center rounded-xl bg-accent text-white transition-opacity disabled:opacity-40 cursor-pointer">
                  <ArrowUp className="h-4 w-4" />
                </button>
              </div>
            </BorderBeam>
          )}
        </form>
      </div>

      <AnimatePresence>
        {graphMsg && (
          <motion.aside initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }}
            transition={{ duration: 0.28, ease: "easeOut" }}
            className="scroll-thin absolute inset-y-0 right-0 z-20 flex w-[min(480px,94%)] flex-col gap-3 overflow-y-auto border-l border-border bg-background p-4 shadow-[-12px_0_40px_rgba(29,27,46,0.12)] xl:static xl:z-auto xl:w-[480px] xl:shrink-0 xl:shadow-none">
            <div className="flex items-center justify-between">
              <div className="flex rounded-xl bg-muted p-0.5 text-xs">
                {(["answer", "chat"] as const).map((v) => (
                  <button key={v} onClick={() => setGraphView(v)}
                    className={`relative rounded-lg px-3 py-1 font-medium transition-colors ${graphView === v ? "text-foreground" : "text-muted-foreground"}`}>
                    {graphView === v && <motion.span layoutId="graph-tab" className="absolute inset-0 rounded-lg bg-background shadow-sm"
                      transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
                    <span className="relative">{v === "answer" ? "This answer" : "This chat"}</span>
                  </button>
                ))}
              </div>
              <div className="flex gap-1">
                <button onClick={() => setFullGraph(true)} title="Expand"
                  className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted cursor-pointer"><Maximize2 className="h-4 w-4" /></button>
                <button onClick={() => setGraphFor(null)} title="Close" className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted cursor-pointer">
                  <X className="h-4 w-4" /></button>
              </div>
            </div>
            <div className="truncate text-[11px] text-muted-foreground">
              {graphView === "answer" ? graphMsg.request : `${turns.length} question${turns.length === 1 ? "" : "s"} in this chat`}</div>
            {graphView === "answer"
              ? <ActivityGraph events={graphMsg.events} request={graphMsg.request ?? ""} live={graphMsg.live} />
              : <ConversationGraph turns={turns} />}
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {graphView === "answer"
                ? "Every node is a real step. Skills are groups: click one to open or fold its tool calls. Violet = running, green = done, amber = waiting for you, red = failed."
                : "Each question in this chat, linked to the skill that handled it."}</p>
          </motion.aside>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {liveOpen && (
          <LiveMode sessionId={sessionId} onClose={() => setLiveOpen(false)}
            onTurn={(user, reply, sid) => {
              setMsgs((all) => [...all, { id: nextId++, role: "user", text: user, events: [], live: false, sources: [] },
                { id: nextId++, role: "ishti", text: reply, events: [], live: false, sources: [], request: user }]);
              if (sid && !sessionId) setSessionId(sid);
              loadSessions();
            }} />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {fullGraph && graphMsg && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
            className="fixed inset-0 z-50 flex flex-col gap-3 bg-background/95 p-6 backdrop-blur-sm"
            onKeyDown={(e) => e.key === "Escape" && setFullGraph(false)}>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-base font-semibold">{graphView === "answer" ? "Activity" : "This chat"}</div>
                <div className="text-xs text-muted-foreground">Scroll to zoom · drag to move · {graphView === "answer" ? graphMsg.request : `${turns.length} questions`}</div>
              </div>
              <button onClick={() => setFullGraph(false)} className="rounded-xl border border-border p-2 text-muted-foreground hover:bg-muted cursor-pointer">
                <Minimize2 className="h-4 w-4" /></button>
            </div>
            <div className="min-h-0 flex-1">
              {graphView === "answer"
                ? <ActivityGraph events={graphMsg.events} request={graphMsg.request ?? ""} live={graphMsg.live} full />
                : <ConversationGraph turns={turns} full />}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default Chat;
