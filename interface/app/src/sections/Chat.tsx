import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, Maximize2, Mic, Minimize2, Network, PanelLeft, PanelLeftClose, Plus, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { del, getJSON, postJSON, streamChat, type Result, type Source, type TraceEvent } from "@/lib/api";
import { ActivityGraph, ConversationGraph, type Turn } from "@/components/ActivityGraph";
import { Thinking } from "@/components/Thinking";
import { FolderScan } from "@/components/FolderScan";
import MatrixOrb from "@/components/rare/matrix-orb";
import FluidOrb from "@/components/rare/fluid-orb";
import DeleteButton from "@/components/rare/delete-button";
import { IshtiBrand } from "@/components/IshtiBrand";
import { Markdown } from "@/components/Markdown";
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
};

export function Chat({ owner, onBusy }: Props) {
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
  const [graphFor, setGraphFor] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [graphView, setGraphView] = useState<"answer" | "chat">("answer");
  const [fullGraph, setFullGraph] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = msgs.some((m) => m.live);
  // Messages sent while ISHTI is working wait here and run in order (one GPU, one model call at a time).
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

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs]);

  const selectSession = useCallback(async (id: string) => {
    setGraphFor(null);
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

  async function send(text: string) {
    text = text.trim();
    if (!text) return;
    setInput("");
    if (busy) { setQueue((q) => [...q, text]); return; }
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
    if (busy || !queue.length) return;
    const [next, ...rest] = queue;
    setQueue(rest);
    send(next);
  }, [busy, queue]); // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleMic() {
    if (recording && recorder.current) { recorder.current.stop(); return; }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      const form = new FormData();
      form.append("audio", new Blob(chunks, { type: rec.mimeType }), "speech.webm");
      const { text } = await fetch("/api/stt", { method: "POST", body: form }).then((r) => r.json());
      if (text) send(text);
    };
    recorder.current = rec;
    rec.start();
    setRecording(true);
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
            animate={{ width: 240, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="flex h-full shrink-0 flex-col overflow-hidden border-r border-border bg-card/40"
          >
            <div className="flex h-full w-[240px] flex-col min-h-0">
              <div className="flex items-center justify-between border-b border-border px-3.5 py-3 shrink-0">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Recent</span>
                <button
                  type="button"
                  onClick={newChat}
                  className="flex items-center gap-1 rounded-lg bg-accent-soft px-2 py-1 text-xs font-medium text-accent transition-colors hover:bg-accent hover:text-white cursor-pointer"
                >
                  <Plus className="h-3.5 w-3.5" /> New
                </button>
              </div>
              <div className="scroll-thin flex-1 min-h-0 space-y-0.5 overflow-y-auto p-2">
                {sessions.length ? (
                  sessions.map((x) => (
                    <div
                      key={x.session_id}
                      onClick={() => selectSession(x.session_id)}
                      className={cn(
                        "group relative flex cursor-pointer items-center justify-between gap-1.5 rounded-xl px-2.5 py-1 text-xs transition-colors",
                        sessionId === x.session_id
                          ? "bg-accent-soft text-accent font-medium shadow-xs"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate">{x.title}</span>
                      <span
                        className={cn(
                          "text-[10px] shrink-0 group-hover:hidden group-has-[[data-state=open]]:hidden",
                          sessionId === x.session_id ? "text-accent" : "opacity-60",
                        )}
                      >
                        {ago(x.updated_at)}
                      </span>
                      <div className="hidden shrink-0 group-hover:flex group-has-[[data-state=open]]:flex items-center">
                        <DeleteButton
                          size="sm"
                          onConfirm={() => deleteSession(x.session_id)}
                        />
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="px-3 py-6 text-center text-xs text-muted-foreground">Your chats appear here.</div>
                )}
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* Main chat conversation view */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-border px-4 sm:px-6 py-3 shrink-0">
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={toggleHistory}
              title={showHistory ? "Hide recent chats" : "Show recent chats"}
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
            >
              {showHistory ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeft className="h-4 w-4" />}
            </button>
            <MatrixOrb state={recording ? "listening" : busy ? "thinking" : "idle"} size={34} color="#6e56cf"
              className="[&>span]:sr-only" />
            <div>
              <IshtiBrand variant="wordmark" size={20} className="translate-y-0.5" />
              <div className="text-[11px] text-muted-foreground">
                {recording ? "Listening…" : busy ? "Working on it" : "Local · private · ready"}</div>
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

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          {msgs.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-5 px-6">
              <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ duration: 0.4 }}>
                <FluidOrb size={120} color="#5b3fd6" />
              </motion.div>
              <div className="text-center flex flex-col items-center">
                <IshtiBrand variant="full" size={48} className="mb-2" />
                <h1 className="text-xl font-semibold">{greeting}, {owner}</h1>
                <p className="mt-1 text-sm text-muted-foreground">Ask anything, or tell me what to do on this PC.</p>
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
                      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-sm text-accent-foreground">
                        {m.text}</div>
                    ) : (
                      <>
                        <IshtiBrand variant="icon" size={28} className="mt-1 shrink-0 shadow-sm" />
                        <div className="min-w-0 flex-1 max-w-full overflow-hidden">
                          <Thinking events={m.events} live={m.live} />
                          {(() => {
                            const scan = m.events.findLast((e) => e.type === "tool" && e.name === "scan");
                            const plan = m.events.findLast((e) => e.type === "tool" && e.name === "plan");
                            return scan ? <FolderScan scan={scan} plan={plan} /> : null;
                          })()}
                          {m.text ? <Markdown text={m.text} />
                                  : m.live && <span className="inline-block h-4 w-2 animate-pulse rounded-sm bg-accent/60" />}
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
          <div className="mx-auto flex w-full max-w-4xl xl:max-w-5xl items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-[0_2px_12px_rgba(29,27,46,0.06)] transition-shadow focus-within:shadow-[0_2px_18px_rgba(110,86,207,0.18)]">
            <textarea ref={box} value={input} onChange={(e) => setInput(e.target.value)} rows={1}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder="Ask or tell ISHTI to do anything…"
              className="scroll-thin max-h-[220px] min-h-[40px] flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2 text-sm leading-relaxed outline-none placeholder:text-muted-foreground" />
            <button type="button" onClick={toggleMic} title="Speak (local whisper)"
              className={`grid h-9 w-9 place-items-center rounded-xl transition-colors cursor-pointer ${recording ? "bg-danger text-white" : "text-muted-foreground hover:bg-muted"}`}>
              {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </button>
            <button type="submit" disabled={!input.trim()} title={busy ? "Queue: runs when ISHTI is free" : "Send"}
              className="grid h-9 w-9 place-items-center rounded-xl bg-accent text-white transition-opacity disabled:opacity-40 cursor-pointer">
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
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
