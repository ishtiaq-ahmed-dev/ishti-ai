import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, Mic, Network, Plus, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON, streamChat, type Result, type Source, type TraceEvent } from "@/lib/api";
import { ActivityGraph } from "@/components/ActivityGraph";
import { Thinking } from "@/components/Thinking";
import { FolderScan } from "@/components/FolderScan";
import MatrixOrb from "@/components/rare/matrix-orb";
import FluidOrb from "@/components/rare/fluid-orb";
import { Markdown } from "@/components/Markdown";

type Msg = { id: number; role: "user" | "ishti"; text: string; events: TraceEvent[]; live: boolean;
             sources: Source[]; request?: string };

let nextId = 1;

function Sources({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.slice(0, 6).map((s, i) => (
        <a key={i} href={s.url} target="_blank" rel="noreferrer"
          className="max-w-[220px] truncate rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-accent hover:text-accent">
          {s.verified ? "✓ " : ""}{s.domain || s.title || s.url}
        </a>
      ))}
    </div>
  );
}

export function Chat({ owner, onBusy }: { owner: string; onBusy: (busy: boolean) => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [graphFor, setGraphFor] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = msgs.some((m) => m.live);

  useEffect(() => { onBusy(busy); }, [busy, onBusy]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs]);
  useEffect(() => {
    getJSON<{ active_session_id: string }>("/api/sessions").then((d) => setSessionId(d.active_session_id));
  }, []);

  const patch = (id: number, f: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === id ? f(m) : m)));

  async function send(text: string) {
    text = text.trim();
    if (!text || busy) return;
    setInput("");
    const reply: Msg = { id: nextId++, role: "ishti", text: "", events: [], live: true, sources: [], request: text };
    setMsgs((all) => [...all, { id: nextId++, role: "user", text, events: [], live: false, sources: [] }, reply]);
    setGraphFor(reply.id);
    try {
      const res: Result = await streamChat(text, sessionId, {
        event: (e) => patch(reply.id, (m) => ({ ...m, events: [...m.events, e] })),
        token: (t) => patch(reply.id, (m) => ({ ...m, text: m.text + t })),
      });
      setSessionId(res.session_id);
      patch(reply.id, (m) => ({ ...m, text: res.final_answer, sources: res.sources ?? [], live: false }));
    } catch (err) {
      patch(reply.id, (m) => ({ ...m, text: `Couldn't reach ISHTI: ${(err as Error).message}`, live: false }));
    }
  }

  async function newChat() {
    const d = await postJSON<{ session_id: string }>("/api/sessions/new", { title: "New Conversation" });
    setSessionId(d.session_id);
    setMsgs([]);
    setGraphFor(null);
  }

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
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="relative flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border px-6 py-3">
          <div className="flex items-center gap-3">
            <MatrixOrb state={recording ? "listening" : busy ? "thinking" : "idle"} size={36} color="#6e56cf"
              className="[&>span]:sr-only" />
            <div>
              <div className="text-sm font-semibold">ISHTI</div>
              <div className="text-[11px] text-muted-foreground">
                {recording ? "Listening…" : busy ? "Working on it" : "Local · private · ready"}</div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => setGraphFor(graphFor ? null : msgs.filter((m) => m.role === "ishti").at(-1)?.id ?? null)}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
              <Network className="h-4 w-4" /> Activity
            </button>
            <button onClick={newChat}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
              <Plus className="h-4 w-4" /> New chat
            </button>
          </div>
        </header>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          {msgs.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-5 px-6">
              <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ duration: 0.4 }}>
                <FluidOrb size={120} color="#5b3fd6" />
              </motion.div>
              <div className="text-center">
                <h1 className="text-xl font-semibold">{greeting}, {owner}</h1>
                <p className="mt-1 text-sm text-muted-foreground">Ask anything, or tell me what to do on this PC.</p>
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-5 px-6 py-6">
              <AnimatePresence initial={false}>
                {msgs.map((m) => (
                  <motion.div key={m.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, ease: "easeOut" }}
                    className={m.role === "user" ? "flex justify-end" : "flex gap-3"}>
                    {m.role === "user" ? (
                      <div className="max-w-[80%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-sm text-accent-foreground">
                        {m.text}</div>
                    ) : (
                      <>
                        <div className="mt-1 h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-violet-400 to-indigo-600 shadow-[0_0_12px_rgba(110,86,207,0.45)]" />
                        <div className="min-w-0 flex-1">
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
                              className="mt-2 flex gap-2">
                              <button onClick={() => send("yes")}
                                className="rounded-xl bg-accent px-3.5 py-1.5 text-xs font-medium text-white transition-transform active:scale-95">Go ahead</button>
                              <button onClick={() => send("no")}
                                className="rounded-xl border border-border px-3.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted">Cancel</button>
                            </motion.div>
                          )}
                          {!m.live && m.events.length > 0 && (
                            <button onClick={() => setGraphFor(m.id)}
                              className="mt-1.5 text-[11px] text-muted-foreground transition-colors hover:text-accent">
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

        <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="px-6 pb-5">
          <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-[0_2px_12px_rgba(29,27,46,0.06)] transition-shadow focus-within:shadow-[0_2px_18px_rgba(110,86,207,0.18)]">
            <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={1}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder="Ask or tell ISHTI to do anything…"
              className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted-foreground" />
            <button type="button" onClick={toggleMic} title="Speak (local whisper)"
              className={`grid h-9 w-9 place-items-center rounded-xl transition-colors ${recording ? "bg-danger text-white" : "text-muted-foreground hover:bg-muted"}`}>
              {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </button>
            <button type="submit" disabled={!input.trim() || busy} title="Send"
              className="grid h-9 w-9 place-items-center rounded-xl bg-accent text-white transition-opacity disabled:opacity-40">
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
              <div>
                <div className="text-sm font-semibold">Activity</div>
                <div className="max-w-[380px] truncate text-[11px] text-muted-foreground">{graphMsg.request}</div>
              </div>
              <button onClick={() => setGraphFor(null)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted">
                <X className="h-4 w-4" /></button>
            </div>
            <ActivityGraph events={graphMsg.events} request={graphMsg.request ?? ""} live={graphMsg.live} />
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              Every node is a real step: the router's choice, each skill, and every tool call it made.
              Violet = running, green = done, amber = waiting for you, red = failed.</p>
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
