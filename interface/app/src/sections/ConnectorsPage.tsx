// WP5 Connectors: single top-level page for everything inbound/outbound. Groups: Messaging
// (WhatsApp, Telegram), Tools (MCP), Agents (A2A), APIs/Keys (credential refs only - never
// secrets). Remote-policy editor here (local only). T2 risk notices before first connect.
// Owner update: Inputs is MERGED here (sensors + accounts + OAuth catalog in one section);
// #inputs deep links land on this page. Nothing was removed from Inputs.
import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bot, Cable, Check, CheckCircle2, ClipboardCheck, MessageCircle, Mic, MicOff, Monitor, Play, Plus, QrCode, RefreshCw, Send, Shield, Trash2, Webhook, X, XCircle } from "lucide-react";
import { getJSON, postJSON, putJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Connector = { id: string; kind: string; status: string; configured: boolean; detail?: string };
type Row = { ts?: number; sender?: string; command?: string; result?: string };
type McpServer = { name: string; command: string; extension: boolean; token_stored: boolean; live: boolean; resolved: string };

const E164 = /^\+[1-9]\d{6,14}$/;

function useConnectors() {
  const [rows, setRows] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    getJSON<{ connectors: Connector[] }>("/api/connectors")
      .then((d) => { setRows(d.connectors); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);
  return { rows, loading, reload: load };
}

/** Reload control numbers from the server's read-back (the source of truth after every save). */
function useControlNumbers(cid: "whatsapp" | "telegram", reload: () => void) {
  const [numbers, setNumbers] = useState<string[]>([]);
  const sync = useCallback(() => {
    putJSON<{ saved: boolean; control_numbers?: string[] }>(`/api/connectors/${cid}/config`, {})
      .then((d) => { if (Array.isArray(d.control_numbers)) setNumbers(d.control_numbers); })
      .catch(() => {});
  }, [cid]);
  useEffect(() => { sync(); const t = setInterval(sync, 8000); return () => clearInterval(t); }, [sync]);
  const save = useCallback(async (next: string[]) => {
    const d = await putJSON<{ saved: boolean; control_numbers?: string[] }>(`/api/connectors/${cid}/config`, { control_numbers: next });
    // Trust the server read-back, not the optimistic list: what the backend stored is what shows.
    setNumbers(Array.isArray(d.control_numbers) ? d.control_numbers : next);
    reload();
  }, [cid, reload]);
  return { numbers, save, sync };
}

function StatusPill({ status }: { status: string }) {
  const tone = status === "connected" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300"
    : status === "connecting" ? "bg-amber-500/15 text-amber-600 dark:text-amber-300"
    : "bg-muted text-muted-foreground";
  return <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", tone)} data-testid={`connector-status-${status}`}>{status}</span>;
}

function WhatsAppCard({ row, reload }: { row?: Connector; reload: () => void }) {
  const [qr, setQr] = useState<{ qr?: string; pairing_code?: string; expires_in?: number } | null>(null);
  const [riskAcked, setRiskAcked] = useState(false);
  const [showRisk, setShowRisk] = useState(false);
  const [draft, setDraft] = useState("");
  const [country, setCountry] = useState("+92");
  const [err, setErr] = useState("");
  const [pairing, setPairing] = useState(false);
  const { numbers, save } = useControlNumbers("whatsapp", reload);

  useEffect(() => {
    // pairing challenge: poll status while connecting; the backend raises connector_qr/status
    if (row?.status !== "connecting") return;
    const t = setInterval(() => {
      getJSON<{ status: string; paired: boolean; detail?: string }>(`/api/connectors/whatsapp/status`)
        .then((s) => { if (s.status === "connected") { setPairing(false); setQr(null); reload(); } })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [row?.status, reload]);

  const connect = async () => {
    if (!riskAcked) { setShowRisk(true); return; }
    setErr("");
    setPairing(true);
    try {
      await postJSON<{ ok: boolean }>("/api/connectors/whatsapp/connect");
      const st = await getJSON<{ status: string; paired: boolean }>("/api/connectors/whatsapp/status");
      if (!st.paired) {
        setQr({ qr: undefined, pairing_code: "····", expires_in: 20 });
        emitQrWatch();
      }
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setPairing(false); }
  };

  // connector_qr events (auto-refresh on expiry) arrive through the shared store sink
  const emitQrWatch = () => {
    window.addEventListener("ishti-connector-qr", ((e: CustomEvent<{ data: { qr?: string; pairing_code?: string; expires_in?: number } }>) => {
      setQr(e.detail.data);
    }) as EventListener, { once: false });
  };

  const addNumber = async () => {
    setErr("");
    const full = draft.startsWith("+") ? draft.trim() : country + draft.replace(/^0+/, "");
    if (!E164.test(full)) { setErr("Use full international format, e.g. +923001234567"); return; }
    if (numbers.includes(full)) { setErr("That number is already in the list."); return; }
    try {
      await save([...numbers, full]);   // server read-back updates the list; nothing to clear locally
      setDraft("");
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const removeNumber = async (n: string) => {
    try { await save(numbers.filter((x) => x !== n)); } catch {}
  };

  return (
    <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-whatsapp">
      <div className="flex items-center gap-2">
        <MessageCircle className="h-4 w-4 text-emerald-500" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">WhatsApp</h3>
        {row && <StatusPill status={row.status} />}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Link ISHTI's own number, then control it from your control number — or from the linked
        number's self-chat when no control number is set.
      </p>

      {showRisk && !riskAcked && (
        <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs" data-testid="wa-risk">
          Automating a WhatsApp account carries a real account-restriction/ban risk. ISHTI sends
          human-paced messages only, and only on your command.
          <div className="mt-1.5 flex gap-2">
            <button data-testid="wa-risk-ack" onClick={() => { setRiskAcked(true); setShowRisk(false); }}
                    className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-accent-foreground">
              I understand — continue
            </button>
            <button onClick={() => setShowRisk(false)} className="rounded-md border border-border px-2 py-1 text-xs">Cancel</button>
          </div>
        </div>
      )}

      <div className="mt-2 flex gap-2">
        <button onClick={connect} disabled={pairing} data-testid="wa-connect"
                className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground disabled:opacity-50">
          {row?.status === "connected" ? "Re-link" : "Link ISHTI's number"}
        </button>
        {row?.status === "connected" && (
          <>
            <button onClick={async () => { await postJSON("/api/connectors/whatsapp/disconnect"); reload(); }}
                    className="rounded-md border border-border px-2.5 py-1 text-xs">Disconnect</button>
            <button onClick={async () => { await postJSON("/api/connectors/whatsapp/test"); }}
                    className="rounded-md border border-border px-2.5 py-1 text-xs">Test (dry-run)</button>
          </>
        )}
      </div>

      {qr && (
        <div className="mt-3 rounded-lg border border-border p-3" data-testid="wa-qr">
          <div className="text-xs font-medium text-foreground">Pairing challenge</div>
          <div className="mt-1 font-mono text-2xl tracking-widest text-accent">{qr.pairing_code || "····"}</div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Type this code from the linked number's self-chat to confirm pairing. QR refreshes automatically on expiry{typeof qr.expires_in === "number" ? ` (${qr.expires_in}s)` : ""}.
          </p>
        </div>
      )}
      {err && <div className="mt-2 text-xs text-destructive" role="alert">{err}</div>}

      <div className="mt-3 border-t border-border pt-2.5">
        <div className="text-xs font-semibold text-foreground">Control numbers</div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          E.164 with country code. Commands from these numbers are accepted. None set = use the linked number's self-chat.
        </p>
        <div className="mt-1.5 flex gap-1.5">
          <select value={country} onChange={(e) => setCountry(e.target.value)} aria-label="Country code"
                  className="rounded-md border border-border bg-background px-1.5 py-1 text-xs">
            <option value="+92">+92 PK</option><option value="+1">+1 US</option>
            <option value="+44">+44 UK</option><option value="+971">+971 AE</option>
            <option value="+966">+966 SA</option>
          </select>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="3001234567"
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void addNumber(); } }}
                 data-testid="wa-number-input"
                 className="w-36 rounded-md border border-border bg-background px-2 py-1 text-xs" />
          <button onClick={() => void addNumber()} data-testid="wa-number-add"
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
            <Plus className="h-3 w-3" /> Add
          </button>
        </div>
        <ul className="mt-1.5 space-y-1" data-testid="wa-numbers">
          {numbers.map((n) => (
            <li key={n} className="flex items-center gap-2 text-xs">
              <span className="font-mono">{n}</span>
              <button onClick={() => void removeNumber(n)} aria-label={`Remove ${n}`}
                      className="text-muted-foreground hover:text-destructive"><Trash2 className="h-3 w-3" /></button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function TelegramCard({ row, reload }: { row?: Connector; reload: () => void }) {
  const [token, setToken] = useState("");
  const [pairing, setPairing] = useState(false);
  const [err, setErr] = useState("");
  const [okMsg, setOkMsg] = useState("");
  const { numbers, save } = useControlNumbers("telegram", reload);
  const [idDraft, setIdDraft] = useState("");
  const deepLink = token ? `https://t.me/ishti_bot?start=${btoa(token.slice(0, 6)).slice(0, 8)}` : "";

  const pair = async () => {
    if (!token.trim()) { setErr("Paste the bot token first."); return; }
    setPairing(true); setErr(""); setOkMsg("");
    try {
      // the token itself goes to the DPAPI-backed endpoint; the policy save below is only the allowlist
      await postJSON("/api/remote/telegram/token", { token: token.trim() });
      await postJSON("/api/connectors/telegram/connect");
      setOkMsg("Token stored. Now send /pair to your bot from Telegram (code shows in ISHTI's desktop settings).");
      setToken("");
      setTimeout(() => { setPairing(false); reload(); }, 1200);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e)); setPairing(false);
    }
  };

  const addId = async () => {
    setErr("");
    const id = idDraft.trim();
    if (!/^\d{3,20}$/.test(id)) { setErr("Telegram user IDs are numeric (get yours from @userinfobot)."); return; }
    try { await save([...numbers, id]); setIdDraft(""); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-telegram">
      <div className="flex items-center gap-2">
        <Send className="h-4 w-4 text-sky-500" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">Telegram</h3>
        {row && <StatusPill status={row.status} />}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Paste the @BotFather token — it is stored in Windows Credential Manager and is never shown again.
      </p>
      <input
        type="password" value={token} onChange={(e) => setToken(e.target.value)} data-testid="tg-token"
        placeholder="123456:AF…your bot token" aria-label="Bot token (masked)"
        className="mt-2 w-full rounded-md border border-border bg-background px-2 py-1 text-xs" />
      <div className="mt-2 flex gap-2">
        <button onClick={() => void pair()} disabled={pairing} data-testid="tg-pair"
          className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground disabled:opacity-50">
          {pairing ? "Storing token…" : "Save token & pair"}
        </button>
        {row?.status === "connected" && (
          <button onClick={async () => { await postJSON("/api/connectors/telegram/disconnect"); reload(); }}
                  className="rounded-md border border-border px-2.5 py-1 text-xs">Disconnect</button>
        )}
      </div>
      {deepLink && (
        <p className="mt-1.5 break-all text-[11px] text-muted-foreground">
          Or open <span className="font-mono">{deepLink}</span> — pairing confirms the owner's account.
        </p>
      )}
      {okMsg && <p className="mt-1.5 text-[11px] text-emerald-500" role="status">{okMsg}</p>}
      {err && <div className="mt-2 text-xs text-destructive" role="alert">{err}</div>}

      <div className="mt-3 border-t border-border pt-2.5">
        <div className="text-xs font-semibold text-foreground">Allowed Telegram IDs</div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Numeric user IDs (from @userinfobot) allowed to command ISHTI.</p>
        <div className="mt-1.5 flex gap-1.5">
          <input value={idDraft} onChange={(e) => setIdDraft(e.target.value)} placeholder="123456789"
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void addId(); } }}
                 data-testid="tg-id-input" aria-label="Telegram user ID"
                 className="w-36 rounded-md border border-border bg-background px-2 py-1 text-xs" />
          <button onClick={() => void addId()} data-testid="tg-id-add"
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
            <Plus className="h-3 w-3" /> Add
          </button>
        </div>
        <ul className="mt-1.5 space-y-1" data-testid="tg-ids">
          {numbers.map((n) => (
            <li key={n} className="flex items-center gap-2 text-xs">
              <span className="font-mono">{n}</span>
              <button onClick={() => void save(numbers.filter((x) => x !== n))} aria-label={`Remove ${n}`}
                      className="text-muted-foreground hover:text-destructive"><Trash2 className="h-3 w-3" /></button>
            </li>
          ))}
        </ul>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        User-account mode (reading your own Saved Messages) is available but OFF — it risks ToS action.
      </p>
    </div>
  );
}

function PolicyEditor() {
  const [policy, setPolicy] = useState({
    quiet_hours: "23:00-07:00", max_per_minute: 20, t2_stepup: true,
    sensitive_output: { client_data: "redact", finance: "redact" },
  });
  const [saved, setSaved] = useState(false);
  return (
    <div className="rounded-xl border border-border bg-card p-4" data-testid="remote-policy">
      <div className="flex items-center gap-2">
        <Shield className="h-4 w-4 text-accent" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">Remote policy (local-only)</h3>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        What remote commands may do. Editable only on this PC — never via a remote message.
      </p>
      <label className="mt-2 flex items-center gap-2 text-xs">
        <input type="checkbox" checked={policy.t2_stepup} data-testid="policy-t2"
               onChange={(e) => setPolicy({ ...policy, t2_stepup: e.target.checked })} />
        Require step-up code for risky (T2) remote actions
      </label>
      <label className="mt-1.5 flex items-center gap-2 text-xs">
        Quiet hours
        <input value={policy.quiet_hours} onChange={(e) => setPolicy({ ...policy, quiet_hours: e.target.value })}
               className="w-24 rounded-md border border-border bg-background px-1.5 py-0.5 text-xs" />
      </label>
      <button
        onClick={() => { void putJSON("/api/connectors/whatsapp/config", { policy }); setSaved(true); setTimeout(() => setSaved(false), 1500); }}
        className="mt-2 rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground" data-testid="policy-save">
        {saved ? "Saved ✓" : "Save policy"}
      </button>
    </div>
  );
}

function McpCard() {
  const [st, setSt] = useState<{ servers: McpServer[]; error?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    getJSON<{ servers: McpServer[]; error?: string }>("/api/mcp/status").then(setSt).catch(() => setSt(null));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const reload = async () => {
    setBusy(true); setMsg("");
    try {
      const d = await postJSON<{ ok: boolean; stopped: string[] }>("/api/mcp/reload");
      setMsg(d.stopped.length ? `Restarted ${d.stopped.join(", ")} on next use.` : "Hub cleared — servers start fresh on next use.");
      load();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-mcp">
      <div className="flex items-center gap-2">
        <Webhook className="h-4 w-4 text-violet-500" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">Tools · MCP servers</h3>
        <button onClick={reload} disabled={busy} data-testid="mcp-reload" title="Restart MCP sessions (reads new tokens/config)"
                className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground disabled:opacity-50 cursor-pointer">
          <RefreshCw className={cn("h-3 w-3", busy && "animate-spin")} /> Reload
        </button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">Local tool servers ISHTI may call (Playwright browser control).</p>
      {!st && <p className="mt-2 text-xs text-muted-foreground">Reading MCP hub state…</p>}
      {st?.error && <p className="mt-2 text-xs text-destructive">{st.error}</p>}
      {st && st.servers.length === 0 && <p className="mt-2 text-xs text-muted-foreground">No MCP servers configured (ishti/config.json → mcp_servers).</p>}
      <ul className="mt-2 space-y-1.5">
        {(st?.servers || []).map((s) => (
          <li key={s.name} className="rounded-lg border border-border/70 bg-background/60 px-2.5 py-1.5 text-xs" data-testid={`mcp-server-${s.name}`}>
            <div className="flex items-center gap-2">
              {s.token_stored ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden /> : <XCircle className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />}
              <span className="font-semibold text-foreground">{s.name}</span>
              <span className={cn("ml-auto rounded-full px-1.5 py-0.5 text-[10px]", s.live ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300" : "bg-muted text-muted-foreground")}>
                {s.live ? "live" : "starts on use"}
              </span>
            </div>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {s.extension ? (s.token_stored ? "Extension token stored — will connect without a popup." : "Missing extension token: paste it in Settings → Playwright Extension token, or reload after adding.")
                : `Isolated profile: ${s.resolved || s.command}`}
            </p>
          </li>
        ))}
      </ul>
      {msg && <p className="mt-2 text-[11px] text-accent" role="status">{msg}</p>}
    </div>
  );
}

function A2aCard() {
  const [agents, setAgents] = useState<{ id: string; state: string; goal?: string }[]>([]);
  const [answer, setAnswer] = useState("");
  const [pending, setPending] = useState<{ agent_id: string; question: string } | null>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(() => {
    getJSON<{ agents: { id: string; state: string; goal?: string }[] }>("/api/agents").then((d) => {
      setAgents(d.agents || []);
      const asking = (d.agents || []).find((a) => a.state === "input_required");
      setPending(asking ? { agent_id: asking.id, question: (asking as any).detail || "needs your input" } : null);
    }).catch(() => {});
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);
  const submit = async () => {
    if (!pending || !answer.trim()) return;
    try {
      await postJSON("/api/agents/events", { type: "input_response", agent_id: pending.agent_id, answer: answer.trim() });
      setMsg(`Answer delivered to ${pending.agent_id}.`);
      setAnswer(""); setPending(null); load();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-a2a">
      <div className="flex items-center gap-2">
        <Bot className="h-4 w-4 text-violet-500" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">Agents · A2A</h3>
        <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">127.0.0.1 only</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">Process-separate specialists with typed envelopes; input requests land here and in chat.</p>
      <ul className="mt-2 space-y-1" data-testid="a2a-agents">
        {agents.slice(0, 6).map((a) => (
          <li key={a.id} className="flex items-center gap-2 text-xs">
            <Play className={cn("h-3 w-3", a.state === "running" ? "text-amber-500" : "text-muted-foreground/50")} aria-hidden />
            <span className="font-mono font-medium text-foreground">{a.id}</span>
            <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">{a.state}</span>
          </li>
        ))}
        {!agents.length && <li className="text-xs text-muted-foreground">Registry loads when the orchestrator first runs.</li>}
      </ul>
      {pending && (
        <div className="mt-2 rounded-lg border border-sky-500/40 bg-sky-500/10 p-2.5" data-testid="a2a-input-required">
          <div className="text-xs font-semibold text-foreground">{pending.agent_id} needs your input</div>
          <p className="mt-0.5 text-[11px] text-foreground">{pending.question}</p>
          <div className="mt-1.5 flex gap-1.5">
            <input value={answer} onChange={(e) => setAnswer(e.target.value)} data-testid="a2a-answer"
                   placeholder="Your answer…" className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs" />
            <button onClick={() => void submit()} className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground">Answer</button>
          </div>
        </div>
      )}
      {msg && <p className="mt-2 text-[11px] text-accent" role="status">{msg}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- Inputs (merged section)
// The old Inputs page, now a section of Connectors (owner request: ONE place for everything
// inbound). Same endpoints, same live controls - mic toggle, WhatsApp QR modal, clipboard/screen
// tests - so nothing was removed; only the nav entry disappeared.
type InputChannel = {
  id: string; name: string; icon: string; kind: string; detail: string;
  state: string; source: string; account?: string;
};
type InputsPayload = { channels: InputChannel[]; accounts: { service: string; connected: boolean }[]; note?: string };

const IN_STATE_TONE: Record<string, string> = {
  on: "bg-emerald-500/10 text-emerald-500 border border-emerald-500/20",
  waiting: "bg-amber-500/10 text-amber-500 border border-amber-500/20",
  ready: "bg-sky-500/10 text-sky-500 border border-sky-500/20",
  off: "bg-muted text-muted-foreground border border-border",
};
const IN_STATE_LABEL: Record<string, string> = { on: "active", waiting: "waiting", ready: "ready", off: "off" };
const IN_KIND_LABEL: Record<string, string> = {
  always_on: "always on", toggle: "switch", setting: "setting", status: "live status",
  opt_in: "on request", account: "connected account",
};

function InputsPanel() {
  const [data, setData] = useState<InputsPayload | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [micLoading, setMicLoading] = useState(false);
  const [qrModalOpen, setQrModalOpen] = useState(false);
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState("");
  const [qrData, setQrData] = useState<{
    connected: boolean; ascii_qr: string; raw_qr: string | null; me: string;
    running: boolean; qr_image?: string | null; lastError?: string | null;
  } | null>(null);
  const [clipboardPreview, setClipboardPreview] = useState<string | null>(null);
  const [screenNotice, setScreenNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    getJSON<InputsPayload>("/api/inputs")
      .then(setData)
      .catch((e: Error) => setError(e?.message || "could not reach /api/inputs"));
  }, []);
  useEffect(load, [load, tick]);
  useEffect(() => { const t = setInterval(() => setTick((n) => n + 1), 15000); return () => clearInterval(t); }, []);

  const openQrModal = async (e: React.MouseEvent) => { e.stopPropagation(); setQrData(null); setQrError(""); setQrModalOpen(true); };

  useEffect(() => {
    if (!qrModalOpen) return;
    let active = true;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const res = await getJSON<{ connected: boolean; ascii_qr: string; raw_qr: string | null; me: string; running: boolean; qr_image?: string | null }>("/api/inputs/wa-qr");
        if (active) { setQrData(res); setQrError(""); if (res.connected) setTick((n) => n + 1); }
      } catch (error) {
        if (active) setQrError(error instanceof Error ? error.message : "Could not read WhatsApp connection state");
      } finally { busy = false; if (active) setQrLoading(false); }
    };
    setQrLoading(true);
    void postJSON("/api/inputs/wa-start", {}).then(poll).catch((error: unknown) => {
      if (active) { setQrError(error instanceof Error ? error.message : "Could not start the WhatsApp bridge"); setQrLoading(false); }
    });
    const interval = setInterval(poll, 3000);
    return () => { active = false; clearInterval(interval); };
  }, [qrModalOpen]);

  const handleToggleMic = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setMicLoading(true);
    try { await postJSON("/api/mic/toggle", {}); setTick((n) => n + 1); }
    catch (err: any) { setError(err?.message || "Failed to toggle microphone"); }
    finally { setMicLoading(false); }
  };

  const handleTestClipboard = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try { const res = await getJSON<{ ok: boolean; preview: string }>("/api/inputs/clipboard"); setClipboardPreview(res.preview); }
    catch { setClipboardPreview("Clipboard access ready"); }
  };

  const handleTestScreen = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try { const res = await getJSON<{ ok: boolean; detail: string }>("/api/inputs/screen"); setScreenNotice(res.detail); }
    catch { setScreenNotice("Screen perception ready"); }
  };

  const channels = data?.channels ?? [];
  const accounts = channels.filter((c) => c.kind === "account");
  const devices = channels.filter((c) => c.kind !== "account");
  const live = devices.filter((c) => c.state === "on").length;

  const InCard = ({ c }: { c: InputChannel }) => {
    const isMic = c.id === "mic";
    const isWa = c.id === "whatsapp";
    const isClip = c.id === "clipboard";
    const isScreen = c.id === "screen";
    return (
      <div className="w-full text-left rounded-2xl border border-border bg-card/60 p-4 shadow-xs transition-all hover:border-border/90 relative">
        <div className="flex items-start gap-3.5">
          <div className="relative mt-1">
            <span className={cn("block h-3 w-3 rounded-full transition-all",
              c.state === "on" ? "bg-emerald-500 ring-4 ring-emerald-500/20"
                : c.state === "waiting" ? "bg-amber-500 ring-4 ring-amber-500/20 animate-pulse"
                : c.state === "ready" ? "bg-sky-500" : "bg-muted-foreground/40")} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-foreground">{c.name}</span>
                <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase", IN_STATE_TONE[c.state] || IN_STATE_TONE.off)}>
                  {IN_STATE_LABEL[c.state] || c.state}
                </span>
                <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">{IN_KIND_LABEL[c.kind] || c.kind}</span>
              </div>
              <div className="flex items-center gap-2">
                {isMic && (
                  <button type="button" disabled={micLoading} onClick={handleToggleMic}
                    className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1 text-xs font-semibold shadow-xs transition-all cursor-pointer",
                      c.state === "on" ? "bg-red-500/10 text-red-500 border border-red-500/30 hover:bg-red-500/20" : "bg-emerald-600 text-white hover:bg-emerald-700")}>
                    {c.state === "on" ? (<><MicOff className="h-3.5 w-3.5" /><span>Mute Mic</span></>) : (<><Mic className="h-3.5 w-3.5" /><span>Turn ON</span></>)}
                  </button>
                )}
                {isWa && (c.state === "waiting" || c.state === "off") && (
                  <button type="button" onClick={openQrModal}
                    className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white shadow-xs hover:bg-emerald-700 transition-all cursor-pointer animate-pulse">
                    <QrCode className="h-3.5 w-3.5" /><span>Scan QR Code</span>
                  </button>
                )}
                {isClip && (
                  <button type="button" onClick={handleTestClipboard}
                    className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] font-medium text-foreground hover:bg-muted/50 cursor-pointer">
                    <ClipboardCheck className="h-3 w-3" /><span>Test Read</span>
                  </button>
                )}
                {isScreen && (
                  <button type="button" onClick={handleTestScreen}
                    className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-[11px] font-medium text-foreground hover:bg-muted/50 cursor-pointer">
                    <Monitor className="h-3 w-3" /><span>Test Perception</span>
                  </button>
                )}
              </div>
            </div>
            <p className="mt-1 text-xs text-muted-foreground leading-relaxed">{c.detail}</p>
            {c.account && <p className="mt-1 font-mono text-[11px] font-medium text-emerald-600 dark:text-emerald-400">{c.account}</p>}
            {isClip && clipboardPreview && (
              <div className="mt-2.5 rounded-xl border border-border/80 bg-muted/40 p-2 text-xs font-mono text-foreground">
                <span className="text-[10px] uppercase font-bold text-muted-foreground mr-1.5">Clipboard Content:</span>{clipboardPreview}
              </div>
            )}
            {isScreen && screenNotice && (
              <div className="mt-2.5 rounded-xl border border-border/80 bg-muted/40 p-2 text-xs font-mono text-emerald-500">
                <span className="text-[10px] uppercase font-bold text-muted-foreground mr-1.5">Screen Status:</span>{screenNotice}
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

  return (
    <section data-testid="inputs-panel">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Inputs · sensors &amp; devices</h3>
        <span className="text-[11px] font-mono text-emerald-500 font-semibold">{live} live now</span>
      </div>
      {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3.5 text-xs text-red-400">{error}</div>}
      {!data && !error && <div className="text-xs text-muted-foreground">Reading active input channels…</div>}
      {data && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 items-start">
            {devices.map((c) => <InCard key={c.id} c={c} />)}
          </div>
          {accounts.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-3 items-start">
              {accounts.map((c) => <InCard key={c.id} c={c} />)}
            </div>
          )}
          {data.note && <p className="text-[11px] text-muted-foreground font-mono">{data.note}</p>}
        </div>
      )}

      {/* WHATSAPP QR CODE SCANNER MODAL (unchanged from the Inputs page) */}
      <AnimatePresence>
        {qrModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md">
            <motion.div initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.95, opacity: 0 }}
              className="relative w-full max-w-md rounded-3xl border border-border bg-card p-6 shadow-2xl overflow-hidden">
              <button type="button" onClick={() => setQrModalOpen(false)}
                className="absolute right-4 top-4 rounded-full p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground cursor-pointer transition-colors" title="Close">
                <X className="h-4 w-4" />
              </button>
              <div className="flex items-center gap-3 mb-5">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-emerald-600/15 text-emerald-500 font-bold border border-emerald-500/25"><QrCode className="h-5 w-5" /></div>
                <div>
                  <h3 className="text-base font-bold text-foreground">Link WhatsApp Account</h3>
                  <p className="text-xs text-muted-foreground">Scan with WhatsApp on your phone to link locally</p>
                </div>
              </div>
              {qrError && <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">{qrError}</div>}
              {qrLoading ? (
                <div className="py-16 text-center space-y-3">
                  <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent" />
                  <div className="text-sm font-medium text-foreground">Contacting WhatsApp bridge…</div>
                  <div className="text-xs text-muted-foreground">Generating encrypted pairing key</div>
                </div>
              ) : qrData?.connected ? (
                <div className="py-8 text-center space-y-3">
                  <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40"><Check className="h-7 w-7" /></div>
                  <h4 className="text-sm font-bold text-foreground">WhatsApp is Connected!</h4>
                  <p className="text-xs text-muted-foreground font-mono">{qrData.me || "Device Authenticated"}</p>
                  <p className="text-xs text-emerald-500 font-medium">Messages stream live 100% locally through your machine.</p>
                  <div className="pt-2">
                    <button type="button" onClick={() => setQrModalOpen(false)}
                      className="rounded-xl bg-emerald-600 px-6 py-2 text-xs font-semibold text-white hover:bg-emerald-700 transition-colors cursor-pointer shadow-xs">Done</button>
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="relative mx-auto flex items-center justify-center rounded-2xl border-2 border-border bg-white p-5 shadow-sm max-w-[300px]">
                    <div className="absolute top-2 left-2 h-4 w-4 border-t-2 border-l-2 border-emerald-600 rounded-tl-sm pointer-events-none" />
                    <div className="absolute top-2 right-2 h-4 w-4 border-t-2 border-r-2 border-emerald-600 rounded-tr-sm pointer-events-none" />
                    <div className="absolute bottom-2 left-2 h-4 w-4 border-b-2 border-l-2 border-emerald-600 rounded-bl-sm pointer-events-none" />
                    <div className="absolute bottom-2 right-2 h-4 w-4 border-b-2 border-r-2 border-emerald-600 rounded-br-sm pointer-events-none" />
                    {qrData?.qr_image ? (
                      <img src={qrData.qr_image} alt="WhatsApp linking QR code"
                        className="block w-full max-w-[260px] h-auto aspect-square select-none [image-rendering:pixelated]"
                        style={{ imageRendering: "pixelated" }} />
                    ) : (
                      <div className="py-14 text-center space-y-2">
                        <div className="mx-auto h-7 w-7 animate-spin rounded-full border-2 border-emerald-600 border-t-transparent" />
                        <div className="text-xs font-medium text-neutral-800">{qrData?.running ? "Generating QR code from WhatsApp bridge…" : "Starting WhatsApp local bridge…"}</div>
                        <div className="text-[11px] text-neutral-500">Takes 2-5 seconds on first start</div>
                      </div>
                    )}
                  </div>
                  <div className="space-y-2 rounded-2xl border border-border/80 bg-muted/40 p-3.5 text-xs text-muted-foreground">
                    <div className="font-semibold text-foreground"><span>How to link:</span></div>
                    <ol className="space-y-1.5 pl-1 leading-relaxed text-[12px]">
                      <li className="flex items-start gap-2"><span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">1</span><span>Open <strong>WhatsApp</strong> on your mobile phone</span></li>
                      <li className="flex items-start gap-2"><span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">2</span><span>Tap <strong>Menu</strong> or <strong>Settings</strong> &gt; <strong>Linked Devices</strong></span></li>
                      <li className="flex items-start gap-2"><span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] font-bold text-emerald-600 dark:text-emerald-400">3</span><span>Tap <strong>Link a Device</strong> and point your camera at this QR code</span></li>
                    </ol>
                  </div>
                  <div className="flex items-center justify-between gap-3 pt-1">
                    <button type="button" onClick={async () => {
                      setQrLoading(true);
                      try {
                        const res = await getJSON<{ connected: boolean; ascii_qr: string; raw_qr: string | null; me: string; running: boolean; qr_image?: string | null }>("/api/inputs/wa-qr");
                        setQrData(res);
                      } catch (error) { setQrError(error instanceof Error ? error.message : "Could not refresh WhatsApp connection state"); } finally { setQrLoading(false); }
                    }} className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted/50 cursor-pointer shadow-xs">
                      <RefreshCw className="h-3.5 w-3.5" /><span>Refresh QR</span>
                    </button>
                    <button type="button" onClick={() => setQrModalOpen(false)}
                      className="rounded-xl bg-emerald-600 px-5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 transition-colors cursor-pointer shadow-xs">Done</button>
                  </div>
                </div>
              )}
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </section>
  );
}

export function ConnectorsPage() {
  const { rows, loading, reload } = useConnectors();
  const byId = useMemo(() => Object.fromEntries(rows.map((r) => [r.id, r])), [rows]);
  const activity: Row[] = []; // wire to /api/remote/status recent rows when backend exposes them
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="connectors-page">
      <header className="flex items-center gap-2">
        <Cable className="h-5 w-5 text-accent" aria-hidden />
        <h2 className="text-lg font-bold text-foreground">Connectors</h2>
        <span className="text-[11px] text-muted-foreground">Inputs + connectors in one place: sensors, messaging, tools, agents.</span>
        <button onClick={reload} aria-label="Refresh" className="ml-auto rounded-md border border-border p-1.5 text-muted-foreground hover:text-foreground">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </header>

      {loading && <div className="text-xs text-muted-foreground">Loading connectors…</div>}

      {/* --- Inputs (merged): live sensors & accounts from /api/inputs (unchanged features) --- */}
      <InputsPanel />

      <section>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Messaging</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <WhatsAppCard row={byId.whatsapp} reload={reload} />
          <TelegramCard row={byId.telegram} reload={reload} />
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        <McpCard />
        <A2aCard />
      </section>

      <section className="rounded-xl border border-border bg-card p-4" data-testid="connector-keys">
        <h3 className="text-sm font-bold text-foreground">APIs &amp; keys</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Credential references only — secrets are stored by ISHTI and are never displayed here.
        </p>
      </section>

      <PolicyEditor />

      <section className="rounded-xl border border-border bg-card p-4">
        <h3 className="text-sm font-bold text-foreground">Recent activity</h3>
        {activity.length === 0 && <p className="mt-1 text-xs text-muted-foreground">Nothing yet.</p>}
      </section>
    </div>
  );
}
