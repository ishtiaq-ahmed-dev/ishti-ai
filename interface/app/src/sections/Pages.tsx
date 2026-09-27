import { AnimatePresence, motion } from "motion/react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Cpu,
  Database,
  FolderKanban,
  History,
  Lock,
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
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { TaskList, type Task } from "@/components/rare/task-list";
import { DeleteButton } from "@/components/rare/delete-button";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { ThemeSwitch } from "@/components/ThemeToggle";
import { CHAT_FONT_SIZES, getChatFontSize, setChatFontSize, type ChatFontSize } from "@/lib/font-size";
import { cn } from "@/lib/utils";
import { ModelsCard, VoiceCard, WhisperCard } from "@/components/ModelsVoices";

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

// ---------------------------------------------------------------- Tasks (reminders & timers)
export function Tasks({ onToggleNav }: { onToggleNav?: () => void }) {
  const [items, setItems] = useState<{ id: number; due: number; text: string }[]>([]);
  const load = () => getJSON<{ reminders: typeof items }>("/api/reminders").then((d) => setItems(d.reminders));
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  const tasks: Task[] = items.map((r) => ({
    id: String(r.id),
    label: `${new Date(r.due * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })} · ${r.text}`,
  }));
  return (
    <Page title="Tasks" subtitle="Reminders and timers. Tick one to cancel it. Add more from chat: “remind me at 5pm to …”." onToggleNav={onToggleNav}>
      <Card>
        {tasks.length ? (
          <TaskList tasks={tasks} accent="#6e56cf" onTasksChange={(next) => {
            next.filter((t) => t.done).forEach((t) => postJSON(`/api/reminders/${t.id}/done`).then(load));
          }} />
        ) : <p className="text-sm text-muted-foreground">Nothing scheduled.</p>}
      </Card>
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

export function Memory({ onToggleNav }: { onToggleNav?: () => void }) {
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
    <Page
      title="Memory"
      subtitle="Exact facts ISHTI looks up before anything else. Fully editable, updated in place, never duplicated."
      className="max-w-6xl"
      onToggleNav={onToggleNav}
      action={
        <button
          type="button"
          onClick={() => setShowNewModal(true)}
          className="flex items-center gap-1.5 rounded-xl bg-accent px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:opacity-90 transition-all cursor-pointer"
        >
          <Plus className="h-4 w-4" /> Add Fact
        </button>
      }
    >
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
                    className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 bg-muted/20 border-b border-border/60 cursor-pointer select-none hover:bg-muted/30 transition-colors"
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
    </Page>
  );
}

// ---------------------------------------------------------------- Skills
type SkillInfo = { name: string; version: string; description: string; route: string; status: string; problem?: string };
export function Skills({ onToggleNav }: { onToggleNav?: () => void }) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [search, setSearch] = useState("");
  const [openWorking, setOpenWorking] = useState(true);
  const [openIssues, setOpenIssues] = useState(true);

  useEffect(() => {
    getJSON<{ skills: SkillInfo[] }>("/api/skills").then((d) => setSkills(d.skills));
  }, []);

  const tone = (s: string) =>
    s === "working"
      ? "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20"
      : s === "broken"
      ? "bg-red-500/10 text-red-500 border border-red-500/20"
      : "bg-amber-500/10 text-amber-500 border border-amber-500/20";

  const filtered = skills.filter(
    (s) =>
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.description.toLowerCase().includes(search.toLowerCase()) ||
      (s.route && s.route.toLowerCase().includes(search.toLowerCase()))
  );

  const working = filtered.filter((s) => s.status === "working");
  const issues = filtered.filter((s) => s.status !== "working");

  return (
    <Page
      title="Skills"
      subtitle="Each skill is a self-contained capability; broken or missing skills are skipped without affecting the system."
      onToggleNav={onToggleNav}
      action={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              const next = !(openWorking && openIssues);
              setOpenWorking(next);
              setOpenIssues(next);
            }}
            className="rounded-xl border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted/40 transition-colors cursor-pointer shadow-xs"
          >
            {openWorking && openIssues ? "Collapse All" : "Expand All"}
          </button>
        </div>
      }
    >
      <div className="space-y-6">
        {/* Search bar & summary */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 rounded-2xl border border-border bg-card/40 p-3 shadow-xs">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search skills by name, description, keywords..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-xl border border-border bg-background py-2 pl-9 pr-4 text-xs text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-hidden"
            />
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground shrink-0 px-1">
            <span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-emerald-500 font-medium border border-emerald-500/20">
              {skills.filter((s) => s.status === "working").length} working
            </span>
            {skills.some((s) => s.status !== "working") && (
              <span className="rounded-full bg-red-500/10 px-2.5 py-1 text-red-500 font-medium border border-red-500/20">
                {skills.filter((s) => s.status !== "working").length} issues
              </span>
            )}
          </div>
        </div>

        {/* Working Skills Section */}
        {working.length > 0 && (
          <div className="rounded-2xl border border-border bg-card/30 overflow-hidden shadow-xs">
            <button
              type="button"
              onClick={() => setOpenWorking(!openWorking)}
              className="w-full flex items-center justify-between p-4 text-left hover:bg-muted/20 transition-colors cursor-pointer select-none group"
            >
              <div className="flex items-center gap-2.5">
                <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
                <span className="text-sm font-semibold text-foreground tracking-tight">Active Skills</span>
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                  {working.length}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-muted-foreground hidden sm:inline">
                  {openWorking ? "Collapse" : "Expand"}
                </span>
                <span
                  className={`grid h-6 w-6 place-items-center rounded-lg bg-muted text-muted-foreground transition-transform duration-200 ${
                    openWorking ? "rotate-180" : ""
                  }`}
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </span>
              </div>
            </button>

            <AnimatePresence initial={false}>
              {openWorking && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.22, ease: "easeInOut" }}
                  className="overflow-hidden border-t border-border"
                >
                  <div className="p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {working.map((s, i) => (
                      <motion.div
                        key={s.name}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i * 0.02 }}
                      >
                        <Card className="h-full flex flex-col justify-between hover:border-accent/40 transition-colors">
                          <div>
                            <div className="mb-1.5 flex items-center justify-between gap-2 min-w-0">
                              <span className="font-semibold text-foreground text-sm truncate">{s.name}</span>
                              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0 ${tone(s.status)}`}>
                                v{s.version}
                              </span>
                            </div>
                            <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3" title={s.description}>
                              {s.description}
                            </p>
                          </div>
                          {s.problem && <p className="mt-2 text-xs text-danger break-words">{s.problem}</p>}
                        </Card>
                      </motion.div>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* Issues / Needs Setup Section */}
        {issues.length > 0 && (
          <div className="rounded-2xl border border-red-500/20 bg-red-500/5 overflow-hidden shadow-xs">
            <button
              type="button"
              onClick={() => setOpenIssues(!openIssues)}
              className="w-full flex items-center justify-between p-4 text-left hover:bg-red-500/10 transition-colors cursor-pointer select-none group"
            >
              <div className="flex items-center gap-2.5">
                <span className="h-2 w-2 rounded-full bg-red-500 shrink-0" />
                <span className="text-sm font-semibold text-foreground tracking-tight">Requires Attention</span>
                <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-[11px] font-medium text-red-400">
                  {issues.length}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-muted-foreground hidden sm:inline">
                  {openIssues ? "Collapse" : "Expand"}
                </span>
                <span
                  className={`grid h-6 w-6 place-items-center rounded-lg bg-muted text-muted-foreground transition-transform duration-200 ${
                    openIssues ? "rotate-180" : ""
                  }`}
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </span>
              </div>
            </button>

            <AnimatePresence initial={false}>
              {openIssues && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.22, ease: "easeInOut" }}
                  className="overflow-hidden border-t border-red-500/20"
                >
                  <div className="p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {issues.map((s, i) => (
                      <motion.div
                        key={s.name}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i * 0.02 }}
                      >
                        <Card className="h-full flex flex-col justify-between border-red-500/30">
                          <div>
                            <div className="mb-1.5 flex items-center justify-between gap-2 min-w-0">
                              <span className="font-semibold text-foreground text-sm truncate">{s.name}</span>
                              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0 ${tone(s.status)}`}>
                                {s.status} · v{s.version}
                              </span>
                            </div>
                            <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3" title={s.description}>
                              {s.description}
                            </p>
                          </div>
                          {s.problem && <p className="mt-2 text-xs text-danger break-words">{s.problem}</p>}
                        </Card>
                      </motion.div>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {filtered.length === 0 && (
          <div className="rounded-2xl border border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
            No skills found matching "{search}".
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

export function System({ onToggleNav }: { onToggleNav?: () => void }) {
  const [t, setT] = useState<Telemetry | null>(null);
  const [activity, setActivity] = useState<{ days: { date: string; count: number }[]; skills: { skill: string; count: number }[] }>();
  const [doctor, setDoctor] = useState<Record<string, Check[]> | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    const load = () => getJSON<Telemetry>("/api/system").then(setT);
    load(); const id = setInterval(load, 3000);
    getJSON<typeof activity>("/api/activity").then(setActivity);
    return () => clearInterval(id);
  }, []);
  const gpu = typeof t?.gpu === "object" ? t.gpu : undefined;
  const max = Math.max(1, ...(activity?.skills.map((s) => s.count) ?? [1]));
  return (
    <Page title="System" subtitle="This PC, live, and what ISHTI has been doing." onToggleNav={onToggleNav}
      action={<button disabled={running} onClick={async () => { setRunning(true); setDoctor((await getJSON<{ checks: Record<string, Check[]> }>("/api/doctor")).checks); setRunning(false); }}
        className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
        {running ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run diagnostics</button>}>
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
        className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
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
            {/* 1. Appearance Card */}
            <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
              <div
                className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
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

            {/* 2. Chat Text Size Card */}
            <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
              <div
                className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
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

            {/* 3. Side Chats Width Card */}
            <Card className="p-0 overflow-hidden shadow-xs border-border/80 bg-card/40">
              <div
                className="flex items-center justify-between gap-3 p-4 cursor-pointer select-none hover:bg-muted/30 transition-colors"
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

            {/* 4. Chrome Profiles Card */}
            <ChromeProfiles
              isOpen={expandedCards.chrome}
              onToggle={() => toggleCard("chrome")}
            />
          </div>
        </div>
      </div>
    </Page>
  );
}

