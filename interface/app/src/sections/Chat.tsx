import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, AudioLines, Check, Copy, FolderOpen, Maximize2, MessageSquare, Mic, Minimize2, Network, PanelLeft, PanelLeftClose, Paperclip, Pencil, Plus, Square, ThumbsDown, ThumbsUp, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { del, getJSON, postJSON, streamChat, type Result, type Source, type TraceEvent } from "@/lib/api";
import { ActivityGraph, ConversationGraph, type Turn } from "@/components/ActivityGraph";
import { Thinking } from "@/components/Thinking";
import { FolderScan } from "@/components/FolderScan";
import { AgentOrb } from "@/components/AgentOrb";
import { resolveAgentState } from "@/lib/agent-state";
import BorderBeam from "border-beam";
import VoiceBeam from "voice-glow";
import { IshtiBrand } from "@/components/IshtiBrand";
import { Markdown } from "@/components/Markdown";
import { MediaPreview } from "@/components/MediaPreview";
import { LiveMode } from "@/components/LiveMode";
import { cn } from "@/lib/utils";
import { useBootState } from "@/lib/boot";

type Msg = {
  id: number;
  role: "user" | "ishti";
  text: string;
  events: TraceEvent[];
  live: boolean;
  sources: Source[];
  request?: string;
  messageId?: string;
  rating?: "positive" | "negative";
  revisionOf?: string;
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
  const boot = useBootState();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [showFeedback, setShowFeedback] = useState(() => {
    try { return localStorage.getItem("ishti-show-answer-feedback") !== "false"; } catch { return true; }
  });
  const [feedbackBusy, setFeedbackBusy] = useState<string | null>(null);
  const [feedbackError, setFeedbackError] = useState<{ messageId: string; text: string } | null>(null);
  const [feedbackReasonFor, setFeedbackReasonFor] = useState<string | null>(null);
  const [feedbackReason, setFeedbackReason] = useState("");
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
  const [transcribing, setTranscribing] = useState(false); // Stop pressed: the last words are being written into the box
  const [liveOpen, setLiveOpen] = useState(false);
  const [audioStream, setAudioStream] = useState<MediaStream | null>(null);
  const [graphView, setGraphView] = useState<"answer" | "chat">("answer");
  const [fullGraph, setFullGraph] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const sessionLoadGeneration = useRef(0);
  const msgsRef = useRef(msgs);
  const uploadInput = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<{ name: string; mention: string; size: number }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scrollContainer = useRef<HTMLDivElement>(null);
  const isInitialLoad = useRef(true);
  const running = msgs.filter((m) => m.live).length;
  const busy = running > 0;

  useEffect(() => { msgsRef.current = msgs; }, [msgs]);

  useEffect(() => {
    onBusy?.(busy);
    if (!busy) {
      try {
        window.dispatchEvent(new CustomEvent("ishti-chat-finished"));
      } catch {}
    }
  }, [busy, onBusy]);

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
    // While the reply is still streaming, pin to the bottom directly: a smooth animation restarted on
    // every single token stutters. A finished message gets the smooth scroll.
    const last = msgs[msgs.length - 1];
    if (last?.live) {
      const el = scrollContainer.current;
      if (el) el.scrollTop = el.scrollHeight;
      else bottom.current?.scrollIntoView({ behavior: "instant", block: "end" });
      return;
    }
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs]);

  const selectSession = useCallback(async (id: string) => {
    const generation = ++sessionLoadGeneration.current;
    setGraphFor(null);
    isInitialLoad.current = true;
    try {
    await postJSON(`/api/sessions/${id}/activate`);
    const { messages } = await getJSON<{ messages: { id?: string; role: string; content: string; sources?: Source[]; metadata?: { events?: TraceEvent[]; feedback?: { rating?: "positive" | "negative" }; revision_of?: string; request?: string } }[] }>(`/api/sessions/${id}/messages`);
    if (generation !== sessionLoadGeneration.current) return;
    try {
      localStorage.setItem("ishti-session-id", id);
      if (window.location.hash.startsWith("#chat") || !window.location.hash) {
        window.history.replaceState(null, "", `#chat/${id}`);
      }
    } catch {}
    let last = "";
    setMsgs(messages.map((m) => {
      if (m.role === "user") last = m.content;
      return m.role === "user"
        ? { id: nextId++, role: "user", text: m.content, events: [], live: false, sources: [], messageId: m.id }
        : { id: nextId++, role: "ishti", text: m.content, events: m.metadata?.events ?? [], live: false,
            sources: m.sources ?? [], request: m.metadata?.request ?? last, messageId: m.id,
            rating: m.metadata?.feedback?.rating, revisionOf: m.metadata?.revision_of };
    }));
    setSessionId(id);
    requestAnimationFrame(() => {
      if (scrollContainer.current) {
        scrollContainer.current.scrollTop = scrollContainer.current.scrollHeight;
      } else {
        bottom.current?.scrollIntoView({ behavior: "instant" });
      }
    });
    } catch (err) {
      if (generation === sessionLoadGeneration.current) console.warn("[chat] session load failed:", err);
    }
  }, []);

  const newChat = useCallback(async () => {
    const generation = ++sessionLoadGeneration.current;
    const d = await postJSON<{ session_id: string }>("/api/sessions/new", { title: "New Conversation" });
    if (generation !== sessionLoadGeneration.current) return;
    try {
      localStorage.setItem("ishti-session-id", d.session_id);
      if (window.location.hash.startsWith("#chat") || !window.location.hash) {
        window.history.replaceState(null, "", `#chat/${d.session_id}`);
      }
    } catch {}
    setSessionId(d.session_id);
    setMsgs([]);
    setUploads([]);
    setGraphFor(null);
    loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    const refresh = (event?: Event) => {
      const detail = (event as CustomEvent<{ session_id?: string }> | undefined)?.detail;
      const target = sessionId;
      if (!target || (detail?.session_id && detail.session_id !== target)) return;
      loadSessions();
      if (msgsRef.current.some((m) => m.live)) return;
      const generation = sessionLoadGeneration.current;
      getJSON<{ messages: { id?: string; role: string; content: string; sources?: Source[]; metadata?: { events?: TraceEvent[]; feedback?: { rating?: "positive" | "negative" }; revision_of?: string; request?: string } }[] }>(`/api/sessions/${target}/messages`)
        .then(({ messages }) => {
          if (generation !== sessionLoadGeneration.current || target !== sessionId || msgsRef.current.some((m) => m.live)) return;
          let last = "";
          setMsgs(messages.map((m) => {
            if (m.role === "user") last = m.content;
            return m.role === "user"
              ? { id: nextId++, role: "user", text: m.content, events: [], live: false, sources: [], messageId: m.id }
              : { id: nextId++, role: "ishti", text: m.content, events: m.metadata?.events ?? [], live: false, sources: m.sources ?? [],
                  messageId: m.id, rating: m.metadata?.feedback?.rating, revisionOf: m.metadata?.revision_of,
                  request: m.metadata?.request ?? last };
          }));
        })
        .catch((err) => console.warn("[chat] live refresh failed:", err));
    };
    const onFinished = () => refresh();
    const onTask = (event: Event) => refresh(event);
    window.addEventListener("ishti-task-completed", onTask);
    window.addEventListener("ishti-chat-finished", onFinished);
    return () => {
      window.removeEventListener("ishti-task-completed", onTask);
      window.removeEventListener("ishti-chat-finished", onFinished);
    };
  }, [sessionId, msgs, loadSessions]);

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
    try {
      const d = await postJSON<{ undo_token: string; deleted: string[] }>("/api/sessions/delete", { ids: [id] });
      setUndo({ token: d.undo_token, ids: d.deleted });
    } catch {
      await del(`/api/sessions/${id}`);
    }
    loadSessions();
    if (sessionId === id) {
      try { localStorage.removeItem("ishti-session-id"); } catch {}
      newChat();
    }
  };

  // ---------------- Recent chats: select / rename / delete-with-undo / share (all local) ----------------
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [undo, setUndo] = useState<{ token: string; ids: string[] } | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareFormat, setShareFormat] = useState<"md" | "pdf">("md");
  const [shareDir, setShareDir] = useState("");
  const [shareName, setShareName] = useState("");
  const [shareBusy, setShareBusy] = useState(false);
  const [shareResult, setShareResult] = useState<{ path: string; copied?: boolean } | null>(null);
  const [shareError, setShareError] = useState("");

  const toggleSelected = useCallback((id: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const saveRename = useCallback(async (id: string) => {
    const title = renameDraft.trim();
    setRenaming(null);
    if (!title) return;
    try {
      const d = await postJSON<{ title: string }>(`/api/sessions/${id}/rename`, { title });
      setSessions((cur) => cur.map((s) => (s.session_id === id ? { ...s, title: d.title } : s)));
    } catch (err) {
      console.warn("[chat] rename failed:", err);
    }
  }, [renameDraft]);

  const deleteSelected = useCallback(async () => {
    const ids = [...selected];
    if (!ids.length) return;
    setConfirmingDelete(false);
    try {
      const d = await postJSON<{ undo_token: string; deleted: string[] }>("/api/sessions/delete", { ids });
      setSelecting(false);
      setSelected(new Set());
      setSessions((cur) => cur.filter((s) => !d.deleted.includes(s.session_id)));
      setUndo({ token: d.undo_token, ids: d.deleted });
      if (sessionId && d.deleted.includes(sessionId)) {
        try { localStorage.removeItem("ishti-session-id"); } catch {}
        newChat();
      }
    } catch (err) {
      console.warn("[chat] delete failed:", err);
    }
  }, [selected, sessionId, newChat]);

  const undoDelete = useCallback(async () => {
    if (!undo) return;
    try { await postJSON("/api/sessions/undo", { undo_token: undo.token }); } catch (err) { console.warn("[chat] undo failed:", err); }
    setUndo(null);
    loadSessions();
  }, [undo, loadSessions]);

  useEffect(() => {  // the undo window is 8 s; after that the trash folder is cleared at next startup
    if (!undo) return;
    const t = setTimeout(() => setUndo(null), 8000);
    return () => clearTimeout(t);
  }, [undo]);

  const openShare = useCallback(() => {
    setShareError("");
    setShareResult(null);
    setShareFormat("md");
    setShareDir("");
    setShareName(`ishti-chats-${new Date().toISOString().slice(0, 10)}.md`);
    setShareOpen(true);
  }, []);

  const doExport = useCallback(async () => {
    setShareBusy(true);
    setShareError("");
    setShareResult(null);
    try {
      const d = await postJSON<{ path: string }>("/api/sessions/export", {
        ids: [...selected], format: shareFormat, dir: shareDir.trim(), name: shareName.trim() || undefined,
      });
      setShareResult({ path: d.path });
    } catch (err) {
      setShareError(err instanceof Error ? err.message : String(err));
    } finally {
      setShareBusy(false);
    }
  }, [selected, shareFormat, shareDir, shareName]);

  const copySelectedAsText = useCallback(async () => {
    try {
      const parts: string[] = [];
      for (const id of selected) {
        const d = await getJSON<{ title: string; messages: { role: string; content: string }[] }>(`/api/sessions/${id}/messages`);
        parts.push(`## ${d.title}`, "");
        for (const m of d.messages) parts.push(`${m.role === "user" ? "You" : "ISHTI"}: ${m.content}`);
        parts.push("");
      }
      await navigator.clipboard.writeText(parts.join("\n"));
      setShareResult({ path: "", copied: true });
    } catch (err) {
      setShareError("Copy failed: " + (err instanceof Error ? err.message : String(err)));
    }
  }, [selected]);

  const allSelected = sessions.length > 0 && sessions.every((s) => selected.has(s.session_id));

  const patch = (id: number, f: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === id ? f(m) : m)));

  const adjustTextareaHeight = useCallback(() => {
    if (box.current) {
      box.current.style.height = "auto";
      const scrollH = box.current.scrollHeight;
      box.current.style.height = `${Math.min(Math.max(scrollH, 40), 220)}px`;
    }
  }, []);

  const addUploads = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setUploadError("");
    try {
      let totalBytes = uploads.reduce((sum, item) => sum + item.size, 0);
      let totalCount = uploads.length;
      for (const file of Array.from(files)) {
        if (totalCount >= 5) throw new Error("A message can include at most 5 files.");
        if (totalBytes + file.size > 50 * 1024 * 1024) throw new Error("Attachments in one message are limited to 50 MB total.");
        const form = new FormData();
        form.append("file", file);
        const response = await fetch("/api/upload", { method: "POST", body: form });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `Upload failed (${response.status})`);
        setUploads((current) => [...current, { name: data.name || file.name, mention: data.mention, size: data.size || file.size }]);
        totalBytes += file.size;
        totalCount += 1;
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (uploadInput.current) uploadInput.current.value = "";
    }
  }, [uploads]);

  const onComposerPaste = useCallback((event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = event.clipboardData.files;
    if (files?.length) {
      event.preventDefault();
      void addUploads(files);
    }
  }, [addUploads]);

  useEffect(() => {
    adjustTextareaHeight();
  }, [input, adjustTextareaHeight]);

  async function send(text: string) {
    // Enter can submit a form even while the disabled send button is visible.
    // Keep an in-flight attachment upload from being silently omitted.
    if (uploading) return;
    text = [text.trim(), ...uploads.map((f) => f.mention)].filter(Boolean).join("\n");
    if (!text) return;
    setUploads([]);
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
      patch(reply.id, (m) => ({ ...m, text: res.final_answer, sources: res.sources ?? [], live: false,
        messageId: res.message_id ?? undefined }));
      loadSessions();
      try { window.dispatchEvent(new CustomEvent("ishti-chat-finished")); } catch {}
    } catch (err) {
      patch(reply.id, (m) => ({ ...m, text: `Couldn't reach ISHTI: ${(err as Error).message}`, live: false }));
      try { window.dispatchEvent(new CustomEvent("ishti-chat-finished")); } catch {}
    }
  }

  async function rateAnswer(messageId: string, rating: "positive" | "negative", reason = "") {
    if (!sessionId || feedbackBusy) return;
    const targetSession = sessionId;
    const generation = sessionLoadGeneration.current;
    setFeedbackBusy(messageId);
    setFeedbackError(null);
    try {
      const result = await postJSON<{
        rating: "positive" | "negative";
        revision?: { id: string; content: string; metadata?: { revision_of?: string; request?: string } } | null;
        error?: string | null;
      }>(`/api/sessions/${targetSession}/feedback`, { message_id: messageId, rating, reason });
      if (generation !== sessionLoadGeneration.current || targetSession !== sessionId) return;
      setMsgs((all) => {
        const updated = all.map((m) => m.messageId === messageId ? { ...m, rating: result.rating } : m);
        if (!result.revision || updated.some((m) => m.messageId === result.revision?.id)) return updated;
        return [...updated, { id: nextId++, role: "ishti", text: result.revision.content,
          events: [], live: false, sources: [], messageId: result.revision.id,
          revisionOf: result.revision.metadata?.revision_of, request: result.revision.metadata?.request }];
      });
      setFeedbackReasonFor(null);
      setFeedbackReason("");
      if (result.error) setFeedbackError({ messageId, text: result.error });
      loadSessions();
    } catch (error) {
      setFeedbackError({ messageId, text: error instanceof Error ? error.message : "Feedback could not be recorded" });
    } finally {
      setFeedbackBusy(null);
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
      // Stop never sends: the full transcript goes into the box so the owner can read/fix it and press Send themselves
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setAudioStream(null);
        setRecording(false);
        if (!chunks.length) return;
        setTranscribing(true);
        try {
          const form = new FormData();
          form.append("audio", new Blob(chunks, { type: rec.mimeType }), "speech.webm");
          const res = await fetch("/api/stt", { method: "POST", body: form });
          if (!res.ok) throw new Error(`STT failed with HTTP ${res.status}`);
          const data = await res.json();
          if (data?.text && data.text.trim()) setInput(data.text.trim());
        } catch (sttErr) {
          console.warn("[Voice] STT transcription failed:", sttErr);
        } finally {
          setTranscribing(false);
          box.current?.focus();
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
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => { setSelecting((v) => !v); setSelected(new Set()); setConfirmingDelete(false); }}
                    title={selecting ? "Leave selection mode" : "Select chats"}
                    className={cn(
                      "rounded-lg border px-2 py-1 text-xs font-medium transition-colors cursor-pointer",
                      selecting
                        ? "border-accent bg-accent-soft text-accent"
                        : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {selecting ? "Cancel" : "Select"}
                  </button>
                  <button
                    type="button"
                    onClick={newChat}
                    className="flex items-center gap-1 rounded-lg bg-accent-soft px-2 py-1 text-xs font-medium text-accent transition-colors hover:bg-accent hover:text-white cursor-pointer"
                  >
                    <Plus className="h-3.5 w-3.5" /> New
                  </button>
                </div>
              </div>
              <div className="scroll-thin flex-1 min-h-0 space-y-1.5 overflow-y-auto p-2.5">
                {sessions.length ? (
                  sessions.map((x) => {
                    const isSelected = sessionId === x.session_id;
                    return (
                      <div
                        key={x.session_id}
                        onClick={() => (selecting ? toggleSelected(x.session_id) : selectSession(x.session_id))}
                        onDoubleClick={(e) => { if (!selecting) { e.stopPropagation(); setRenaming(x.session_id); setRenameDraft(x.title); } }}
                        className={cn(
                          "group relative flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-colors duration-150",
                          isSelected
                            ? "border-accent/70 bg-gradient-to-r from-accent/15 via-accent/8 to-transparent text-foreground font-semibold shadow-[0_2px_12px_rgba(110,86,207,0.12)] dark:shadow-[0_2px_12px_rgba(139,117,255,0.18)]"
                            : "border-border/60 dark:border-white/10 bg-card/40 dark:bg-card/20 text-muted-foreground hover:border-accent/40 hover:bg-card/80 dark:hover:bg-card/45 hover:text-foreground",
                        )}
                      >
                        {/* Active vertical indicator bar on the left */}
                        {isSelected && (
                          <div className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-accent shadow-[0_0_10px_var(--color-accent)]" />
                        )}

                        {/* Selection checkbox (select mode only) */}
                        {selecting && (
                          <span
                            className={cn(
                              "grid h-4 w-4 shrink-0 place-items-center rounded border transition-colors",
                              selected.has(x.session_id) ? "border-accent bg-accent text-white" : "border-border",
                            )}
                          >
                            {selected.has(x.session_id) && <Check className="h-3 w-3" />}
                          </span>
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

                        {/* Topic Title (click the pencil or double-click to rename: Enter saves, Esc cancels) */}
                        {renaming === x.session_id ? (
                          <input
                            autoFocus
                            value={renameDraft}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => setRenameDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); saveRename(x.session_id); }
                              if (e.key === "Escape") { e.preventDefault(); setRenaming(null); }
                            }}
                            onBlur={() => saveRename(x.session_id)}
                            className="min-w-0 flex-1 rounded-md border border-accent/60 bg-background px-1.5 py-0.5 text-[13px] text-foreground outline-none"
                          />
                        ) : (
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
                        )}

                        {/* Trailing Timestamp & Action (fixed width & absolute overlay so chat items never shift or move) */}
                        <div className="shrink-0 relative flex items-center justify-end h-5 min-w-[50px] ml-1">
                          <span
                            className={cn(
                              "text-[10.5px] tabular-nums transition-opacity duration-150",
                              "group-hover:opacity-0 group-has-[[data-state=open]]:opacity-0",
                              isSelected
                                ? "rounded-md bg-accent/20 px-1.5 py-0.5 text-accent font-semibold text-[10px]"
                                : "text-muted-foreground/60"
                            )}
                          >
                            {ago(x.updated_at)}
                          </span>
                          <div className="absolute right-0 flex items-center gap-0.5 opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity duration-150">
                            <button
                              type="button"
                              title="Rename chat"
                              onClick={(e) => { e.stopPropagation(); setRenaming(x.session_id); setRenameDraft(x.title); }}
                              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              title="Delete chat"
                              onClick={(e) => { e.stopPropagation(); void deleteSession(x.session_id, e); }}
                              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-danger/10 hover:text-danger cursor-pointer"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
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

              {/* Select-mode action bar: select all, delete (confirmed) or share the chosen chats */}
              {selecting && (
                <div className="shrink-0 border-t border-border bg-card/60 p-2.5">
                  <div className="flex items-center justify-between gap-1.5 px-0.5 pb-2 text-[11px] text-muted-foreground">
                    <button
                      type="button"
                      onClick={() => setSelected(allSelected ? new Set() : new Set(sessions.map((s) => s.session_id)))}
                      className="font-medium transition-colors hover:text-foreground cursor-pointer"
                    >
                      {allSelected ? "Clear all" : "Select all"}
                    </button>
                    <span>{selected.size} selected</span>
                  </div>
                  {confirmingDelete ? (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={deleteSelected}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-danger px-2 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-danger/90 cursor-pointer"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Delete {selected.size}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingDelete(false)}
                        className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
                      >
                        Keep
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        disabled={!selected.size}
                        onClick={() => setConfirmingDelete(true)}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-danger/90 px-2 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-danger disabled:cursor-default disabled:opacity-40 cursor-pointer"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Delete
                      </button>
                      <button
                        type="button"
                        disabled={!selected.size}
                        onClick={openShare}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border px-2 py-1.5 text-[11px] font-semibold text-foreground transition-colors hover:bg-muted disabled:cursor-default disabled:opacity-40 cursor-pointer"
                      >
                        <Copy className="h-3.5 w-3.5" /> Share
                      </button>
                    </div>
                  )}
                </div>
              )}
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

      {/* Undo toast: 8 s to bring deleted chats back (they wait in data/sessions/.trash).
          No AnimatePresence here on purpose: an interrupted exit animation used to leave the toast
          in the DOM (and on top of the action bar, eating clicks). It unmounts the moment undo clears. */}
      {undo && (
          <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
            className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
            <div className="flex items-center gap-3 rounded-2xl border border-border bg-background/95 px-4 py-2.5 shadow-2xl backdrop-blur-md">
              <Trash2 className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-xs text-foreground">
                {undo.ids.length} chat{undo.ids.length > 1 ? "s" : ""} deleted
              </span>
              <button type="button" onClick={undoDelete}
                className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-accent/90 cursor-pointer">
                Undo
              </button>
              <button type="button" onClick={() => setUndo(null)} title="Dismiss"
                className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted cursor-pointer">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </motion.div>
      )}

      {/* Share: save the chosen chats to a local Markdown/PDF file, or copy them as text. Nothing is uploaded. */}
      {shareOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl border border-border bg-background p-5 shadow-2xl">
            <h3 className="text-sm font-semibold text-foreground">
              Share {selected.size} chat{selected.size > 1 ? "s" : ""}
            </h3>
            <p className="mt-1 text-[11px] text-muted-foreground">Saved on this PC only - nothing is uploaded.</p>
            <div className="mt-4 space-y-3">
              <div>
                <div className="text-[11px] font-medium text-muted-foreground">Format</div>
                <div className="mt-1 flex gap-2">
                  {(["md", "pdf"] as const).map((f) => (
                    <button key={f} type="button" onClick={() => { setShareFormat(f); setShareName((n) => n.replace(/\.(md|pdf)$/i, `.${f}`)); }}
                      className={cn("rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer",
                        shareFormat === f ? "border-accent bg-accent-soft text-accent" : "border-border text-muted-foreground hover:text-foreground")}>
                      {f === "md" ? "Markdown (.md)" : "PDF (.pdf)"}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-medium text-muted-foreground">Folder (empty = ISHTI's own exports folder)</div>
                <input value={shareDir} onChange={(e) => setShareDir(e.target.value)} spellCheck={false}
                  placeholder={"e.g. D:\\Documents\\ISHTI"}
                  className="mt-1 w-full rounded-lg border border-border bg-muted/30 px-2.5 py-1.5 text-xs text-foreground outline-none focus:border-accent" />
              </div>
              <div>
                <div className="text-[11px] font-medium text-muted-foreground">File name</div>
                <input value={shareName} onChange={(e) => setShareName(e.target.value)} spellCheck={false}
                  className="mt-1 w-full rounded-lg border border-border bg-muted/30 px-2.5 py-1.5 text-xs text-foreground outline-none focus:border-accent" />
              </div>
            </div>
            {shareError && <p className="mt-3 text-[11px] text-danger">{shareError}</p>}
            {shareResult?.copied && <p className="mt-3 text-[11px] text-emerald-500">Copied to the clipboard.</p>}
            {shareResult?.path && (
              <div className="mt-3 rounded-lg border border-border/60 bg-muted/30 p-2">
                <p className="break-all text-[11px] text-muted-foreground">{shareResult.path}</p>
                <button type="button" onClick={() => postJSON("/api/reveal", { path: shareResult.path }).catch(() => {})}
                  className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-accent hover:underline cursor-pointer">
                  <FolderOpen className="h-3 w-3" /> Show in folder
                </button>
              </div>
            )}
            <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
              <button type="button" onClick={copySelectedAsText}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
                <Copy className="h-3.5 w-3.5" /> Copy as text
              </button>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => { setShareOpen(false); setShareResult(null); setShareError(""); }}
                  className="rounded-xl border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted cursor-pointer">
                  Close
                </button>
                <button type="button" disabled={shareBusy} onClick={doExport}
                  className="rounded-xl bg-accent px-3.5 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-accent/90 disabled:opacity-50 cursor-pointer">
                  {shareBusy ? "Saving…" : `Save ${shareFormat.toUpperCase()}`}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

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
                  <span className="text-amber-500 font-medium">Listening to voice… press Stop when you are done</span>
                ) : transcribing ? (
                  <span className="text-amber-500 font-medium">Writing down what you said…</span>
                ) : busy ? (
                  <span className="text-accent font-medium truncate">
                    {running > 1 ? `Working on ${running} tasks` : resolveAgentState(msgs.find((m) => m.live)?.events, true).label}
                  </span>
                ) : boot && !boot.ready ? (
                  <span className="text-muted-foreground">{boot.unreachable ? "Waiting for the server…" : `Starting up · ${boot.progress}% — you can already type`}</span>
                ) : (
                  "Local · private · ready"
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => setShowFeedback((visible) => {
              const next = !visible;
              try { localStorage.setItem("ishti-show-answer-feedback", String(next)); } catch {}
              return next;
            })}
              className="rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer"
              aria-pressed={showFeedback} title="Show or hide answer feedback controls">
              Feedback {showFeedback ? "On" : "Off"}
            </button>
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
              <motion.div
                initial={{ scale: 0.9, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.4 }}
                className="relative flex flex-col items-center justify-center cursor-pointer group select-none"
                onClick={newChat}
                title="ISHTI - Start New Chat"
              >
                <div className="absolute inset-0 -m-6 rounded-3xl bg-accent/15 blur-2xl pointer-events-none" />
                <IshtiBrand
                  variant="squircle"
                  size={76}
                  className="mb-3.5 drop-shadow-md group-hover:opacity-90 transition-opacity"
                />
                <IshtiBrand
                  variant="wordmark"
                  height={30}
                  className="drop-shadow-xs group-hover:opacity-90 transition-opacity"
                />
                <span className="text-[10px] font-bold tracking-[0.24em] text-muted-foreground uppercase mt-1 mb-2">
                  PERSONAL AI ASSISTANT
                </span>
              </motion.div>
              <div className="text-center flex flex-col items-center">
                <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">{greeting}, {owner}</h1>
                <p className="mt-1.5 text-sm text-muted-foreground max-w-md">
                  Your private executive AI workstation. What would you like to accomplish today?
                </p>
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
                          {m.revisionOf && <p className="mt-1 text-[11px] text-muted-foreground">Revised answer after your feedback · no action was repeated</p>}
                          {showFeedback && !m.live && m.messageId && !/\(yes\/no\)\s*$/i.test(m.text) && (
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                              <button type="button" onClick={() => void rateAnswer(m.messageId!, "positive")}
                                disabled={!!feedbackBusy} aria-label="This answer was helpful" aria-pressed={m.rating === "positive"}
                                className={cn("inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 hover:text-foreground disabled:opacity-50", m.rating === "positive" && "text-emerald-500 border-emerald-500/40")}>
                                <ThumbsUp className="h-3.5 w-3.5" /> Helpful
                              </button>
                              <button type="button" onClick={() => void rateAnswer(m.messageId!, "negative")}
                                disabled={!!feedbackBusy} aria-label="This answer was wrong; try another answer" aria-pressed={m.rating === "negative"}
                                className={cn("inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 hover:text-foreground disabled:opacity-50", m.rating === "negative" && "text-amber-500 border-amber-500/40")}>
                                <ThumbsDown className="h-3.5 w-3.5" /> Try again
                              </button>
                              <button type="button" onClick={() => { setFeedbackReasonFor(m.messageId!); setFeedbackReason(""); }}
                                className="hover:text-foreground">Explain what was wrong</button>
                              {feedbackBusy === m.messageId && <span role="status">Reviewing this answer…</span>}
                              {feedbackReasonFor === m.messageId && (
                                <div className="flex w-full gap-2">
                                  <input value={feedbackReason} onChange={(event) => setFeedbackReason(event.target.value.slice(0, 1000))}
                                    placeholder="Optional detail for a better revision" aria-label="What was wrong with this answer"
                                    className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1.5 text-foreground" />
                                  <button type="button" onClick={() => void rateAnswer(m.messageId!, "negative", feedbackReason)}
                                    disabled={!!feedbackBusy} className="rounded-lg bg-accent px-2 py-1.5 text-white disabled:opacity-50">Revise</button>
                                </div>
                              )}
                            </div>
                          )}
                          {feedbackError && feedbackError.messageId === m.messageId && <p role="alert" className="mt-1 text-xs text-destructive">{feedbackError.text}</p>}
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

        <form onSubmit={(e) => { e.preventDefault(); if (!recording && !transcribing && !uploading) send(input); }}
          onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
          onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); void addUploads(e.dataTransfer.files); } }}
          className="px-4 sm:px-6 pb-5 pt-1 shrink-0">
          <input ref={uploadInput} type="file" multiple hidden onChange={(e) => addUploads(e.currentTarget.files)} />
          {(uploads.length > 0 || uploading || uploadError) && (
            <div className="mx-auto mb-2 flex w-full max-w-4xl xl:max-w-5xl flex-wrap items-center gap-2 text-xs">
              {uploads.map((file, index) => <span key={`${file.name}-${index}`} className="inline-flex max-w-[240px] items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-1">
                <span className="truncate">{file.name}</span>
                <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setUploads((all) => all.filter((_, i) => i !== index))} className="text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /></button>
              </span>)}
              {uploading && <span className="text-muted-foreground">Uploading…</span>}
              {uploadError && <span role="alert" className="text-danger">{uploadError}</span>}
            </div>
          )}
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
                  onPaste={onComposerPaste}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!recording && !transcribing && !uploading) send(input); } }}
                  placeholder="Ask or tell ISHTI to do anything…"
                  style={{ fontSize: "var(--chat-input-size)" }}
                  className="scroll-thin max-h-[220px] min-h-[40px] flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2 leading-relaxed outline-none placeholder:text-muted-foreground" />
                <button type="button" onClick={() => uploadInput.current?.click()} disabled={uploading} title="Attach files for ISHTI to inspect" aria-label="Attach files"
                  className="grid h-9 w-9 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-muted disabled:opacity-40 cursor-pointer"><Paperclip className="h-4 w-4" /></button>
                <button type="button" onClick={toggleMic} title="Record a voice message (reply comes as text)"
                  className={`grid h-9 w-9 place-items-center rounded-xl transition-colors cursor-pointer ${recording ? "bg-danger text-white" : "text-muted-foreground hover:bg-muted"}`}>
                  {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </button>
                <button type="button" onClick={() => setLiveOpen(true)} title="Live: talk with ISHTI out loud (the only mode that speaks)"
                  className="flex h-9 items-center gap-1.5 rounded-xl border border-accent/40 px-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft/40 cursor-pointer">
                  <AudioLines className="h-4 w-4" /> Live
                </button>
                <button type="submit" disabled={(!input.trim() && !uploads.length) || uploading || recording || transcribing} title={recording ? "Stop the recording first" : transcribing ? "Writing down what you said…" : canStart ? "Send" : "Queue: runs when a slot is free"}
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
                  onPaste={onComposerPaste}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!recording && !transcribing && !uploading) send(input); } }}
                  placeholder="Ask or tell ISHTI to do anything…"
                  style={{ fontSize: "var(--chat-input-size)" }}
                  className="scroll-thin max-h-[220px] min-h-[40px] flex-1 resize-none overflow-y-auto bg-transparent px-2 py-2 leading-relaxed outline-none placeholder:text-muted-foreground" />
                <button type="button" onClick={() => uploadInput.current?.click()} disabled={uploading} title="Attach files for ISHTI to inspect" aria-label="Attach files"
                  className="grid h-9 w-9 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-muted disabled:opacity-40 cursor-pointer"><Paperclip className="h-4 w-4" /></button>
                <button type="button" onClick={toggleMic} title="Record a voice message (reply comes as text)"
                  className={`grid h-9 w-9 place-items-center rounded-xl transition-colors cursor-pointer ${recording ? "bg-danger text-white" : "text-muted-foreground hover:bg-muted"}`}>
                  {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </button>
                <button type="button" onClick={() => setLiveOpen(true)} title="Live: talk with ISHTI out loud (the only mode that speaks)"
                  className="flex h-9 items-center gap-1.5 rounded-xl border border-accent/40 px-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft/40 cursor-pointer">
                  <AudioLines className="h-4 w-4" /> Live
                </button>
                <button type="submit" disabled={(!input.trim() && !uploads.length) || uploading || recording || transcribing} title={recording ? "Stop the recording first" : transcribing ? "Writing down what you said…" : canStart ? "Send" : "Queue: runs when a slot is free"}
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
