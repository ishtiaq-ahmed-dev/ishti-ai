import { AnimatePresence, motion } from "motion/react";
import {
  Check,
  Database,
  FolderKanban,
  History,
  Lock,
  PanelLeft,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  User,
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

  const handleDeleteAttr = async (entity: string, attr: string) => {
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

  return (
    <Page
      title="Memory"
      subtitle="Exact facts ISHTI looks up before anything else. Fully editable, updated in place, never duplicated."
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
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card/40 p-3">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search facts, attributes, or values..."
              className="w-full rounded-xl border border-border/80 bg-background/80 pl-9 pr-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
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

          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1 rounded-lg bg-muted/60 px-2.5 py-1 font-medium">
              <Database className="h-3 w-3 text-accent" />
              <span>{totalFacts} facts</span>
              <span>·</span>
              <span>{totalEntities} entities</span>
            </span>
            <button
              type="button"
              onClick={() => load(true)}
              disabled={isLoading}
              title="Refresh facts"
              className="rounded-lg border border-border/80 p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors cursor-pointer"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} />
            </button>
          </div>
        </div>

        {/* Facts Cards Grid */}
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
          <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3">
            {Object.entries(groups).map(([entity, rows], i) => {
              const isOwner = entity.toLowerCase() === "owner";
              const isProject = entity.toLowerCase().includes("project") || entity.includes(":\\") || entity.includes("/");
              return (
                <motion.div
                  key={entity}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.03 }}
                >
                  <Card className="flex flex-col h-full border-border/70 bg-card/60 shadow-xs">
                    {/* Entity Header */}
                    <div className="mb-3.5 flex items-center justify-between border-b border-border/50 pb-2.5">
                      <div className="flex items-center gap-2 min-w-0">
                        <div
                          className={cn(
                            "grid h-7 w-7 shrink-0 place-items-center rounded-lg text-xs",
                            isOwner
                              ? "bg-violet-500/15 text-violet-500"
                              : isProject
                              ? "bg-amber-500/15 text-amber-500"
                              : "bg-sky-500/15 text-sky-500"
                          )}
                        >
                          {isOwner ? (
                            <User className="h-3.5 w-3.5" />
                          ) : isProject ? (
                            <FolderKanban className="h-3.5 w-3.5" />
                          ) : (
                            <Database className="h-3.5 w-3.5" />
                          )}
                        </div>
                        <div className="min-w-0">
                          <h3 className="truncate font-semibold text-foreground text-sm capitalize">
                            {entity}
                          </h3>
                          <span className="text-[11px] text-muted-foreground">
                            {rows.length} {rows.length === 1 ? "fact" : "facts"}
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => setAddingAttr({ entity, attr: "", value: "" })}
                          title={`Add attribute to ${entity}`}
                          className="flex items-center gap-1 rounded-lg border border-border/70 px-2 py-1 text-[11px] font-medium text-muted-foreground hover:border-accent hover:text-accent hover:bg-accent/5 transition-colors cursor-pointer"
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
                      </div>
                    </div>

                    {/* Fact rows */}
                    <div className="flex-1 space-y-2.5">
                      {rows.map((f) => {
                        const isThisEditing =
                          editing?.entity === f.entity && editing?.originalAttr === f.attr;
                        const isThisSaving = savingKey === `${f.entity}:${f.attr}`;
                        const isThisDeleting = deletingKey === `${f.entity}:${f.attr}`;

                        if (isThisEditing) {
                          return (
                            <div
                              key={f.attr}
                              className="rounded-xl border border-accent/80 bg-background p-3 shadow-xs space-y-2"
                            >
                              <div>
                                <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                  Attribute Key
                                </label>
                                <input
                                  type="text"
                                  value={editing.attr}
                                  onChange={(e) =>
                                    setEditing({ ...editing, attr: e.target.value })
                                  }
                                  className="mt-0.5 w-full rounded-lg border border-border bg-card/60 px-2 py-1 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                                />
                              </div>
                              <div>
                                <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                  Value
                                </label>
                                <textarea
                                  rows={2}
                                  value={editing.value}
                                  onChange={(e) =>
                                    setEditing({ ...editing, value: e.target.value })
                                  }
                                  className="mt-0.5 w-full rounded-lg border border-border bg-card/60 px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent resize-none"
                                />
                              </div>
                              <div className="flex items-center justify-end gap-1.5 pt-1">
                                <button
                                  type="button"
                                  onClick={() => setEditing(null)}
                                  className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted transition-colors cursor-pointer"
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
                                  className="flex items-center gap-1 rounded-lg bg-accent px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50 transition-opacity cursor-pointer"
                                >
                                  <Check className="h-3 w-3" />
                                  {isThisSaving ? "Saving..." : "Save"}
                                </button>
                              </div>
                            </div>
                          );
                        }

                        return (
                          <div
                            key={f.attr}
                            className={cn(
                              "group relative rounded-xl border border-border/50 bg-background/60 p-2.5 hover:border-accent/40 hover:bg-background/90 transition-all",
                              isThisDeleting && "opacity-40 pointer-events-none"
                            )}
                          >
                            {/* Key and Actions Row */}
                            <div className="flex items-start justify-between gap-2">
                              <span
                                title={f.attr}
                                className="inline-block max-w-[200px] truncate rounded-md bg-accent/10 px-2 py-0.5 text-[11px] font-mono font-medium text-accent border border-accent/15 select-all"
                              >
                                {f.attr}
                              </span>
                              <div className="flex items-center gap-1 shrink-0 opacity-80 group-hover:opacity-100 transition-opacity">
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
                                  className="rounded-md p-1 text-muted-foreground hover:bg-accent/10 hover:text-accent transition-colors cursor-pointer"
                                >
                                  <Pencil className="h-3 w-3" />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteAttr(f.entity, f.attr)}
                                  title="Delete this attribute"
                                  className="rounded-md p-1 text-muted-foreground hover:bg-danger/10 hover:text-danger transition-colors cursor-pointer"
                                >
                                  <Trash2 className="h-3 w-3" />
                                </button>
                              </div>
                            </div>

                            {/* Value Row */}
                            <div className="mt-1.5 text-xs font-normal text-foreground break-words select-text leading-relaxed">
                              {f.value}
                            </div>

                            {/* Historical / Previous Row */}
                            {f.previous && (
                              <div className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground/75">
                                <History className="h-2.5 w-2.5 shrink-0" />
                                <span className="truncate">
                                  was: <span className="line-through">{f.previous}</span>
                                </span>
                              </div>
                            )}
                          </div>
                        );
                      })}

                      {/* Inline Add Attribute Form */}
                      {addingAttr?.entity === entity && (
                        <div className="rounded-xl border border-dashed border-accent/70 bg-accent/5 p-3 space-y-2">
                          <div className="text-xs font-semibold text-accent">
                            New attribute for {entity}
                          </div>
                          <div>
                            <input
                              type="text"
                              value={addingAttr.attr}
                              onChange={(e) =>
                                setAddingAttr({ ...addingAttr, attr: e.target.value })
                              }
                              placeholder="Attribute key (e.g. phone, bio)"
                              className="w-full rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                            />
                          </div>
                          <div>
                            <textarea
                              rows={2}
                              value={addingAttr.value}
                              onChange={(e) =>
                                setAddingAttr({ ...addingAttr, value: e.target.value })
                              }
                              placeholder="Value..."
                              className="w-full rounded-lg border border-border bg-background px-2.5 py-1 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-accent resize-none"
                            />
                          </div>
                          <div className="flex items-center justify-end gap-1.5 pt-1">
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
                              className="rounded-lg bg-accent px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
                            >
                              Add Attribute
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </Card>
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
                    placeholder="e.g. Owner, Work, Project X, Laptop"
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
                    placeholder="e.g. email, gpu_model, location"
                    className="mt-1 w-full rounded-xl border border-border bg-card/60 px-3 py-2 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Value</label>
                  <textarea
                    rows={2}
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    placeholder="e.g. RTX 5070 Ti, ishtiaq@example.com"
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
  useEffect(() => { getJSON<{ skills: SkillInfo[] }>("/api/skills").then((d) => setSkills(d.skills)); }, []);
  const tone = (s: string) => s === "working" ? "bg-emerald-50 text-emerald-700" : s === "broken" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700";
  return (
    <Page title="Skills" subtitle="Each skill is a folder with a manifest; a broken one is skipped without affecting the rest." onToggleNav={onToggleNav}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {skills.map((s, i) => (
          <motion.div key={s.name} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.025 }}>
            <Card className="h-full flex flex-col justify-between">
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2 min-w-0">
                  <span className="font-semibold truncate">{s.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0 ${tone(s.status)}`}>{s.status} · v{s.version}</span>
                </div>
                <p className="text-sm text-muted-foreground break-words">{s.description}</p>
              </div>
              {s.problem && <p className="mt-2 text-xs text-danger break-words">{s.problem}</p>}
            </Card>
          </motion.div>
        ))}
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
type Profile = { key: string; profile: string; purpose: string; connected: boolean };

/** The owner's own Chrome profiles ISHTI may drive (via the Playwright Extension). Tokens stay on this PC. */
function ChromeProfiles() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => { getJSON<{ profiles: Profile[] }>("/api/chrome_profiles").then((d) => setProfiles(d.profiles)); }, []);
  const save = (key: string, token: string) =>
    postJSON<{ profiles: Profile[] }>("/api/chrome_profiles", { key, token }).then((d) => { setProfiles(d.profiles); setDraft({}); });
  return (
    <Card>
      <div className="font-medium">Chrome profiles</div>
      <div className="mb-3 text-xs text-muted-foreground">
        ISHTI only ever uses these two profiles of your Chrome. In each one, install the{" "}
        <a className="text-accent" href="https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"
          target="_blank" rel="noreferrer">Playwright Extension</a>, open it, copy its token and paste it here.
        Until then ISHTI uses its own separate browser (no logins). Sending messages or submitting forms always asks you first.
      </div>
      <div className="space-y-2">
        {profiles.map((p) => (
          <div key={p.key} className="flex flex-wrap items-center gap-2 rounded-xl border border-border p-2.5">
            <span className={`h-2 w-2 rounded-full ${p.connected ? "bg-success" : "bg-muted-foreground/40"}`} />
            <div className="min-w-[160px] flex-1">
              <div className="text-sm font-medium">{p.profile} · {p.key === "whatsapp" ? "WhatsApp" : "Search & logins"}</div>
              <div className="text-[11px] text-muted-foreground">{p.connected ? "Connected" : "Not connected"}</div>
            </div>
            <input type="password" placeholder={p.connected ? "Replace token…" : "Paste token…"} value={draft[p.key] ?? ""}
              onChange={(e) => setDraft({ ...draft, [p.key]: e.target.value })}
              className="w-56 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs outline-none focus:border-accent" />
            <button disabled={!draft[p.key]} onClick={() => save(p.key, draft[p.key])}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40">Save</button>
            {p.connected && (
              <button onClick={() => save(p.key, "")} className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted">
                Disconnect</button>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

export function Settings({ onToggleNav }: { onToggleNav?: () => void }) {
  const [p, setP] = useState<{ audio_output: boolean; wake_enabled: boolean; wake_model: string; models: Record<string, string> }>();
  const [chatSize, setChatSize] = useState<ChatFontSize>(getChatFontSize);

  useEffect(() => { getJSON<typeof p>("/api/prefs").then(setP); }, []);

  const handleSizeChange = (newSize: ChatFontSize) => {
    setChatSize(newSize);
    setChatFontSize(newSize);
  };

  if (!p) return null;
  return (
    <Page title="Settings" subtitle="Everything runs on this PC. Models are set in ishti/config.json." className="max-w-4xl" onToggleNav={onToggleNav}>
      <div className="space-y-3">
        <Card className="flex items-center justify-between">
          <div><div className="font-medium">Appearance</div><div className="text-xs text-muted-foreground">Switch between pure white light mode and pure black dark mode.</div></div>
          <ThemeSwitch />
        </Card>

        {/* Chat text size */}
        <Card>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="font-medium">Chat text size</div>
              <div className="text-xs text-muted-foreground">Adjust text size across messages, inputs, and code in conversations.</div>
            </div>
            <div className="flex items-center rounded-xl bg-muted p-1 gap-1 shrink-0">
              {CHAT_FONT_SIZES.map((fs) => (
                <button
                  key={fs.key}
                  type="button"
                  onClick={() => handleSizeChange(fs.key)}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-xs transition-all cursor-pointer",
                    chatSize === fs.key
                      ? "bg-background text-foreground shadow-xs font-semibold"
                      : "text-muted-foreground hover:text-foreground font-medium"
                  )}
                >
                  {fs.label}
                </button>
              ))}
            </div>
          </div>
          {/* Live Preview */}
          <div className="mt-3.5 rounded-xl border border-border/60 bg-muted/20 p-3.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">Live Preview</div>
            <div className="space-y-2">
              <div className="flex justify-end">
                <div
                  className="rounded-2xl rounded-br-md bg-accent px-4 py-2 text-white max-w-md shadow-xs"
                  style={{ fontSize: "var(--chat-font-size)", lineHeight: "var(--chat-leading)" }}
                >
                  Can you check the latest file updates on my PC?
                </div>
              </div>
              <div className="flex gap-2">
                <div
                  className="rounded-2xl bg-card border border-border px-4 py-2 text-foreground max-w-md shadow-xs"
                  style={{ fontSize: "var(--chat-font-size)", lineHeight: "var(--chat-leading)" }}
                >
                  Found 3 updated folders. Local and ready to execute anytime.
                </div>
              </div>
            </div>
          </div>
        </Card>

        <Card className="flex items-center justify-between">
          <div><div className="font-medium">Read answers aloud</div><div className="text-xs text-muted-foreground">Piper voice, local. Same as saying “mute” / “read that”.</div></div>
          <button onClick={() => postJSON<typeof p>("/api/prefs", { audio_output: !p.audio_output }).then(setP)}
            className={`relative h-6 w-11 rounded-full transition-colors ${p.audio_output ? "bg-accent" : "bg-muted"}`} aria-pressed={p.audio_output}>
            <motion.span layout transition={{ type: "spring", stiffness: 600, damping: 32 }}
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow ${p.audio_output ? "right-0.5" : "left-0.5"}`} />
          </button>
        </Card>
        <ChromeProfiles />
        <Card><div className="font-medium">Hands-free</div>
          <div className="text-xs text-muted-foreground">Wake word “{p.wake_model.replace("_", " ")}” is {p.wake_enabled ? "on" : "off"} (needs a microphone).</div></Card>
        <Card><div className="mb-1 font-medium">Models</div>
          {Object.entries(p.models).map(([role, m]) => <div key={role} className="text-sm"><span className="inline-block w-20 text-muted-foreground">{role}</span>{m}</div>)}</Card>
        <Card><div className="font-medium">Credits</div>
          <div className="text-xs text-muted-foreground">Animated components from <a className="text-accent" href="https://rareui.com" target="_blank" rel="noreferrer">Rare UI</a>. See THIRD_PARTY_NOTICES.md.</div></Card>
      </div>
    </Page>
  );
}
