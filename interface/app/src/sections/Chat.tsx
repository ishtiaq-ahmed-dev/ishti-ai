import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, AudioLines, Check, Copy, Maximize2, MessageSquare, Mic, Minimize2, Network, PanelLeft, PanelLeftClose, Paperclip, Pencil, Plus, Square, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AgentOrb } from "@/components/AgentOrb";
import { ActivityGraph, ActivityList, ConversationGraph, type Turn } from "@/components/ActivityGraph";
import BorderBeam from "border-beam";
import VoiceBeam from "voice-glow";
import { IshtiBrand } from "@/components/IshtiBrand";
import { Markdown } from "@/components/Markdown";
import { MediaPreview } from "@/components/MediaPreview";
import { LiveMode } from "@/components/LiveMode";
import { cn } from "@/lib/utils";
import { useBootState } from "@/lib/boot";
import { chatStore, type StoreChat, type StoreMessage, type StoreRun } from "@/lib/chat-store";
import { toggleAllThinking, Thinking, StreamedDraftBanner, UnverifiedBanner, runUnverified } from "@/components/RunStatusCard";
import { FeedbackPanel } from "@/components/FeedbackPanel";

function ago(iso: number) {
  const m = Math.round((Date.now() - iso * 1000) / 60000);
  return m < 1 ? "now" : m < 60 ? `${m}m` : m < 1440 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`;
}

// C1: copy a message's plain text (clipboard API, with an execCommand fallback for older WebView2 builds).
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {}
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

// C1: per-message copy button. A real <button> (keyboard + aria-label) that shows Copied for 1.5 s.
function CopyMessageButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const onCopy = () => {
    void copyText(text).then((ok) => {
      if (!ok) return;
      setCopied(true);
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={copied ? "Copied" : "Copy message"}
      title={copied ? "Copied" : "Copy message"}
      data-testid="copy-message"
      className={cn(
        "grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground/70 transition-opacity duration-150",
        "opacity-0 group-hover/msg:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
        "hover:text-foreground hover:bg-muted/60 cursor-pointer",
        copied && "opacity-100 text-accent",
      )}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

// Source chips under an answer: fed from the run's research steps (CONTRACT v2 run_step.sources).
function Sources({ run }: { run?: StoreRun }) {
  const urls = [...new Set((run?.steps || []).flatMap((s) => s.sources))];
  if (!urls.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {urls.slice(0, 6).map((u, i) => (
        <a key={i} href={u} target="_blank" rel="noreferrer"
          className="max-w-[220px] truncate rounded-full border border-border bg-background px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-accent hover:text-accent">
          {u.replace(/^https?:\/\//, "").split("/")[0]}
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
  // The v2 store (CONTRACT §14) is the single source of truth: it lives at app level, so switching
  // sections or reloading never unmounts a running stream. This component only subscribes to it.
  const [, force] = useState(0);
  useEffect(() => chatStore.subscribe(() => force((n) => n + 1)), []);

  const [showFeedback, setShowFeedback] = useState(() => {
    try { return localStorage.getItem("ishti-show-answer-feedback") !== "false"; } catch { return true; }
  });
  const [input, setInput] = useState("");
  const activeChatId = chatStore.activeChatId;
  const storeMsgs = chatStore.messagesOf(activeChatId);
  const liveRuns = activeChatId ? chatStore.runsOfChat(activeChatId).filter((r) => r.state === "running" || r.state === "queued") : [];
  const pendingRun = liveRuns.length ? liveRuns[liveRuns.length - 1] : null;
  const busy = liveRuns.length > 0;
  const chats = chatStore.chatList();
  // T2: offer Expand/Collapse-all when this chat shows more than one thinking block.
  const thinkingCount = storeMsgs.filter((m) => {
    const r = m.run_id ? chatStore.runs.get(m.run_id) : null;
    return r && r.steps.length > 0 && ["done", "failed", "cancelled"].includes(r.state);
  }).length;

  const [showHistory, setShowHistory] = useState(() => {
    try {
      const saved = localStorage.getItem("ishti-chat-show-history");
      if (saved !== null) return saved === "true";
    } catch {}
    // Phone widths: the drawer is a full-screen overlay, so don't cover the chat on first load.
    return typeof window === "undefined" ? true : window.innerWidth >= 640;
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
  // Recent-chats features (restored on the v2 store): client-side search, multi-select with
  // confirmed delete (local hide + undo), rename, and export of the selected chats.
  const [search, setSearch] = useState("");
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [undoOpen, setUndoOpen] = useState(false);
  const [managementError, setManagementError] = useState("");
  const [shareOpen, setShareOpen] = useState(false);
  const [shareResult, setShareResult] = useState<{ path: string; copied?: boolean } | null>(null);
  const [shareError, setShareError] = useState("");
  // Activity panel (per-answer trace + whole-chat turns) + live draft state for the orb/AgentOrb header.
  // graphFor: a message_id, "run:{run_id}", or "chat" (panel open, chat has no runs -> empty state).
  const [graphFor, setGraphFor] = useState<string | null>(null);
  const [graphView, setGraphView] = useState<"answer" | "chat">("answer");
  const [activityView, setActivityView] = useState<"list" | "graph">("list"); // T3: plain list is the default tab
  const [fullGraph, setFullGraph] = useState(false);

  useEffect(() => {
    const handleWidthChange = (e: Event) => {
      const detail = (e as CustomEvent<number>).detail;
      if (typeof detail === "number" && detail >= 180 && detail <= 550) setHistoryWidth(detail);
    };
    window.addEventListener("ishti-side-chats-width-changed", handleWidthChange);
    return () => window.removeEventListener("ishti-side-chats-width-changed", handleWidthChange);
  }, []);

  // History-panel resize: the pointer is captured on the handle so pointerup/pointercancel always
  // reach us even when released outside the window; every exit path (pointerup, pointercancel,
  // lostpointercapture, window blur, unmount) restores body cursor/userSelect. Previously a pointer
  // released outside the window left text selection disabled for the whole session.
  const startResizing = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    const handle = e.currentTarget as HTMLElement;
    const startX = e.clientX;
    const startWidth = historyWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    let currentW = historyWidth;
    let ended = false;
    const onPointerMove = (ev: PointerEvent) => {
      const delta = ev.clientX - startX;
      const maxWidth = Math.max(350, Math.min(550, window.innerWidth * 0.45));
      const nextWidth = Math.max(180, Math.min(Math.round(startWidth + delta), maxWidth));
      currentW = nextWidth;
      setHistoryWidth(nextWidth);
      try { localStorage.setItem("ishti-recent-chats-width", String(nextWidth)); } catch {}
    };
    const onResizeEnd = () => {
      if (ended) return;
      ended = true;
      setIsResizing(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onResizeEnd);
      window.removeEventListener("pointercancel", onResizeEnd);
      window.removeEventListener("blur", onResizeEnd);
      handle.removeEventListener("lostpointercapture", onResizeEnd);
      try { handle.releasePointerCapture(e.pointerId); } catch {}
      try { window.dispatchEvent(new CustomEvent("ishti-side-chats-width-changed", { detail: currentW })); } catch {}
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onResizeEnd);
    window.addEventListener("pointercancel", onResizeEnd);
    window.addEventListener("blur", onResizeEnd);
    handle.addEventListener("lostpointercapture", onResizeEnd);
    try { handle.setPointerCapture(e.pointerId); } catch {}
  }, [historyWidth]);

  // Belt-and-braces: if Chat unmounts mid-resize (section switch), restore body styles.
  useEffect(() => () => {
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  const [focusedSkill, setFocusedSkill] = useState<string | null>(() => {
    try { return localStorage.getItem("ishti-focused-skill"); } catch { return null; }
  });
  const clearFocusedSkill = useCallback(() => {
    setFocusedSkill(null);
    try { localStorage.removeItem("ishti-focused-skill"); } catch {}
  }, []);

  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false); // Stop pressed: the last words are being written into the box
  const [liveOpen, setLiveOpen] = useState(false);
  const [audioStream, setAudioStream] = useState<MediaStream | null>(null);
  // Live voice turns are display-only: the legacy live socket has no v2 chat binding yet.
  const [liveTurns, setLiveTurns] = useState<{ user: string; reply: string }[]>([]);
  const recorder = useRef<MediaRecorder | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<{ name: string; mention: string; size: number; action: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const scrollContainer = useRef<HTMLDivElement>(null);
  const wasBusy = useRef(false);
  const creatingChat = useRef(false);

  useEffect(() => {
    const transitioned = wasBusy.current && !busy;
    wasBusy.current = busy;
    onBusy?.(busy);
    if (transitioned) {
      try { window.dispatchEvent(new CustomEvent("ishti-chat-finished")); } catch {}
    }
  }, [busy, onBusy]);

  // WP1: hydrate the composer from the persisted per-chat draft (after the active chat is known)
  useEffect(() => {
    if (activeChatId) {
      const d = chatStore.draft(activeChatId);
      if (d) setInput(d);
    }
  }, [activeChatId]);

  const toggleHistory = useCallback(() => {
    setShowHistory((h) => {
      const next = !h;
      try { localStorage.setItem("ishti-chat-show-history", String(next)); } catch {}
      return next;
    });
  }, []);

  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 220)}px`; }
  }, [input]);

  useEffect(() => {
    const el = scrollContainer.current;
    if (el) el.scrollTop = el.scrollHeight;
    else bottom.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [storeMsgs.length, pendingRun?.text, pendingRun?.run_id, liveTurns.length]);

  // ---------------- derived selectors ----------------
  const q = search.trim().toLowerCase();
  const inProfile = chats.filter((c) => (c.profile || "personal") === chatStore.activeProfile);
  const visibleChats = (q ? inProfile.filter((c) => (c.title || "").toLowerCase().includes(q)) : inProfile);
  // Profiles never merge: each chat keeps the profile it was created with (runs, prompt layers
  // and memory namespace follow the chat), and the sidebar shows the two namespaces apart.
  const personalChats = visibleChats.filter((c) => (c.profile || "personal") === "personal");
  const allSelected = visibleChats.length > 0 && visibleChats.every((c) => selected.has(c.chat_id));
  const toggleSelected = useCallback((id: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);
  // T3: the Activity panel is open for any chat that has at least one run, defaulting to the last one.
  const chatRuns = activeChatId ? chatStore.runsOfChat(activeChatId) : [];
  const lastChatRun = chatRuns.length
    ? [...chatRuns].sort((a, b) => (a.started_at || 0) - (b.started_at || 0))[chatRuns.length - 1]
    : null;
  const graphRun = (() => {
    if (!graphFor) return null;
    if (graphFor === "chat") return lastChatRun;
    if (graphFor.startsWith("run:")) {
      // T3: a run key must keep resolving after the run FINISHES (it used to be looked up only in
      // liveRuns, so the panel went blank the moment a run completed).
      const id = graphFor.slice(4);
      return liveRuns.find((r) => r.run_id === id) || chatRuns.find((r) => r.run_id === id)
        || chatStore.runs.get(id) || null;
    }
    const msg = storeMsgs.find((m) => m.message_id === graphFor);
    return msg ? (chatStore.runs.get(msg.run_id) || null) : null;
  })();
  const turns: Turn[] = (() => {
    const out: Turn[] = [];
    let lastQuestion = "";
    for (const m of storeMsgs) {
      if (m.role === "user") { lastQuestion = m.text; continue; }
      const r = chatStore.runs.get(m.run_id);
      const steps = r?.steps || [];
      // the skill that took the turn, by name ("Using x" rows are the legacy shape of a skill step)
      const used = steps.find((s) => s.kind === "skill" || /^Using\s/i.test(s.label))?.label.replace(/^Using\s+/i, "");
      out.push({ question: lastQuestion, skill: used || (steps.some((s) => s.kind === "research") ? "research" : steps[0]?.kind || null), ms: undefined });
    }
    return out;
  })();

  useEffect(() => { // persist scroll position per chat (restored on return)
    const el = scrollContainer.current;
    if (el && activeChatId) chatStore.setScroll(activeChatId, el.scrollTop);
  });

  useEffect(() => { // keep pinned to the bottom while a run streams
    if (pendingRun) {
      const el = scrollContainer.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, [pendingRun?.text]);
  const confirmDelete = useCallback(async () => {
    setConfirmingDelete(false);
    const ids = [...selected];
    if (!ids.length) return;
    setSelecting(false);
    setSelected(new Set());
    setManagementError("");
    try {
      await chatStore.trashChats(ids);
      setUndoOpen(true);
      window.setTimeout(() => setUndoOpen(false), 8000);
    } catch (err) {
      setManagementError(err instanceof Error ? err.message : String(err));
    }
  }, [selected]);

  const saveRename = useCallback(async (id: string) => {
    const title = renameDraft.trim();
    setRenaming(null);
    if (!title) return;
    setManagementError("");
    try { await chatStore.setTitle(id, title); }
    catch (err) { setManagementError(err instanceof Error ? err.message : String(err)); }
  }, [renameDraft]);

  const doExport = useCallback(async () => {
    setShareError("");
    setShareResult(null);
    try {
      const parts: string[] = [];
      for (const id of selected) {
        const msgs = await (async () => {
          chatStore.setActiveChat(id);
          await chatStore.hydrateMessages(id);
          return chatStore.messagesOf(id);
        })();
        parts.push(`## ${chatStore.chats.get(id)?.title || id}`, "");
        for (const m of msgs) parts.push(`${m.role === "user" ? "You" : "ISHTI"}: ${m.text}`);
        parts.push("");
      }
      await navigator.clipboard.writeText(parts.join("\n"));
      setShareResult({ path: "(clipboard)", copied: true });
    } catch (err) {
      setShareError(err instanceof Error ? err.message : String(err));
    }
  }, [selected]);

  const newChat = useCallback(async () => {
    if (creatingChat.current) return;
    creatingChat.current = true;
    try {
      await chatStore.newChat();
      setInput("");
      setUploads([]);
      clearFocusedSkill();
      setManagementError("");
    } catch (err) {
      setManagementError(`Could not start a new chat: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      creatingChat.current = false;
    }
  }, [clearFocusedSkill]);

  // Deep links: #chat/new starts a chat, #chat/{id} selects it once the store knows the chats.
  // Runs on mount/ready/hashchange ONLY - never on activeChatId changes, or clicking a row would
  // race the (still stale) URL hash and flip straight back.
  useEffect(() => {
    if (!chatStore.ready) return;
    const apply = () => {
      const m = window.location.hash.match(/^#chat\/([^/?#]+)/i);
      if (!m) return;
      if (m[1] === "new") {
        void newChat();
        return;
      }
      if (m[1] !== chatStore.activeChatId && chatStore.chats.has(m[1])) chatStore.setActiveChat(m[1]);
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, [chatStore.ready, newChat]);

  // Keep the URL + the legacy localStorage key in step with the store's active chat.
  useEffect(() => {
    if (!activeChatId) return;
    try {
      localStorage.setItem("ishti-active-chat-v1", activeChatId);
      localStorage.setItem("ishti-session-id", activeChatId);
      if (window.location.hash.startsWith("#chat") || !window.location.hash) {
        window.history.replaceState(null, "", `#chat/${activeChatId}`);
      }
    } catch {}
  }, [activeChatId]);

  useEffect(() => {
    const handleStartNewChat = (e?: Event) => {
      const detail = (e as CustomEvent<{ focusedSkill?: string }> | undefined)?.detail;
      if (detail?.focusedSkill) {
        setFocusedSkill(detail.focusedSkill);
        try { localStorage.setItem("ishti-focused-skill", detail.focusedSkill); } catch {}
      } else {
        clearFocusedSkill();
      }
      void newChat();
    };
    window.addEventListener("ishti-start-new-chat", handleStartNewChat);
    return () => window.removeEventListener("ishti-start-new-chat", handleStartNewChat);
  }, [newChat, clearFocusedSkill]);

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
        const action = String(data.action || data.mention || "inspect this file").split(/[\r\n]/, 1)[0];
        setUploads((current) => [...current, { name: file.name, mention: data.mention, size: data.size || file.size, action }]);
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

  useEffect(() => { adjustTextareaHeight(); }, [input, adjustTextareaHeight]);

  async function send(text: string) {
    text = [text.trim(), ...uploads.map((f) => f.mention)].filter(Boolean).join("\n");
    if (!text) return;
    setUploads([]);
    setInput("");
    if (box.current) box.current.style.height = "40px";
    if (focusedSkill && !text.toLowerCase().startsWith("[skill:") && !text.toLowerCase().startsWith("/use ") && !text.toLowerCase().startsWith("/skill ")) {
      text = `[skill:${focusedSkill}] ${text}`;
    }
    // Single v2 send path: durable, idempotent on client_msg_id (WP1). No legacy stream here.
    let chatId = chatStore.activeChatId;
    if (!chatId) chatId = (await chatStore.newChat()).chat_id;
    await chatStore.send(chatId, text);
  }

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
          setManagementError(`Voice transcription failed: ${sttErr instanceof Error ? sttErr.message : String(sttErr)}`);
        } finally {
          setTranscribing(false);
          box.current?.focus();
        }
      };
      recorder.current = rec;
      rec.start(1000); // 1 s chunks feed the live transcript
      setRecording(true);
    } catch (micErr) {
      setManagementError(`Microphone unavailable: ${micErr instanceof Error ? micErr.message : String(micErr)}`);
      setRecording(false);
      setAudioStream(null);
    }
  }

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  const renderMessage = (m: StoreMessage) => (
    <motion.div key={m.message_id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className={m.role === "user" ? "group/msg flex justify-end min-w-0" : "group/msg flex gap-3 min-w-0 max-w-full"}>
      {m.role === "user" ? (
        <div className="group/msg flex min-w-0 max-w-full items-center gap-1">
          <div
            className="ishti-selectable max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-accent-foreground shadow-xs"
            style={{ fontSize: "var(--chat-font-size)", lineHeight: "var(--chat-leading)" }}
          >
          {(() => {
            const skillMatch = m.text.match(/^\[skill:([a-z0-9_-]+)\]\s*(.*)$/is);
            if (skillMatch) {
              return (
                <>
                  <span className="inline-block mr-2 rounded-md bg-white/25 px-1.5 py-0.5 text-[10px] font-mono font-bold uppercase tracking-wider">
                    {skillMatch[1]}
                  </span>
                  {skillMatch[2]}
                </>
              );
            }
            return m.text;
          })()}
          </div>
          <CopyMessageButton text={m.text} />
        </div>
      ) : (
        <>
          <IshtiBrand variant="icon" size={32} className="mt-0.5 shrink-0 shadow-xs" />
          <div
            className="ishti-selectable min-w-0 flex-1 max-w-full overflow-hidden"
            style={{ fontSize: "var(--chat-font-size)", lineHeight: "var(--chat-leading)" }}
          >
            {m.run_id && <Thinking runId={m.run_id} run={chatStore.runs.get(m.run_id)} />}
            <Markdown text={m.text} />
            <MediaPreview text={m.text} onSend={(p) => { setInput(`send "${p}" on whatsapp to `); box.current?.focus(); }} />
            <Sources run={m.run_id ? chatStore.runs.get(m.run_id) : undefined} />
            {/* WP6: channel tag for messages that arrived via WhatsApp/Telegram commands */}
            {m.channel && m.channel !== "text" && (
              <span className="mt-1 inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground" data-testid={`channel-tag-${m.channel}`}>
                via {m.channel}
              </span>
            )}
            {runUnverified(chatStore.runs.get(m.run_id) || null) && (
              <UnverifiedBanner detail={runUnverified(chatStore.runs.get(m.run_id) || null)} />
            )}
            <div className="mt-1 flex items-center gap-2">
              <CopyMessageButton text={m.text} />
              {/* WP2: v2 feedback bound to message_id + run_id (replaces the legacy single-chat rating) */}
              {showFeedback && <FeedbackPanel messageId={m.message_id} runId={m.run_id} />}
              {(chatStore.runs.get(m.run_id)?.steps.length || 0) > 0 && (
                <button onClick={() => setGraphFor(m.message_id)}
                  className="text-[11px] text-muted-foreground transition-colors hover:text-accent cursor-pointer">
                  View activity graph
                </button>
              )}
            </div>
            {/* T2 quick-replies: only the orchestrator asks, only the owner answers */}
            {m.text && /\(yes\/no\)\s*$/i.test(m.text) && (
              <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}
                className="mt-2.5 flex flex-wrap gap-2">
                <button onClick={() => void send("yes")}
                  className="rounded-xl bg-accent px-4 py-2 text-xs font-semibold text-white shadow-xs transition-transform active:scale-95 cursor-pointer hover:opacity-90">Go ahead</button>
                <button onClick={() => void send("no")}
                  className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted cursor-pointer">Cancel</button>
              </motion.div>
            )}
          </div>
        </>
      )}
    </motion.div>
  );

  const renderChatRow = (c: StoreChat) => {
    const isSelected = activeChatId === c.chat_id;
    return (
      <div
        key={c.chat_id}
        onClick={() => (selecting ? toggleSelected(c.chat_id) : chatStore.setActiveChat(c.chat_id))}
        onDoubleClick={(e) => { if (!selecting) { e.stopPropagation(); setRenaming(c.chat_id); setRenameDraft(c.title || ""); } }}
        className={cn(
          "group relative flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-sm transition-colors duration-150",
          isSelected
            ? "border-accent/70 bg-gradient-to-r from-accent/15 via-accent/8 to-transparent text-foreground font-semibold shadow-[0_2px_12px_rgba(110,86,207,0.12)] dark:shadow-[0_2px_12px_rgba(139,117,255,0.18)]"
            : "border-border/60 dark:border-white/10 bg-card/40 dark:bg-card/20 text-muted-foreground hover:border-accent/40 hover:bg-card/80 dark:hover:bg-card/45 hover:text-foreground",
        )}
      >
        {isSelected && (
          <div className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-accent shadow-[0_0_10px_var(--color-accent)]" />
        )}
        {selecting && (
          <span className={cn("grid h-4 w-4 shrink-0 place-items-center rounded border transition-colors",
            selected.has(c.chat_id) ? "border-accent bg-accent text-white" : "border-border")}>
            {selected.has(c.chat_id) && <Check className="h-3 w-3" />}
          </span>
        )}
        <MessageSquare
          className={cn(
            "h-4 w-4 shrink-0 transition-colors",
            isSelected ? "text-accent" : "text-muted-foreground/60 group-hover:text-muted-foreground"
          )}
        />
        {renaming === c.chat_id ? (
          <input autoFocus value={renameDraft} onClick={(e) => e.stopPropagation()}
            onChange={(e) => setRenameDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); saveRename(c.chat_id); }
              if (e.key === "Escape") { e.preventDefault(); setRenaming(null); }
            }}
            onBlur={() => saveRename(c.chat_id)}
            className="min-w-0 flex-1 rounded-md border border-accent/60 bg-background px-1.5 py-0.5 text-[13px] text-foreground outline-none"
          />
        ) : (
          <span className={cn("min-w-0 flex-1 truncate text-[13.5px] leading-snug",
            isSelected ? "text-foreground font-semibold" : "text-foreground/80 group-hover:text-foreground font-medium")}>
            {c.title || "New chat"}
          </span>
        )}
        {c.unread > 0 && (
          <span data-testid="chat-unread" className="shrink-0 rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-bold text-accent-foreground">
            {c.unread}
          </span>
        )}
        <div className="shrink-0 relative flex items-center justify-end h-5 min-w-[50px] ml-1">
          <span className={cn("text-[10.5px] tabular-nums transition-opacity duration-150 group-hover:opacity-0",
            isSelected ? "rounded-md bg-accent/20 px-1.5 py-0.5 text-accent font-semibold text-[10px]" : "text-muted-foreground/60")}>
            {ago(c.updated_at)}
          </span>
          <div className="absolute right-0 flex items-center gap-0.5 opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity duration-150">
            <button type="button" title="Rename chat"
              onClick={(e) => { e.stopPropagation(); setRenaming(c.chat_id); setRenameDraft(c.title || ""); }}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button type="button" title="Delete chat"
              onClick={(e) => { e.stopPropagation(); setDeleteTarget({ id: c.chat_id, title: c.title || "" }); }}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-danger/10 hover:text-danger cursor-pointer">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="relative flex h-full min-h-0 w-full overflow-hidden">
      {/* Side Recent Chats panel: the v2 chat list (CONTRACT §14 GET /api/chats) */}
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
                      selecting ? "border-accent bg-accent-soft text-accent" : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {selecting ? "Cancel" : "Select"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void newChat()}
                    className="flex items-center gap-1 rounded-lg bg-accent-soft px-2 py-1 text-xs font-medium text-accent transition-colors hover:bg-accent hover:text-white cursor-pointer"
                  >
                    <Plus className="h-3.5 w-3.5" /> New
                  </button>
                </div>
              </div>
              <div className="border-b border-border px-2.5 pb-2.5 pt-1">
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats…"
                  aria-label="Search chats" spellCheck={false}
                  className="w-full rounded-lg border border-border bg-muted/30 px-2.5 py-1.5 text-xs text-foreground outline-none focus:border-accent" />
              </div>
              <div className="scroll-thin flex-1 min-h-0 space-y-1.5 overflow-y-auto p-2.5">
                {visibleChats.length ? (
                  personalChats.map((c) => renderChatRow(c))
                ) : (
                  <div className="rounded-xl border border-dashed border-border/70 p-6 text-center text-xs text-muted-foreground">
                    {chatStore.ready ? (search ? "No chats match that search." : "Your chats appear here.") : "Loading chats…"}
                  </div>
                )}
              </div>
              {selecting && (
                <div className="shrink-0 border-t border-border bg-card/60 p-2.5">
                  <div className="flex items-center justify-between gap-1.5 px-0.5 pb-2 text-[11px] text-muted-foreground">
                    <button type="button" onClick={() => setSelected(allSelected ? new Set() : new Set(visibleChats.map((c) => c.chat_id)))}
                      className="font-medium transition-colors hover:text-foreground cursor-pointer">
                      {allSelected ? "Clear all" : "Select all"}
                    </button>
                    <span>{selected.size} selected</span>
                  </div>
                  {confirmingDelete ? (
                    <div className="flex items-center gap-1.5">
                      <button type="button" onClick={() => void confirmDelete()}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-danger px-2 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-danger/90 cursor-pointer">
                        <Trash2 className="h-3.5 w-3.5" /> Delete {selected.size}
                      </button>
                      <button type="button" onClick={() => setConfirmingDelete(false)}
                        className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
                        Keep
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <button type="button" disabled={!selected.size} onClick={() => setConfirmingDelete(true)}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-danger/90 px-2 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-danger disabled:cursor-default disabled:opacity-40 cursor-pointer">
                        <Trash2 className="h-3.5 w-3.5" /> Delete
                      </button>
                      <button type="button" disabled={!selected.size} onClick={() => setShareOpen(true)}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border px-2 py-1.5 text-[11px] font-semibold text-foreground transition-colors hover:bg-muted disabled:cursor-default disabled:opacity-40 cursor-pointer">
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
            <AgentOrb live={busy} recording={recording} size={42} className="shrink-0" />
            <div>
              <IshtiBrand
                variant="wordmark"
                height={26}
                className="translate-y-0.5 cursor-pointer hover:opacity-80 transition-opacity"
                onClick={() => void newChat()}
                title="ISHTI - Start New Chat"
              />
              <div className="text-[11px] text-muted-foreground flex items-center gap-1.5 min-w-0">
                {recording ? (
                  <span className="text-amber-500 font-medium">Listening to voice… press Stop when you are done</span>
                ) : transcribing ? (
                  <span className="text-amber-500 font-medium">Writing down what you said…</span>
                ) : busy ? (
                  <span className="text-accent font-medium truncate">
                    {liveRuns.length > 1 ? `Working on ${liveRuns.length} tasks` : "Working on it…"}
                  </span>
                ) : chatStore.connection === "reconnecting" ? (
                  <span className="text-amber-500 font-medium">Reconnecting to the server…</span>
                ) : boot && !boot.ready ? (
                  <span className="text-muted-foreground">{boot.unreachable ? "Waiting for the server…" : `Starting up · ${boot.progress}% — you can already type`}</span>
                ) : (
                  "Ready"
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {thinkingCount > 1 && (
              <span className="flex items-center text-[11px] text-muted-foreground" data-testid="thinking-bulk">
                <button type="button" onClick={() => toggleAllThinking(true)}
                  className="rounded-md px-1.5 py-1 transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
                  title="Expand every thinking panel in this chat">
                  Expand all
                </button>
                <span aria-hidden>·</span>
                <button type="button" onClick={() => toggleAllThinking(false)}
                  className="rounded-md px-1.5 py-1 transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
                  title="Collapse every thinking panel in this chat">
                  Collapse all
                </button>
              </span>
            )}
            <button type="button" onClick={() => setShowFeedback((visible) => {
              const next = !visible;
              try { localStorage.setItem("ishti-show-answer-feedback", String(next)); } catch {}
              return next;
            })}
              className="rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer"
              aria-pressed={showFeedback} title="Show or hide answer feedback controls">
              Feedback {showFeedback ? "On" : "Off"}
            </button>
            <button onClick={() => setGraphFor(graphFor ? null : (pendingRun ? `run:${pendingRun.run_id}` : lastChatRun ? `run:${lastChatRun.run_id}` : "chat"))}
              aria-expanded={!!graphFor}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
              <Network className="h-4 w-4" /> Activity
            </button>
            <button onClick={() => void newChat()}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
              <Plus className="h-4 w-4" /> New chat
            </button>
          </div>
        </header>

        <div ref={scrollContainer} className="scroll-thin min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          {storeMsgs.length === 0 && !pendingRun && liveTurns.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-6 px-6">
              <motion.div
                initial={{ scale: 0.9, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.4 }}
                className="relative flex flex-col items-center justify-center cursor-pointer group select-none"
                onClick={() => void newChat()}
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
                  Your AI workspace. What would you like to accomplish today?
                </p>
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-4xl xl:max-w-5xl space-y-5 px-4 sm:px-6 py-6 min-w-0">
              <AnimatePresence initial={false}>
                {storeMsgs.map(renderMessage)}
                {liveTurns.map((t, i) => (
                  <div key={`live-${i}`} className="space-y-3">
                    <div className="flex justify-end min-w-0">
                      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-accent-foreground shadow-xs">
                        {t.user}
                      </div>
                    </div>
                    <div className="flex gap-3 min-w-0 max-w-full">
                      <IshtiBrand variant="icon" size={32} className="mt-0.5 shrink-0 shadow-xs" />
                      <div className="min-w-0 flex-1 max-w-full overflow-hidden">
                        <Markdown text={t.reply} />
                      </div>
                    </div>
                  </div>
                ))}
                {pendingRun && (
                  <motion.div key={pendingRun.run_id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.22, ease: "easeOut" }} className="flex gap-3 min-w-0 max-w-full" data-testid="pending-run">
                    <IshtiBrand variant="icon" size={32} className="mt-0.5 shrink-0 shadow-xs" />
                    <div className="min-w-0 flex-1 max-w-full overflow-hidden space-y-2">
                      <Thinking run={pendingRun} onCancel={(id) => void chatStore.cancelRun(id)} />
                      {pendingRun.text ? (
                        <>
                          <StreamedDraftBanner />
                          <div data-testid="run-draft">
                            <Markdown text={pendingRun.text} />
                          </div>
                        </>
                      ) : null}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              <div ref={bottom} />
            </div>
          )}
        </div>

        <form onSubmit={(e) => { e.preventDefault(); if (!recording && !transcribing && !uploading) void send(input); }}
          onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
          onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); void addUploads(e.dataTransfer.files); } }}
          className="px-4 sm:px-6 pb-5 pt-1 shrink-0">
          <input ref={uploadInput} type="file" multiple hidden onChange={(e) => addUploads(e.currentTarget.files)} />
          {(uploads.length > 0 || uploading || uploadError) && (
            <div className="mx-auto mb-2 flex w-full max-w-4xl xl:max-w-5xl flex-wrap items-center gap-2 text-xs">
              {uploads.map((file, index) => <span key={`${file.name}-${index}`} className="inline-flex max-w-[360px] items-center gap-1 rounded-xl border border-border bg-muted/40 px-2 py-1">
                <span className="min-w-0"><span className="block truncate font-medium">{file.name}</span><span className="block truncate text-[10px] text-muted-foreground">ISHTI will {file.action}</span></span>
                <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setUploads((all) => all.filter((_, i) => i !== index))} className="text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /></button>
              </span>)}
              {uploading && <span className="text-muted-foreground">Uploading…</span>}
              {uploadError && <span role="alert" className="text-danger">{uploadError}</span>}
            </div>
          )}

          {focusedSkill && (
            <div className="mx-auto mb-2 flex w-full max-w-4xl xl:max-w-5xl items-center justify-between rounded-xl border border-accent/40 bg-accent/10 px-3.5 py-1.5 shadow-xs">
              <div className="flex items-center gap-2 min-w-0">
                <span className="flex h-2 w-2 rounded-full bg-accent animate-pulse shrink-0" />
                <span className="text-xs font-bold text-accent font-mono shrink-0">
                  Target Skill: {focusedSkill}
                </span>
                <span className="text-[11px] text-muted-foreground truncate">
                  Tasks in this chat will be executed directly using this skill
                </span>
              </div>
              <button
                type="button"
                onClick={clearFocusedSkill}
                className="text-muted-foreground hover:text-foreground text-xs p-1 rounded-md hover:bg-muted transition-colors cursor-pointer shrink-0 ml-2"
                title="Clear skill focus"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {recording ? (
            <VoiceBeam
              active={recording}
              stream={audioStream}
              colorVariant="gold"
              theme="auto"
              className="mx-auto w-full max-w-4xl xl:max-w-5xl rounded-2xl"
            >
              <div className="flex w-full items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-[0_2px_12px_rgba(29,27,46,0.06)] transition-shadow focus-within:shadow-[0_2px_18px_rgba(110,86,207,0.18)]">
                <textarea ref={box} value={input} onChange={(e) => { setInput(e.target.value); if (activeChatId) chatStore.setDraft(activeChatId, e.target.value); }} rows={1}
                  onPaste={onComposerPaste}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!recording && !transcribing && !uploading) void send(input); } }}
                  placeholder={focusedSkill ? `Assign a task using ${focusedSkill} skill…` : "Ask or tell ISHTI to do anything…"}
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
                <button type="submit" disabled={(!input.trim() && !uploads.length) || uploading || recording || transcribing} title={recording ? "Stop the recording first" : transcribing ? "Writing down what you said…" : "Send"}
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
                <textarea ref={box} value={input} onChange={(e) => { setInput(e.target.value); if (activeChatId) chatStore.setDraft(activeChatId, e.target.value); }} rows={1}
                  onPaste={onComposerPaste}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!recording && !transcribing && !uploading) void send(input); } }}
                  placeholder={focusedSkill ? `Assign a task using ${focusedSkill} skill…` : "Ask or tell ISHTI to do anything…"}
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
                <button type="submit" disabled={(!input.trim() && !uploads.length) || uploading || recording || transcribing} title={recording ? "Stop the recording first" : transcribing ? "Writing down what you said…" : "Send"}
                  className="grid h-9 w-9 place-items-center rounded-xl bg-accent text-white transition-opacity disabled:opacity-40 cursor-pointer">
                  <ArrowUp className="h-4 w-4" />
                </button>
              </div>
            </BorderBeam>
          )}
        </form>
      </div>

      <AnimatePresence>
        {liveOpen && (
          <LiveMode sessionId={activeChatId} onClose={() => setLiveOpen(false)}
            onTurn={(user, reply) => {
              setLiveTurns((all) => [...all, { user, reply }]);
              if (activeChatId) void chatStore.persistLiveTurn(activeChatId, user, reply);
            }} />
        )}
      </AnimatePresence>

      {/* Undo toast: 8 s to restore hidden chats (nothing is destroyed server-side) */}
      {undoOpen && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
          className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
          <div className="flex items-center gap-3 rounded-2xl border border-border bg-background/95 px-4 py-2.5 shadow-2xl backdrop-blur-md">
            <Trash2 className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="text-xs text-foreground">Chat deleted</span>
            <button type="button" onClick={() => { void chatStore.undoTrash().then(() => setUndoOpen(false)).catch((err) => setManagementError(err instanceof Error ? err.message : String(err))); }}
              className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-accent/90 cursor-pointer">
              Undo
            </button>
            <button type="button" onClick={() => setUndoOpen(false)} title="Dismiss"
              className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted cursor-pointer">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </motion.div>
      )}

      {(managementError || chatStore.error) && (
        <div role="alert" className="fixed bottom-20 left-1/2 z-[60] -translate-x-1/2 rounded-xl border border-danger/40 bg-background px-4 py-2 text-xs text-danger shadow-xl">
          {managementError || chatStore.error}
        </div>
      )}

      {/* Delete confirmation dialog: Yes / No (server-side trash; undo restores) */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 animate-in fade-in duration-150"
          onClick={() => setDeleteTarget(null)}>
          <div className="w-full max-w-sm rounded-2xl border border-border bg-background p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-xl bg-danger/15 text-danger shrink-0">
                <Trash2 className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-semibold text-foreground">Delete this chat?</h3>
                <p className="mt-1 text-xs text-muted-foreground truncate font-medium">{deleteTarget.title || "Untitled Conversation"}</p>
              </div>
            </div>
            <p className="mt-3 text-xs text-muted-foreground leading-relaxed">
              The chat is hidden from your list. You can undo for 8 seconds after deleting.
            </p>
            <div className="mt-5 flex items-center justify-end gap-2">
              <button type="button" onClick={() => setDeleteTarget(null)}
                className="rounded-xl border border-border px-3.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer">
                No, keep
              </button>
              <button type="button" onClick={() => { const id = deleteTarget.id; setDeleteTarget(null); setManagementError(""); void chatStore.trashChats([id]).then(() => { setUndoOpen(true); window.setTimeout(() => setUndoOpen(false), 8000); }).catch((err) => setManagementError(err instanceof Error ? err.message : String(err))); }}
                className="rounded-xl bg-danger px-4 py-1.5 text-xs font-semibold text-white hover:bg-danger/90 transition-colors shadow-xs cursor-pointer">
                Yes, delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Share: copy the selected chats' text locally. Nothing is uploaded. */}
      {shareOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl border border-border bg-background p-5 shadow-2xl">
            <h3 className="text-sm font-semibold text-foreground">Share {selected.size} chat{selected.size > 1 ? "s" : ""}</h3>
            <p className="mt-1 text-[11px] text-muted-foreground">Copies the conversation text to your clipboard - nothing is uploaded.</p>
            <div className="mt-4 space-y-3">
              <p className="text-xs text-muted-foreground">Markdown text</p>
            </div>
            {shareError && <p className="mt-3 text-[11px] text-danger">{shareError}</p>}
            {shareResult?.copied && <p className="mt-3 text-[11px] text-emerald-500">Copied to the clipboard.</p>}
            <div className="mt-5 flex items-center justify-end gap-2">
              <button type="button" onClick={() => { setShareOpen(false); setShareResult(null); setShareError(""); }}
                className="rounded-xl border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted cursor-pointer">
                Close
              </button>
              <button type="button" disabled={!selected.size} onClick={() => void doExport()}
                className="rounded-xl bg-accent px-3.5 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-accent/90 disabled:opacity-50 cursor-pointer">
                Copy
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Activity panel: a plain list of real run steps (default) plus the SVG graph as the second tab. */}
      <AnimatePresence>
        {graphFor && (
          <motion.aside initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }}
            transition={{ duration: 0.28, ease: "easeOut" }}
            data-testid="activity-panel"
            className="scroll-thin absolute inset-y-0 right-0 z-20 flex w-[min(480px,94%)] min-w-0 flex-col gap-3 overflow-y-auto overflow-x-hidden border-l border-border bg-background p-4 pb-6 shadow-[-12px_0_40px_rgba(29,27,46,0.12)] xl:static xl:z-auto xl:w-[480px] xl:shrink-0 xl:shadow-none">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-1.5">
                {graphRun && (
                  <div className="flex rounded-xl bg-muted p-0.5 text-xs">
                    {(["list", "graph"] as const).map((v) => (
                      <button key={v} onClick={() => setActivityView(v)} data-testid={`activity-view-${v}`}
                        aria-pressed={activityView === v}
                        className={`relative rounded-lg px-3 py-1 font-medium transition-colors cursor-pointer ${activityView === v ? "text-foreground" : "text-muted-foreground"}`}>
                        {v === "list" ? "List" : "Graph"}
                      </button>
                    ))}
                  </div>
                )}
                {graphRun && activityView === "graph" && (
                  <div className="flex rounded-xl bg-muted p-0.5 text-xs">
                    {(["answer", "chat"] as const).map((v) => (
                      <button key={v} onClick={() => setGraphView(v)}
                        className={`relative rounded-lg px-3 py-1 font-medium transition-colors cursor-pointer ${graphView === v ? "text-foreground" : "text-muted-foreground"}`}>
                        {v === "answer" ? "This answer" : "This chat"}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex shrink-0 gap-1">
                {graphRun && activityView === "graph" && (
                  <button onClick={() => setFullGraph(true)} title="Expand"
                    className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted cursor-pointer"><Maximize2 className="h-4 w-4" /></button>
                )}
                <button onClick={() => setGraphFor(null)} title="Close" aria-label="Close activity"
                  className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted cursor-pointer">
                  <X className="h-4 w-4" /></button>
              </div>
            </div>
            {!graphRun ? (
              <p className="rounded-2xl border border-dashed border-border p-4 text-center text-xs text-muted-foreground" data-testid="activity-empty">
                No activity yet — send a message and this chat's steps will appear here.
              </p>
            ) : (
              <>
                <div className="min-w-0 break-words text-[11px] text-muted-foreground">
                  {activityView === "list"
                    ? `${chatRuns.length} run${chatRuns.length === 1 ? "" : "s"} in this chat`
                    : graphView === "answer"
                      ? (graphRun.steps[graphRun.steps.length - 1]?.label || "Run steps")
                      : `${turns.length} question${turns.length === 1 ? "" : "s"} in this chat`}
                </div>
                {activityView === "list" ? (
                  <ActivityList runs={graphView === "answer" && graphFor !== "chat" ? [graphRun] : chatRuns} />
                ) : graphView === "answer" ? (
                  <ActivityGraph
                    steps={graphRun.steps || []}
                    request={storeMsgs.find((m) => m.run_id === graphRun.run_id)?.text || ""}
                    state={graphRun.state}
                    ms={graphRun.started_at && graphRun.ended_at ? (graphRun.ended_at - graphRun.started_at) * 1000 : undefined}
                  />
                ) : (
                  <ConversationGraph turns={turns} />
                )}
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  {activityView === "list"
                    ? "Every row is a real step from this run. Tap a row for its detail. Updates live while ISHTI works."
                    : graphView === "answer"
                      ? "Every node is a real step from this run. Violet = running, green = done, amber = waiting for you, red = failed."
                      : "Each question in this chat, linked to how it was handled."}
                </p>
              </>
            )}
          </motion.aside>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {fullGraph && graphRun && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}
            className="fixed inset-0 z-50 flex flex-col gap-3 bg-background/95 p-6 backdrop-blur-sm"
            onKeyDown={(e) => e.key === "Escape" && setFullGraph(false)}>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-base font-semibold">Activity</div>
                <div className="text-xs text-muted-foreground">Scroll to zoom · drag to move · {graphView === "answer" ? graphRun.run_id : `${turns.length} questions`}</div>
              </div>
              <button onClick={() => setFullGraph(false)} className="rounded-xl border border-border p-2 text-muted-foreground hover:bg-muted cursor-pointer">
                <Minimize2 className="h-4 w-4" /></button>
            </div>
            <div className="min-h-0 flex-1">
              {graphView === "answer" ? (
                <ActivityGraph
                  steps={graphRun.steps || []}
                  request={storeMsgs.find((m) => m.run_id === graphRun.run_id)?.text || ""}
                  state={graphRun.state}
                  ms={graphRun.started_at && graphRun.ended_at ? (graphRun.ended_at - graphRun.started_at) * 1000 : undefined}
                  full
                />
              ) : (
                <ConversationGraph turns={turns} full />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default Chat;
