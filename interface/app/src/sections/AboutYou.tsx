import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { Page } from "@/sections/Pages";

type About = {
  facts: Record<string, string>;
  prefs: Record<string, string>;
  instructions: string;
  sent: { style: string; context: string };
};

const label = (attr: string) => attr.replace(/^prefers_/, "").replace(/_/g, " ");
const errorText = (error: unknown) => error instanceof Error ? error.message : "The request failed.";

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
  attr: string; value: string; onSave: (attr: string, value: string) => Promise<boolean>; onDelete: () => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(value), [value]);
  return (
    <motion.li layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: -8 }}
      className="group flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-muted/50">
      <span className="w-40 shrink-0 truncate text-xs font-medium capitalize text-muted-foreground" title={attr}>{label(attr)}</span>
      {editing ? (
        <form className="flex min-w-0 flex-1 items-center gap-1.5"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!draft.trim()) return;
            setBusy(true);
            const ok = await onSave(attr, draft.trim());
            setBusy(false);
            if (ok) setEditing(false);
          }}>
          <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={`Edit ${label(attr)}`}
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
            className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm outline-none focus:border-accent" />
          <button type="submit" disabled={busy} title="Save" className="rounded-md p-1 text-accent hover:bg-accent-soft disabled:opacity-40 cursor-pointer"><Check className="h-4 w-4" /></button>
          <button type="button" title="Cancel" onClick={() => setEditing(false)}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted cursor-pointer"><X className="h-4 w-4" /></button>
        </form>
      ) : (
        <>
          <span className="min-w-0 flex-1 break-words text-sm">{value}</span>
          <span className={`flex shrink-0 gap-0.5 transition-opacity ${confirmingDelete ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"}`}>
            {confirmingDelete ? (<>
              <button type="button" disabled={busy} onClick={() => setConfirmingDelete(false)}
                className="rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted disabled:opacity-40 cursor-pointer">Cancel</button>
              <button type="button" disabled={busy} onClick={async () => {
                setBusy(true);
                const ok = await onDelete();
                setBusy(false);
                if (!ok) setConfirmingDelete(false);
              }}
                className="rounded-md bg-danger px-2 py-1 text-[11px] font-medium text-white disabled:opacity-40 cursor-pointer">{busy ? "Forgetting…" : "Confirm forget"}</button>
            </>) : (<>
            <button type="button" title="Edit" aria-label={`Edit ${label(attr)}`} onClick={() => setEditing(true)}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer"><Pencil className="h-3.5 w-3.5" /></button>
            <button type="button" title="Forget" aria-label={`Forget ${label(attr)}`} onClick={() => setConfirmingDelete(true)}
              className="rounded-md p-1 text-muted-foreground hover:bg-danger/10 hover:text-danger cursor-pointer"><Trash2 className="h-3.5 w-3.5" /></button>
            </>)}
          </span>
        </>
      )}
    </motion.li>
  );
}

function AddRow({ prefix = "", placeholder, onAdd }: { prefix?: string; placeholder: string; onAdd: (a: string, v: string) => Promise<boolean> }) {
  const [attr, setAttr] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form className="mt-2 flex flex-wrap items-center gap-2 px-3"
      onSubmit={async (e) => {
        e.preventDefault();
        const key = attr.trim().toLowerCase().replace(/\W+/g, "_").replace(/^_|_$/g, "");
        if (!key || !value.trim()) return;
        setBusy(true);
        const ok = await onAdd(prefix + key.replace(/^prefers_/, ""), value.trim());
        setBusy(false);
        if (ok) { setAttr(""); setValue(""); }
      }}>
      <input value={attr} onChange={(e) => setAttr(e.target.value)} placeholder={placeholder} aria-label="What"
        className="w-40 rounded-lg border border-border bg-background px-2 py-1 text-xs outline-none focus:border-accent" />
      <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="value" aria-label="Value"
        className="min-w-0 flex-1 rounded-lg border border-border bg-background px-2 py-1 text-sm outline-none focus:border-accent" />
      <button type="submit" disabled={busy || !attr.trim() || !value.trim()}
        className="flex items-center gap-1 rounded-lg bg-accent px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40 cursor-pointer">
        <Plus className="h-3.5 w-3.5" /> {busy ? "Adding…" : "Add"}</button>
    </form>
  );
}

type LearnedFact = { id: string; fact_type: string; value: string; provenance: { source: string; confidence: number; reference: string } };

/** What ISHTI has worked out about the owner from real use (language, habits, frequent tasks) - visible, with source, deletable. */
function LearnedBehavior() {
  const [facts, setFacts] = useState<LearnedFact[] | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    getJSON<{ facts: LearnedFact[] }>("/api/owner/behavior")
      .then((d) => setFacts((prev) => (JSON.stringify(prev) === JSON.stringify(d.facts) ? prev : d.facts)))
      .catch((e) => setErr(errorText(e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 20000); return () => clearInterval(t); }, [load]);
  const forgetFact = async (f: LearnedFact) => {
    if (!window.confirm(`Forget this learned fact?

${f.value}`)) return;
    try { await del(`/api/owner/behavior/${encodeURIComponent(f.id)}?confirm=1`); load(); } catch (e) { setErr(errorText(e)); }
  };
  return (
    <Section title="What ISHTI learned about you" hint="Worked out automatically from how you use it (language, when you work, what you ask for most). Each item shows where it came from. Delete anything that is wrong.">
      {err && <p role="alert" className="mb-2 text-xs text-danger">{err}</p>}
      {facts && !facts.length && <p className="text-xs text-muted-foreground">Nothing yet. It learns after a few conversations.</p>}
      <ul className="space-y-1.5" data-testid="learned-facts">
        {(facts || []).map((f) => (
          <li key={f.id} className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5 text-sm">
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">{f.fact_type.replace("_", " ")}</span>
            <span className="min-w-0 flex-1 break-words">{f.value}</span>
            <span className="text-[10px] text-muted-foreground" title={f.provenance.reference}>{f.provenance.source} · {Math.round(f.provenance.confidence * 100)}%</span>
            <button type="button" aria-label="Forget this" onClick={() => void forgetFact(f)} className="rounded p-1 text-muted-foreground hover:text-danger cursor-pointer"><Trash2 className="h-3.5 w-3.5" /></button>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** Everything ISHTI knows about the owner and sends with their messages - visible and editable personal profile. */
export function AboutYouBody() {
  const [about, setAbout] = useState<About | null>(null);
  const [instructions, setInstructions] = useState("");
  const [saved, setSaved] = useState(false);
  const [showSent, setShowSent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [savingInstructions, setSavingInstructions] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const a = await getJSON<About>("/api/about");
      setAbout(a);
      setInstructions(a.instructions);
    } catch (error) {
      setLoadError(errorText(error));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
    const handleMemoryEvent = () => void load();
    window.addEventListener("ishti-memory-updated", handleMemoryEvent);
    return () => window.removeEventListener("ishti-memory-updated", handleMemoryEvent);
  }, [load]);

  const save = async (attr: string, value: string) => {
    setActionError("");
    try {
      await postJSON("/api/facts", { entity: "owner", attr, value });
      await load();
      return true;
    } catch (error) {
      setActionError(errorText(error));
      return false;
    }
  };
  const forget = async (attr: string) => {
    setActionError("");
    try {
      await del(`/api/facts/owner/${encodeURIComponent(attr)}`);
      await load();
      return true;
    } catch (error) {
      setActionError(errorText(error));
      return false;
    }
  };
  const saveInstructions = async () => {
    setSavingInstructions(true); setActionError("");
    try {
      const a = await postJSON<About>("/api/about/instructions", { instructions });
      setAbout(a); setSaved(true); setTimeout(() => setSaved(false), 1800);
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setSavingInstructions(false);
    }
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

  if (!about) {
    if (loadError) {
      return (
        <div role="alert" className="rounded-2xl border border-danger/30 bg-danger/10 p-5 text-sm text-danger">
          <p>Could not load what ISHTI knows about you: {loadError}</p>
          <button type="button" onClick={() => void load()} className="mt-3 rounded-lg border border-danger/40 px-3 py-1.5 text-xs font-medium hover:bg-danger/10 cursor-pointer">Try again</button>
        </div>
      );
    }
    return (
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start" aria-label={loading ? "Loading About you" : "About you is unavailable"}>
        <div className="lg:col-span-7 space-y-4">
          <div className="h-64 rounded-2xl border border-border bg-card/40 animate-pulse p-4" />
          <div className="h-20 rounded-2xl border border-border bg-card/40 animate-pulse p-4" />
        </div>
        <div className="lg:col-span-5 space-y-4">
          <div className="h-44 rounded-2xl border border-border bg-card/40 animate-pulse p-4" />
          <div className="h-44 rounded-2xl border border-border bg-card/40 animate-pulse p-4" />
        </div>
      </div>
    );
  }

  return (
    <>
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
          {loadError && <p role="alert" className="lg:col-span-12 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">Could not refresh this page: {loadError}</p>}
          {actionError && <p role="alert" className="lg:col-span-12 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">Changes were not saved: {actionError}</p>}
          {/* Left Column: Custom Instructions & Live Prompt Context */}
          <div className="lg:col-span-7 space-y-4">
            <Section
              title="Custom instructions"
              hint="Sent with every message to personalize ISHTI's responses. Example: answer in short bullet points, reply in Roman Urdu when I write in it, always show prices as a table."
            >
              <textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                rows={7}
                maxLength={4000}
                aria-label="Custom instructions"
                placeholder="How should ISHTI answer and work for you?"
                className="scroll-thin w-full resize-y rounded-xl border border-border bg-background p-3 text-sm leading-relaxed outline-none focus:border-accent"
              />
              <div className="mt-2.5 flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground font-mono">
                  {instructions.length} / 4000 chars
                </span>
                <div className="flex items-center gap-3">
                  <AnimatePresence>
                    {saved && (
                      <motion.span
                        initial={{ opacity: 0, y: 2 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="text-xs font-medium text-accent"
                      >
                        ✓ Saved instructions
                      </motion.span>
                    )}
                  </AnimatePresence>
                  <button
                    type="button"
                    onClick={saveInstructions}
                    disabled={savingInstructions || instructions === about.instructions}
                    className="rounded-lg bg-accent px-3.5 py-1.5 text-xs font-medium text-white disabled:opacity-40 hover:opacity-90 transition-opacity cursor-pointer shadow-xs"
                  >
                    {savingInstructions ? "Saving…" : "Save instructions"}
                  </button>
                </div>
              </div>
            </Section>

            <LearnedBehavior />

            {/* Prompt Transparency Dropdown */}
            <section className="rounded-2xl border border-border bg-background shadow-xs">
              <button
                type="button"
                onClick={() => setShowSent((v) => !v)}
                aria-expanded={showSent}
                className="flex w-full items-center justify-between px-4 py-3.5 text-left text-sm font-semibold cursor-pointer sm:px-5 hover:bg-muted/30 rounded-2xl transition-colors"
              >
                <div className="flex items-center gap-2">
                  <span>What is sent with every message</span>
                  <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] font-mono text-muted-foreground">
                    System prompt
                  </span>
                </div>
                <ChevronDown
                  className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${
                    showSent ? "rotate-180" : ""
                  }`}
                />
              </button>
              <AnimatePresence initial={false}>
                {showSent && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="space-y-3 border-t border-border/60 px-4 py-4 sm:px-5">
                      <p className="text-xs text-muted-foreground">
                        Exact text the local neural core receives. Chat answers also get the facts below; other skills get only the style.
                      </p>
                      <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-muted/60 p-3 font-mono text-[11.5px] leading-relaxed select-text">
                        {about.sent.style}
                      </pre>
                      {about.sent.context && (
                        <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-muted/60 p-3 font-mono text-[11.5px] leading-relaxed select-text">
                          {about.sent.context}
                        </pre>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </section>
          </div>

          {/* Right Column: Profile Facts & Learned Preferences */}
          <div className="lg:col-span-5 space-y-4">
            <Section
              title="Personal Details"
              hint="Personal and professional details ISHTI uses to assist you."
            >
              {Object.keys(about.facts).length ? rows(about.facts) : (
                <p className="px-3 py-2 text-xs text-muted-foreground italic">No personal details saved yet.</p>
              )}
              <AddRow placeholder="e.g. role or city" onAdd={save} />
            </Section>

            <Section
              title="Preferences learned from your chats"
              hint="Standing preferences ISHTI picked up from how you talk to it. Fix or remove any that are wrong."
            >
              {Object.keys(about.prefs).length ? (
                rows(about.prefs)
              ) : (
                <p className="px-3 py-2 text-xs text-muted-foreground italic">
                  No custom preferences saved yet.
                </p>
              )}
              <AddRow prefix="prefers_" placeholder="e.g. answer length" onAdd={save} />
            </Section>
          </div>
        </div>
    </>
  );
}

/** The About-you content as its own page (old #about link; the Learning page shows the same body as a tab). */
export function AboutYou({ onToggleNav }: { onToggleNav?: () => void }) {
  return (
    <Page
      title="About you"
      subtitle="What ISHTI knows about you and how it should work for you. Edit anything; it applies to your next message."
      className="max-w-6xl"
      onToggleNav={onToggleNav}
    >
      <AboutYouBody />
    </Page>
  );
}

