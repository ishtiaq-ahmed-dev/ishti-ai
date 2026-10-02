import { AnimatePresence, motion } from "motion/react";
import { ArrowLeft, History, LineChart, Play, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { del, getJSON, postJSON } from "@/lib/api";
import { AnimatedCounter } from "@/components/rare/animated-counter";
import { Markdown } from "@/components/Markdown";
import { Page } from "@/sections/Pages";
import { cn } from "@/lib/utils";

type Item = {
  kind: string; id: string; label: string; name?: string; price?: number; currency?: string; unit?: string;
  change_pct?: number | null; spark?: number[]; error?: string;
};
type Chart = { symbol: string; dates: string[]; closes: number[]; currency: string };
type Saved = {
  id: number; ts: number; symbol: string; name: string; currency: string; price?: number; action: string;
  entry?: number; stop?: number; t1?: number; t2?: number; question?: string; analysis?: string;
  data?: { days?: number; headlines?: number; sources?: string[]; kind?: string };
};

const SYM: Record<string, string> = { USD: "$", EUR: "€", GBP: "£" };
const decimalsFor = (v: number) => (v >= 1000 ? 0 : v >= 10 ? 2 : 4);

function Spark({ data, up }: { data: number[]; up: boolean }) {
  if (data.length < 2) return <div className="h-10" />;
  const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * 100},${36 - ((v - min) / span) * 32}`).join(" ");
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-10 w-full" aria-hidden>
      <motion.polyline points={pts} fill="none" strokeWidth={1.6} vectorEffect="non-scaling-stroke"
        stroke={up ? "var(--success)" : "var(--danger)"} initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
        transition={{ duration: 0.9, ease: "easeOut" }} />
    </svg>
  );
}

const sma = (c: number[], n: number) => c.map((_, i) => (i + 1 >= n ? c.slice(i + 1 - n, i + 1).reduce((a, b) => a + b, 0) / n : NaN));

/** Price with 50/200-day averages; hover shows the date and close. */
function PriceChart({ chart }: { chart: Chart }) {
  const [hover, setHover] = useState<number | null>(null);
  const { closes, dates } = chart;
  const s50 = useMemo(() => sma(closes, 50), [closes]);
  const s200 = useMemo(() => sma(closes, 200), [closes]);
  const all = [...closes, ...s50.filter(Number.isFinite), ...s200.filter(Number.isFinite)];
  const min = Math.min(...all), max = Math.max(...all), span = max - min || 1;
  const W = 600, H = 220, x = (i: number) => (i / (closes.length - 1)) * W, y = (v: number) => H - 8 - ((v - min) / span) * (H - 24);
  const line = (s: number[]) => s.map((v, i) => (Number.isFinite(v) ? `${x(i)},${y(v)}` : null)).filter(Boolean).join(" ");
  const up = closes[closes.length - 1] >= closes[0];
  const cur = SYM[chart.currency] ?? `${chart.currency} `;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-56 w-full" role="img" aria-label={`${chart.symbol} price over one year`}
        onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(Math.round(((e.clientX - r.left) / r.width) * (closes.length - 1))); }}
        onMouseLeave={() => setHover(null)}>
        <defs><linearGradient id="fillg" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={up ? "var(--success)" : "var(--danger)"} stopOpacity="0.18" />
          <stop offset="1" stopColor={up ? "var(--success)" : "var(--danger)"} stopOpacity="0" /></linearGradient></defs>
        <polygon points={`0,${H} ${line(closes)} ${W},${H}`} fill="url(#fillg)" />
        <motion.polyline points={line(closes)} fill="none" stroke={up ? "var(--success)" : "var(--danger)"} strokeWidth={2}
          initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.1, ease: "easeOut" }} />
        <polyline points={line(s50)} fill="none" stroke="var(--accent)" strokeWidth={1.2} strokeDasharray="4 3" />
        <polyline points={line(s200)} fill="none" stroke="#d98a1c" strokeWidth={1.2} strokeDasharray="6 4" />
        {hover !== null && hover >= 0 && hover < closes.length && (
          <g><line x1={x(hover)} x2={x(hover)} y1={0} y2={H} stroke="currentColor" strokeOpacity={0.25} />
            <circle cx={x(hover)} cy={y(closes[hover])} r={4} fill={up ? "var(--success)" : "var(--danger)"} /></g>
        )}
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-4 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1"><span className="h-0.5 w-4" style={{ background: up ? "var(--success)" : "var(--danger)" }} />Price</span>
        <span className="flex items-center gap-1"><span className="h-0.5 w-4 bg-accent" />50-day avg</span>
        <span className="flex items-center gap-1"><span className="h-0.5 w-4" style={{ background: "#d98a1c" }} />200-day avg</span>
        {hover !== null && closes[hover] !== undefined && (
          <span className="ml-auto tabular-nums text-foreground">{dates[hover]} · {cur}{closes[hover].toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
        )}
      </div>
    </div>
  );
}

const actionTone = (a: string) =>
  a.startsWith("BUY") ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
    : a === "WAIT" ? "border-amber-500/30 bg-amber-500/10 text-amber-500"
      : a === "AVOID" ? "border-red-500/30 bg-red-500/10 text-red-500"
        : "border-border bg-muted text-muted-foreground";

const money = (v: number | undefined, cur: string) =>
  v == null ? "-" : `${SYM[cur] ?? ""}${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}${SYM[cur] ? "" : ` ${cur}`}`;

export function Markets({ onToggleNav }: { onToggleNav?: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [add, setAdd] = useState("");
  const [tab, setTab] = useState<"live" | "history">("live");
  const [addError, setAddError] = useState("");
  const [analysis, setAnalysis] = useState<{ reply: string; charts: Chart[] } | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const load = () => { setLoading(true); return getJSON<{ items: Item[] }>("/api/markets/watchlist").then((d) => setItems(d.items)).finally(() => setLoading(false)); };
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, []);

  const analyze = async (what: string) => {
    if (!what.trim()) return;
    setQ(what); setAnalyzing(true); setAnalysis(null);
    try {
      setAnalysis(await getJSON(`/api/markets/analyze?q=${encodeURIComponent(what.trim())}`));
      if (tab === "history") void loadHistory();   // a fresh run from the History tab shows up in the list too
    } finally { setAnalyzing(false); }
  };

  // ---- History tab: every saved question and analysis (H)
  const [saved, setSaved] = useState<Saved[] | null>(null);
  const [hq, setHq] = useState("");
  const [openSaved, setOpenSaved] = useState<Saved | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const loadHistory = useCallback((search = "") => {
    return getJSON<{ items: Saved[] }>(`/api/markets/history?q=${encodeURIComponent(search)}`)
      .then((d) => setSaved(d.items ?? []))
      .catch(() => setSaved([]));
  }, []);
  useEffect(() => { if (tab === "history") void loadHistory(hq); }, [tab, loadHistory, hq]);
  const rerun = async (s: Saved) => {
    setBusyId(s.id);
    try {
      const d = await postJSON<{ question: string; result: { reply?: string; charts?: Chart[] } }>(`/api/markets/history/${s.id}/rerun`, {});
      setAnalysis({ reply: d.result?.reply ?? "", charts: d.result?.charts ?? [] });
      setQ(d.question || s.question || s.name);
      setTab("live");
      await loadHistory(hq);
    } finally { setBusyId(null); }
  };
  const removeSaved = async (s: Saved) => {
    await del(`/api/markets/history/${s.id}`).catch(() => {});
    setOpenSaved((cur) => (cur?.id === s.id ? null : cur));
    await loadHistory(hq);
  };
  const addItem = async () => {
    if (!add.trim()) return;
    setAddError("");
    const r = await postJSON<{ items?: Item[]; error?: string }>("/api/markets/watchlist", { add: add.trim() });
    if (r.error) setAddError(r.error); else { setItems(r.items ?? null); setAdd(""); }
  };
  const remove = async (it: Item) => {
    const r = await postJSON<{ items: Item[] }>("/api/markets/watchlist", { remove: { kind: it.kind, id: it.id } });
    setItems(r.items);
  };

  return (
    <Page title="Markets" subtitle="Live prices and a technical analyzer. Stocks via Yahoo Finance (the data OpenBB uses), PSX from the exchange, crypto from CoinGecko."
      onToggleNav={onToggleNav}
      action={<button type="button" onClick={load} title="Refresh" className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs cursor-pointer hover:bg-muted">
        <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh</button>}>
      <div role="tablist" aria-label="Markets view" className="mb-4 inline-flex rounded-xl border border-border bg-muted/40 p-0.5 text-xs font-medium">
        {([["live", "Live"], ["history", "History"]] as const).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 transition-colors cursor-pointer",
              tab === k ? "bg-background text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
            {k === "history" && <History className="h-3.5 w-3.5" />}{label}
          </button>))}
      </div>

      {tab === "live" && (<>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {items === null ? (
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-28 rounded-2xl border border-border bg-card/40 p-3.5 animate-pulse flex flex-col justify-between">
              <div className="space-y-1.5">
                <div className="h-3 w-1/2 rounded bg-muted" />
                <div className="h-2 w-1/3 rounded bg-muted" />
              </div>
              <div className="h-5 w-2/3 rounded bg-muted" />
            </div>
          ))
        ) : (
          <AnimatePresence initial={false}>
            {items.map((it, i) => {
              const up = (it.change_pct ?? 0) >= 0;
            return (
              <motion.div key={`${it.kind}:${it.id}`} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95 }} transition={{ delay: i * 0.03 }}
                className="group relative rounded-2xl border border-border bg-background transition-shadow hover:shadow-md">
                <button type="button" title={it.kind === "fx" || it.kind === "metal" ? it.name ?? it.label : `Analyze ${it.label}`}
                  disabled={it.kind === "fx" || it.kind === "metal"} onClick={() => analyze(it.label)}
                  className="w-full p-3 text-left cursor-pointer disabled:cursor-default">
                  <div className="text-xs font-semibold">{it.label}</div>
                  <div className="truncate text-[10px] text-muted-foreground">{it.name ?? it.kind.toUpperCase()}</div>
                  {it.error || it.price == null ? <div className="mt-2 text-xs text-muted-foreground">unavailable</div> : (
                    <div className="mt-1.5 text-lg font-semibold tabular-nums">
                      <AnimatedCounter value={it.price} decimals={decimalsFor(it.price)} prefix={SYM[it.currency ?? ""] ?? ""} />
                      {!SYM[it.currency ?? ""] && <span className="ml-1 text-[10px] font-normal text-muted-foreground">{it.currency}</span>}
                      {it.unit && <span className="text-[10px] font-normal text-muted-foreground">/{it.unit}</span>}
                    </div>
                  )}
                  {it.change_pct != null && (
                    <div className={`text-xs tabular-nums ${up ? "text-success" : "text-danger"}`}>{up ? "▲" : "▼"} {Math.abs(it.change_pct).toFixed(2)}%</div>
                  )}
                  <Spark data={it.spark ?? []} up={up} />
                </button>
                <button type="button" aria-label={`Remove ${it.label}`} title="Remove" onClick={() => remove(it)}
                  className="absolute right-2 top-2 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-muted group-hover:opacity-100 focus:opacity-100 cursor-pointer">
                  <X className="h-3 w-3" /></button>
              </motion.div>
            );
          })}
        </AnimatePresence>
        )}
      </div>

      <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); addItem(); }}>
        <input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="Add to watchlist: apple, OGDC, eth, usd/aed, silver…"
          aria-label="Add to watchlist" className="min-w-0 flex-1 rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent" />
        <button type="submit" disabled={!add.trim()} className="flex items-center gap-1 rounded-xl bg-accent px-3 py-2 text-xs font-medium text-white disabled:opacity-40 cursor-pointer">
          <Plus className="h-3.5 w-3.5" /> Add</button>
        {addError && <span className="text-xs text-danger">{addError}</span>}
      </form>

      <section className="mt-6 rounded-2xl border border-border bg-background p-4 sm:p-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><LineChart className="h-4 w-4 text-accent" /> Analyzer</h2>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); analyze(q); }}>
          <div className="relative min-w-0 flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Analyze a stock or coin: nvidia, TSLA, bitcoin, Lucky Cement…"
              aria-label="Analyze" className="w-full rounded-xl border border-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:border-accent" />
          </div>
          <button type="submit" disabled={!q.trim() || analyzing} className="rounded-xl bg-accent px-4 py-2 text-xs font-medium text-white disabled:opacity-40 cursor-pointer">
            {analyzing ? "Analyzing…" : "Analyze"}</button>
        </form>
        <AnimatePresence mode="wait">
          {analyzing && <motion.div key="l" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-4 h-56 animate-pulse rounded-xl bg-muted/60" />}
          {analysis && !analyzing && (
            <motion.div key="a" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-4 grid gap-4 lg:grid-cols-[3fr_2fr]">
              <div>{analysis.charts?.[0] ? <PriceChart chart={analysis.charts[0]} /> : <p className="text-sm text-muted-foreground">No price history for this one.</p>}</div>
              <Markdown text={analysis.reply} />
            </motion.div>
          )}
        </AnimatePresence>
      </section>
      </>)}

      {tab === "history" && (
        <section className="rounded-2xl border border-border bg-background p-4 sm:p-5" data-testid="markets-history">
          {openSaved ? (
            <div>
              <button type="button" onClick={() => setOpenSaved(null)} className="mb-3 flex items-center gap-1 text-xs text-accent cursor-pointer">
                <ArrowLeft className="h-3.5 w-3.5" /> All saved analyses</button>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">{openSaved.name || openSaved.symbol}</h2>
                <span className="text-xs text-muted-foreground">{openSaved.symbol}</span>
                <span className={cn("rounded-md border px-2 py-0.5 text-[11px] font-semibold", actionTone(openSaved.action))}>{openSaved.action}</span>
                <span className="ml-auto text-xs text-muted-foreground">{new Date(openSaved.ts * 1000).toLocaleString()}</span>
              </div>
              {openSaved.question && <p className="mt-1 text-xs text-muted-foreground">You asked: {openSaved.question}</p>}
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
                {([["Price then", openSaved.price], ["Buy near", openSaved.entry], ["Stop", openSaved.stop], ["Target 1", openSaved.t1], ["Target 2", openSaved.t2]] as const).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-border/70 bg-card/40 p-2"><dt className="text-muted-foreground">{k}</dt>
                    <dd className="font-semibold tabular-nums">{money(v, openSaved.currency)}</dd></div>))}
              </dl>
              {openSaved.data?.sources && (
                <p className="mt-2 text-[11px] text-muted-foreground">Data used: {openSaved.data.sources.join(", ")}
                  {openSaved.data.days ? ` · ${openSaved.data.days} days of prices` : ""}{openSaved.data.headlines ? ` · ${openSaved.data.headlines} headlines` : ""}</p>)}
              <div className="mt-4 rounded-xl border border-border/60 bg-card/30 p-3 text-sm">
                {openSaved.analysis ? <Markdown text={openSaved.analysis} /> : <p className="text-muted-foreground">The write-up was not saved for this older entry. Run it again to get a fresh one.</p>}
              </div>
              <div className="mt-3 flex gap-2">
                <button type="button" disabled={busyId === openSaved.id} onClick={() => rerun(openSaved)}
                  className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 cursor-pointer"><Play className="h-3.5 w-3.5" /> {busyId === openSaved.id ? "Running…" : "Run again"}</button>
                <button type="button" onClick={() => removeSaved(openSaved)}
                  className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-danger hover:bg-danger/10 cursor-pointer"><Trash2 className="h-3.5 w-3.5" /> Delete</button>
              </div>
            </div>
          ) : (
            <>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input value={hq} onChange={(e) => setHq(e.target.value)} placeholder="Search your saved analyses: symbol, name, question or BUY / WAIT / AVOID…"
                  aria-label="Search history" className="w-full rounded-xl border border-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:border-accent" />
              </div>
              {saved === null ? <p className="mt-4 text-sm text-muted-foreground">Loading…</p>
                : saved.length === 0 ? <p className="mt-6 text-center text-sm text-muted-foreground">{hq ? "Nothing matches that search." : "Nothing saved yet. Every analysis you ask for is kept here with its buy / sell plan."}</p>
                : (
                  <ul className="mt-3 divide-y divide-border/60">
                    {saved.map((s) => (
                      <li key={s.id} className="flex flex-wrap items-center gap-2 py-2.5">
                        <button type="button" onClick={() => setOpenSaved(s)} className="min-w-0 flex-1 text-left cursor-pointer">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold">{s.name || s.symbol}</span>
                            <span className="text-[11px] text-muted-foreground">{s.symbol}</span>
                            <span className={cn("rounded-md border px-1.5 py-0.5 text-[10px] font-semibold", actionTone(s.action))}>{s.action}</span>
                          </span>
                          <span className="block truncate text-[11px] text-muted-foreground">
                            {new Date(s.ts * 1000).toLocaleString()} · {s.question || "analysis"}{s.entry != null ? ` · buy near ${money(s.entry, s.currency)}` : ""}{s.t1 != null ? ` · target ${money(s.t1, s.currency)}` : ""}</span>
                        </button>
                        <button type="button" title="Run again" disabled={busyId === s.id} onClick={() => rerun(s)}
                          className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40 cursor-pointer"><Play className="h-3.5 w-3.5" /></button>
                        <button type="button" title="Delete" onClick={() => removeSaved(s)}
                          className="rounded-lg p-1.5 text-muted-foreground hover:bg-danger/10 hover:text-danger cursor-pointer"><Trash2 className="h-3.5 w-3.5" /></button>
                      </li>))}
                  </ul>)}
            </>
          )}
        </section>
      )}
    </Page>
  );
}
