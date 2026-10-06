// WP1 app-level chat store (CONTRACT v2 §14): single source of truth OUTSIDE route components.
// Chats/messages/runs live here so switching sections never unmounts chat state. Hydrated from
// GET /api/chats + /api/chats/{id}/messages, advanced by one shared event stream (since_seq replay,
// auto-resync on gap). Drafts + scroll positions persisted per chat (survive navigation, reload).
import { deleteJSON, getJSON, patchJSON, postJSON, putJSON } from "@/lib/api";

export type Profile = "personal" | "business";
export type RunState = "queued" | "running" | "done" | "failed" | "cancelled";

export type StoreMessage = {
  seq: number;
  message_id: string;
  role: "user" | "assistant";
  text: string;
  run_id: string;
  ts: number;
  channel: string; // text | whatsapp | telegram (WP6 channel tag)
  pending?: boolean; // optimistic user message not yet acked
};

export type StoreRun = {
  run_id: string;
  chat_id: string;
  state: RunState;
  text: string; // accumulated streamed text (working draft)
  steps: { kind: "research" | "tool" | "verify"; label: string; n: number; sources: string[]; ts?: number }[];
  profile?: string;
  error?: string | null;
  started_at?: number | null;
  ended_at?: number | null;
  lastSeq: number; // last event seq applied to this run (resync guard)
};

export type StoreChat = {
  chat_id: string;
  title: string;
  profile: Profile;
  created_at: number;
  updated_at: number;
  unread: number;
};

type Listener = () => void;

const DRAFT_KEY = "ishti-chat-drafts-v1"; // {chatId: text}
const SCROLL_KEY = "ishti-chat-scrolls-v1"; // {chatId: scrollTop}

class ChatStore {
  chats = new Map<string, StoreChat>();
  messages = new Map<string, StoreMessage[]>(); // chat_id -> ordered
  runs = new Map<string, StoreRun>(); // run_id -> state (cross-chat: pending runs keep their chat)
  activeChatId: string | null = null;
  activeProfile: Profile = "personal";
  connection: "connecting" | "live" | "reconnecting" = "connecting";
  error: string | null = null;
  lastSeq = 0;
  ready = false;

  private listeners = new Set<Listener>();
  private pollTimer: number | null = null;
  private started = false;
  private generation = 0; // invalidates in-flight sends on restart

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    for (const fn of [...this.listeners]) {
      try { fn(); } catch {}
    }
  }

  // ---------------- drafts (per chat, persisted) ----------------
  drafts: Record<string, string> = (() => {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}"); } catch { return {}; }
  })();

  setDraft(chatId: string, text: string) {
    if (text) this.drafts[chatId] = text;
    else delete this.drafts[chatId];
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(this.drafts)); } catch {}
    this.emit();
  }

  draft(chatId: string | null): string {
    return (chatId && this.drafts[chatId]) || "";
  }

  // ---------------- scroll positions ----------------
  scrollFor(chatId: string): number {
    try { return (JSON.parse(localStorage.getItem(SCROLL_KEY) || "{}") as Record<string, number>)[chatId] || 0; } catch { return 0; }
  }

  setScroll(chatId: string, top: number) {
    try {
      const all = JSON.parse(localStorage.getItem(SCROLL_KEY) || "{}");
      all[chatId] = top;
      localStorage.setItem(SCROLL_KEY, JSON.stringify(all));
    } catch {}
  }

  // ---------------- lifecycle ----------------
  async start() {
    if (this.started) return;
    this.started = true;
    // Fresh store context: replay from 0. Replay is idempotent (message_created dedupes by
    // message_id, run events converge in log order), so a full replay can never corrupt state -
    // but a stale lastSeq CAN silently skip history (observed live), so reset it here.
    this.lastSeq = 0;
    this.reconciled = false;
    const gen = ++this.generation;
    try {
      const p = await getJSON<{ active: Profile }>("/api/profile");
      if (gen === this.generation) this.activeProfile = p.active;
    } catch {}
    try {
      const d = await getJSON<{ chats: StoreChat[] }>("/api/chats");
      if (gen !== this.generation) return;
      for (const c of d.chats) {
        this.chats.set(c.chat_id, { ...c, unread: 0, profile: c.profile || "personal" });
      }
      const saved = this.savedActiveChat(this.activeProfile);
      this.activeChatId = saved && this.chats.has(saved) ? saved : d.chats[0]?.chat_id || null;
      if (!this.chats.size) {
        const c = await postJSON<{ chat_id: string; profile: Profile }>("/api/chats", {});
        this.chats.set(c.chat_id, { chat_id: c.chat_id, title: "New chat", profile: c.profile, created_at: Date.now() / 1000, updated_at: Date.now() / 1000, unread: 0 });
        this.activeChatId = c.chat_id;
      }
      if (this.activeChatId) await this.hydrateMessages(this.activeChatId);
      this.ready = true;
      this.emit();
    } catch {
      this.ready = true; // backend down: UI still renders, retry loop keeps trying
      this.emit();
    }
    this.pollEvents(0); // one shared stream for the whole app
  }

  private savedActiveChat(profile: Profile = this.activeProfile): string | null {
    try { return localStorage.getItem(`ishti-active-chat-v1-${profile}`) || localStorage.getItem("ishti-active-chat-v1"); } catch { return null; }
  }

  setActiveChat(chatId: string) {
    if (!this.chats.has(chatId)) return;
    this.activeChatId = chatId;
    const chat = this.chats.get(chatId)!;
    chat.unread = 0;
    try {
      localStorage.setItem("ishti-active-chat-v1", chatId);
      localStorage.setItem(`ishti-active-chat-v1-${chat.profile}`, chatId);
    } catch {}
    void this.hydrateMessages(chatId);
    this.emit();
  }

  async newChat(profile?: Profile): Promise<StoreChat> {
    // Pin the profile at creation: a chat keeps its profile forever (its runs, prompt layers and
    // memory namespace follow the chat), so an in-flight global switch must not blur it.
    const wanted = profile ?? this.activeProfile;
    const cur = this.activeChatId ? this.chats.get(this.activeChatId) : null;
    if (cur && cur.profile === wanted && !(this.messages.get(cur.chat_id) || []).length) return cur; // already blank
    const c = await postJSON<{ chat_id: string; profile: Profile }>("/api/chats", { profile: wanted });
    const chat: StoreChat = { chat_id: c.chat_id, title: "New chat", profile: c.profile, created_at: Date.now() / 1000, updated_at: Date.now() / 1000, unread: 0 };
    this.chats.set(c.chat_id, chat);
    this.messages.set(c.chat_id, []);
    this.activeChatId = c.chat_id;
    try {
      localStorage.setItem("ishti-active-chat-v1", c.chat_id);
      localStorage.setItem(`ishti-active-chat-v1-${chat.profile}`, c.chat_id);
    } catch {}
    this.emit();
    return chat;
  }

  async hydrateMessages(chatId: string) {
    try {
      const d = await getJSON<{ messages: StoreMessage[] }>(`/api/chats/${chatId}/messages`);
      const server = d.messages || [];
      const serverIds = new Set(server.map((m) => m.message_id));
      const local = this.messages.get(chatId) || [];
      // Server rows are authoritative; keep local-only rows (recovered run-* answers and
      // optimistic pending rows until the server acks them). The previous logic rebuilt the
      // list without rows it already had, silently deleting history on every re-hydrate.
      const acked = (m: StoreMessage) => m.pending && server.some((s) => s.role === m.role && s.text === m.text);
      const merged = [...server, ...local.filter((m) => !serverIds.has(m.message_id) && !acked(m) && (m.pending || m.message_id.startsWith("run-")))];
      merged.sort((a, b) => (a.seq === b.seq ? a.ts - b.ts : a.seq - b.seq));
      this.messages.set(chatId, merged);
      this.emit();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
      this.emit();
    }
  }

  // ---------------- send (optimistic, idempotent) ----------------
  async send(chatId: string, text: string, attachments?: unknown[]): Promise<{ message_id: string; run_id: string } | null> {
    const clean = text.trim();
    if (!clean) return null;
    const clientMsgId = (crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`);
    const optimistic: StoreMessage = {
      seq: Number.MAX_SAFE_INTEGER, message_id: `pending-${clientMsgId}`, role: "user",
      text: clean, run_id: "", ts: Date.now() / 1000, channel: "text", pending: true,
    };
    this.messages.set(chatId, [...(this.messages.get(chatId) || []), optimistic]);
    this.setDraft(chatId, "");
    this.emit();
    try {
      const res = await postJSON<{ message_id: string; run_id: string; duplicate?: boolean }>(
        `/api/chats/${chatId}/messages`,
        { client_msg_id: clientMsgId, text: clean, attachments: attachments ?? [] });
      // ack: replace the optimistic row with the server row (no duplicates on retry)
      const list = (this.messages.get(chatId) || []).filter((m) => m.message_id !== optimistic.message_id);
      if (!list.some((m) => m.message_id === res.message_id)) {
        list.push({ seq: Number.MAX_SAFE_INTEGER - 1, message_id: res.message_id, role: "user",
                    text: clean, run_id: res.run_id, ts: Date.now() / 1000, channel: "text" });
      }
      list.sort((a, b) => (a.seq === b.seq ? a.ts - b.ts : a.seq - b.seq));
      this.messages.set(chatId, list);
      if (!this.runs.has(res.run_id)) {
        this.runs.set(res.run_id, { run_id: res.run_id, chat_id: chatId, state: "queued",
                                    text: "", steps: [], lastSeq: this.lastSeq });
      }
      this.emit();
      this.error = null;
      return res;
    } catch (err) {
      // offline: keep the message pending and restore the draft (never drop the user's text)
      this.setDraft(chatId, clean);
      this.error = `Message not sent: ${err instanceof Error ? err.message : String(err)}`;
      this.emit();
      return null;
    }
  }

  async cancelRun(runId: string) {
    try {
      await postJSON(`/api/runs/${runId}/cancel`);
      this.error = null;
    } catch (err) {
      this.error = `Could not stop the run: ${err instanceof Error ? err.message : String(err)}`;
      this.emit();
    }
  }

  async persistLiveTurn(chatId: string, user: string, reply: string) {
    try {
      await postJSON(`/api/chats/${chatId}/live-turns`, { user, reply });
      await this.hydrateMessages(chatId);
      this.error = null;
    } catch (err) {
      this.error = `Live turn was not saved: ${err instanceof Error ? err.message : String(err)}`;
      this.emit();
    }
  }

  // ---------------- one shared event stream (poll with since_seq; auto-resync on gap) ----------------
  private pollFailures = 0;

  private async pollEvents(since: number) {
    if (this.generation === 0) return;
    const gen = this.generation;
    try {
      const d = await getJSON<{ events: { seq: number; type: string; data: any }[]; last_seq: number }>(
        `/api/events?since_seq=${since}`);
      if (gen !== this.generation) return;
      this.connection = "live";
      this.pollFailures = 0;
      for (const ev of d.events) this.applyEvent(ev);
      if (since === 0) await this.reconcileRuns(); // first join: trust the runs table over a truncated replay
      // gap detection: the log moved past `since` without contiguous rows -> resync from the messages endpoint
      if (since > 0 && d.events.length && d.events[0].seq > since + 1 && this.activeChatId) {
        void this.hydrateMessages(this.activeChatId);
      }
      this.lastSeq = Math.max(this.lastSeq, d.last_seq);
      this.emit();
    } catch {
      this.pollFailures += 1;
      if (this.pollFailures >= 2) this.connection = "reconnecting";
    }
    const backoff = Math.min(2000, 250 * Math.max(1, this.pollFailures));
    this.pollTimer = window.setTimeout(() => this.pollEvents(this.lastSeq), backoff);
  }

  stop() {
    this.generation = 0;
    if (this.pollTimer) { clearTimeout(this.pollTimer); this.pollTimer = null; }
  }

  restart() {
    this.started = false;
    this.stop();
    void this.start();
  }

  // ---------------- event application (pure state transitions) ----------------
  applyEvent(ev: { seq: number; type: string; data: any; ts?: number }) {
    if (ev.seq <= this.lastSeq) return; // duplicate suppression (idempotent replay)
    this.lastSeq = ev.seq;
    const d = ev.data || {};
    switch (ev.type) {
      case "message_created": {
        const chatId = d.chat_id as string;
        const m = d.message as StoreMessage;
        if (!chatId || !m) break;
        const list = (this.messages.get(chatId) || []).filter(
          (x) => x.message_id !== m.message_id && !(x.pending && x.text === m.text && m.role === "user"));
        list.push(m);
        list.sort((a, b) => a.seq - b.seq);
        this.messages.set(chatId, list);
        const chat = this.chats.get(chatId);
        if (chat) {
          chat.updated_at = Math.max(chat.updated_at, m.ts);
          if (m.role === "user" && chat.title === "New chat") {
            const t = (m.text || "").split(/\s+/).join(" ").trim();
            if (t) chat.title = t.length > 48 ? t.slice(0, 48).trimEnd() + "…" : t;
          }
          if (chatId !== this.activeChatId && m.role === "assistant") chat.unread += 1;
        }
        break;
      }
      case "run_started": {
        this.runs.set(d.run_id, {
          run_id: d.run_id, chat_id: d.chat_id, state: "running", text: "", steps: [],
          profile: d.profile, started_at: ev.ts || Date.now() / 1000, lastSeq: ev.seq,
        });
        break;
      }
      case "run_step": {
        const run = this.runs.get(d.run_id);
        if (run) {
          run.steps = [...run.steps, { kind: d.kind, label: d.label, n: d.n, sources: d.sources || [], ts: d.ts }];
          run.lastSeq = ev.seq;
        }
        break;
      }
      case "run_delta": {
        const run = this.runs.get(d.run_id);
        if (run && typeof d.text === "string") {
          run.text += d.text; // chunked deltas
          run.lastSeq = ev.seq;
        }
        break;
      }
      case "run_finished": {
        const run = this.runs.get(d.run_id);
        if (run) {
          run.state = d.cancelled ? "cancelled" : "done";
          if (typeof d.text === "string") run.text = d.text;
          run.ended_at = ev.ts || Date.now() / 1000;
          run.lastSeq = ev.seq;
        }
        // Belt for the missed-publish case: if the final assistant message event was never
        // published (or arrived while offline mid-replay), synthesize the row locally so the
        // answer still lands. hydrateMessages dedupes on message_id once the server row exists.
        if (d.chat_id && d.message_id && typeof d.text === "string" && d.text) {
          const list = this.messages.get(d.chat_id) || [];
          if (!list.some((m) => m.message_id === d.message_id)) {
            list.push({ seq: Number.MAX_SAFE_INTEGER - 1, message_id: d.message_id, role: "assistant",
                        text: d.text, run_id: d.run_id || "", ts: Date.now() / 1000, channel: "text" });
            list.sort((a, b) => (a.seq === b.seq ? a.ts - b.ts : a.seq - b.seq));
            this.messages.set(d.chat_id, list);
            const chat = this.chats.get(d.chat_id);
            if (chat && d.chat_id !== this.activeChatId) chat.unread += 1;
          }
        }
        break;
      }
      case "run_failed": {
        const run = this.runs.get(d.run_id);
        if (run) { run.state = "failed"; run.error = d.error || "failed"; run.ended_at = ev.ts || Date.now() / 1000; run.lastSeq = ev.seq; }
        break;
      }
      case "chat_renamed": {
        const chat = this.chats.get(d.chat_id);
        if (chat && typeof d.title === "string") chat.title = d.title;
        break;
      }
      case "chat_deleted": {
        this.chats.delete(d.chat_id);
        break;
      }
      case "chat_restored":
        break;
      case "profile_changed": {
        if (d.active) this.activeProfile = d.active;
        if (d.chat_id && d.profile) {
          const c = this.chats.get(d.chat_id);
          if (c) c.profile = d.profile;
        }
        break;
      }
      case "connector_status":
      case "connector_qr":
      case "agent_message":
      case "agent_status":
      case "learning_update":
        break; // consumed by the panels via onEvent callback below
      default:
        break;
    }
    this.eventSink?.(ev);
    for (const fn of this.sinks) {
      try { fn(ev); } catch {}
    }
  }

  // ---------------- startup reconcile (GET /api/runs is the runs authority) ----------------
  // A replay can end mid-run (event log truncation, or a run that never published run_finished -
  // the backend bug fixed in runs.py). Reconcile once at startup: terminal states from the runs
  // table, and answers whose assistant row is missing are recovered from the run's stored text.
  reconciled = false;
  private async reconcileRuns() {
    if (this.reconciled) return;
    this.reconciled = true;
    try {
      const d = await getJSON<{ runs: any[] }>("/api/runs?limit=200");
      for (const r of d.runs || []) {
        const known = this.runs.get(r.run_id);
        const terminal = ["done", "failed", "cancelled"].includes(r.state);
        const hydrated: StoreRun = {
          run_id: r.run_id, chat_id: r.chat_id, state: r.state, text: r.text || "",
          steps: Array.isArray(r.steps) ? r.steps : [], profile: r.profile,
          error: r.error, started_at: r.started_at, ended_at: r.ended_at,
          lastSeq: known?.lastSeq ?? this.lastSeq,
        };
        if (!known || terminal) this.runs.set(r.run_id, { ...(known || hydrated), ...hydrated });
        // recover the answer for a chat whose assistant row never landed (pre-fix or lost publish)
        const chatId = r.chat_id as string;
        if (r.state === "done" && r.text && chatId && this.chats.has(chatId)) {
          const synthId = `run-${r.run_id}`;
          const list = this.messages.get(chatId) || [];
          if (!list.some((m) => m.run_id === r.run_id && m.role === "assistant")) {
            list.push({ seq: Number.MAX_SAFE_INTEGER - 2, message_id: synthId, role: "assistant",
                        text: r.text, run_id: r.run_id, ts: r.ended_at || Date.now() / 1000, channel: "text" });
            list.sort((a, b) => (a.seq === b.seq ? a.ts - b.ts : a.seq - b.seq));
            this.messages.set(chatId, list);
            const chat = this.chats.get(chatId);
            if (chat && chatId !== this.activeChatId) chat.unread += 1;
          }
        }
      }
      this.emit();
    } catch {}
  }

  eventSink: ((ev: { seq: number; type: string; data: any }) => void) | null = null;

  // multi-sink fan-out for panels (agents/learning) without extra sockets
  private sinks = new Set<(ev: { seq: number; type: string; data: any }) => void>();

  addSink(fn: (ev: { seq: number; type: string; data: any }) => void): () => void {
    this.sinks.add(fn);
    return () => this.sinks.delete(fn);
  }

  // ---------------- profile (WP4) ----------------
  async setProfile(p: Profile) {
    await putJSON("/api/profile", { active: p });
    this.activeProfile = p;
    const d = await getJSON<{ chats: StoreChat[] }>("/api/chats");
    for (const [id, chat] of [...this.chats.entries()]) {
      if ((chat.profile || "personal") === p) this.chats.delete(id);
    }
    for (const chat of d.chats || []) {
      this.chats.set(chat.chat_id, { ...chat, unread: 0, profile: chat.profile || p });
    }
    const saved = this.savedActiveChat(p);
    const mine = (saved && this.chats.get(saved)?.profile === p ? this.chats.get(saved) : null)
      || [...this.chats.values()].filter((c) => c.profile === p).sort((a, b) => b.updated_at - a.updated_at)[0];
    if (mine) this.setActiveChat(mine.chat_id);
    else if (this.ready) await this.newChat(p);
    await this.reconcileRunsForProfile();
    this.emit();
  }

  private async reconcileRunsForProfile() {
    this.reconciled = false;
    await this.reconcileRuns();
  }

  // ---------------- chat list: durable server rename + soft-delete/undo ----------------
  lastTrash: { chat: StoreChat; token: string }[] = [];

  async setTitle(chatId: string, title: string) {
    const clean = title.trim();
    if (!clean) throw new Error("A chat title cannot be empty.");
    const updated = await patchJSON<StoreChat>(`/api/chats/${chatId}`, { title: clean });
    const chat = this.chats.get(chatId);
    if (chat) Object.assign(chat, updated);
    this.emit();
  }

  async trashChats(chatIds: string[]) {
    if (!chatIds.length) return;
    const trashed: { chat: StoreChat; token: string }[] = [];
    for (const id of chatIds) {
      const chat = this.chats.get(id);
      if (!chat || chat.profile !== this.activeProfile) continue;
      const result = await deleteJSON<{ chat_id: string; undo_token: string }>(`/api/chats/${id}`);
      trashed.push({ chat: { ...chat }, token: result.undo_token });
      this.chats.delete(id);
    }
    this.lastTrash = trashed;
    if (chatIds.includes(this.activeChatId || "")) {
      const next = this.chatList()[0];
      if (next) this.setActiveChat(next.chat_id);
      else this.activeChatId = null;
    }
    this.emit();
  }

  async undoTrash() {
    const pending = [...this.lastTrash];
    if (!pending.length) return;
    for (const item of pending) {
      const result = await patchJSON<{ chat: StoreChat }>(`/api/chats/${item.chat.chat_id}`, {
        restore: true, undo_token: item.token,
      });
      this.chats.set(item.chat.chat_id, { ...(result.chat || item.chat), unread: 0 });
    }
    this.lastTrash = [];
    this.emit();
  }

  chatList(): StoreChat[] {
    return [...this.chats.values()]
      .filter((c) => c.profile === this.activeProfile)
      .sort((a, b) => b.updated_at - a.updated_at)
      .map((c) => c);
  }

  messagesOf(chatId: string | null): StoreMessage[] {
    return (chatId && this.messages.get(chatId)) || [];
  }

  runsOfChat(chatId: string): StoreRun[] {
    return [...this.runs.values()].filter((r) => r.chat_id === chatId);
  }

  pendingRunOfChat(chatId: string): StoreRun | null {
    const live = this.runsOfChat(chatId).filter((r) => r.state === "running" || r.state === "queued");
    return live.length ? live[live.length - 1] : null;
  }

  unreadTotal(): number {
    return [...this.chats.values()].reduce((s, c) => s + c.unread, 0);
  }
}

export const chatStore = new ChatStore();
// QA handle: lets live-ui probes read store state (messages/lastSeq/reconciled) from the console.
if (typeof window !== "undefined") (window as any).__ishti = chatStore;
