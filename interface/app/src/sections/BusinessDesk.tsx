import { cloneElement, useCallback, useEffect, useId, useState, type ReactElement, type ReactNode } from "react";
import { Activity, ArrowRight, Building2, Check, ChevronDown, Download, FileText, Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import { del, getJSON, postJSON } from "@/lib/api";
import { Page } from "@/sections/Pages";

type Profile = { name: string; currency: string; timezone: string; goal: string; enabled: boolean; interval_minutes: number; lead_days: number; stale_days: number; revenue_drop_pct: number };
type Source = { id: string; kind: string; label: string; hash: string; path: string | null; observed_at: number; record_count: number; error: string | null };
type Proposal = { id: string; kind: string; title: string; why: string; value_minor: number | null; currency: string; score: number; source_hash: string; source_label: string; row_id: string; evidence: Record<string, unknown>; action: string; draft: string | null; state: string };
type Outcome = { id: string; result: string; amount_minor: number; currency: string; receipt: string; verification: string };
type Desk = { profile: Profile; status: string; heartbeat: number; runner: string | null; last_cycle: number | null; next_run: number; last_error: string | null; sources: Source[]; proposals: Proposal[]; accepted: Proposal[]; outcomes: Outcome[]; learning: { kind: string; accepted: number; dismissed: number; known_outcomes: number; useful: number; priority_adjustment: number }[]; metrics: { awaiting_owner: number; prepared: number; owner_reported_collected_minor: number; owner_reported_won_minor: number }; brief: string };
const input = "w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
const button = "inline-flex items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50 disabled:cursor-wait";
const primary = `${button} bg-blue-600 text-white border-blue-600 hover:bg-blue-700`;
const examples: Record<string, string> = {
  invoices: "id,customer,due_date,balance,currency,status\nINV-001,Customer name,2026-10-01,250.00,USD,open",
  leads: "id,name,last_contact,value,currency,status\nLEAD-001,Prospect name,2026-10-01,1000.00,USD,qualified",
  sales: "id,date,revenue,currency\nSALE-001,2026-10-01,500.00,USD",
};
const human = (value: string) => value.replaceAll("_", " ");
function amount(value: number, currency: string) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(value / 100); }
  catch { return `${currency} ${(value / 100).toFixed(2)}`; }
}
function stamp(value: number | null) { return value ? new Date(value * 1000).toLocaleString() : "Not observed yet"; }
function Field({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return <div className="grid min-w-0 gap-1.5 text-sm"><label className="font-medium" htmlFor={id}>{label}</label>{cloneElement(children as ReactElement<{ id?: string }>, { id })}</div>;
}

export default function BusinessDesk({ onToggleNav }: { onToggleNav?: () => void }) {
  const [desk, setDesk] = useState<Desk | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const [kind, setKind] = useState("invoices");
  const [label, setLabel] = useState("");
  const [csv, setCsv] = useState("");
  const [path, setPath] = useState("");
  const [mode, setMode] = useState("snapshot");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [outcomeForms, setOutcomeForms] = useState<Record<string, { result: string; amount: string; receipt: string; note: string }>>({});
  const load = useCallback(async (initial = false) => {
    const next = await getJSON<Desk>("/api/business/dashboard");
    setDesk(next); if (initial) setProfile(next.profile);
  }, []);
  useEffect(() => {
    let live = true;
    load(true).catch(e => { if (live) setError(String(e.message || e)); });
    const timer = window.setInterval(() => { if (!document.hidden) load().catch(e => { if (live) setError(String(e.message || e)); }); }, 30000);
    return () => { live = false; window.clearInterval(timer); };
  }, [load]);
  async function act(job: () => Promise<unknown>, message: string) {
    setBusy(true); setError(""); setNotice("");
    try { await job(); await load(); setNotice(message); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function saveProfile() {
    if (profile) await act(() => postJSON("/api/business/profile", profile), "Business policy saved.");
  }
  async function toggle() {
    if (!desk) return;
    const next = { ...desk.profile, enabled: !desk.profile.enabled };
    await act(async () => { await postJSON("/api/business/profile", next); setProfile(next); }, next.enabled ? "Observation enabled. The runner checks the durable schedule." : "Observation paused.");
  }
  async function decide(p: Proposal, choice: string) {
    await act(() => postJSON(`/api/business/decisions/${p.id}`, { choice, source_hash: p.source_hash, note: notes[p.id] || "" }), choice === "accept" ? "Draft prepared locally. Review it below; nothing has been sent." : "Decision recorded. This unchanged evidence will not repeat the proposal.");
  }
  const editProfile = (values: Partial<Profile>) => setProfile(p => p ? { ...p, ...values } : p);
  return <Page title="Business Desk" subtitle="Know what needs attention. Decide with evidence. Learn from the result." onToggleNav={onToggleNav}
    action={<div className="flex gap-2"><button className={button} disabled={busy} onClick={() => act(() => postJSON("/api/business/observe"), "Observation finished.")}><RefreshCw size={15} />Observe now</button><button className={desk?.profile.enabled ? button : primary} disabled={busy || !desk} onClick={toggle}>{desk?.profile.enabled ? <Pause size={15} /> : <Play size={15} />}{desk?.profile.enabled ? "Pause" : "Enable observation"}</button></div>}>
    <div className="space-y-6">
      {error && <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm">{error}</div>}
      {notice && <div role="status" className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 text-sm">{notice}</div>}
      {!desk ? <div className="rounded-xl border border-border p-6">{error ? <button className={button} onClick={() => act(() => load(true), "Loaded.")}>Try again</button> : "Loading your business workspace…"}</div> : <>
        <section className="rounded-xl border border-border bg-card p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-4"><div className="flex items-center gap-3"><Building2 className="text-blue-500" size={24} /><div><h2 className="text-lg font-semibold">{desk.profile.name || "Set up your business"}</h2><p className="mt-1 text-sm text-muted-foreground">{desk.profile.goal || "Start with your goal and one real data source."}</p></div></div><span className="flex items-center gap-2 rounded-full border border-border px-3 py-1 text-xs"><Activity size={13} className={desk.status === "active" ? "text-emerald-700 dark:text-emerald-500" : "text-amber-500"} />{human(desk.status)} · {desk.runner || "no runner seen"}</span></div>
          <p className="mt-4 text-sm leading-relaxed">{desk.brief}</p>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground"><span>Last cycle: {stamp(desk.last_cycle)}</span><span>Runner heartbeat: {stamp(desk.heartbeat)}</span><span>Cadence: {desk.profile.interval_minutes} min</span></div>
          {desk.last_error && <p role="alert" className="mt-3 text-sm text-amber-600 dark:text-amber-400">{desk.last_error}</p>}
        </section>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[
          ["Awaiting your decision", String(desk.metrics.awaiting_owner), "Prioritized by rule and recorded feedback"],
          ["Prepared for review", String(desk.metrics.prepared), "Local drafts and checklists"],
          ["Reported collections", amount(desk.metrics.owner_reported_collected_minor, desk.profile.currency), "Owner reports · causality unproven"],
          ["Reported won deals", amount(desk.metrics.owner_reported_won_minor, desk.profile.currency), "Deal value · not recognized revenue"],
        ].map(([title, value, detail]) => <div key={title} className="rounded-xl border border-border bg-card p-4"><p className="text-xs text-muted-foreground">{title}</p><p className="my-2 text-2xl font-semibold tabular-nums">{value}</p><p className="text-xs text-muted-foreground">{detail}</p></div>)}</div>
        <section><div className="mb-3 flex items-center justify-between"><h2 className="text-base font-semibold">Attention queue</h2><span className="text-xs text-muted-foreground">Every item links to an accepted source snapshot</span></div>
          {!desk.proposals.length ? <div className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">{desk.sources.length ? "No current proposals. Enable observation and run a cycle; fresh data may have nothing to flag." : "Import invoices, leads or sales below to find your first evidence-backed opportunity."}</div> : <div className="grid gap-4 lg:grid-cols-2">{desk.proposals.map(p => <article key={p.id} className="min-w-0 rounded-xl border border-border bg-card p-5">
            <div className="flex items-start justify-between gap-3"><div><p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">{human(p.kind)}</p><h3 className="font-semibold">{p.title}</h3></div><span className="shrink-0 rounded-lg bg-blue-500/10 px-2 py-1 text-xs text-blue-600 dark:text-blue-400">Priority {p.score}</span></div>
            <p className="mt-3 text-sm leading-relaxed">{p.why}</p>{p.value_minor !== null && <p className="mt-2 font-medium tabular-nums">{amount(p.value_minor, p.currency)} <span className="text-xs font-normal text-muted-foreground">{p.kind === "revenue_drop" ? "recorded difference" : "potential value / exposure"}</span></p>}
            <p className="mt-3 text-sm text-muted-foreground">{p.action}</p>
            <details className="mt-3 rounded-lg border border-border p-3 text-xs"><summary className="cursor-pointer">Evidence · {p.source_label} · {p.row_id}</summary><p className="mt-2 break-all text-muted-foreground">Snapshot SHA-256: {p.source_hash}</p><dl className="mt-2 space-y-1">{Object.entries(p.evidence).map(([key, value]) => <div key={key} className="flex flex-wrap gap-2"><dt className="font-medium">{human(key)}:</dt><dd className="break-all">{String(value ?? "unknown")}</dd></div>)}</dl></details>
            <label className="mt-3 block"><span className="text-xs text-muted-foreground">Your decision context (optional)</span><input className={`${input} mt-1`} aria-label={`Decision context for ${p.title}`} maxLength={1000} value={notes[p.id] || ""} onChange={e => setNotes({ ...notes, [p.id]: e.target.value })} /></label>
            <div className="mt-4 flex gap-2"><button className={primary} disabled={busy} onClick={() => decide(p, "accept")}><Check size={15} />Prepare draft</button><button className={button} disabled={busy} onClick={() => decide(p, "dismiss")}>Dismiss</button></div>
          </article>)}</div>}
        </section>
        {!!desk.accepted.length && <section><h2 className="mb-3 font-semibold">Prepared work & outcomes</h2><div className="space-y-4">{desk.accepted.map(p => { const form = outcomeForms[p.id] || { result: "useful", amount: "0", receipt: "", note: "" }; const update = (v: Partial<typeof form>) => setOutcomeForms({ ...outcomeForms, [p.id]: { ...form, ...v } }); return <article key={p.id} className="rounded-xl border border-border bg-card p-5"><div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold">{p.title}</h3><span className="text-xs text-muted-foreground">Local draft · not sent</span></div><pre className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-muted/40 p-4 font-sans text-sm leading-relaxed">{p.draft}</pre><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Field label="What happened?"><select aria-label={`Outcome for ${p.title}`} className={input} value={form.result} onChange={e => update({ result: e.target.value })}>{[...(p.kind === "overdue_invoice" ? ["collected"] : p.kind === "neglected_lead" ? ["won"] : []), "useful", "not_useful", "lost", "unknown"].map(s => <option key={s} value={s}>{human(s)}</option>)}</select></Field><Field label={`Actual amount (${p.currency})`}><input className={input} inputMode="decimal" aria-label={`Actual amount for ${p.title}`} value={form.amount} onChange={e => update({ amount: e.target.value })} /></Field><Field label="Receipt / CRM reference"><input className={input} aria-label={`Receipt for ${p.title}`} maxLength={300} value={form.receipt} onChange={e => update({ receipt: e.target.value })} /></Field><Field label="What did we learn?"><input className={input} aria-label={`Outcome note for ${p.title}`} maxLength={1000} value={form.note} onChange={e => update({ note: e.target.value })} /></Field></div><button className={`${button} mt-3`} disabled={busy} onClick={() => act(() => postJSON(`/api/business/decisions/${p.id}/outcome`, form), "Outcome recorded once. This is an owner report, not independently verified ROI.")}><ArrowRight size={15} />Record outcome</button></article>; })}</div></section>}
        <div className="grid gap-5 xl:grid-cols-2">
          <section className="min-w-0 rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Business evidence</h2><p className="mt-1 text-sm text-muted-foreground">One accepted source per type. Originals stay in your tools. Re-import snapshots or approve a local file to watch.</p><div className="my-4 space-y-2">{desk.sources.map(s => <div key={s.id} className="flex items-start justify-between gap-3 rounded-lg border border-border p-3"><div className="min-w-0"><p className="text-sm font-medium">{s.label} <span className="font-normal text-muted-foreground">· {s.record_count} records</span></p><p className="mt-1 text-xs text-muted-foreground">{human(s.kind)} · {s.path ? "Watched local CSV" : "Imported snapshot"} · {stamp(s.observed_at)}</p>{s.error && <p className="mt-1 text-xs text-amber-600">{s.error}</p>}</div><button aria-label={`Remove ${s.label}`} className={button} disabled={busy} onClick={() => act(() => del(`/api/business/sources/${s.id}`), "Source disconnected; unaccepted proposals withdrawn.")}><Trash2 size={14} /></button></div>)}</div>
            <div className="grid gap-3 sm:grid-cols-2"><Field label="Data type"><select className={input} value={kind} onChange={e => setKind(e.target.value)}><option value="invoices">Invoices</option><option value="leads">Leads</option><option value="sales">Sales</option></select></Field><Field label="Source name"><input className={input} maxLength={120} placeholder="October invoices" value={label} onChange={e => setLabel(e.target.value)} /></Field></div>
            <div className="my-3 flex flex-wrap gap-2">{[["snapshot", "Import snapshot"], ["watch", "Watch local CSV"]].map(([key, title]) => <button className={mode === key ? primary : button} key={key} onClick={() => setMode(key)}>{title}</button>)}</div>
            {mode === "snapshot" ? <><Field label="Choose a UTF-8 CSV (maximum 1 MB)"><input type="file" accept=".csv,text/csv" className="max-w-full text-sm" onChange={async e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 1024*1024) { setError("CSV must be at most 1 MB."); return; } try { setCsv(await file.text()); if (!label) setLabel(file.name); } catch { setError("Could not read this file."); } }} /></Field><Field label="Or paste CSV"><textarea className={`${input} mt-3 min-h-28 font-mono text-xs`} value={csv} onChange={e => setCsv(e.target.value)} maxLength={1024*1024} spellCheck={false} /></Field></> : <Field label="Exact local CSV path"><input className={input} placeholder="C:\Business\invoices.csv" value={path} onChange={e => setPath(e.target.value)} maxLength={1000} /></Field>}
            <details className="mt-3 text-xs"><summary className="cursor-pointer text-muted-foreground">Required columns & sample (not imported automatically)</summary><pre className="mt-2 overflow-x-auto rounded-lg bg-muted/50 p-3">{examples[kind].replaceAll("USD", desk.profile.currency)}</pre><p className="mt-2 text-muted-foreground">Dates: YYYY-MM-DD. Amounts: plain decimal, at most two decimal places. All rows use {desk.profile.currency}. Sales use the last two complete seven-day windows; missing exports can look like a drop.</p></details>
            <button className={`${primary} mt-4`} disabled={busy || !label || (mode === "watch" ? !path : !csv)} onClick={() => act(async () => { await postJSON("/api/business/sources", { kind, label, ...(mode === "watch" ? { path } : { content: csv }) }); setCsv(""); setPath(""); }, "Source accepted and encrypted. Enable observation to create proposals.")}><FileText size={15} />Accept this source</button>
          </section>
          <div className="space-y-5"><section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">Consult with ISHTI</h2><p className="mt-1 text-sm text-muted-foreground">One advisory call through your selected model. In API mode, the question and up to ten proposals go to your provider. Routine observation stays local.</p><label className="mt-3 block"><span className="sr-only">Consultation question</span><textarea className={`${input} min-h-24`} maxLength={1500} placeholder="Which opportunity should I act on first, and what evidence is missing?" value={question} onChange={e => setQuestion(e.target.value)} /></label><button className={`${button} mt-3`} disabled={busy || !question.trim()} onClick={() => act(async () => { const result = await postJSON<{ answer: string }>("/api/business/consult", { question }); setAnswer(result.answer); }, "Advisory consultation completed; it did not execute actions.")}>Ask my selected model <ArrowRight size={14} /></button>{answer && <div className="mt-4 whitespace-pre-wrap break-words rounded-lg bg-muted/40 p-4 text-sm leading-relaxed">{answer}</div>}</section>
          <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">What the business has taught us</h2><p className="mt-1 text-sm text-muted-foreground">Priority adjusts only after five known owner-reported outcomes for a category. Learning changes attention, never action permissions.</p>{!desk.learning.length ? <p className="mt-4 text-sm text-muted-foreground">Decision and outcome history will appear here.</p> : <div className="mt-4 space-y-3">{desk.learning.map(l => <div key={l.kind} className="flex flex-wrap justify-between gap-2 border-t border-border pt-3 text-sm"><span className="font-medium capitalize">{human(l.kind)}</span><span className="text-muted-foreground">{l.known_outcomes} outcomes · {l.useful} useful · priority {l.priority_adjustment > 0 ? "+" : ""}{l.priority_adjustment}</span></div>)}</div>}{!!desk.outcomes.length && <p className="mt-4 text-xs text-muted-foreground">{desk.outcomes.length} recent outcome receipts. Reported collections and won deal values are kept separate.</p>}</section></div>
        </div>
        <section className="rounded-xl border border-border bg-card p-5"><button className="flex w-full items-center justify-between text-left font-semibold" aria-expanded={settings} onClick={() => setSettings(!settings)}>Business profile & observation policy<ChevronDown size={17} /></button>{settings && profile && <div className="mt-4 space-y-4"><div className="grid gap-3 sm:grid-cols-3"><Field label="Business name"><input className={input} value={profile.name} maxLength={120} onChange={e => editProfile({ name: e.target.value })} /></Field><Field label="Currency code"><input className={input} value={profile.currency} maxLength={3} onChange={e => editProfile({ currency: e.target.value.toUpperCase() })} /></Field><Field label="Business timezone"><input className={input} value={profile.timezone} placeholder="Asia/Karachi" onChange={e => editProfile({ timezone: e.target.value })} /></Field></div><Field label="What should the business improve?"><textarea className={input} maxLength={1000} value={profile.goal} onChange={e => editProfile({ goal: e.target.value })} /></Field><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[["interval_minutes", "Observe every (minutes)", 5, 1440], ["lead_days", "Lead contact gap (days)", 1, 90], ["stale_days", "Snapshot age limit (days)", 1, 90], ["revenue_drop_pct", "Sales drop threshold (%)", 5, 90]].map(([key, title, min, max]) => <Field key={String(key)} label={String(title)}><input className={input} type="number" min={Number(min)} max={Number(max)} value={profile[key as keyof Profile] as number} onChange={e => editProfile({ [String(key)]: Number(e.target.value) })} /></Field>)}</div><button className={primary} disabled={busy} onClick={saveProfile}>Save business policy</button></div>}</section>
        <section className="rounded-xl border border-border p-5 text-sm"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Continuous operation</h2><a className={button} href="/api/business/export" download><Download size={15} />Export sensitive workspace</a></div><p className="mt-2 leading-relaxed text-muted-foreground">Observation runs while ISHTI is open. To keep it running after the app closes, configure and enable it, then install the current-user Windows worker. The PC must remain on and awake, with the user session signed in. Restart recovery checks the latest state; it does not replay customer actions.</p><code className="mt-3 block overflow-x-auto rounded-lg bg-muted/50 p-3 text-xs">venv\Scripts\python tools\business_worker.py --install</code><p className="mt-3 text-xs text-muted-foreground">The Stop control pauses business observation persistently. No message, purchase, payment, ad change or public post is automated in this release. Review exported files before sharing: they contain business data.</p></section>
      </>}
    </div>
  </Page>;
}
