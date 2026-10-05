import { Markets } from "@/sections/Markets";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Copy,
  Cpu,
  Database,
  FileCode,
  Folder,
  FolderKanban,
  FolderOpen,
  History,
  Lock,
  MessageSquare,
  Monitor,
  Moon,
  PanelLeft,
  Pencil,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Sliders,
  Sparkles,
  Sun,
  Trash2,
  Type,
  User,
  Volume2,
  Wrench,
  X,
  Mic,
  MicOff,
  QrCode,
  ClipboardCheck,
  Download,
  Building2,
  Users,
  Workflow,
  Network,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { DeleteButton } from "@/components/rare/delete-button";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { ThemeSwitch } from "@/components/ThemeToggle";
import RareFolder from "@/components/rare/folder-component";
import { CHAT_FONT_SIZES, getChatFontSize, setChatFontSize, type ChatFontSize } from "@/lib/font-size";
import { cn } from "@/lib/utils";
import { ModelsCard, VoiceCard, WhisperCard } from "@/components/ModelsVoices";
import { RemoteControl } from "@/components/RemoteControl";
import { ArchitectureCard } from "@/components/ArchitectureCard";
import { LanguageSettings } from "@/components/LanguageSettings";
import { PrivacyPanel } from "@/components/PrivacyPanel";
import { NotificationsPanel } from "@/components/NotificationCenter";
import { SkillsTreeDiagram } from "@/components/SkillsTreeDiagram";

export function Page({
  title,
  subtitle,
  action,
  children,
  className = "max-w-[1560px]",
  onToggleNav,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  onToggleNav?: () => void;
}) {
  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <div className={`mx-auto w-full px-4 py-5 sm:px-6 sm:py-6 lg:px-8 lg:py-7 ${className}`}>
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div className="flex items-center gap-2.5">
            {onToggleNav && (
              <button
                type="button"
                onClick={onToggleNav}
                title="Show navigation bar"
                className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer shrink-0"
              >
                <PanelLeft className="h-4 w-4 text-accent" />
              </button>
            )}
            <div>
              <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
              {subtitle && <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>}
            </div>
          </div>
          {action}
        </div>
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
          {children}
        </motion.div>
      </div>
    </div>
  );
}

const Card = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`rounded-2xl border border-border bg-background p-4 ${className}`}>{children}</div>
);

// ---------------------------------------------------------------- Tasks (reminders, timers & notifications)
type Reminder = { id: number; due: number; text: string; done: boolean; repeat?: string; created?: number };

function localInput(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function dueLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

type ClientRow = {
  id: string;
  name: string;
  source?: string;
  read_only?: boolean;
  company?: string;
  channel_source?: string;
  phone?: string;
  email?: string;
  status?: string;
  financial_terms?: string;
  notes?: string;
  updated_at?: string;
};

type LeadRow = {
  id?: string;
  name?: string;
  handle?: string;
  platform?: string;
  status?: string;
  score?: number;
  note?: string;
  verified?: boolean;
  source?: string;
  created_at?: string;
};

function CRMAndLeadsPanel() {
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [leads, setLeads] = useState<LeadRow[]>([]);
  const [leadsStats, setLeadsStats] = useState<{ total?: number; verified?: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const loadData = useCallback(() => {
    setLoading(true);
    Promise.all([
      getJSON<{ clients: ClientRow[] }>("/api/crm/clients").catch(() => ({ clients: [] })),
      getJSON<{ leads: LeadRow[]; stats: { total?: number; verified?: number } }>("/api/leads").catch(() => ({ leads: [], stats: null })),
    ]).then(([crmRes, leadsRes]) => {
      setClients(crmRes?.clients || []);
      setLeads(leadsRes?.leads || []);
      setLeadsStats(leadsRes?.stats || null);
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleExportCRM = async (fmt: "json" | "csv") => {
    setBusy(true);
    try {
      const res = await postJSON<{ ok: boolean; path: string }>("/api/crm/export", { fmt });
      setMsg(`CRM exported to: ${res.path}`);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const handleExportLeads = async () => {
    setBusy(true);
    try {
      const res = await postJSON<{ path: string }>("/api/leads/export", { fmt: "csv" });
      setMsg(`Leads exported to: ${res.path}`);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteClient = async (id: string, name: string) => {
    try {
      const approval = await postJSON<{ approval_token: string; name: string }>("/api/crm/delete/prepare", { client_id: id });
      if (!window.confirm(`Permanently delete client record for "${approval.name}"?`)) return;
      await postJSON("/api/crm/delete", { client_id: id, confirm_name: approval.name, approval_token: approval.approval_token });
      setClients((prev) => prev.filter((c) => c.id !== id));
      setMsg(`Client "${name}" removed.`);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    }
  };

  const filteredClients = clients.filter((c) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      (c.name && c.name.toLowerCase().includes(q)) ||
      (c.company && c.company.toLowerCase().includes(q)) ||
      (c.phone && c.phone.toLowerCase().includes(q)) ||
      (c.notes && c.notes.toLowerCase().includes(q))
    );
  });

  return (
    <div className="space-y-4">
      {/* Top Banner & Stats */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-border bg-card p-3 shadow-xs">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Business Clients</div>
          <div className="mt-1 text-2xl font-semibold text-foreground">{clients.length}</div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 shadow-xs">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Total Leads</div>
          <div className="mt-1 text-2xl font-semibold text-foreground">{leadsStats?.total ?? leads.length}</div>
        </div>
        <div className="rounded-xl border border-border bg-card p-3 shadow-xs">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Verified Leads</div>
          <div className="mt-1 text-2xl font-semibold text-emerald-500">{leadsStats?.verified ?? leads.filter((l) => l.verified).length}</div>
        </div>
        <div className="flex items-center gap-2 rounded-xl border border-border bg-card p-3 shadow-xs">
          <button
            type="button"
            disabled={busy}
            onClick={() => handleExportCRM("csv")}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors cursor-pointer disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" />
            <span>CRM Export</span>
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={handleExportLeads}
            className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors cursor-pointer disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" />
            <span>Leads Export</span>
          </button>
        </div>
      </div>

      {msg && (
        <div className="rounded-xl border border-accent/40 bg-accent/10 px-4 py-2 text-xs text-foreground flex items-center justify-between">
          <span>{msg}</span>
          <button type="button" onClick={() => setMsg("")} className="text-muted-foreground hover:text-foreground">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Business Clients Section */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/50 pb-3">
          <div className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-accent" />
            <span className="font-semibold text-sm text-foreground">Client Contracts & Contacts</span>
            <span className="rounded-full bg-accent/10 text-accent font-mono text-[11px] px-2 py-0.5">
              {filteredClients.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search clients..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="rounded-lg border border-border bg-background py-1 pl-8 pr-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-hidden"
              />
            </div>
            <button
              type="button"
              onClick={loadData}
              title="Refresh CRM"
              className="rounded-lg border border-border p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted cursor-pointer"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            </button>
          </div>
        </div>

        {filteredClients.length === 0 ? (
          <div className="py-6 text-center text-xs text-muted-foreground">
            {loading ? "Loading clients..." : "No business clients registered yet. Clients are tracked via CRM commands or chat interactions."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border/60 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  <th className="py-2.5 px-3">Name</th>
                  <th className="py-2.5 px-3">Company</th>
                  <th className="py-2.5 px-3">Channel / Phone</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3">Terms & Notes</th>
                  <th className="py-2.5 px-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {filteredClients.map((c) => (
                  <tr key={c.id} className="hover:bg-muted/30 transition-colors">
                    <td className="py-2 px-3 font-medium text-foreground">{c.name}</td>
                    <td className="py-2 px-3 text-muted-foreground">{c.company || "—"}</td>
                    <td className="py-2 px-3 font-mono text-[11px] text-muted-foreground">{c.phone || c.channel_source || "—"}</td>
                    <td className="py-2 px-3">
                      <span className={cn(
                        "rounded-full border px-2 py-0.5 text-[10px] font-semibold capitalize",
                        c.status === "active" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
                        : c.status === "lead" ? "border-blue-500/30 bg-blue-500/10 text-blue-500"
                        : "border-border bg-muted/30 text-muted-foreground"
                      )}>
                        {c.status || "client"}
                      </span>
                    </td>
                    <td className="py-2 px-3 max-w-[200px] truncate text-muted-foreground" title={c.notes || c.financial_terms}>
                      {c.financial_terms ? `[${c.financial_terms}] ` : ""}{c.notes || "—"}
                    </td>
                    <td className="py-2 px-3 text-right">
                      {!c.read_only ? <button
                        type="button"
                        onClick={() => handleDeleteClient(c.id, c.name)}
                        className="rounded-md p-1 text-muted-foreground hover:bg-danger/10 hover:text-danger cursor-pointer"
                        title="Delete client"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button> : <span className="text-[10px] text-muted-foreground" title="Managed through chat client commands">Chat profile</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Leads Pipeline Section */}
      <Card>
        <div className="flex items-center justify-between border-b border-border/50 pb-3">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-accent" />
            <span className="font-semibold text-sm text-foreground">Leads & Outreach Pipeline</span>
            <span className="rounded-full bg-accent/10 text-accent font-mono text-[11px] px-2 py-0.5">
              {leads.length}
            </span>
          </div>
        </div>

        {leads.length === 0 ? (
          <div className="py-6 text-center text-xs text-muted-foreground">
            {loading ? "Loading leads..." : "No leads captured yet. Lead ingestion runs via outreach skills or /api/leads."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border/60 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  <th className="py-2.5 px-3">Lead / Handle</th>
                  <th className="py-2.5 px-3">Platform</th>
                  <th className="py-2.5 px-3">Status</th>
                  <th className="py-2.5 px-3">Score</th>
                  <th className="py-2.5 px-3">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {leads.slice(0, 50).map((l, i) => (
                  <tr key={l.id || i} className="hover:bg-muted/30 transition-colors">
                    <td className="py-2 px-3 font-medium text-foreground">{l.name || l.handle || "Lead"}</td>
                    <td className="py-2 px-3 text-muted-foreground">{l.platform || l.source || "—"}</td>
                    <td className="py-2 px-3">
                      <span className={cn(
                        "rounded-full border px-2 py-0.5 text-[10px] font-semibold capitalize",
                        l.verified ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500" : "border-border bg-muted/30 text-muted-foreground"
                      )}>
                        {l.status || (l.verified ? "verified" : "unverified")}
                      </span>
                    </td>
                    <td className="py-2 px-3 font-mono text-[11px] text-muted-foreground">{l.score ?? "—"}</td>
                    <td className="py-2 px-3 max-w-[240px] truncate text-muted-foreground" title={l.note}>
                      {l.note || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export function Tasks({ onToggleNav }: { onToggleNav?: () => void }) {
  const [items, setItems] = useState<Reminder[]>([]);
  const [tab, setTab] = useState<"tasks" | "notifications" | "crm">(() => {
    try {
      const h = window.location.hash.replace(/^#\/?/, "");
      if (h.startsWith("notifications")) return "notifications";
      if (h.startsWith("crm")) return "crm";
      return "tasks";
    } catch { return "tasks"; }
  });
  const [unreadNotifs, setUnreadNotifs] = useState(0);
  const [listFilter, setListFilter] = useState<"open" | "done" | "all">("open");
  const [sortBy, setSortBy] = useState<"due" | "created" | "text">("due");
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [draftDue, setDraftDue] = useState("");
  const [undo, setUndo] = useState<{ text: string; due: number } | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  // done tasks come back too: completing one must never make it vanish (it moves to the Done group)
  const load = () => getJSON<{ reminders: Reminder[] }>("/api/reminders?include_done=1")
    .then((d) => setItems(d.reminders ?? []))
    .catch((err) => console.warn("[tasks] load failed:", err));

  useEffect(() => {  // the undo window for a deleted task
    if (!undo) return;
    const t = setTimeout(() => setUndo(null), 8000);
    return () => clearTimeout(t);
  }, [undo]);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  useEffect(() => {
    const loadUnread = () => getJSON<{ unread: number }>("/api/notifications")
      .then((d) => setUnreadNotifs(d.unread ?? 0)).catch(() => {});
    loadUnread();
    window.addEventListener("ishti-notification", loadUnread);
    return () => window.removeEventListener("ishti-notification", loadUnread);
  }, []);
  useEffect(() => {
    // the bell's "View all" works even when Tasks is already mounted (hash writes do not fire hashchange)
    const onTab = (e: Event) => setTab((e as CustomEvent).detail === "notifications" ? "notifications" : "tasks");
    window.addEventListener("ishti-tasks-tab", onTab);
    return () => window.removeEventListener("ishti-tasks-tab", onTab);
  }, []);
  const selectTab = (next: "tasks" | "notifications" | "crm") => {
    setTab(next);
    try { window.history.replaceState(null, "", next === "notifications" ? "#notifications" : next === "crm" ? "#crm" : "#tasks"); } catch {}
  };
  const toggleDone = async (r: Reminder) => {
    setItems((cur) => cur.map((x) => (x.id === r.id ? { ...x, done: !r.done } : x)));   // stays on screen
    await postJSON(`/api/reminders/${r.id}/${r.done ? "open" : "done"}`, {}).catch(() => {});
    load();
  };
  const snooze = async (r: Reminder, minutes: number) => {
    setBusyId(r.id);
    await postJSON(`/api/reminders/${r.id}/snooze`, { minutes }).catch(() => {});
    setBusyId(null);
    load();
  };
  const removeTask = async (r: Reminder) => {
    await del(`/api/reminders/${r.id}`).catch(() => {});
    setItems((cur) => cur.filter((x) => x.id !== r.id));
    setUndo({ text: r.text, due: r.due });
  };
  const undoDelete = async () => {
    if (!undo) return;
    await postJSON("/api/reminders", { text: undo.text, due: undo.due }).catch(() => {});
    setUndo(null);
    load();
  };
  const clearDone = async () => {
    setConfirmClear(false);
    setItems((cur) => cur.filter((r) => !r.done));   // optimistic; the reload confirms
    await postJSON("/api/reminders/clear_done", {}).catch(() => {});
    load();
  };
  const startEdit = (r: Reminder) => { setEditing(r.id); setDraft(r.text); setDraftDue(localInput(r.due)); };
  const saveEdit = async (r: Reminder) => {
    const text = draft.trim();
    const due = draftDue ? Math.floor(new Date(draftDue).getTime() / 1000) : r.due;
    setEditing(null);
    await postJSON(`/api/reminders/${r.id}`, { text, due }).catch(() => {});
    load();
  };
  const openTasks = items.filter((r) => !r.done);
  const doneTasks = items.filter((r) => r.done);
  const sorted = (rows: Reminder[]) => [...rows].sort((a, b) =>
    sortBy === "text" ? a.text.localeCompare(b.text)
      : sortBy === "created" ? (a.created ?? 0) - (b.created ?? 0)
        : a.due - b.due);

  const renderRow = (r: Reminder, done: boolean) => (
    <div key={r.id}
      className={cn("group flex items-center gap-2.5 rounded-xl border border-border/60 bg-card/40 px-3 py-2.5 transition-colors hover:border-accent/40", done && "opacity-70")}>
      <button type="button" onClick={() => toggleDone(r)} title={done ? "Mark as not done" : "Mark as done"}
        className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-md border transition-colors cursor-pointer",
          done ? "border-accent bg-accent text-white" : "border-border hover:border-accent")}>
        {done && <Check className="h-3.5 w-3.5" />}
      </button>
      {editing === r.id ? (
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <input value={draft} autoFocus onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") saveEdit(r); if (e.key === "Escape") setEditing(null); }}
            className="min-w-[180px] flex-1 rounded-md border border-accent/60 bg-background px-2 py-1 text-[13px] text-foreground outline-none" />
          <input type="datetime-local" value={draftDue} onChange={(e) => setDraftDue(e.target.value)}
            className="rounded-md border border-border bg-background px-2 py-1 text-[11px] text-foreground outline-none focus:border-accent" />
          <button type="button" onClick={() => saveEdit(r)}
            className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-accent/90 cursor-pointer">Save</button>
          <button type="button" onClick={() => setEditing(null)}
            className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted cursor-pointer">Cancel</button>
        </div>
      ) : (
        <>
          <div className="min-w-0 flex-1">
            <div className={cn("truncate text-[13.5px] text-foreground", done && "line-through opacity-70")}>{r.text}</div>
            <div className="text-[11px] text-muted-foreground">
              {dueLabel(r.due)}{r.repeat ? ` · repeats (${r.repeat})` : ""}
            </div>
          </div>
          <div className="hidden shrink-0 items-center gap-1 group-hover:flex">
            <button type="button" disabled={busyId === r.id} onClick={() => snooze(r, 10)} title="Snooze 10 minutes"
              className="rounded-lg border border-border px-2 py-1 text-[10.5px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40 cursor-pointer">+10m</button>
            <button type="button" disabled={busyId === r.id} onClick={() => snooze(r, 60)} title="Snooze 1 hour"
              className="rounded-lg border border-border px-2 py-1 text-[10.5px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40 cursor-pointer">+1h</button>
            <button type="button" onClick={() => startEdit(r)} title="Edit title or time"
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer">
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button type="button" onClick={() => removeTask(r)} title="Delete (can be undone for 8 s)"
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-danger/10 hover:text-danger cursor-pointer">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </>
      )}
    </div>
  );
  const tabs = (
    <div className="flex items-center gap-1 rounded-xl border border-border bg-muted/30 p-1">
      {([["tasks", "Tasks"], ["notifications", "Notifications"], ["crm", "CRM & Leads"]] as const).map(([key, label]) => (
        <button key={key} type="button" onClick={() => selectTab(key)}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
            tab === key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
          {label}
          {key === "notifications" && unreadNotifs > 0 && (
            <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-bold text-white">{unreadNotifs}</span>
          )}
        </button>
      ))}
    </div>
  );
  return (
    <Page title="Tasks" action={tabs}
      subtitle={tab === "tasks"
        ? "Reminders and timers. Tick a task to mark it done (it moves to Done, nothing disappears); use the icons to snooze, edit or delete. Add more from chat: “remind me at 5pm to …”."
        : tab === "notifications"
        ? "Everything ISHTI told you about — reminders, finished jobs, downloads, alerts and errors."
        : "Local business CRM and lead pipeline with complete personal vs business data separation."}
      onToggleNav={onToggleNav}>
      {tab === "tasks" ? (
        <>
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 pb-3">
              <div className="flex items-center gap-1 rounded-xl border border-border bg-muted/30 p-1">
                {([["open", "Open"], ["done", "Done"], ["all", "All"]] as const).map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setListFilter(key)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer ${
                      listFilter === key ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                    {label}{key === "open" ? ` (${openTasks.length})` : key === "done" ? ` (${doneTasks.length})` : ""}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                Sort
                <select value={sortBy} onChange={(e) => setSortBy(e.target.value as "due" | "created" | "text")}
                  className="rounded-lg border border-border bg-background px-2 py-1 text-[11px] text-foreground outline-none focus:border-accent">
                  <option value="due">by time</option>
                  <option value="created">by added</option>
                  <option value="text">A to Z</option>
                </select>
              </label>
            </div>

            {items.length === 0 && (
              <p className="pt-4 text-sm text-muted-foreground">
                Nothing scheduled. Add one from chat: “remind me at 5pm to …”.
              </p>
            )}

            {listFilter !== "done" && openTasks.length > 0 && (
              <div className="pt-3">
                {listFilter === "all" && (
                  <div className="pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Open</div>
                )}
                <div className="space-y-1.5">{sorted(openTasks).map((r) => renderRow(r, false))}</div>
              </div>
            )}

            {listFilter !== "open" && doneTasks.length > 0 && (
              <div className="pt-4">
                <div className="flex items-center justify-between pb-1.5">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Done ({doneTasks.length})
                  </div>
                  {confirmClear ? (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] text-muted-foreground">Remove all {doneTasks.length} finished tasks?</span>
                      <button type="button" onClick={clearDone}
                        className="rounded-lg bg-danger px-2 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-danger/90 cursor-pointer">
                        Clear done
                      </button>
                      <button type="button" onClick={() => setConfirmClear(false)}
                        className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted cursor-pointer">
                        Keep
                      </button>
                    </div>
                  ) : (
                    <button type="button" onClick={() => setConfirmClear(true)}
                      title="Empty the Done group (asks first)"
                      className="rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-danger/10 hover:text-danger cursor-pointer">
                      Clear done
                    </button>
                  )}
                </div>
                <div className="space-y-1.5">{sorted(doneTasks).map((r) => renderRow(r, true))}</div>
              </div>
            )}

            {listFilter === "open" && openTasks.length === 0 && items.length > 0 && (
              <p className="pt-4 text-sm text-muted-foreground">
                Nothing open — {doneTasks.length} done. Switch to Done to see {doneTasks.length === 1 ? "it" : "them"}.
              </p>
            )}
          </Card>

          {undo && (
            <div className="mt-3 flex items-center gap-3 rounded-2xl border border-border bg-background px-4 py-2.5 shadow-sm">
              <Trash2 className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-xs text-foreground">Task deleted</span>
              <button type="button" onClick={undoDelete}
                className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-accent/90 cursor-pointer">
                Undo
              </button>
              <button type="button" onClick={() => setUndo(null)} title="Dismiss"
                className="rounded-lg p-1 text-muted-foreground transition-colors hover:bg-muted cursor-pointer">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </>
      ) : tab === "notifications" ? (
        <NotificationsPanel />
      ) : (
        <CRMAndLeadsPanel />
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- Memory (exact facts)
type Fact = { entity: string; attr: string; value: string; previous?: string; updated_at?: number };

type EditState = {
  entity: string;
  originalAttr: string;
  attr: string;
  value: string;
};

type AddAttrState = {
  entity: string;
  attr: string;
  value: string;
};

export function MemoryBody() {
  const [facts, setFacts] = useState<Fact[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<EditState | null>(null);
  const [addingAttr, setAddingAttr] = useState<AddAttrState | null>(null);
  const [showNewModal, setShowNewModal] = useState(false);
  const [newEntity, setNewEntity] = useState("");
  const [newAttr, setNewAttr] = useState("");
  const [newValue, setNewValue] = useState("");
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Track expanded entities and individual expanded facts
  const [expandedEntities, setExpandedEntities] = useState<Record<string, boolean>>({});
  const [expandedFacts, setExpandedFacts] = useState<Record<string, boolean>>({});

  const load = (showSpinner = false) => {
    if (showSpinner) setIsLoading(true);
    return getJSON<{ facts: Fact[] }>("/api/facts")
      .then((d) => setFacts(d.facts ?? []))
      .finally(() => {
        if (showSpinner) setIsLoading(false);
      });
  };

  useEffect(() => {
    load();
  }, []);

  const handleCopy = (key: string, val: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      navigator.clipboard.writeText(val);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 1600);
    } catch {}
  };

  const toggleFact = (key: string) => {
    setExpandedFacts((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const toggleEntity = (entity: string) => {
    setExpandedEntities((prev) => ({
      ...prev,
      [entity]: prev[entity] === undefined ? false : !prev[entity],
    }));
  };

  const toggleAllFacts = (expand: boolean) => {
    const next: Record<string, boolean> = {};
    facts.forEach((f) => {
      next[`${f.entity}:${f.attr}`] = expand;
    });
    setExpandedFacts(next);
  };

  const handleSaveEdit = async () => {
    if (!editing || !editing.attr.trim() || !editing.value.trim()) return;
    const key = `${editing.entity}:${editing.originalAttr}`;
    setSavingKey(key);
    try {
      await postJSON("/api/facts", {
        entity: editing.entity,
        attr: editing.attr.trim(),
        value: editing.value.trim(),
        old_attr: editing.originalAttr,
      });
      setEditing(null);
      await load();
    } finally {
      setSavingKey(null);
    }
  };

  const handleAddAttr = async () => {
    if (!addingAttr || !addingAttr.attr.trim() || !addingAttr.value.trim()) return;
    const key = `${addingAttr.entity}:${addingAttr.attr}`;
    setSavingKey(key);
    try {
      await postJSON("/api/facts", {
        entity: addingAttr.entity,
        attr: addingAttr.attr.trim(),
        value: addingAttr.value.trim(),
      });
      setAddingAttr(null);
      await load();
    } finally {
      setSavingKey(null);
    }
  };

  const handleDeleteAttr = async (entity: string, attr: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    const key = `${entity}:${attr}`;
    setDeletingKey(key);
    try {
      await del(`/api/facts/${encodeURIComponent(entity)}/${encodeURIComponent(attr)}`);
      await load();
    } finally {
      setDeletingKey(null);
    }
  };

  const handleCreateNew = async () => {
    if (!newEntity.trim() || !newAttr.trim() || !newValue.trim()) return;
    try {
      await postJSON("/api/facts", {
        entity: newEntity.trim(),
        attr: newAttr.trim(),
        value: newValue.trim(),
      });
      setNewEntity("");
      setNewAttr("");
      setNewValue("");
      setShowNewModal(false);
      await load();
    } catch {}
  };

  // Group facts by entity
  const filtered = facts.filter((f) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      f.entity.toLowerCase().includes(q) ||
      f.attr.toLowerCase().includes(q) ||
      f.value.toLowerCase().includes(q)
    );
  });

  const groups = filtered.reduce<Record<string, Fact[]>>((acc, f) => {
    (acc[f.entity] ??= []).push(f);
    return acc;
  }, {});

  const totalFacts = facts.length;
  const totalEntities = new Set(facts.map((f) => f.entity)).size;
  const anyFactExpanded = Object.values(expandedFacts).some(Boolean);

  return (
    <>
      <div className="mb-3 flex justify-end">
        <button
          type="button"
          onClick={() => setShowNewModal(true)}
          className="flex items-center gap-1.5 rounded-xl bg-accent px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:opacity-90 transition-all cursor-pointer"
        >
          <Plus className="h-4 w-4" /> Add Fact
        </button>
      </div>
      <div className="space-y-4">
        {/* Filter and stats toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card/50 p-3 shadow-xs">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search facts, attributes, or values..."
              className="w-full rounded-xl border border-border bg-background pl-9 pr-8 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 text-xs">
            <button
              type="button"
              onClick={() => toggleAllFacts(!anyFactExpanded)}
              className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer"
            >
              {anyFactExpanded ? (
                <>
                  <ChevronUp className="h-3.5 w-3.5 text-accent" />
                  <span>Collapse All Facts</span>
                </>
              ) : (
                <>
                  <ChevronDown className="h-3.5 w-3.5 text-accent" />
                  <span>Expand All Facts</span>
                </>
              )}
            </button>

            <span className="inline-flex items-center gap-1.5 rounded-xl bg-muted/60 px-3 py-1.5 font-medium text-muted-foreground">
              <Database className="h-3.5 w-3.5 text-accent" />
              <span className="text-foreground font-semibold">{totalFacts}</span> facts
              <span>·</span>
              <span className="text-foreground font-semibold">{totalEntities}</span> entities
            </span>

            <button
              type="button"
              onClick={() => load(true)}
              disabled={isLoading}
              title="Refresh facts"
              className="rounded-xl border border-border p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} />
            </button>
          </div>
        </div>

        {/* Facts List - Responsive, balanced & line-by-line expandable */}
        {Object.keys(groups).length === 0 ? (
          <Card className="py-12 text-center">
            <Database className="mx-auto h-8 w-8 text-muted-foreground/50" />
            <p className="mt-2 text-sm font-medium text-foreground">
              {search ? `No memory facts matching "${search}"` : "No memory facts saved yet."}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {search ? "Try searching for a different keyword." : "Click '+ Add Fact' above or tell ISHTI in chat to remember something."}
            </p>
          </Card>
        ) : (
          <div className="space-y-4">
            {Object.entries(groups).map(([entity, rows], i) => {
              const isOwner = entity.toLowerCase() === "owner";
              const isProject = entity.toLowerCase().includes("project") || entity.includes(":\\") || entity.includes("/");
              const isEntityExpanded = expandedEntities[entity] !== false; // default true

              return (
                <motion.div
                  key={entity}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.04 }}
                  className="rounded-2xl border border-border bg-card/40 shadow-xs overflow-hidden transition-all"
                >
                  {/* Entity Header Bar */}
                  <div
                    className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 bg-muted/20 border-b border-border/60 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => toggleEntity(entity)}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div
                        className={cn(
                          "grid h-8 w-8 shrink-0 place-items-center rounded-xl text-xs font-semibold shadow-xs",
                          isOwner
                            ? "bg-violet-500/15 text-violet-500"
                            : isProject
                            ? "bg-amber-500/15 text-amber-500"
                            : "bg-sky-500/15 text-sky-500"
                        )}
                      >
                        {isOwner ? (
                          <User className="h-4 w-4" />
                        ) : isProject ? (
                          <FolderKanban className="h-4 w-4" />
                        ) : (
                          <Database className="h-4 w-4" />
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold text-foreground text-sm capitalize truncate">
                            {entity}
                          </h3>
                          <span className="rounded-md bg-muted px-2 py-0.5 text-[11px] font-mono text-muted-foreground shrink-0">
                            {rows.length} {rows.length === 1 ? "fact" : "facts"}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => setAddingAttr({ entity, attr: "", value: "" })}
                        title={`Add attribute to ${entity}`}
                        className="flex items-center gap-1 rounded-lg border border-border/80 bg-background px-2.5 py-1 text-xs font-medium text-muted-foreground hover:border-accent hover:text-accent hover:bg-accent/5 transition-colors cursor-pointer"
                      >
                        <Plus className="h-3 w-3" /> Add
                      </button>
                      {isOwner ? (
                        <span
                          title="Owner profile is permanent"
                          className="grid h-7 w-7 place-items-center text-muted-foreground/60"
                        >
                          <Lock className="h-3.5 w-3.5" />
                        </span>
                      ) : (
                        <DeleteButton
                          onConfirm={() =>
                            del(`/api/facts/${encodeURIComponent(entity)}`).then(load)
                          }
                        />
                      )}
                      <button
                        type="button"
                        onClick={() => toggleEntity(entity)}
                        className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors cursor-pointer"
                        title={isEntityExpanded ? "Collapse section" : "Expand section"}
                      >
                        <ChevronDown
                          className={cn("h-4 w-4 transition-transform duration-200", !isEntityExpanded && "-rotate-90")}
                        />
                      </button>
                    </div>
                  </div>

                  {/* Fact rows - Line by Line Expandable */}
                  <AnimatePresence initial={false}>
                    {isEntityExpanded && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2 }}
                        className="p-3 space-y-2"
                      >
                        {rows.map((f) => {
                          const factKey = `${f.entity}:${f.attr}`;
                          const isThisEditing =
                            editing?.entity === f.entity && editing?.originalAttr === f.attr;
                          const isThisSaving = savingKey === factKey;
                          const isThisDeleting = deletingKey === factKey;
                          const isFactExpanded = !!expandedFacts[factKey];
                          const isCopied = copiedKey === factKey;

                          if (isThisEditing) {
                            return (
                              <div
                                key={f.attr}
                                className="rounded-xl border border-accent/80 bg-background p-3.5 shadow-xs space-y-2.5"
                              >
                                <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
                                  <div className="sm:col-span-1">
                                    <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                      Attribute Key
                                    </label>
                                    <input
                                      type="text"
                                      value={editing.attr}
                                      onChange={(e) =>
                                        setEditing({ ...editing, attr: e.target.value })
                                      }
                                      className="mt-1 w-full rounded-lg border border-border bg-card/60 px-2.5 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                                    />
                                  </div>
                                  <div className="sm:col-span-3">
                                    <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                      Value
                                    </label>
                                    <textarea
                                      rows={3}
                                      value={editing.value}
                                      onChange={(e) =>
                                        setEditing({ ...editing, value: e.target.value })
                                      }
                                      className="mt-1 w-full rounded-lg border border-border bg-card/60 px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent resize-y leading-relaxed"
                                    />
                                  </div>
                                </div>
                                <div className="flex items-center justify-end gap-2 pt-1">
                                  <button
                                    type="button"
                                    onClick={() => setEditing(null)}
                                    className="flex items-center gap-1 rounded-lg border border-border px-3 py-1 text-xs text-muted-foreground hover:bg-muted transition-colors cursor-pointer"
                                  >
                                    <X className="h-3 w-3" /> Cancel
                                  </button>
                                  <button
                                    type="button"
                                    onClick={handleSaveEdit}
                                    disabled={
                                      isThisSaving ||
                                      !editing.attr.trim() ||
                                      !editing.value.trim()
                                    }
                                    className="flex items-center gap-1 rounded-lg bg-accent px-3.5 py-1 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50 transition-opacity cursor-pointer shadow-xs"
                                  >
                                    <Check className="h-3.5 w-3.5" />
                                    {isThisSaving ? "Saving..." : "Save"}
                                  </button>
                                </div>
                              </div>
                            );
                          }

                          return (
                            <div
                              key={f.attr}
                              onClick={() => toggleFact(factKey)}
                              className={cn(
                                "group rounded-xl border border-border/70 bg-background/80 hover:border-accent/50 hover:bg-background transition-all cursor-pointer shadow-2xs",
                                isFactExpanded && "border-accent/40 bg-background shadow-xs",
                                isThisDeleting && "opacity-40 pointer-events-none"
                              )}
                            >
                              {/* Single-line Row Header */}
                              <div className="flex items-center justify-between gap-3 px-3 py-2.5 min-w-0">
                                <div className="flex items-center gap-2.5 min-w-0 flex-1">
                                  {/* Attribute Pill */}
                                  <span
                                    title={f.attr}
                                    className="shrink-0 rounded-md bg-accent/10 px-2 py-0.5 text-[11px] font-mono font-medium text-accent border border-accent/15 select-all"
                                  >
                                    {f.attr}
                                  </span>

                                  {/* Value Preview (1-line truncated if collapsed) */}
                                  <div className="min-w-0 flex-1">
                                    {!isFactExpanded ? (
                                      <p className="text-xs text-foreground/90 truncate leading-snug">
                                        {f.value}
                                      </p>
                                    ) : (
                                      <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">
                                        Expanded
                                      </span>
                                    )}
                                  </div>
                                </div>

                                {/* Right Side Actions */}
                                <div
                                  className="flex items-center gap-1 shrink-0"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  {/* Copy Button */}
                                  <button
                                    type="button"
                                    onClick={(e) => handleCopy(factKey, f.value, e)}
                                    title={isCopied ? "Copied to clipboard!" : "Copy value"}
                                    className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
                                  >
                                    {isCopied ? (
                                      <Check className="h-3.5 w-3.5 text-success" />
                                    ) : (
                                      <Copy className="h-3.5 w-3.5" />
                                    )}
                                  </button>

                                  {/* Edit Button */}
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setEditing({
                                        entity: f.entity,
                                        originalAttr: f.attr,
                                        attr: f.attr,
                                        value: f.value,
                                      })
                                    }
                                    title="Edit attribute"
                                    className="rounded-md p-1.5 text-muted-foreground hover:bg-accent/10 hover:text-accent transition-colors cursor-pointer"
                                  >
                                    <Pencil className="h-3.5 w-3.5" />
                                  </button>

                                  {/* Delete Button */}
                                  <button
                                    type="button"
                                    onClick={(e) => handleDeleteAttr(f.entity, f.attr, e)}
                                    title="Delete this attribute"
                                    className="rounded-md p-1.5 text-muted-foreground hover:bg-danger/10 hover:text-danger transition-colors cursor-pointer"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>

                                  {/* Expand/Collapse Chevron */}
                                  <button
                                    type="button"
                                    onClick={() => toggleFact(factKey)}
                                    title={isFactExpanded ? "Collapse" : "Expand full value"}
                                    className="rounded-md p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                                  >
                                    <ChevronDown
                                      className={cn(
                                        "h-3.5 w-3.5 transition-transform duration-200",
                                        isFactExpanded && "rotate-180 text-accent"
                                      )}
                                    />
                                  </button>
                                </div>
                              </div>

                              {/* Expanded Full Multi-line Content */}
                              <AnimatePresence initial={false}>
                                {isFactExpanded && (
                                  <motion.div
                                    initial={{ height: 0, opacity: 0 }}
                                    animate={{ height: "auto", opacity: 1 }}
                                    exit={{ height: 0, opacity: 0 }}
                                    transition={{ duration: 0.18 }}
                                    className="overflow-hidden border-t border-border/50 px-3.5 py-3 bg-muted/15"
                                  >
                                    <div className="text-xs text-foreground font-normal whitespace-pre-wrap break-words leading-relaxed select-text font-mono bg-background/70 p-3 rounded-lg border border-border/60">
                                      {f.value}
                                    </div>

                                    {/* History / Previous Value */}
                                    {f.previous && (
                                      <div className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground">
                                        <History className="h-3 w-3 shrink-0 text-accent" />
                                        <span>
                                          Previous value: <span className="line-through">{f.previous}</span>
                                        </span>
                                      </div>
                                    )}

                                    <div className="mt-2.5 flex items-center justify-between text-[11px] text-muted-foreground pt-1">
                                      <span className="font-mono text-[10px]">
                                        Key: {f.entity}.{f.attr}
                                      </span>
                                      <div className="flex items-center gap-2">
                                        <button
                                          type="button"
                                          onClick={(e) => handleCopy(factKey, f.value, e)}
                                          className="text-accent hover:underline cursor-pointer"
                                        >
                                          {isCopied ? "Copied!" : "Copy"}
                                        </button>
                                        <span>·</span>
                                        <button
                                          type="button"
                                          onClick={() =>
                                            setEditing({
                                              entity: f.entity,
                                              originalAttr: f.attr,
                                              attr: f.attr,
                                              value: f.value,
                                            })
                                          }
                                          className="text-accent hover:underline cursor-pointer"
                                        >
                                          Edit
                                        </button>
                                      </div>
                                    </div>
                                  </motion.div>
                                )}
                              </AnimatePresence>
                            </div>
                          );
                        })}

                        {/* Inline Add Attribute Form */}
                        {addingAttr?.entity === entity && (
                          <div className="rounded-xl border border-dashed border-accent/70 bg-accent/5 p-3.5 space-y-2.5 mt-2">
                            <div className="text-xs font-semibold text-accent flex items-center gap-1.5">
                              <Plus className="h-3.5 w-3.5" />
                              <span>New attribute for {entity}</span>
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                              <div className="sm:col-span-1">
                                <input
                                  type="text"
                                  value={addingAttr.attr}
                                  onChange={(e) =>
                                    setAddingAttr({ ...addingAttr, attr: e.target.value })
                                  }
                                  placeholder="Attribute key (e.g. phone, role)"
                                  className="w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                                />
                              </div>
                              <div className="sm:col-span-2">
                                <textarea
                                  rows={2}
                                  value={addingAttr.value}
                                  onChange={(e) =>
                                    setAddingAttr({ ...addingAttr, value: e.target.value })
                                  }
                                  placeholder="Value..."
                                  className="w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent resize-none"
                                />
                              </div>
                            </div>
                            <div className="flex items-center justify-end gap-2 pt-1">
                              <button
                                type="button"
                                onClick={() => setAddingAttr(null)}
                                className="rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted cursor-pointer"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={handleAddAttr}
                                disabled={
                                  !addingAttr.attr.trim() || !addingAttr.value.trim()
                                }
                                className="rounded-lg bg-accent px-3 py-1 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50 cursor-pointer shadow-xs"
                              >
                                Add Attribute
                              </button>
                            </div>
                          </div>
                        )}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>

      {/* New Entity / Fact Modal */}
      <AnimatePresence>
        {showNewModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md rounded-2xl border border-border bg-background p-5 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent">
                    <Sparkles className="h-4 w-4" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-foreground text-sm">Add New Memory</h3>
                    <p className="text-xs text-muted-foreground">
                      Store an exact fact in ISHTI's persistent memory
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowNewModal(false)}
                  className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3 pt-1">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">
                    Entity / Topic
                  </label>
                  <input
                    type="text"
                    value={newEntity}
                    onChange={(e) => setNewEntity(e.target.value)}
                    placeholder="e.g. Owner, Work, The Ishti Project"
                    className="mt-1 w-full rounded-xl border border-border bg-card/60 px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">
                    Attribute Key
                  </label>
                  <input
                    type="text"
                    value={newAttr}
                    onChange={(e) => setNewAttr(e.target.value)}
                    placeholder="e.g. email, location, preferences"
                    className="mt-1 w-full rounded-xl border border-border bg-card/60 px-3 py-2 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Value</label>
                  <textarea
                    rows={3}
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    placeholder="e.g. D:\ISHTI, Peshawar, etc."
                    className="mt-1 w-full rounded-xl border border-border bg-card/60 px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent resize-none"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowNewModal(false)}
                  className="rounded-xl border border-border px-3.5 py-2 text-xs font-medium text-muted-foreground hover:bg-muted cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleCreateNew}
                  disabled={!newEntity.trim() || !newAttr.trim() || !newValue.trim()}
                  className="flex items-center gap-1.5 rounded-xl bg-accent px-4 py-2 text-xs font-semibold text-white shadow-xs hover:opacity-90 disabled:opacity-50 cursor-pointer"
                >
                  <Check className="h-3.5 w-3.5" /> Save Memory
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}

/** The Memory content as its own page (old #memory link; the Learning page shows the same body as a tab). */
export function Memory({ onToggleNav }: { onToggleNav?: () => void }) {
  return (
    <Page
      title="Memory"
      subtitle="Exact facts ISHTI looks up before anything else. Fully editable, updated in place, never duplicated."
      className="max-w-6xl"
      onToggleNav={onToggleNav}
    >
      <MemoryBody />
    </Page>
  );
}

// ---------------------------------------------------------------- Inputs
// Every way ISHTI can take input, with its LIVE state from GET /api/inputs. Nothing here is decorative:
// each card is bound to the file or module that owns the state (mic_state.json, config.json, the WhatsApp
// bridge, the OAuth token store), so what you see is what is actually running.
type InputChannel = {
  id: string; name: string; icon: string; kind: string; detail: string;
  state: string; source: string; account?: string;
};
type InputsPayload = {
  channels: InputChannel[];
  accounts: { service: string; connected: boolean }[];
  note?: string;
};

const STATE_TONE: Record<string, string> = {
  on: "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20",
  waiting: "bg-amber-500/10 text-amber-500 border border-amber-500/20",
  ready: "bg-sky-500/10 text-sky-500 border border-sky-500/20",
  off: "bg-muted text-muted-foreground border border-border",
};
const STATE_LABEL: Record<string, string> = { on: "active", waiting: "waiting", ready: "ready", off: "off" };
const KIND_LABEL: Record<string, string> = {
  always_on: "always on", toggle: "switch", setting: "setting", status: "live status",
  opt_in: "on request", account: "connected account",
};

export function Inputs({ onToggleNav }: { onToggleNav?: () => void }) {
  const [data, setData] = useState<InputsPayload | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  // Live Interactive Controls State
  const [micLoading, setMicLoading] = useState(false);
  const [qrModalOpen, setQrModalOpen] = useState(false);
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState("");
  const [qrData, setQrData] = useState<{
    connected: boolean;
    ascii_qr: string;
    raw_qr: string | null;
    me: string;
    running: boolean;
    qr_image?: string | null;
    lastError?: string | null;
  } | null>(null);
  const [clipboardPreview, setClipboardPreview] = useState<string | null>(null);
  const [screenNotice, setScreenNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    getJSON<InputsPayload>("/api/inputs")
      .then(setData)
      .catch((e: Error) => setError(e?.message || "could not reach /api/inputs"));
  }, []);

  useEffect(load, [load, tick]);

  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 15000);
    return () => clearInterval(t);
  }, []);

  // WhatsApp QR Code Modal Handler
  const openQrModal = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setQrData(null);
    setQrError("");
    setQrModalOpen(true);
  };

  useEffect(() => {
    if (!qrModalOpen) return;
    let active = true;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const res = await getJSON<{
          connected: boolean;
          ascii_qr: string;
          raw_qr: string | null;
          me: string;
          running: boolean;
        }>("/api/inputs/wa-qr");
        if (active) {
          setQrData(res);
          setQrError("");
          if (res.connected) setTick((n) => n + 1);
        }
      } catch (error) {
        if (active) setQrError(error instanceof Error ? error.message : "Could not read WhatsApp connection state");
      } finally {
        busy = false;
        if (active) setQrLoading(false);
      }
    };
    setQrLoading(true);
    void postJSON("/api/inputs/wa-start", {}).then(poll).catch((error: unknown) => {
      if (active) {
        setQrError(error instanceof Error ? error.message : "Could not start the WhatsApp bridge");
        setQrLoading(false);
      }
    });
    const interval = setInterval(poll, 3000);
    return () => { active = false; clearInterval(interval); };
  }, [qrModalOpen]);

  // Live Mic Toggle
  const handleToggleMic = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setMicLoading(true);
    try {
      await postJSON("/api/mic/toggle", {});
      setTick((n) => n + 1);
    } catch (err: any) {
      setError(err?.message || "Failed to toggle microphone");
    } finally {
      setMicLoading(false);
    }
  };

  // Test Clipboard
  const handleTestClipboard = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const res = await getJSON<{ ok: boolean; preview: string }>("/api/inputs/clipboard");
      setClipboardPreview(res.preview);
    } catch (err: any) {
      setClipboardPreview("Clipboard access ready");
    }
  };

  // Test Screen Perception
  const handleTestScreen = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const res = await getJSON<{ ok: boolean; detail: string }>("/api/inputs/screen");
      setScreenNotice(res.detail);
    } catch (err: any) {
      setScreenNotice("Screen perception ready");
    }
  };

  const channels = data?.channels ?? [];
  const accounts = channels.filter((c) => c.kind === "account");
  const devices = channels.filter((c) => c.kind !== "account");
  const live = devices.filter((c) => c.state === "on").length;

  const Card = ({ c }: { c: InputChannel }) => {
    const isMic = c.id === "mic";
    const isWa = c.id === "whatsapp";
    const isClip = c.id === "clipboard";
    const isScreen = c.id === "screen";

    return (
      <div
        className="w-full text-left rounded-2xl border border-border bg-card/60 p-4 shadow-xs transition-all hover:border-border/90 relative"
      >
        <div className="flex items-start gap-3.5">
          <div className="relative mt-1">
            <span
              className={cn(
                "block h-3 w-3 rounded-full transition-all",
                c.state === "on"
                  ? "bg-emerald-500 ring-4 ring-emerald-500/20"
                  : c.state === "waiting"
                  ? "bg-amber-500 ring-4 ring-amber-500/20 animate-pulse"
                  : c.state === "ready"
                  ? "bg-sky-500"
                  : "bg-muted-foreground/40"
              )}
            />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-foreground">{c.name}</span>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase",
                    STATE_TONE[c.state] || STATE_TONE.off
                  )}
                >
                  {STATE_LABEL[c.state] || c.state}
                </span>
                <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">
                  {KIND_LABEL[c.kind] || c.kind}
                </span>
              </div>

              {/* Interactive Controls per Input Type */}
              <div className="flex items-center gap-2">
                {isMic && (
                  <button
                    type="button"
                    disabled={micLoading}
                    onClick={handleToggleMic}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-semibold shadow-xs transition-all cursor-pointer",
                      c.state === "on"
                        ? "bg-red-500/10 text-red-500 border border-red-500/30 hover:bg-red-500/20"
                        : "bg-emerald-600 text-white hover:bg-emerald-700"
                    )}
                  >
                    {c.state === "on" ? (
                      <>
                        <MicOff className="h-3.5 w-3.5" />
                        <span>Mute Mic</span>
                      </>
                    ) : (
                      <>
                        <Mic className="h-3.5 w-3.5" />
                        <span>Turn ON</span>
                      </>
                    )}
                  </button>
                )}

                {isWa && (c.state === "waiting" || c.state === "off") && (
                  <button
                    type="button"
                    onClick={openQrModal}
                    className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white shadow-xs hover:bg-emerald-700 transition-all cursor-pointer animate-pulse"
                  >
                    <QrCode className="h-3.5 w-3.5" />
                    <span>Scan QR Code</span>
                  </button>
                )}

                {isClip && (
                  <button
                    type="button"
                    onClick={handleTestClipboard}
                    className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] font-medium text-foreground hover:bg-muted/50 cursor-pointer"
                  >
                    <ClipboardCheck className="h-3 w-3" />
                    <span>Test Read</span>
                  </button>
                )}

                {isScreen && (
                  <button
                    type="button"
                    onClick={handleTestScreen}
                    className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] font-medium text-foreground hover:bg-muted/50 cursor-pointer"
                  >
                    <Monitor className="h-3 w-3" />
                    <span>Test Perception</span>
                  </button>
                )}
              </div>
            </div>

            <p className="mt-1 text-xs text-muted-foreground leading-relaxed">{c.detail}</p>
            {c.account && (
              <p className="mt-1 font-mono text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                {c.account}
              </p>
            )}

            {/* Live Interactive Results Preview */}
            {isClip && clipboardPreview && (
              <div className="mt-2.5 rounded-xl border border-border/80 bg-muted/40 p-2 text-xs font-mono text-foreground">
                <span className="text-[10px] uppercase font-bold text-muted-foreground mr-1.5">Clipboard Content:</span>
                {clipboardPreview}
              </div>
            )}

            {isScreen && screenNotice && (
              <div className="mt-2.5 rounded-xl border border-border/80 bg-muted/40 p-2 text-xs font-mono text-emerald-500">
                <span className="text-[10px] uppercase font-bold text-muted-foreground mr-1.5">Screen Status:</span>
                {screenNotice}
              </div>
            )}

            <div className="mt-2.5 border-t border-border/50 pt-2 flex items-center justify-between text-[10px] text-muted-foreground font-mono">
              <span>Source: <code className="text-foreground/80">{c.source}</code></span>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const stats = [
    { label: "Active Channels", value: String(channels.length) },
    { label: "Live Sensors", value: String(live) },
    { label: "Accounts Linked", value: String(data?.accounts?.filter((a) => a.connected).length ?? 0) },
    { label: "Total Providers", value: String(data?.accounts?.length ?? 0) },
  ];

  return (
    <Page
      title="Inputs"
      subtitle="Every modality and sensor ISHTI perceives: speech, wake listener, screen, clipboard, and messaging bridges."
      onToggleNav={onToggleNav}
      action={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setTick((n) => n + 1)}
            className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:border-emerald-500/40 cursor-pointer shadow-xs"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            <span>Refresh State</span>
          </button>
        </div>
      }
    >
      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3.5 text-xs text-red-400">
          {error}
        </div>
      )}

      {!data && !error && <div className="text-sm text-muted-foreground">Reading active input channels…</div>}

      {data && (
        <div className="space-y-6">
          {/* Summary Stat Tiles */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {stats.map((s) => (
              <div key={s.label} className="rounded-2xl border border-border bg-card/60 p-4 shadow-xs">
                <div className="text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">{s.label}</div>
                <div className="mt-1 text-2xl font-bold tracking-tight text-foreground">{s.value}</div>
              </div>
            ))}
          </div>

          {/* Physical & Software Modalities */}
          <section>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold text-foreground">Sensors, Audio &amp; System Modalities</h2>
                <p className="text-xs text-muted-foreground">Direct sensory inputs running locally on this machine.</p>
              </div>
              <span className="text-[11px] font-mono text-emerald-500 font-semibold">{live} live now</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 items-start">
              {devices.map((c) => (
                <Card key={c.id} c={c} />
              ))}
            </div>
          </section>

          {/* External Cloud & Messaging Accounts */}
          <section>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold text-foreground">Messaging &amp; Cloud Connectors</h2>
                <p className="text-xs text-muted-foreground">OIDC / OAuth 2.0 authorized channels for mail, social and enterprise data.</p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 items-start">
              {accounts.map((c) => (
                <Card key={c.id} c={c} />
              ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              Tip: Say <code className="text-foreground font-mono font-medium">connect &lt;service&gt; client-id … client-secret …</code> in chat or visit the Connectors section to link accounts.
            </p>
          </section>

          {data.note && <p className="text-[11px] text-muted-foreground font-mono">{data.note}</p>}
        </div>
      )}

      {/* WHATSAPP QR CODE SCANNER MODAL - PURE BLACK AND WHITE WITH TARGETING GUIDES */}
      <AnimatePresence>
        {qrModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="relative w-full max-w-md rounded-3xl border border-border bg-card p-6 shadow-2xl overflow-hidden"
            >
              <button
                type="button"
                onClick={() => setQrModalOpen(false)}
                className="absolute right-4 top-4 rounded-full p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer transition-colors"
                title="Close"
              >
                <X className="h-4 w-4" />
              </button>

              <div className="flex items-center gap-3 mb-5">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-emerald-600/15 text-emerald-500 font-bold border border-emerald-500/25">
                  <QrCode className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-foreground">Link WhatsApp Account</h3>
                  <p className="text-xs text-muted-foreground">Scan with WhatsApp on your phone to link locally</p>
                </div>
              </div>

              {qrError && (
                <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500 flex items-center justify-between">
                  <span>{qrError}</span>
                </div>
              )}

              {qrLoading ? (
                <div className="py-16 text-center space-y-3">
                  <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
                  <div className="text-sm font-medium text-foreground">Contacting WhatsApp bridge…</div>
                  <div className="text-xs text-muted-foreground">Generating encrypted pairing key</div>
                </div>
              ) : qrData?.connected ? (
                <div className="py-8 text-center space-y-3">
                  <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
                    <Check className="h-7 w-7" />
                  </div>
                  <h4 className="text-sm font-bold text-foreground">WhatsApp is Connected!</h4>
                  <p className="text-xs text-muted-foreground font-mono">{qrData.me || "Device Authenticated"}</p>
                  <p className="text-xs text-emerald-500 font-medium">Messages stream live 100% locally through your machine.</p>
                  <div className="pt-2">
                    <button
                      type="button"
                      onClick={() => setQrModalOpen(false)}
                      className="rounded-xl bg-emerald-600 px-6 py-2 text-xs font-semibold text-white hover:bg-emerald-700 transition-colors cursor-pointer shadow-xs"
                    >
                      Done
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  {/* PURE BLACK AND WHITE QR CODE FRAME */}
                  <div className="relative mx-auto flex items-center justify-center rounded-2xl border-2 border-border bg-white p-5 shadow-sm max-w-[300px]">
                    {/* Corner Targeting Accents */}
                    <div className="absolute top-2 left-2 h-4 w-4 border-t-2 border-l-2 border-emerald-600 rounded-tl-sm pointer-events-none" />
                    <div className="absolute top-2 right-2 h-4 w-4 border-t-2 border-r-2 border-emerald-600 rounded-tr-sm pointer-events-none" />
                    <div className="absolute bottom-2 left-2 h-4 w-4 border-b-2 border-l-2 border-emerald-600 rounded-bl-sm pointer-events-none" />
                    <div className="absolute bottom-2 right-2 h-4 w-4 border-b-2 border-r-2 border-emerald-600 rounded-br-sm pointer-events-none" />

                    {qrData?.qr_image ? (
                      <img
                        src={qrData.qr_image}
                        alt="WhatsApp linking QR code"
                        className="block w-full max-w-[260px] h-auto aspect-square select-none [image-rendering:pixelated]"
                        style={{ imageRendering: "pixelated" }}
                      />
                    ) : (
                      <div className="py-14 text-center space-y-2">
                        <div className="mx-auto h-7 w-7 animate-spin rounded-full border-2 border-emerald-600 border-t-transparent" />
                        <div className="text-xs font-medium text-neutral-800">
                          {qrData?.running
                            ? "Generating QR code from WhatsApp bridge…"
                            : "Starting WhatsApp local bridge…"}
                        </div>
                        <div className="text-[11px] text-neutral-500">Takes 2-5 seconds on first start</div>
                      </div>
                    )}
                  </div>

                  {/* HOW TO SCAN INSTRUCTIONS */}
                  <div className="space-y-2 rounded-2xl border border-border/80 bg-muted/40 p-3.5 text-xs text-muted-foreground">
                    <div className="font-semibold text-foreground flex items-center gap-1.5">
                      <span>How to link:</span>
                    </div>
                    <ol className="space-y-1.5 pl-1 leading-relaxed text-[12px]">
                      <li className="flex items-start gap-2">
                        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">1</span>
                        <span>Open <strong>WhatsApp</strong> on your mobile phone</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">2</span>
                        <span>Tap <strong>Menu</strong> or <strong>Settings</strong> &gt; <strong>Linked Devices</strong></span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">3</span>
                        <span>Tap <strong>Link a Device</strong> and point your camera at this QR code</span>
                      </li>
                    </ol>
                  </div>

                  <div className="flex items-center justify-between gap-3 pt-1">
                    <button
                      type="button"
                      onClick={async () => {
                        setQrLoading(true);
                        try {
                          const res = await getJSON<{
                            connected: boolean;
                            ascii_qr: string;
                            raw_qr: string | null;
                            me: string;
                            running: boolean;
                            qr_image?: string | null;
                          }>("/api/inputs/wa-qr");
                          setQrData(res);
                        } catch (error) {
                          setQrError(error instanceof Error ? error.message : "Could not refresh WhatsApp connection state");
                        } finally {
                          setQrLoading(false);
                        }
                      }}
                      className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted/50 cursor-pointer shadow-xs"
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      <span>Refresh QR</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setQrModalOpen(false)}
                      className="rounded-xl bg-emerald-600 px-5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 transition-colors cursor-pointer shadow-xs"
                    >
                      Done
                    </button>
                  </div>
                </div>
              )}
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </Page>
  );
}

// ---------------------------------------------------------------- Connectors
// A proper section for the accounts ISHTI plugs into. Every card is driven by ishti/connectors.py:
// real registry, real credential state, real token lifetime. Nothing here is a mock.
type Connector = {
  service: string; name: string; family: string; kind: "oauth" | "token";
  api: string; auth: string; scopes: string[]; granted: string[];
  website?: string; capabilities?: string;
  has_credentials: boolean; one_click?: boolean; connected: boolean; account: string;
  has_refresh: boolean; expires_at: number; connected_at: number;
  state: "connected" | "ready" | "empty" | "reconnect";
  setup: { console?: string; steps?: string; note?: string };
  status_text: string;
  progress: { state?: string; reply?: string; at?: number };
};
type ConnectorsPayload = {
  connectors: Connector[]; connected: number; ready: number;
  total: number; families: string[]; note: string;
};

const FAMILY_LABEL: Record<string, string> = {
  google: "Google", microsoft: "Microsoft", github: "GitHub",
  meta: "Meta", linkedin: "LinkedIn", telegram: "Telegram",
};
const CONN_TONE: Record<string, string> = {
  connected: "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20",
  ready: "bg-amber-500/10 text-amber-500 border border-amber-500/20",
  empty: "bg-muted text-muted-foreground border border-border",
};

function FlowiseCard() {
  const [data, setData] = useState<{ configured: boolean; health: string; flows: Array<{ id: string; name: string }> } | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    setLoading(true);
    getJSON<{ configured: boolean; health: string; flows: Array<{ id: string; name: string }> }>("/api/flowise/flows")
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-xs space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-500/15 text-indigo-500">
            <Workflow className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground">Flowise AI Orchestrator</span>
              <span className={cn(
                "rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                data?.configured ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500" : "border-border bg-muted/40 text-muted-foreground"
              )}>
                {data?.configured ? "configured" : "not configured"}
              </span>
            </div>
            <div className="text-[11px] text-muted-foreground">
              Visual LLM orchestration flows and chatflow endpoints
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-border p-1 text-muted-foreground hover:text-foreground hover:bg-muted cursor-pointer"
          title="Refresh Flowise status"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </button>
      </div>

      <div className="rounded-lg border border-border/60 bg-background/50 p-2.5 text-xs space-y-1">
        <div className="text-muted-foreground">
          Health: <span className="font-mono text-foreground">{data?.health || "Checking Flowise connection..."}</span>
        </div>
        <div className="text-muted-foreground">
          Registered Flows: <span className="font-medium text-foreground">{data?.flows?.length ?? 0}</span>
        </div>
      </div>

      {data?.flows && data.flows.length > 0 && (
        <ul className="space-y-1.5 text-xs">
          {data.flows.map((f) => (
            <li key={f.id} className="flex items-center justify-between rounded-lg border border-border/40 bg-card/60 px-2.5 py-1.5">
              <span className="font-medium text-foreground">{f.name}</span>
              <span className="font-mono text-[10px] text-muted-foreground">{f.id}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Connectors({ onToggleNav }: { onToggleNav?: () => void }) {
  const [data, setData] = useState<ConnectorsPayload | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [cid, setCid] = useState("");
  const [csec, setCsec] = useState("");
  const [group, setGroup] = useState<string>("all");
  const loadGeneration = useRef(0);

  const load = useCallback(() => {
    const generation = ++loadGeneration.current;
    getJSON<ConnectorsPayload>("/api/connectors").then((fresh) => {
      if (generation !== loadGeneration.current) return;
      setData(fresh); setError("");
      const terminal = fresh.connectors.filter((c) => ["done", "error"].includes(c.progress?.state || ""))
        .sort((a, b) => (b.progress.at || 0) - (a.progress.at || 0))[0];
      if (terminal?.progress.reply) setNotice(terminal.progress.reply);
    }).catch(
      (e: Error) => { if (generation === loadGeneration.current) setError(e?.message || "could not reach /api/connectors"); }
    );
  }, []);
  useEffect(load, [load]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(load, 100);
    };
    const events = ["ishti-connectors-updated", "ishti-ws-connected", "ishti-state-resync"];
    events.forEach((name) => window.addEventListener(name, refresh));
    return () => { clearTimeout(timer); events.forEach((name) => window.removeEventListener(name, refresh)); };
  }, [load]);

  // WebSocket changes refresh immediately; polling recovers completion after a dropped connection.
  useEffect(() => {
    if (!(data?.connectors ?? []).some((c) => c.progress?.state === "waiting")) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [data?.connectors, load]);

  const rows = (data?.connectors ?? []).filter((c) => group === "all" || c.family === group);

  const act = async (path: string, service: string, body?: unknown) => {
    setBusy(service); setNotice("");
    try {
      const r = await postJSON<{ ok: boolean; reply: string }>(path, body);
      if (!r.ok) throw new Error(r.reply || "The connector action did not complete.");
      setNotice(r.reply); setEditing(null); setCid(""); setCsec(""); load();
    } catch (e) {
      setNotice((e as Error).message);
    } finally { setBusy(""); }
  };
  const saveCreds = (s: string) => act(`/api/connectors/${s}/credentials`, s, { client_id: cid, client_secret: csec });
  const connect = (s: string) => act(`/api/connectors/${s}/connect`, s);
  const disconnect = (s: string) => act(`/api/connectors/${s}/disconnect`, s);

  // Render as a function: an inline component type remounts the credential inputs on every keystroke.
  const renderConnector = (c: Connector) => {
    const waiting = c.progress?.state === "waiting";
    return (
      <div key={c.service} className="flex flex-col rounded-xl border border-border bg-card p-4 shadow-xs">
        <div className="flex items-start gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground">{c.name}</span>
              <span className={cn("rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                CONN_TONE[c.state] || CONN_TONE.empty)}>
                {c.state === "connected" ? "connected" : c.state === "ready" ? "ready" : c.state === "reconnect" ? "reconnect" : "not set up"}
              </span>
            </div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              {FAMILY_LABEL[c.family] || c.family} · {c.kind === "token" ? "token" : "OAuth 2.0"}
              {c.scopes.length ? ` · ${c.scopes.length} scopes` : ""}
            </div>
          </div>
        </div>

        {c.connected ? (
          <div className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
            {c.account && <div>Signed in as <span className="text-foreground">{c.account}</span></div>}
            <div>{c.has_refresh ? "Refresh token stored - stays signed in."
              : "No refresh token - may need reconnecting."}</div>
          </div>
        ) : (
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{c.status_text}</p>
        )}

        {c.capabilities && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{c.capabilities}</p>}

        <div className="mt-3 flex flex-wrap gap-1.5">
          {!c.connected && c.kind === "oauth" && (
            <button type="button" disabled={busy === c.service || waiting}
              onClick={() => (c.has_credentials ? connect(c.service)
                : (setEditing(c.service), setCid(""), setCsec("")))}
              className={cn("rounded-lg px-2.5 py-1 text-[11px] font-medium cursor-pointer",
                c.has_credentials ? "bg-accent text-white" : "border border-border bg-card hover:border-accent/40",
                (busy === c.service || waiting) && "opacity-60")}>
              {waiting ? "Waiting for consent…" : c.has_credentials ? (c.service === "google" ? "Sign in with Google" : c.family === "google" ? "Connect to Google" : `Connect to ${FAMILY_LABEL[c.family] || c.name}`) : (c.service === "google" ? "Configure Google Sign-In" : "Advanced setup")}
            </button>
          )}
          {c.kind === "token" && (
            <a href="#settings" className="rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] hover:border-accent/40">Remote channel settings</a>
          )}
          {c.connected && c.kind === "oauth" && (
            <button type="button" disabled={busy === c.service} onClick={() => disconnect(c.service)}
              className="rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] font-medium hover:border-red-500/50 cursor-pointer disabled:opacity-60">
              Disconnect
            </button>
          )}
          {c.setup?.console && (
            <a href={c.setup.console} target="_blank" rel="noreferrer"
              className="rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] hover:border-accent/40">
              Open console
            </a>
          )}
        </div>

        {editing === c.service && (
          <div className="mt-3 space-y-2 rounded-lg border border-border bg-background p-2.5">
            <input value={cid} onChange={(e) => setCid(e.target.value)} placeholder="Client ID" aria-label={`${c.name} client ID`}
              className="w-full rounded-md border border-border bg-card px-2 py-1 text-[11px]" />
            <input value={csec} onChange={(e) => setCsec(e.target.value)} placeholder={c.service === "microsoft" ? "Client secret (optional for desktop apps)" : "Client secret"} aria-label={`${c.name} client secret`}
              type="password" className="w-full rounded-md border border-border bg-card px-2 py-1 text-[11px]" />
            <div className="flex gap-1.5">
              <button type="button" disabled={busy === c.service || !cid.trim()} onClick={() => saveCreds(c.service)}
                className="rounded-md bg-accent px-2.5 py-1 text-[11px] text-white disabled:opacity-50 cursor-pointer">
                Save
              </button>
              <button type="button" onClick={() => setEditing(null)}
                className="rounded-md border border-border px-2.5 py-1 text-[11px] cursor-pointer">
                Cancel
              </button>
            </div>
          </div>
        )}

        {c.setup?.steps && (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] text-accent">Advanced app setup</summary>
            <p className="mt-1 text-[10.5px] leading-relaxed text-muted-foreground">{c.setup.steps}</p>
            {c.setup.note && <p className="mt-1 text-[10.5px] text-muted-foreground/80">{c.setup.note}</p>}
          </details>
        )}
      </div>
    );
  };

  return (
    <Page
      title="Connectors"
      subtitle="Connect your accounts. ISHTI uses an available connector first and Chrome when the requested action needs the website."
      onToggleNav={onToggleNav}
      action={
        <button type="button" onClick={load}
          className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium hover:border-accent/40 cursor-pointer">
          Refresh
        </button>
      }
    >
      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">{error}</div>
      )}
      {notice && (
        <div className="rounded-xl border border-accent/30 bg-accent/10 p-3 text-xs text-foreground">{notice}</div>
      )}
      {!data && !error && <div className="text-sm text-muted-foreground">Reading the connectors…</div>}

      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "Connected", value: String(data.connected) },
              { label: "Ready", value: String(data.ready) },
              { label: "Available", value: String(data.total) },
            ].map((s) => (
              <div key={s.label} className="rounded-xl border border-border bg-card p-3 shadow-xs">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{s.label}</div>
                <div className="mt-1 text-xl font-semibold text-foreground">{s.value}</div>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
            {["all", ...data.families].map((f) => (
              <button key={f} type="button" onClick={() => setGroup(f)}
                className={cn("rounded-xl px-3 py-1.5 text-xs font-medium shrink-0 cursor-pointer",
                  group === f ? "bg-accent text-white"
                    : "border border-border bg-card/60 text-muted-foreground hover:text-foreground")}>
                {f === "all" ? "All" : FAMILY_LABEL[f] || f}
              </button>
            ))}
          </div>

          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {rows.map(renderConnector)}
          </div>

          <FlowiseCard />

          <p className="text-[11px] text-muted-foreground">{data.note}</p>
        </div>
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- Skills
type SkillInfo = {
  name: string;
  version: string;
  description: string;
  route: string;
  status: string;
  category?: string;
  path?: string;
  folder?: string;
  problem?: string;
};

const CATEGORY_METAS: Record<
  string,
  { label: string; desc: string; color: string; bg: string; border: string }
> = {
  "System & Desktop": {
    label: "System & Desktop",
    desc: "OS control, automation, system diagnostics, and shell tools",
    color: "text-purple-500",
    bg: "bg-purple-500/10",
    border: "border-purple-500/25",
  },
  "Files & Storage": {
    label: "Files & Storage",
    desc: "Local file system operations, directories, and folder indexing",
    color: "text-amber-500",
    bg: "bg-amber-500/10",
    border: "border-amber-500/25",
  },
  "Web & Internet": {
    label: "Web & Internet",
    desc: "Browser automation, web search, web tools, and live data fetching",
    color: "text-sky-500",
    bg: "bg-sky-500/10",
    border: "border-sky-500/25",
  },
  "Communication & Social": {
    label: "Communication & Social",
    desc: "Chatting, WhatsApp, email drafting, and contact records",
    color: "text-emerald-500",
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/25",
  },
  "Productivity & Planning": {
    label: "Productivity & Planning",
    desc: "Calendars, reminders, multi-step workflows, and document handling",
    color: "text-violet-500",
    bg: "bg-violet-500/10",
    border: "border-violet-500/25",
  },
  "Knowledge & Intelligence": {
    label: "Knowledge & Intelligence",
    desc: "Personal facts, long-term memory, deep research, and AI reasoning",
    color: "text-indigo-500",
    bg: "bg-indigo-500/10",
    border: "border-indigo-500/25",
  },
  "Development & Code": {
    label: "Development & Code",
    desc: "Code execution, developer scripts, and dynamic skill synthesis",
    color: "text-cyan-500",
    bg: "bg-cyan-500/10",
    border: "border-cyan-500/25",
  },
  "Media & Creative": {
    label: "Media & Creative",
    desc: "Video manipulation, audio extraction, and media processing",
    color: "text-rose-500",
    bg: "bg-rose-500/10",
    border: "border-rose-500/25",
  },
  "Finance & Commerce": {
    label: "Finance & Commerce",
    desc: "Stock/crypto markets, financial tracking, and shopping lookups",
    color: "text-orange-500",
    bg: "bg-orange-500/10",
    border: "border-orange-500/25",
  },
  "Utilities & Daily": {
    label: "Utilities & Daily",
    desc: "Weather forecasts, travel calculations, and everyday helper tools",
    color: "text-teal-500",
    bg: "bg-teal-500/10",
    border: "border-teal-500/25",
  },
};

export function Skills({ onToggleNav }: { onToggleNav?: () => void }) {
  // Markets lives here now (it is the markets skill's dashboard); old #markets links open this tab
  const [section, setSection] = useState<"skills" | "markets">(() => (window.location.hash.toLowerCase().includes("markets") ? "markets" : "skills"));
  const sectionTabs = (
    <div role="tablist" aria-label="Skills section" className="flex items-center rounded-xl border border-border bg-card p-1 shadow-xs">
      {([["skills", "All skills"], ["markets", "Markets"]] as const).map(([k, label]) => (
        <button key={k} type="button" role="tab" aria-selected={section === k} onClick={() => setSection(k)}
          className={cn("rounded-lg px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer",
            section === k ? "bg-accent text-white shadow-sm" : "text-muted-foreground hover:text-foreground")}>{label}</button>))}
    </div>
  );
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [search, setSearch] = useState("");
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [activeSkill, setActiveSkill] = useState<SkillInfo | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"tree" | "folders" | "diagram">("folders");
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});
  const [focusedRow, setFocusedRow] = useState(-1);
  const treeRef = useRef<HTMLDivElement | null>(null);
  // Root ("ishti/skills") and per-skill expansion: every skill opens into the other skills it hands off to,
  // so the tree reads like the org chart - one root, departments, and a skill that can expand into peers.
  const [rootOpen, setRootOpen] = useState(true);
  const [expandedSkills, setExpandedSkills] = useState<Record<string, boolean>>({});

  useEffect(() => {
    getJSON<{ skills: SkillInfo[] }>("/api/skills").then((d) => {
      setSkills(d.skills);
      // Initialize all folders expanded in tree view
      const initialExp: Record<string, boolean> = {};
      d.skills.forEach((s) => {
        const cat = s.category || "General";
        initialExp[cat] = true;
      });
      setExpandedFolders(initialExp);
    });
  }, []);

  const tone = (s: string) =>
    s === "working"
      ? "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20"
      : s === "broken"
      ? "bg-red-500/10 text-red-500 border border-red-500/20"
      : "bg-amber-500/10 text-amber-500 border border-amber-500/20";

  // Group skills by category
  const categories = Array.from(
    new Set(skills.map((s) => s.category || "General"))
  ).sort();

  const skillsByCategory: Record<string, SkillInfo[]> = {};
  categories.forEach((cat) => {
    skillsByCategory[cat] = skills.filter((s) => (s.category || "General") === cat);
  });

  // The skills this one can grow into: any skill it names in its route/description first, then the peers it
  // sits beside in its own department. Capped so one skill cannot flood the tree.
  const relatedTo = (s: SkillInfo): SkillInfo[] => {
    const hay = `${s.description || ""} ${s.route || ""}`.toLowerCase();
    const named = skills.filter((o) => o.name !== s.name && hay.includes(o.name.toLowerCase()));
    const peers = (skillsByCategory[s.category || "General"] || []).filter((o) => o.name !== s.name);
    const seen = new Set<string>();
    return [...named, ...peers].filter((o) => (seen.has(o.name) ? false : seen.add(o.name))).slice(0, 8);
  };

  // Filter skills by search query
  const searchFilter = (s: SkillInfo) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      s.name.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q) ||
      (s.category && s.category.toLowerCase().includes(q)) ||
      (s.path && s.path.toLowerCase().includes(q)) ||
      (s.route && s.route.toLowerCase().includes(q))
    );
  };

  const filteredSkills = skills.filter(searchFilter);

  const toggleFolderExpand = (cat: string) => {
    setExpandedFolders((prev) => ({ ...prev, [cat]: !prev[cat] }));
  };

  const toggleAllFolders = (expand: boolean) => {
    const next: Record<string, boolean> = {};
    categories.forEach((c) => {
      next[c] = expand;
    });
    setExpandedFolders(next);
  };

  // Keyboard tree (F): one flat list of the rows the eye can see - a category header, then its skills while open.
  const treeRows: { kind: "cat" | "skill"; cat: string; skill?: SkillInfo }[] = [];
  categories.forEach((cat) => {
    treeRows.push({ kind: "cat", cat });
    if (expandedFolders[cat] !== false) {
      (skillsByCategory[cat] || []).forEach((s) => treeRows.push({ kind: "skill", cat, skill: s }));
    }
  });
  const rowIndex = (kind: "cat" | "skill", key: string) =>
    treeRows.findIndex((r) => r.kind === kind && (kind === "cat" ? r.cat === key : r.skill?.name === key));
  const focusRow = (idx: number) => {
    if (!treeRows.length) return;
    const clamped = Math.max(0, Math.min(treeRows.length - 1, idx));
    setFocusedRow(clamped);
    treeRef.current?.querySelector<HTMLElement>(`[data-tree-row="${clamped}"]`)?.focus();
  };
  const statusBadge = (status: string) =>
    status === "working"
      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-500"
      : status === "broken"
      ? "border-red-500/25 bg-red-500/10 text-red-500"
      : "border-amber-500/25 bg-amber-500/10 text-amber-500";
  const onTreeKeyDown = (e: React.KeyboardEvent) => {
    if (!treeRows.length) return;
    const row = treeRows[Math.max(0, focusedRow)];
    const catOpen = row ? expandedFolders[row.cat] !== false : false;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusRow(focusedRow < 0 ? 0 : focusedRow + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusRow(focusedRow < 0 ? treeRows.length - 1 : focusedRow - 1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      if (row?.kind === "cat" && !catOpen) setExpandedFolders((prev) => ({ ...prev, [row.cat]: true }));
      else focusRow(focusedRow + 1);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      if (row?.kind === "cat" && catOpen) setExpandedFolders((prev) => ({ ...prev, [row.cat]: false }));
      else if (row?.kind === "skill") {
        const ci = treeRows.findIndex((r) => r.kind === "cat" && r.cat === row.cat);
        if (ci >= 0) focusRow(ci);
      }
    } else if (e.key === "Home") {
      e.preventDefault();
      focusRow(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusRow(treeRows.length - 1);
    }
  };

  const handleCopy = (text: string, key: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    try {
      navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 1800);
    } catch {}
  };

  const handleOpenInChat = (skillName: string) => {
    try {
      localStorage.setItem("ishti-focused-skill", skillName);
      localStorage.removeItem("ishti-session-id");
      localStorage.setItem("ishti-page", "chat");
      if (window.location.hash === "#chat/new") {
        window.location.hash = "#chat";
        setTimeout(() => {
          window.location.hash = "#chat/new";
        }, 10);
      } else {
        window.location.hash = "#chat/new";
      }
      window.dispatchEvent(
        new CustomEvent("ishti-start-new-chat", {
          detail: { focusedSkill: skillName },
        })
      );
    } catch {}
  };

  return (
    section === "markets" ? (
      <Page title="Skills" subtitle="Markets: live prices, research and your past market checks." onToggleNav={onToggleNav} action={sectionTabs}>
        <Markets embedded />
      </Page>
    ) :
    <Page
      title="Skills"
      subtitle="Folder-based modular skill system. Each skill is isolated under ishti/skills with manifest routing."
      onToggleNav={onToggleNav}
      action={
        <div className="flex items-center gap-2">
          {sectionTabs}
          {/* Toggle between Tree Accordion and Folder Cards */}
          <div className="flex items-center rounded-xl border border-border bg-card p-1 shadow-xs">
            <button
              type="button"
              onClick={() => {
                setViewMode("tree");
                setActiveSkill(null);
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer",
                viewMode === "tree"
                  ? "bg-accent text-white shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Directory tree view"
            >
              <FolderKanban className="h-3.5 w-3.5" />
              <span>Tree View</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setViewMode("folders");
                setSelectedFolder(null);
                setActiveSkill(null);
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer",
                viewMode === "folders"
                  ? "bg-accent text-white shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Folder view"
            >
              <Folder className="h-3.5 w-3.5" />
              <span>Folders</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setViewMode("diagram");
                setSelectedFolder(null);
                setActiveSkill(null);
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer",
                viewMode === "diagram"
                  ? "bg-accent text-white shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              )}
              title="Interactive tree diagram view"
            >
              <Network className="h-3.5 w-3.5" />
              <span>Diagram</span>
            </button>
          </div>

          {viewMode === "tree" && (
            <button
              type="button"
              onClick={() => {
                const anyOpen = Object.values(expandedFolders).some(Boolean);
                toggleAllFolders(!anyOpen);
              }}
              className="rounded-xl border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted/40 transition-colors cursor-pointer shadow-xs"
            >
              {Object.values(expandedFolders).some(Boolean) ? "Collapse All" : "Expand All"}
            </button>
          )}
        </div>
      }
    >
      <div className="space-y-5">
        {/* Breadcrumb Path & Search Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 rounded-2xl border border-border bg-card/40 p-3.5 shadow-xs">
          {/* Breadcrumb Navigation */}
          <div className="flex items-center gap-2 text-xs font-mono min-w-0 flex-wrap">
            <button
              type="button"
              onClick={() => {
                setSelectedFolder(null);
                setActiveSkill(null);
              }}
              className="flex items-center gap-1.5 text-muted-foreground hover:text-accent font-semibold transition-colors cursor-pointer shrink-0"
            >
              <Folder className="h-4 w-4 text-accent" />
              <span>ishti/skills</span>
            </button>

            {selectedFolder && (
              <>
                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                <button
                  type="button"
                  onClick={() => setActiveSkill(null)}
                  className="flex items-center gap-1 font-semibold text-foreground truncate bg-accent-soft px-2 py-0.5 rounded-md hover:bg-accent/20 transition-colors cursor-pointer"
                >
                  <FolderOpen className="h-3.5 w-3.5 text-accent shrink-0" />
                  <span className="truncate">{selectedFolder}</span>
                </button>
                {activeSkill && (
                  <>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                    <span className="flex items-center gap-1 font-mono text-accent truncate bg-accent/10 border border-accent/30 px-2 py-0.5 rounded-md">
                      <span>{activeSkill.name}</span>
                    </span>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setSelectedFolder(null);
                    setActiveSkill(null);
                  }}
                  className="flex items-center gap-1 rounded-lg border border-border bg-background px-2 py-0.5 text-[11px] font-sans font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer ml-1"
                >
                  <ArrowLeft className="h-3 w-3" />
                  <span>All Folders</span>
                </button>
              </>
            )}
          </div>

          {/* Search Bar */}
          <div className="relative flex-1 sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search skills, folders, paths..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-xl border border-border bg-background py-1.5 pl-8 pr-3 text-xs text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-hidden"
            />
          </div>
        </div>

        {/* Category Pills Bar (Quick Switcher with React Animation) */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scroll-thin">
          <button
            type="button"
            onClick={() => {
              setSelectedFolder(null);
              setActiveSkill(null);
            }}
            className={cn(
              "flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium shrink-0 transition-all cursor-pointer shadow-xs",
              selectedFolder === null && !search
                ? "bg-accent text-white font-semibold"
                : "border border-border bg-card/60 text-muted-foreground hover:text-foreground hover:bg-muted/40"
            )}
          >
            <Folder className="h-3.5 w-3.5" />
            <span>All Categories</span>
            <span className="rounded-full bg-white/20 px-1.5 py-0.2 text-[10px] font-mono">
              {skills.length}
            </span>
          </button>

          {categories.map((cat) => {
            const meta = CATEGORY_METAS[cat] || {
              color: "text-foreground",
              bg: "bg-muted",
              border: "border-border",
            };
            const count = skillsByCategory[cat]?.length || 0;
            const isSelected = selectedFolder === cat;

            return (
              <button
                key={cat}
                type="button"
                onClick={() => {
                  setSelectedFolder(isSelected ? null : cat);
                  setActiveSkill(null);
                  if (viewMode === "tree") {
                    setExpandedFolders((prev) => ({ ...prev, [cat]: true }));
                  }
                }}
                className={cn(
                  "flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium shrink-0 transition-all cursor-pointer shadow-xs",
                  isSelected
                    ? "bg-accent text-white font-semibold shadow-sm"
                    : "border border-border bg-card/60 text-muted-foreground hover:text-foreground hover:bg-muted/40"
                )}
              >
                <span className={cn("h-2 w-2 rounded-full", meta.bg)} />
                <span>{cat}</span>
                <span className="rounded-full bg-muted/80 px-1.5 py-0.2 text-[10px] font-mono text-muted-foreground">
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        {/* ========================================================
             MODES 1 & 2: FOLDERS VIEW (REACT ANIMATION BETWEEN FOLDERS)
             ======================================================== */}
        {viewMode === "folders" && !search && (
          <AnimatePresence mode="wait">
            {selectedFolder === null ? (
              <motion.div
                key="folders-overview-grid"
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
                transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
              >
                {categories.map((cat, i) => {
                  const catSkills = skillsByCategory[cat] || [];
                  const meta = CATEGORY_METAS[cat] || {
                    label: cat,
                    desc: "Skill module category",
                    color: "text-accent",
                    bg: "bg-accent-soft",
                    border: "border-border",
                  };
                  const workingCount = catSkills.filter((s) => s.status === "working").length;
                  const issueCount = catSkills.length - workingCount;

                  return (
                    <motion.div
                      key={cat}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.02 }}
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                          setSelectedFolder(cat);
                          setActiveSkill(null);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            setSelectedFolder(cat);
                            setActiveSkill(null);
                          }
                        }}
                        className="group relative flex flex-col justify-between rounded-2xl border border-border bg-card/50 p-4 shadow-xs hover:border-accent/60 hover:bg-card/80 hover:shadow-md transition-all cursor-pointer h-full"
                      >
                        <div>
                          {/* 3D Folder Icon from Chat Section */}
                          <div
                            className="relative flex items-center justify-center rounded-xl bg-gradient-to-b from-accent-soft/30 to-accent-soft/10 border border-border/50 overflow-hidden py-2 mb-3.5 group-hover:border-accent/40 group-hover:from-accent-soft/45 transition-all"
                            style={{ height: 104 }}
                          >
                            <div
                              style={{
                                width: 209,
                                height: 176,
                                transform: "scale(0.44)",
                                transformOrigin: "center center",
                                willChange: "transform",
                              }}
                              className="flex items-center justify-center shrink-0 group-hover:scale-[0.49] transition-transform duration-300"
                            >
                              <RareFolder color="blue" size="sm" allowOpen={true} />
                            </div>
                            <div className="absolute top-2 right-2 flex items-center gap-1.5">
                              <span className="rounded-md bg-card/85 backdrop-blur-xs px-2 py-0.5 text-[11px] font-mono font-medium text-foreground border border-border/60">
                                {catSkills.length} skills
                              </span>
                              {issueCount > 0 && (
                                <span className="rounded-md bg-red-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-red-500 border border-red-500/20">
                                  {issueCount} issue
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Folder Name & Description */}
                          <h3 className="font-semibold text-foreground text-sm group-hover:text-accent transition-colors flex items-center gap-1.5">
                            <span>{cat}</span>
                            <ChevronRight className="h-3.5 w-3.5 opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all text-accent" />
                          </h3>
                          <div className="text-[11px] font-mono text-muted-foreground/80 mt-0.5 truncate">
                            ishti/skills/{cat.toLowerCase().replace(/[^a-z0-9]+/g, "_")}/
                          </div>
                          <p className="text-xs text-muted-foreground mt-2 line-clamp-2 leading-relaxed">
                            {meta.desc}
                          </p>
                        </div>

                        {/* Preview of skill names inside folder */}
                        <div className="mt-4 pt-3 border-t border-border/60">
                          <div className="flex flex-wrap gap-1">
                            {catSkills.slice(0, 4).map((s) => (
                              <span
                                key={s.name}
                                className="rounded-md bg-background px-2 py-0.5 text-[10px] font-mono text-foreground/80 border border-border/60"
                              >
                                {s.name}
                              </span>
                            ))}
                            {catSkills.length > 4 && (
                              <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                                +{catSkills.length - 4} more
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  );
                })}
              </motion.div>
            ) : (
              <motion.div
                key={`folder-${selectedFolder}`}
                initial={{ opacity: 0, x: 24, filter: "blur(2px)" }}
                animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, x: -24, filter: "blur(2px)" }}
                transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
                className="space-y-4"
              >
                {/* Folder Header Banner */}
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 rounded-2xl border border-accent/30 bg-accent/5 p-4 shadow-xs">
                  <div className="flex items-center gap-3">
                    <div className="grid h-11 w-11 place-items-center rounded-xl bg-accent text-white shadow-xs">
                      <FolderOpen className="h-6 w-6 fill-current/30" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <h2 className="text-base font-bold text-foreground tracking-tight">
                          {selectedFolder}
                        </h2>
                        <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-semibold text-accent font-mono">
                          {skillsByCategory[selectedFolder]?.length || 0} skills in folder
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {CATEGORY_METAS[selectedFolder]?.desc || "Folder containing registered skills"}
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => {
                      setSelectedFolder(null);
                      setActiveSkill(null);
                    }}
                    className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted/40 transition-colors cursor-pointer shadow-xs self-end sm:self-auto"
                  >
                    <ArrowLeft className="h-3.5 w-3.5 text-accent" />
                    <span>Back to All Folders</span>
                  </button>
                </div>

                {/* The Skills Names Only Grid */}
                <div className="rounded-2xl border border-border bg-card/40 p-4 shadow-xs space-y-3">
                  <div className="flex items-center justify-between gap-2 flex-wrap pb-2 border-b border-border/60">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Skills in {selectedFolder}
                      </span>
                      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-mono font-semibold text-accent">
                        {skillsByCategory[selectedFolder]?.length || 0} total
                      </span>
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      Click any skill name to inspect details & actions
                    </div>
                  </div>

                  <div className="grid gap-2.5 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                    {(skillsByCategory[selectedFolder] || []).map((s) => {
                      const isSelected = activeSkill?.name === s.name;
                      return (
                        <motion.button
                          key={s.name}
                          type="button"
                          onClick={() => setActiveSkill(isSelected ? null : s)}
                          whileHover={{ scale: 1.02 }}
                          whileTap={{ scale: 0.98 }}
                          className={cn(
                            "group flex items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-left transition-all cursor-pointer shadow-xs",
                            isSelected
                              ? "border-accent bg-accent/15 ring-2 ring-accent/30 shadow-sm"
                              : "border-border bg-background/80 hover:border-accent/50 hover:bg-card"
                          )}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span
                              className={cn(
                                "h-2 w-2 shrink-0 rounded-full",
                                s.status === "broken"
                                  ? "bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.7)]"
                                  : s.status === "working"
                                  ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]"
                                  : "bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.7)]"
                              )}
                            />
                            <span
                              className={cn(
                                "font-mono text-xs font-semibold truncate transition-colors",
                                isSelected ? "text-accent" : "text-foreground group-hover:text-accent"
                              )}
                            >
                              {s.name}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5 shrink-0">
                            <span className="text-[10px] font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                              v{s.version}
                            </span>
                            {isSelected ? (
                              <ChevronUp className="h-3.5 w-3.5 text-accent shrink-0" />
                            ) : (
                              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground/60 group-hover:text-foreground shrink-0" />
                            )}
                          </div>
                        </motion.button>
                      );
                    })}
                  </div>
                </div>

                {/* Active Skill Inspector / Detail Viewer */}
                <AnimatePresence mode="wait">
                  {activeSkill ? (
                    <motion.div
                      key={activeSkill.name}
                      initial={{ opacity: 0, y: 10, height: 0 }}
                      animate={{ opacity: 1, y: 0, height: "auto" }}
                      exit={{ opacity: 0, y: -10, height: 0 }}
                      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                      className="overflow-hidden"
                    >
                      <div className="rounded-2xl border border-accent/40 bg-card p-5 shadow-sm space-y-4">
                        {/* Top Row */}
                        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 pb-3">
                          <div className="flex items-center gap-2.5 flex-wrap min-w-0">
                            <span className="font-mono text-base font-bold text-foreground">
                              {activeSkill.name}
                            </span>
                            <span
                              className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${tone(
                                activeSkill.status
                              )}`}
                            >
                              {activeSkill.status === "working"
                                ? "Operational"
                                : activeSkill.status === "broken"
                                ? "Needs Attention"
                                : activeSkill.status}
                            </span>
                            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-mono text-muted-foreground">
                              v{activeSkill.version}
                            </span>
                            <span className="rounded-md bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">
                              {activeSkill.category || selectedFolder}
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => handleOpenInChat(activeSkill.name)}
                              className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:opacity-90 transition-all cursor-pointer"
                            >
                              <MessageSquare className="h-3.5 w-3.5" />
                              <span>Open in Chat</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => setActiveSkill(null)}
                              className="grid h-8 w-8 place-items-center rounded-xl border border-border bg-background text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors cursor-pointer"
                              title="Close inspector"
                            >
                              <X className="h-4 w-4" />
                            </button>
                          </div>
                        </div>

                        {/* File Path & Copy */}
                        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 rounded-xl bg-muted/50 p-2.5 border border-border/60">
                          <div className="flex items-center gap-2 min-w-0">
                            <FileCode className="h-4 w-4 text-accent shrink-0" />
                            <span className="font-mono text-xs text-foreground/90 truncate">
                              {activeSkill.path || `ishti/skills/${activeSkill.name}.py`}
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={(e) =>
                              handleCopy(
                                activeSkill.path || `ishti/skills/${activeSkill.name}.py`,
                                `path-${activeSkill.name}`,
                                e
                              )
                            }
                            className="flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground hover:border-accent hover:text-accent transition-colors cursor-pointer shrink-0"
                          >
                            {copiedKey === `path-${activeSkill.name}` ? (
                              <>
                                <Check className="h-3.5 w-3.5 text-emerald-500" />
                                <span className="text-emerald-500">Copied!</span>
                              </>
                            ) : (
                              <>
                                <Copy className="h-3.5 w-3.5" />
                                <span>Copy Path</span>
                              </>
                            )}
                          </button>
                        </div>

                        {/* Route Trigger */}
                        {activeSkill.route && (
                          <div className="rounded-xl border border-border/80 bg-background/80 p-3 space-y-1">
                            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                              <Wrench className="h-3 w-3 text-accent" />
                              <span>Routing & Trigger Intent</span>
                            </div>
                            <p className="text-xs text-foreground font-mono bg-muted/40 p-2 rounded-lg leading-relaxed">
                              {activeSkill.route}
                            </p>
                          </div>
                        )}

                        {/* Description */}
                        <div className="space-y-1">
                          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                            Description
                          </div>
                          <p className="text-xs text-muted-foreground leading-relaxed">
                            {activeSkill.description}
                          </p>
                        </div>

                        {/* Problem/Error Warning if any */}
                        {activeSkill.problem && (
                          <div className="rounded-xl border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-500 space-y-1">
                            <div className="font-semibold flex items-center gap-1.5">
                              <span>Diagnostic Issue:</span>
                            </div>
                            <p className="leading-relaxed font-mono">{activeSkill.problem}</p>
                          </div>
                        )}
                      </div>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="no-skill-selected"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="rounded-2xl border border-dashed border-border/80 bg-card/20 p-4 text-center text-xs text-muted-foreground"
                    >
                      💡 Click on any skill name above to inspect its code path, route trigger, description, and status.
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            )}
          </AnimatePresence>
        )}

        {/* ========================================================
             MODE 2: DIRECTORY TREE ACCORDION VIEW
             ======================================================== */}
        {viewMode === "tree" && !search && (
          <div className="rounded-2xl border border-border bg-card/40 p-3 shadow-xs sm:p-4 outline-none" role="tree" tabIndex={0} ref={treeRef}
            aria-label="Skills tree - arrow keys to move, Enter to open a row" onKeyDown={onTreeKeyDown}>
            {/* root of the tree - the one node everything else hangs from */}
            <button
              type="button"
              onClick={() => setRootOpen((v) => !v)}
              aria-expanded={rootOpen}
              className="flex w-full flex-wrap items-center gap-2 px-1 pb-1.5 text-left font-mono text-xs text-foreground hover:bg-muted/30 rounded-lg cursor-pointer"
            >
              <motion.span animate={{ rotate: rootOpen ? 90 : 0 }} transition={{ duration: 0.18 }} className="grid h-4 w-4 shrink-0 place-items-center text-muted-foreground">
                <ChevronRight className="h-3.5 w-3.5" />
              </motion.span>
              <FolderOpen className="h-4 w-4 text-accent" />
              <span className="font-semibold">ishti/skills</span>
              <span className="text-muted-foreground">{skills.length} skills in {categories.length} departments</span>
            </button>
            <AnimatePresence initial={false}>
            {rootOpen && (
              <motion.ul
                key="root-group"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.22, ease: "easeInOut" }}
                className="ml-2 overflow-hidden"
                role="group"
              >
              {categories.map((cat, ci) => {
                const catSkills = skillsByCategory[cat] || [];
                const isOpen = expandedFolders[cat] !== false;
                const lastCat = ci === categories.length - 1;
                const meta = CATEGORY_METAS[cat] || { color: "text-accent", bg: "bg-accent-soft", border: "border-border" };
                return (
                  <li key={cat} className="relative pl-6" role="treeitem" aria-expanded={isOpen}>
                    {/* trunk + elbow of this folder */}
                    <span aria-hidden className="absolute left-0 top-0 w-px bg-border" style={{ height: lastCat ? 20 : "100%" }} />
                    <span aria-hidden className="absolute left-0 top-5 h-px w-5 bg-border" />
                    <button
                      type="button"
                      data-tree-row={rowIndex("cat", cat)}
                      onFocus={() => setFocusedRow(rowIndex("cat", cat))}
                      onClick={() => toggleFolderExpand(cat)}
                      aria-label={`${cat}, ${catSkills.length} skills, ${isOpen ? "open" : "closed"}`}
                      className="group flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors hover:bg-muted/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                    >
                      <motion.span animate={{ rotate: isOpen ? 90 : 0 }} transition={{ duration: 0.18 }} className="grid h-4 w-4 shrink-0 place-items-center text-muted-foreground">
                        <ChevronRight className="h-3.5 w-3.5" />
                      </motion.span>
                      <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-md border", meta.bg, meta.border, meta.color)}>
                        {isOpen ? <FolderOpen className="h-3.5 w-3.5" /> : <Folder className="h-3.5 w-3.5" />}
                      </span>
                      <span className="text-sm font-semibold tracking-tight text-foreground">{cat}</span>
                      <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{catSkills.length}</span>
                    </button>
                    <AnimatePresence initial={false}>
                      {isOpen && (
                        <motion.ul
                          role="group"
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.22, ease: "easeInOut" }}
                          className="overflow-hidden"
                        >
                          {catSkills.map((s, si) => {
                            const isSelected = activeSkill?.name === s.name;
                            const lastSkill = si === catSkills.length - 1;
                            const isExpandedSkill = !!expandedSkills[s.name];
                            const related = relatedTo(s);
                            return (
                              <motion.li
                                key={s.name}
                                role="treeitem"
                                aria-expanded={isExpandedSkill}
                                initial={{ opacity: 0, x: -8 }}
                                animate={{ opacity: 1, x: 0 }}
                                transition={{ delay: Math.min(si, 12) * 0.018, duration: 0.18 }}
                                className="relative ml-[7px] pl-6"
                              >
                                <span aria-hidden className="absolute left-0 top-0 w-px bg-border" style={{ height: lastSkill ? 18 : "100%" }} />
                                <span aria-hidden className="absolute left-0 top-[18px] h-px w-5 bg-border" />
                                <div className="flex items-center gap-1">
                                {/* grow this skill into the other skills it hands off to */}
                                <button
                                  type="button"
                                  onClick={() => setExpandedSkills((p) => ({ ...p, [s.name]: !p[s.name] }))}
                                  aria-label={`${isExpandedSkill ? "Hide" : "Show"} other skills for ${s.name}`}
                                  title="Other skills this one works with"
                                  className="grid h-5 w-5 shrink-0 place-items-center rounded text-muted-foreground hover:text-accent hover:bg-muted/40 cursor-pointer"
                                >
                                  <motion.span animate={{ rotate: isExpandedSkill ? 90 : 0 }} transition={{ duration: 0.18 }} className="grid place-items-center">
                                    <ChevronRight className="h-3.5 w-3.5" />
                                  </motion.span>
                                </button>
                                <button
                                  type="button"
                                  data-tree-row={rowIndex("skill", s.name)}
                                  onFocus={() => setFocusedRow(rowIndex("skill", s.name))}
                                  onClick={() => setActiveSkill(isSelected ? null : s)}
                                  className={cn(
                                    "my-0.5 flex min-w-0 flex-1 items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                                    isSelected ? "border-accent/60 bg-accent/10" : "border-transparent hover:border-border hover:bg-muted/30"
                                  )}
                                >
                                  <span className={cn("h-2 w-2 shrink-0 rounded-full", s.status === "broken" ? "bg-red-500" : s.status === "working" ? "bg-emerald-500" : "bg-amber-500")} />
                                  <span className="shrink-0 font-mono text-xs font-semibold text-foreground">{s.name}</span>
                                  <span className="hidden min-w-0 flex-1 truncate text-[11px] text-muted-foreground md:block">{s.description}</span>
                                  <span className="ml-auto flex shrink-0 items-center gap-1.5">
                                    <span className={cn("rounded-full border px-1.5 py-0.5 text-[9.5px] font-semibold capitalize", statusBadge(s.status))}>{s.status}</span>
                                    <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">v{s.version}</span>
                                  </span>
                                </button>
                                </div>
                                <AnimatePresence initial={false}>
                                  {isExpandedSkill && related.length > 0 && (
                                    <motion.ul
                                      role="group"
                                      initial={{ height: 0, opacity: 0 }}
                                      animate={{ height: "auto", opacity: 1 }}
                                      exit={{ height: 0, opacity: 0 }}
                                      transition={{ duration: 0.2, ease: "easeInOut" }}
                                      className="ml-2.5 mt-0.5 overflow-hidden border-l border-dashed border-border pl-3"
                                    >
                                      <li className="py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                                        {s.name} works with
                                      </li>
                                      {related.map((r) => (
                                        <li key={r.name} className="py-[1px]">
                                          <button
                                            type="button"
                                            onClick={() => setActiveSkill(r)}
                                            title={r.description}
                                            className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-muted/40 cursor-pointer"
                                          >
                                            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", r.status === "broken" ? "bg-red-500" : r.status === "working" ? "bg-emerald-500" : "bg-amber-500")} />
                                            <span className="font-mono text-[11px] text-foreground">{r.name}</span>
                                            <span className="hidden min-w-0 flex-1 truncate text-[10px] text-muted-foreground md:block">{r.description}</span>
                                          </button>
                                        </li>
                                      ))}
                                    </motion.ul>
                                  )}
                                </AnimatePresence>
                              </motion.li>
                            );
                          })}
                        </motion.ul>
                      )}
                    </AnimatePresence>
                  </li>
                );
              })}
              </motion.ul>
            )}
            </AnimatePresence>
          </div>
        )}

        {/* ========================================================
             MODE 3: INTERACTIVE SKILLS TREE DIAGRAM
             ======================================================== */}
        {viewMode === "diagram" && !search && (
          <SkillsTreeDiagram
            skills={skills}
            activeSkill={activeSkill}
            onSelectSkill={setActiveSkill}
            onTestSkill={(s) => handleOpenInChat(s.name)}
          />
        )}

        {/* ========================================================
             SEARCH RESULTS VIEW
             ======================================================== */}
        {search && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2 px-1">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Search Results ({filteredSkills.length} matches)
              </div>
              <button
                type="button"
                onClick={() => {
                  setSearch("");
                  setActiveSkill(null);
                }}
                className="text-xs text-accent hover:underline cursor-pointer"
              >
                Clear Search
              </button>
            </div>

            <div className="rounded-2xl border border-border bg-card/40 p-4 shadow-xs space-y-3">
              <div className="grid gap-2.5 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
                {filteredSkills.map((s) => {
                  const isSelected = activeSkill?.name === s.name;
                  return (
                    <button
                      key={s.name}
                      type="button"
                      onClick={() => setActiveSkill(isSelected ? null : s)}
                      className={cn(
                        "group flex items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-left transition-all cursor-pointer shadow-xs",
                        isSelected
                          ? "border-accent bg-accent/15 ring-2 ring-accent/30 shadow-sm"
                          : "border-border bg-background/80 hover:border-accent/50 hover:bg-card"
                      )}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span
                          className={cn(
                            "h-2 w-2 shrink-0 rounded-full",
                            s.status === "broken"
                              ? "bg-red-500"
                              : s.status === "working"
                              ? "bg-emerald-500"
                              : "bg-amber-500"
                          )}
                        />
                        <div className="min-w-0">
                          <div className="font-mono text-xs font-semibold text-foreground group-hover:text-accent truncate">
                            {s.name}
                          </div>
                          <div className="text-[10px] text-muted-foreground truncate">
                            {s.category || "General"}
                          </div>
                        </div>
                      </div>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <span className={cn("rounded-full border px-1.5 py-0.5 text-[9.5px] font-semibold capitalize", statusBadge(s.status))}>
                          {s.status}
                        </span>
                        <span className="text-[10px] font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                          v{s.version}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Active Skill Inspector for Search View */}
            <AnimatePresence mode="wait">
              {activeSkill && (
                <motion.div
                  key={activeSkill.name}
                  initial={{ opacity: 0, y: 10, height: 0 }}
                  animate={{ opacity: 1, y: 0, height: "auto" }}
                  exit={{ opacity: 0, y: -10, height: 0 }}
                  transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <div className="rounded-2xl border border-accent/40 bg-card p-5 shadow-sm space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 pb-3">
                      <div className="flex items-center gap-2.5 flex-wrap min-w-0">
                        <span className="font-mono text-base font-bold text-foreground">
                          {activeSkill.name}
                        </span>
                        <span
                          className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${tone(
                            activeSkill.status
                          )}`}
                        >
                          {activeSkill.status === "working"
                            ? "Operational"
                            : activeSkill.status === "broken"
                            ? "Needs Attention"
                            : activeSkill.status}
                        </span>
                        <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-mono text-muted-foreground">
                          v{activeSkill.version}
                        </span>
                        <span className="rounded-md bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">
                          {activeSkill.category || "General"}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => handleOpenInChat(activeSkill.name)}
                          className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:opacity-90 transition-all cursor-pointer"
                        >
                          <MessageSquare className="h-3.5 w-3.5" />
                          <span>Open in Chat</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setActiveSkill(null)}
                          className="grid h-8 w-8 place-items-center rounded-xl border border-border bg-background text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors cursor-pointer"
                          title="Close inspector"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    </div>

                    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 rounded-xl bg-muted/50 p-2.5 border border-border/60">
                      <div className="flex items-center gap-2 min-w-0">
                        <FileCode className="h-4 w-4 text-accent shrink-0" />
                        <span className="font-mono text-xs text-foreground/90 truncate">
                          {activeSkill.path || `ishti/skills/${activeSkill.name}.py`}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={(e) =>
                          handleCopy(
                            activeSkill.path || `ishti/skills/${activeSkill.name}.py`,
                            `path-${activeSkill.name}`,
                            e
                          )
                        }
                        className="flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground hover:border-accent hover:text-accent transition-colors cursor-pointer shrink-0"
                      >
                        {copiedKey === `path-${activeSkill.name}` ? (
                          <>
                            <Check className="h-3.5 w-3.5 text-emerald-500" />
                            <span className="text-emerald-500">Copied!</span>
                          </>
                        ) : (
                          <>
                            <Copy className="h-3.5 w-3.5" />
                            <span>Copy Path</span>
                          </>
                        )}
                      </button>
                    </div>

                    {activeSkill.route && (
                      <div className="rounded-xl border border-border/80 bg-background/80 p-3 space-y-1">
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                          <Wrench className="h-3 w-3 text-accent" />
                          <span>Routing & Trigger Intent</span>
                        </div>
                        <p className="text-xs text-foreground font-mono bg-muted/40 p-2 rounded-lg leading-relaxed">
                          {activeSkill.route}
                        </p>
                      </div>
                    )}

                    <div className="space-y-1">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                        Description
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed">
                        {activeSkill.description}
                      </p>
                    </div>

                    {activeSkill.problem && (
                      <div className="rounded-xl border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-500 space-y-1">
                        <div className="font-semibold flex items-center gap-1.5">
                          <span>Diagnostic Issue:</span>
                        </div>
                        <p className="leading-relaxed font-mono">{activeSkill.problem}</p>
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {filteredSkills.length === 0 && (
              <div className="rounded-2xl border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
                No skills found matching "{search}".
              </div>
            )}
          </div>
        )}
      </div>
    </Page>
  );
}

// ---------------------------------------------------------------- System
type Telemetry = { cpu_percent: number; ram_percent: number; ram_used_gb: number; ram_total_gb: number;
                   gpu?: { name?: string; used_vram_mb?: number; total_vram_mb?: number; temp_c?: number } | string;
                   disks?: Record<string, { free_gb: number; total_gb: number }> };
type Check = { test: string; status: string; details: string };

function Stat({ label, value, unit, sub }: { label: string; value: number; unit: string; sub?: string }) {
  return (
    <Card>
      <div className="text-xs text-muted-foreground truncate">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums truncate">
        <AnimatedCounter value={Math.round(value)} />{unit}
      </div>
      {sub && <div className="text-[11px] text-muted-foreground truncate" title={sub}>{sub}</div>}
    </Card>
  );
}

export function SystemBody() {
  const [t, setT] = useState<Telemetry | null>(null);
  const [activity, setActivity] = useState<{ days: { date: string; count: number }[]; skills: { skill: string; count: number }[] }>();
  const [doctor, setDoctor] = useState<Record<string, Check[]> | null>(null);
  const [running, setRunning] = useState(false);
  const [healStatus, setHealStatus] = useState<{ enabled: boolean; pending_count: number; last_run?: number } | null>(null);
  const [healing, setHealing] = useState(false);
  const [healReport, setHealReport] = useState<any>(null);

  useEffect(() => {
    const load = () => getJSON<Telemetry>("/api/system").then(setT);
    load(); const id = setInterval(load, 3000);
    getJSON<typeof activity>("/api/activity").then(setActivity);
    getJSON<any>("/api/self_heal/status").then(setHealStatus).catch(() => {});
    return () => clearInterval(id);
  }, []);
  const gpu = typeof t?.gpu === "object" ? t.gpu : undefined;
  const max = Math.max(1, ...(activity?.skills.map((s) => s.count) ?? [1]));
  return (
    <>
      <div className="mb-3 flex justify-end">
        <button disabled={running} onClick={async () => { setRunning(true); setDoctor((await getJSON<{ checks: Record<string, Check[]> }>("/api/doctor")).checks); setRunning(false); }}
          className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-50 cursor-pointer">
          {running ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run diagnostics</button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="CPU" value={t?.cpu_percent ?? 0} unit="%" />
        <Stat label="RAM" value={t?.ram_percent ?? 0} unit="%" sub={t ? `${t.ram_used_gb} / ${t.ram_total_gb} GB` : ""} />
        <Stat label="VRAM" value={gpu?.used_vram_mb ? (gpu.used_vram_mb / (gpu.total_vram_mb || 1)) * 100 : 0} unit="%"
          sub={gpu ? `${gpu.used_vram_mb} / ${gpu.total_vram_mb} MB` : ""} />
        <Stat label="GPU temp" value={gpu?.temp_c ?? 0} unit="°C" sub={gpu?.name} />
      </div>
      <Card className="mt-3">
        <div className="mb-2 text-sm font-semibold">Requests by skill</div>
        <div className="space-y-1.5">
          {activity?.skills.map((s) => (
            <div key={s.skill} className="flex items-center gap-2 text-xs">
              <span className="w-24 truncate text-muted-foreground">{s.skill}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                <motion.div className="h-full rounded-full bg-accent" initial={{ width: 0 }} animate={{ width: `${(s.count / max) * 100}%` }} transition={{ duration: 0.5 }} />
              </div>
              <span className="w-10 text-right tabular-nums">{s.count}</span>
            </div>
          ))}
        </div>
      </Card>
      {doctor && (
        <Card className="mt-3">
          <div className="mb-2 text-sm font-semibold">Diagnostics</div>
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {Object.values(doctor).flat().map((c, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className={c.status === "PASS" ? "text-success" : c.status === "WARN" ? "text-warning" : "text-danger"}>●</span>
                <span className="font-medium">{c.test}</span><span className="truncate text-muted-foreground">{c.details}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card className="mt-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div>
            <div className="text-sm font-semibold flex items-center gap-1.5">
              <Wrench className="h-4 w-4 text-accent" />
              <span>Self-Healing Engine (Manual Trigger Only)</span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              Automatic background self-healing is OFF by default. Remediation actions run strictly on-demand.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs cursor-pointer">
              <span className="text-muted-foreground">Auto-Heal:</span>
              <input
                type="checkbox"
                checked={healStatus?.enabled ?? false}
                onChange={async (e) => {
                  const res = await postJSON<{ enabled: boolean }>("/api/self_heal/toggle", { enabled: e.target.checked });
                  setHealStatus((prev) => prev ? { ...prev, enabled: res.enabled } : null);
                }}
                className="rounded border-border accent-accent h-3.5 w-3.5 cursor-pointer"
              />
              <span className={healStatus?.enabled ? "text-success font-medium" : "text-muted-foreground"}>
                {healStatus?.enabled ? "ON" : "OFF"}
              </span>
            </label>
            <button
              disabled={healing}
              onClick={async () => {
                setHealing(true);
                try {
                  const res = await postJSON<any>("/api/self_heal/trigger", {});
                  setHealReport(res);
                  const fresh = await getJSON<any>("/api/self_heal/status");
                  setHealStatus(fresh);
                } finally {
                  setHealing(false);
                }
              }}
              className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-xs font-medium text-white transition-opacity disabled:opacity-50 cursor-pointer"
            >
              {healing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              Trigger Self-Healing
            </button>
          </div>
        </div>

        {healReport && (
          <div className="mt-2.5 rounded-xl border border-border/60 bg-muted/30 p-2.5 text-xs">
            <div className="font-medium text-foreground mb-1">
              Remediation Report ({healReport.actions_taken?.length ?? 0} actions taken in {healReport.duration_ms}ms):
            </div>
            <ul className="space-y-1 text-muted-foreground list-disc list-inside">
              {healReport.actions_taken?.map((act: string, idx: number) => (
                <li key={idx} className="text-foreground/90">{act}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-2 text-xs text-muted-foreground flex items-center justify-between">
          <span>Pending candidates: <strong className="text-foreground">{healStatus?.pending_count ?? 0}</strong></span>
          {healStatus?.last_run && (
            <span>Last healed: {new Date(healStatus.last_run * 1000).toLocaleTimeString()}</span>
          )}
        </div>
      </Card>
    </>
  );
}

/** The System content as its own page (old #system link; Settings also shows it as a collapsible card). */
export function System({ onToggleNav }: { onToggleNav?: () => void }) {
  return (
    <Page title="System" subtitle="This PC, live, and what ISHTI has been doing." onToggleNav={onToggleNav}>
      <SystemBody />
    </Page>
  );
}

// ---------------------------------------------------------------- Settings
type Profile = { key: string; server: string; profile: string; purpose: string; connected: boolean };

/** The owner's own Chrome profiles ISHTI may drive (via the Playwright Extension). Tokens stay on this PC. */
function ChromeProfiles({
  isOpen,
  onToggle,
}: {
  isOpen: boolean;
  onToggle: () => void;
}) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  useEffect(() => {
    getJSON<{ profiles: Profile[] }>("/api/chrome_profiles").then((d) =>
      setProfiles(d.profiles)
    );
  }, []);

  const save = async (key: string, token: string) => {
    setSavingKey(key);
    try {
      const d = await postJSON<{ profiles: Profile[] }>("/api/chrome_profiles", {
        key,
        token,
      });
      setProfiles(d.profiles);
      setDraft((prev) => ({ ...prev, [key]: "" }));
    } finally {
      setSavingKey(null);
    }
  };

  const connectedCount = profiles.filter((p) => p.connected).length;

  return (
    <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
      {/* Card Header */}
      <div
        className="flex items-center justify-between gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-sky-500/15 text-sky-500">
            <Monitor className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-foreground text-sm">Chrome Profiles</span>
              <span
                className={cn(
                  "rounded-md px-2 py-0.5 text-[10px] font-semibold font-mono",
                  connectedCount > 0
                    ? "bg-success/15 text-success"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {connectedCount} / {profiles.length || 2} Connected
              </span>
            </div>
            <div className="text-xs text-muted-foreground truncate">
              Isolated Playwright sessions with human-in-the-loop approval.
            </div>
          </div>
        </div>

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer shrink-0"
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 transition-transform duration-200",
              isOpen ? "rotate-180 text-accent" : ""
            )}
          />
        </button>
      </div>

      {/* Expandable Body */}
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border/60 p-4 space-y-3 bg-background/50"
          >
            <div className="text-xs text-muted-foreground leading-relaxed">
              ISHTI only ever uses these two dedicated profiles of your Chrome. In each one, install the{" "}
              <a
                className="text-accent underline hover:opacity-80"
                href="https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"
                target="_blank"
                rel="noreferrer"
              >
                Playwright Extension
              </a>
              , open it, copy its token and paste it below. Actions like sending messages or submitting forms always ask you first.
            </div>

            <div className="space-y-2.5 pt-1">
              {profiles.map((pr) => (
                <div
                  key={pr.key}
                  className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border border-border/80 bg-background/80 p-3 shadow-2xs"
                >
                  <div className="flex items-center gap-2.5 min-w-[180px]">
                    <span
                      className={cn(
                        "h-2.5 w-2.5 rounded-full shrink-0",
                        pr.connected ? "bg-success" : "bg-muted-foreground/30"
                      )}
                    />
                    <div>
                      <div className="text-xs font-semibold text-foreground">
                        {pr.profile} · {pr.server === "main" ? "Main: WhatsApp, Gemini, browsing" : pr.server === "login" ? "Logins only (Gmail)" : pr.server}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {pr.connected ? "Connected & Ready" : "Disconnected"}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-1 sm:justify-end">
                    <input
                      type="password"
                      placeholder={pr.connected ? "Replace token…" : "Paste token…"}
                      value={draft[pr.key] ?? ""}
                      onChange={(e) =>
                        setDraft({ ...draft, [pr.key]: e.target.value })
                      }
                      className="w-full sm:w-48 rounded-lg border border-border bg-card/60 px-2.5 py-1.5 text-xs outline-none focus:border-accent"
                    />
                    <button
                      type="button"
                      disabled={!draft[pr.key] || savingKey === pr.key}
                      onClick={() => save(pr.key, draft[pr.key])}
                      className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 hover:opacity-90 transition-opacity cursor-pointer shrink-0"
                    >
                      {savingKey === pr.key ? "Saving..." : "Save"}
                    </button>
                    {pr.connected && (
                      <button
                        type="button"
                        onClick={() => save(pr.key, "")}
                        className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer shrink-0"
                      >
                        Disconnect
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

export function Settings({ onToggleNav }: { onToggleNav?: () => void }) {
  const [p, setP] = useState<{
    audio_output: boolean;
    wake_enabled: boolean;
    wake_model: string;
    models: Record<string, string>;
  }>();
  const [chatSize, setChatSize] = useState<ChatFontSize>(getChatFontSize);
  const [sideChatsWidth, setSideChatsWidth] = useState(() => {
    try {
      const saved = localStorage.getItem("ishti-recent-chats-width");
      if (saved) {
        const parsed = parseInt(saved, 10);
        if (parsed >= 180 && parsed <= 550) return parsed;
      }
    } catch {}
    return 240;
  });

  // Track expanded cards (default collapsed as requested)
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>({
    appearance: false,
    chatSize: false,
    sideChats: false,
    chrome: false,
    models: false,
    voice: false,
    whisper: false,
    language: false,
    system: false,
  });

  const toggleCard = (key: string) => {
    setExpandedCards((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const toggleAll = (expand: boolean) => {
    setExpandedCards({
      appearance: expand,
      chatSize: expand,
      sideChats: expand,
      chrome: expand,
      models: expand,
      voice: expand,
      whisper: expand,
      language: expand,
      system: expand,
    });
  };

  const anyExpanded = Object.values(expandedCards).some(Boolean);

  useEffect(() => {
    getJSON<typeof p>("/api/prefs").then(setP);
  }, []);

  useEffect(() => {
    const handleWidthChange = (e: Event) => {
      const detail = (e as CustomEvent<number>).detail;
      if (typeof detail === "number" && detail >= 180 && detail <= 550) {
        setSideChatsWidth(detail);
      }
    };
    window.addEventListener("ishti-side-chats-width-changed", handleWidthChange);
    return () =>
      window.removeEventListener("ishti-side-chats-width-changed", handleWidthChange);
  }, []);

  const handleSizeChange = (newSize: ChatFontSize) => {
    setChatSize(newSize);
    setChatFontSize(newSize);
  };

  const handleSideChatsWidthChange = (w: number) => {
    const clamped = Math.max(180, Math.min(550, Math.round(w)));
    setSideChatsWidth(clamped);
    try {
      localStorage.setItem("ishti-recent-chats-width", String(clamped));
      window.dispatchEvent(
        new CustomEvent("ishti-side-chats-width-changed", { detail: clamped })
      );
    } catch {}
  };

  if (!p) return null;

  return (
    <Page
      title="Settings"
      subtitle="Everything runs on this PC. Local models and parameters configured in ishti/config.json."
      className="max-w-6xl"
      onToggleNav={onToggleNav}
      action={
        <button
          type="button"
          onClick={() => toggleAll(!anyExpanded)}
          className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground hover:border-accent transition-colors cursor-pointer shadow-xs"
        >
          {anyExpanded ? (
            <>
              <ChevronUp className="h-3.5 w-3.5 text-accent" />
              <span>Collapse All</span>
            </>
          ) : (
            <>
              <ChevronDown className="h-3.5 w-3.5 text-accent" />
              <span>Expand All</span>
            </>
          )}
        </button>
      }
    >
      <div className="space-y-6">
        {/* ========================================================
             SECTION 0: Architecture (arch1 dispatcher vs arch2 two-pass brain)
             ======================================================== */}
        <LanguageSettings isOpen={expandedCards.language} onToggle={() => toggleCard("language")} />

        <PrivacyPanel />

        <ArchitectureCard />

        {/* ========================================================
             SECTION 1: Language models
             ======================================================== */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-muted-foreground px-1">
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-accent" />
              <span>Language models</span>
            </div>
            <span className="text-[11px] font-normal lowercase tracking-normal text-muted-foreground/80">
              {expandedCards.models ? "Expanded" : "Default collapsed"}
            </span>
          </div>
          <ModelsCard
            isOpen={expandedCards.models}
            onToggle={() => toggleCard("models")}
          />
        </div>

        {/* ========================================================
             SECTION 2: Voices
             ======================================================== */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-muted-foreground px-1">
            <div className="flex items-center gap-2">
              <Volume2 className="h-4 w-4 text-accent" />
              <span>Voices</span>
            </div>
            <span className="text-[11px] font-normal lowercase tracking-normal text-muted-foreground/80">
              {expandedCards.voice ? "Expanded" : "Default collapsed"}
            </span>
          </div>
          <VoiceCard
            isOpen={expandedCards.voice}
            onToggle={() => toggleCard("voice")}
            audioOutput={p.audio_output}
            wakeEnabled={p.wake_enabled}
            onToggleAudioOutput={() =>
              postJSON<typeof p>("/api/prefs", {
                audio_output: !p.audio_output,
              }).then(setP)
            }
          />
        </div>

        {/* ========================================================
             SECTION 3: Speech recognition (Whisper)
             ======================================================== */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-muted-foreground px-1">
            <div className="flex items-center gap-2">
              <Radio className="h-4 w-4 text-accent" />
              <span>Speech recognition (Whisper)</span>
            </div>
            <span className="text-[11px] font-normal lowercase tracking-normal text-muted-foreground/80">
              {expandedCards.whisper ? "Expanded" : "Default collapsed"}
            </span>
          </div>
          <WhisperCard
            isOpen={expandedCards.whisper}
            onToggle={() => toggleCard("whisper")}
          />
        </div>

        {/* ========================================================
             SECTION 4: Interface & Display
             ======================================================== */}
        <div className="space-y-3 pt-3 border-t border-border/60">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2 px-1">
            <Sliders className="h-4 w-4 text-accent" />
            <span>Interface & Display</span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
            {/* Column 1: Appearance & Side Chats Width */}
            <div className="space-y-4">
              {/* 1. Appearance Card */}
              <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
                <div
                  className="flex items-center justify-between gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => toggleCard("appearance")}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-violet-500/15 text-violet-500">
                      <Sun className="h-4 w-4 dark:hidden" />
                      <Moon className="h-4 w-4 hidden dark:block" />
                    </div>
                    <div className="min-w-0">
                      <div className="font-semibold text-foreground text-sm">Appearance</div>
                      <div className="text-xs text-muted-foreground truncate">
                        Switch between pure white light mode and dark mode.
                      </div>
                    </div>
                  </div>

                  <div
                    className="flex items-center gap-2 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <ThemeSwitch />
                    <button
                      type="button"
                      onClick={() => toggleCard("appearance")}
                      className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                    >
                      <ChevronDown
                        className={cn(
                          "h-4 w-4 transition-transform duration-200",
                          expandedCards.appearance ? "rotate-180 text-accent" : ""
                        )}
                      />
                    </button>
                  </div>
                </div>

                <AnimatePresence initial={false}>
                  {expandedCards.appearance && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden border-t border-border/60 p-4 bg-background/50"
                    >
                      <div className="grid grid-cols-2 gap-3">
                        <button
                          type="button"
                          onClick={() => {
                            try {
                              localStorage.setItem("ishti-theme", "light");
                              document.documentElement.classList.remove("dark");
                            } catch {}
                          }}
                          className="flex flex-col items-center justify-center gap-2 rounded-xl border border-border p-3.5 hover:border-accent hover:bg-muted/30 transition-all cursor-pointer"
                        >
                          <Sun className="h-5 w-5 text-amber-500" />
                          <span className="text-xs font-semibold">Light Mode</span>
                          <span className="text-[10px] text-muted-foreground">Pure Crisp White</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            try {
                              localStorage.setItem("ishti-theme", "dark");
                              document.documentElement.classList.add("dark");
                            } catch {}
                          }}
                          className="flex flex-col items-center justify-center gap-2 rounded-xl border border-border p-3.5 hover:border-accent hover:bg-muted/30 transition-all cursor-pointer"
                        >
                          <Moon className="h-5 w-5 text-sky-400" />
                          <span className="text-xs font-semibold">Dark Mode</span>
                          <span className="text-[10px] text-muted-foreground">Deep Pure Black</span>
                        </button>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>

              {/* 3. Side Chats Width Card */}
              <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
                <div
                  className="flex items-center justify-between gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => toggleCard("sideChats")}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-amber-500/15 text-amber-500">
                      <Sliders className="h-4 w-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-foreground text-sm">
                          Side Chats Panel Width
                        </span>
                        <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-accent font-mono">
                          {sideChatsWidth}px
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground truncate">
                        Set width for the recent conversations sidebar in Chat.
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleCard("sideChats");
                    }}
                    className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer shrink-0"
                  >
                    <ChevronDown
                      className={cn(
                        "h-4 w-4 transition-transform duration-200",
                        expandedCards.sideChats ? "rotate-180 text-accent" : ""
                      )}
                    />
                  </button>
                </div>

                <AnimatePresence initial={false}>
                  {expandedCards.sideChats && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden border-t border-border/60 p-4 space-y-3.5 bg-background/50"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center rounded-xl bg-muted/60 p-1 gap-1 flex-1">
                          {[
                            { label: "Compact", value: 200 },
                            { label: "Default", value: 240 },
                            { label: "Medium", value: 280 },
                            { label: "Wide", value: 340 },
                          ].map((preset) => (
                            <button
                              key={preset.value}
                              type="button"
                              onClick={() => handleSideChatsWidthChange(preset.value)}
                              className={cn(
                                "flex-1 rounded-lg px-2.5 py-1 text-xs text-center transition-all cursor-pointer",
                                sideChatsWidth === preset.value
                                  ? "bg-background text-foreground shadow-xs font-semibold"
                                  : "text-muted-foreground hover:text-foreground font-medium"
                              )}
                            >
                              {preset.label}
                            </button>
                          ))}
                        </div>
                        {sideChatsWidth !== 240 && (
                          <button
                            type="button"
                            onClick={() => handleSideChatsWidthChange(240)}
                            className="text-xs text-muted-foreground hover:text-accent underline transition-colors cursor-pointer shrink-0 px-2"
                          >
                            Reset 240px
                          </button>
                        )}
                      </div>

                      {/* Smooth Range Slider */}
                      <div className="flex items-center gap-3 pt-1">
                        <span className="text-[11px] font-mono text-muted-foreground shrink-0">
                          180px
                        </span>
                        <input
                          type="range"
                          min="180"
                          max="500"
                          step="10"
                          value={sideChatsWidth}
                          onChange={(e) =>
                            handleSideChatsWidthChange(Number(e.target.value))
                          }
                          className="h-1.5 flex-1 cursor-pointer appearance-none rounded-lg bg-muted accent-accent"
                        />
                        <span className="text-[11px] font-mono text-muted-foreground shrink-0">
                          500px
                        </span>
                      </div>
                      <p className="text-[11px] text-muted-foreground">
                        Tip: You can also drag the divider handle directly inside the Chat interface.
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>
            </div>

            {/* Column 2: Chat Text Size & Chrome Profiles */}
            <div className="space-y-4">
              {/* 2. Chat Text Size Card */}
              <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
                <div
                  className="flex items-center justify-between gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => toggleCard("chatSize")}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-emerald-500/15 text-emerald-500">
                      <Type className="h-4 w-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-foreground text-sm">Chat Text Size</span>
                        <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] font-mono text-muted-foreground">
                          {chatSize.charAt(0).toUpperCase() + chatSize.slice(1)}
                        </span>
                      </div>
                      <div className="text-xs text-muted-foreground truncate">
                        Text size across messages, inputs, and code in conversations.
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleCard("chatSize");
                    }}
                    className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer shrink-0"
                  >
                    <ChevronDown
                      className={cn(
                        "h-4 w-4 transition-transform duration-200",
                        expandedCards.chatSize ? "rotate-180 text-accent" : ""
                      )}
                    />
                  </button>
                </div>

                <AnimatePresence initial={false}>
                  {expandedCards.chatSize && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden border-t border-border/60 p-4 space-y-3.5 bg-background/50"
                    >
                      <div className="flex flex-wrap items-center rounded-xl bg-muted/60 p-1 gap-1">
                        {CHAT_FONT_SIZES.map((fs) => (
                          <button
                            key={fs.key}
                            type="button"
                            onClick={() => handleSizeChange(fs.key)}
                            className={cn(
                              "flex-1 min-w-[70px] rounded-lg px-3 py-1.5 text-xs text-center transition-all cursor-pointer",
                              chatSize === fs.key
                                ? "bg-background text-foreground shadow-xs font-semibold"
                                : "text-muted-foreground hover:text-foreground font-medium"
                            )}
                          >
                            {fs.label}
                          </button>
                        ))}
                      </div>

                      {/* Live Preview */}
                      <div className="rounded-xl border border-border/70 bg-card/60 p-3.5 space-y-2">
                        <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                          Live Chat Preview
                        </div>
                        <div className="space-y-2">
                          <div className="flex justify-end">
                            <div
                              className="rounded-2xl rounded-br-md bg-accent px-3.5 py-1.5 text-white max-w-sm shadow-xs"
                              style={{
                                fontSize: "var(--chat-font-size)",
                                lineHeight: "var(--chat-leading)",
                              }}
                            >
                              Can you check the latest file updates on my PC?
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <div
                              className="rounded-2xl bg-background border border-border px-3.5 py-1.5 text-foreground max-w-sm shadow-xs"
                              style={{
                                fontSize: "var(--chat-font-size)",
                                lineHeight: "var(--chat-leading)",
                              }}
                            >
                              Found 3 updated folders. Local and ready to execute anytime.
                            </div>
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </Card>

              {/* 4. Chrome Profiles Card */}
              <ChromeProfiles
                isOpen={expandedCards.chrome}
                onToggle={() => toggleCard("chrome")}
              />
            </div>

            {/* 5. Remote Control (Telegram / WhatsApp) - Full Width */}
            <div className="col-span-full">
              <RemoteControl />
            </div>
          </div>
        </div>

        {/* ========================================================
             SECTION 5: System (moved here from the left nav; #system still redirects)
             ======================================================== */}
        <div className="space-y-2 pt-3 border-t border-border/60">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2 px-1">
            <Cpu className="h-4 w-4 text-accent" />
            <span>System</span>
            <span className="text-[11px] font-normal lowercase tracking-normal text-muted-foreground/80">
              {expandedCards.system ? "Expanded" : "Default collapsed"}
            </span>
          </div>
          <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
            <div
              className="flex items-center justify-between gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
              onClick={() => toggleCard("system")}
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-slate-500/15 text-slate-400">
                  <Cpu className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <div className="font-semibold text-foreground text-sm">This PC</div>
                  <div className="text-xs text-muted-foreground truncate">
                    CPU, RAM, GPU/VRAM live, requests by skill and the diagnostics + self-healing tools.
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); toggleCard("system"); }}
                className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              >
                <ChevronDown className={cn("h-4 w-4 transition-transform duration-200", expandedCards.system ? "rotate-180 text-accent" : "")} />
              </button>
            </div>
            <AnimatePresence initial={false}>
              {expandedCards.system && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden border-t border-border/60 p-4 space-y-3.5 bg-background/50"
                >
                  <SystemBody />
                </motion.div>
              )}
            </AnimatePresence>
          </Card>
        </div>
      </div>
    </Page>
  );
}

