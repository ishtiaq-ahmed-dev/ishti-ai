import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { Page } from "@/sections/Pages";

type About = {
  facts: Record<string, string>;
  prefs: Record<string, string>;
  instructions: string;
  sent: { style: string; context: string };
};

const label = (attr: string) => attr.replace(/^prefers_/, "").replace(/_/g, " ");

function Section({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-border bg-background p-4 sm:p-5">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-0.5 mb-3 text-xs text-muted-foreground">{hint}</p>
      {children}
    </section>
  );
}

/** One editable owner fact: click the pencil to change it, the bin to forget it. */
function Row({ attr, value, onSave, onDelete }: {
  attr: string; value: string; onSave: (attr: string, value: string) => Promise<void>; onDelete: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <motion.li layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -8 }}
      className="group flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-muted/50">
      <span className="w-40 shrink-0 truncate text-xs font-medium capitalize text-muted-foreground" title={attr}>{label(attr)}</span>
      {editing ? (
        <form className="flex min-w-0 flex-1 items-center gap-1.5"
          onSubmit={async (e) => { e.preventDefault(); if (draft.trim()) { await onSave(attr, draft.trim()); setEditing(false); } }}>
          <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={`Edit ${label(attr)}`}
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm outline-none focus:border-accent" />
          <button type="submit" title="Save" className="rounded-md p-1 text-accent hover:bg-accent-soft cursor-pointer"><Check className="h-4 w-4" /></button>
          <button type="button" title="Cancel" onClick={() => setEditing(false)}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted cursor-pointer"><X className="h-4 w-4" /></button>
        </form>
      ) : (
        <>
          <span className="min-w-0 flex-1 break-words text-sm">{value}</span>
          <span className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
            <button type="button" title="Edit" aria-label={`Edit ${label(attr)}`} onClick={() => setEditing(true)}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer"><Pencil className="h-3.5 w-3.5" /></button>
            <button type="button" title="Forget" aria-label={`Forget ${label(attr)}`} onClick={onDelete}
              className="rounded-md p-1 text-muted-foreground hover:bg-danger/10 hover:text-danger cursor-pointer"><Trash2 className="h-3.5 w-3.5" /></button>
          </span>
        </>
      )}
    </motion.li>
  );
}

function AddRow({ prefix = "", placeholder, onAdd }: { prefix?: string; placeholder: string; onAdd: (a: string, v: string) => Promise<void> }) {
  const [attr, setAttr] = useState("");
  const [value, setValue] = useState("");
  return (
    <form className="mt-2 flex flex-wrap items-center gap-2 px-3"
      onSubmit={async (e) => {
        e.preventDefault();
        const key = attr.trim().toLowerCase().replace(/\W+/g, "_").replace(/^_|_$/g, "");
        if (!key || !value.trim()) return;
        await onAdd(prefix + key.replace(/^prefers_/, ""), value.trim());
        setAttr(""); setValue("");
      }}>
      <input value={attr} onChange={(e) => setAttr(e.target.value)} placeholder={placeholder} aria-label="What"
        className="w-40 rounded-lg border border-border bg-background px-2 py-1 text-xs outline-none focus:border-accent" />
      <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="value" aria-label="Value"
        className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm outline-none focus:border-accent" />
      <button type="submit" disabled={!attr.trim() || !value.trim()}
        className="flex items-center gap-1 rounded-lg bg-accent px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40 cursor-pointer">
        <Plus className="h-3.5 w-3.5" /> Add</button>
    </form>
  );
}

/** Everything ISHTI knows about the owner and sends with their messages - visible and editable, like Claude's profile. */
export function AboutYou({ onToggleNav }: { onToggleNav?: () => void }) {
  const [about, setAbout] = useState<About | null>(null);
  const [instructions, setInstructions] = useState("");
  const [saved, setSaved] = useState(false);
  const [showSent, setShowSent] = useState(false);

  const load = () => getJSON<About>("/api/about").then((a) => { setAbout(a); setInstructions(a.instructions); });
  useEffect(() => { load(); }, []);

  const save = async (attr: string, value: string) => { await postJSON("/api/facts", { entity: "owner", attr, value }); await load(); };
  const forget = async (attr: string) => { await del(`/api/facts/owner/${encodeURIComponent(attr)}`); await load(); };
  const saveInstructions = async () => {
    const a = await postJSON<About>("/api/about/instructions", { instructions });
    setAbout(a); setSaved(true); setTimeout(() => setSaved(false), 1800);
  };

  const rows = (items: Record<string, string>) => (
    <ul className="divide-y divide-border/50">
      <AnimatePresence initial={false}>
        {Object.entries(items).map(([a, v]) => (
          <Row key={a} attr={a} value={v} onSave={save} onDelete={() => forget(a)} />
        ))}
      </AnimatePresence>
    </ul>
  );

  return (
    <Page title="About you" subtitle="What ISHTI knows about you and how it should work for you. Edit anything; it applies to your next message."
      className="max-w-3xl" onToggleNav={onToggleNav}>
      {!about ? <p className="text-sm text-muted-foreground">Loading…</p> : (
        <div className="space-y-4">
          <Section title="Custom instructions" hint="Sent with every message, like Claude's personal preferences. Example: answer in short bullet points, reply in Roman Urdu when I write in it, always show prices as a table.">
            <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={5} maxLength={4000}
              aria-label="Custom instructions" placeholder="How should ISHTI answer and work for you?"
              className="scroll-thin w-full resize-y rounded-xl border border-border bg-background p-3 text-sm leading-relaxed outline-none focus:border-accent" />
            <div className="mt-2 flex items-center justify-end gap-3">
              <AnimatePresence>{saved && <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="text-xs text-accent">Saved</motion.span>}</AnimatePresence>
              <button type="button" onClick={saveInstructions} disabled={instructions === about.instructions}
                className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 cursor-pointer">Save instructions</button>
            </div>
          </Section>

          <Section title="About you" hint="Personal and professional details. ISHTI adds these when you tell it (for example 'I'm in 7th semester') and updates them in place.">
            {rows(about.facts)}
            <AddRow placeholder="e.g. semester" onAdd={save} />
          </Section>

          <Section title="Preferences learned from your chats" hint="Standing preferences ISHTI picked up from how you talk to it. Fix or remove any that are wrong.">
            {Object.keys(about.prefs).length ? rows(about.prefs) : <p className="px-3 text-sm text-muted-foreground">None yet.</p>}
            <AddRow prefix="prefers_" placeholder="e.g. answer length" onAdd={save} />
          </Section>

          <section className="rounded-2xl border border-border bg-background">
            <button type="button" onClick={() => setShowSent((v) => !v)} aria-expanded={showSent}
              className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-semibold cursor-pointer sm:px-5">
              What is sent with every message
              <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${showSent ? "rotate-180" : ""}`} />
            </button>
            <AnimatePresence initial={false}>
              {showSent && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                  className="overflow-hidden">
                  <div className="space-y-3 px-4 pb-4 sm:px-5">
                    <p className="text-xs text-muted-foreground">Exact text the local model receives. Chat answers also get the facts below; other skills get only the style.</p>
                    <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-muted/60 p-3 font-mono text-[12px] leading-relaxed">{about.sent.style}</pre>
                    {about.sent.context && <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-muted/60 p-3 font-mono text-[12px] leading-relaxed">{about.sent.context}</pre>}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </section>
        </div>
      )}
    </Page>
  );
}
