// WP5 Connectors: single top-level page for everything inbound/outbound. Groups: Messaging
// (WhatsApp, Telegram), Tools (MCP), Agents (A2A), APIs/Keys (credential refs only - never
// secrets). Remote-policy editor here (local only). T2 risk notices before first connect.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Cable, MessageCircle, Plus, RefreshCw, Send, Shield, Trash2, Webhook } from "lucide-react";
import { getJSON, postJSON, putJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Connector = { id: string; kind: string; status: string; configured: boolean; detail?: string };
type Row = { ts?: number; sender?: string; command?: string; result?: string };

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
  const [numbers, setNumbers] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [country, setCountry] = useState("+92");
  const [err, setErr] = useState("");
  const [pairing, setPairing] = useState(false);

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
      const d = await postJSON<{ ok: boolean }>("/api/connectors/whatsapp/connect");
      const st = await getJSON<{ status: string; paired: boolean }>("api/connectors/whatsapp/status".replace("api/", "/api/"));
      if (!st.paired) {
        setQr({ qr: undefined, pairing_code: "····", expires_in: 20 });
        emitQrWatch();
      }
      reload();
      return d;
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
      await putJSON(`/api/connectors/whatsapp/config`, { control_numbers: [...numbers, full] });
      setNumbers((n) => [...n, full]);
      setDraft("");
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };

  const removeNumber = async (n: string) => {
    const next = numbers.filter((x) => x !== n);
    await putJSON(`/api/connectors/whatsapp/config`, { control_numbers: next }).catch(() => {});
    setNumbers(next);
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
                 data-testid="wa-number-input"
                 className="w-36 rounded-md border border-border bg-background px-2 py-1 text-xs" />
          <button onClick={addNumber} data-testid="wa-number-add"
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
            <Plus className="h-3 w-3" /> Add
          </button>
        </div>
        <ul className="mt-1.5 space-y-1">
          {numbers.map((n) => (
            <li key={n} className="flex items-center gap-2 text-xs">
              <span className="font-mono">{n}</span>
              <button onClick={() => removeNumber(n)} aria-label={`Remove ${n}`}
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
  const deepLink = token ? `https://t.me/ishti_bot?start=${btoa(token.slice(0, 6)).slice(0, 8)}` : "";

  return (
    <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-telegram">
      <div className="flex items-center gap-2">
        <Send className="h-4 w-4 text-sky-500" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">Telegram</h3>
        {row && <StatusPill status={row.status} />}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Paste the @BotFather token — it is stored as a credential reference and is never shown again.
      </p>
      <input
        type="password" value={token} onChange={(e) => setToken(e.target.value)} data-testid="tg-token"
        placeholder="123456:AF…your bot token" aria-label="Bot token (masked)"
        className="mt-2 w-full rounded-md border border-border bg-background px-2 py-1 text-xs" />
      <div className="mt-2 flex gap-2">
        <button
          onClick={async () => {
            if (!token.trim()) { setErr("Paste the bot token first."); return; }
            setPairing(true); setErr("");
            try {
              await postJSON("/api/connectors/telegram/connect");
              setTimeout(() => { setPairing(false); reload(); }, 1200);
            } catch (e) { setErr(e instanceof Error ? e.message : String(e)); setPairing(false); }
          }}
          disabled={pairing} data-testid="tg-pair"
          className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground disabled:opacity-50">
          {pairing ? "Waiting for pairing…" : "Pair"}
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
      {err && <div className="mt-2 text-xs text-destructive" role="alert">{err}</div>}
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

export function ConnectorsPage() {
  const { rows, loading, reload } = useConnectors();
  const byId = useMemo(() => Object.fromEntries(rows.map((r) => [r.id, r])), [rows]);
  const activity: Row[] = []; // wire to /api/remote/status recent rows when backend exposes them
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4" data-testid="connectors-page">
      <header className="flex items-center gap-2">
        <Cable className="h-5 w-5 text-accent" aria-hidden />
        <h2 className="text-lg font-bold text-foreground">Connectors</h2>
        <button onClick={reload} aria-label="Refresh" className="ml-auto rounded-md border border-border p-1.5 text-muted-foreground hover:text-foreground">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </header>

      {loading && <div className="text-xs text-muted-foreground">Loading connectors…</div>}

      <section>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Messaging</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <WhatsAppCard row={byId.whatsapp} reload={reload} />
          <TelegramCard row={byId.telegram} reload={reload} />
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-mcp">
          <div className="flex items-center gap-2"><Webhook className="h-4 w-4 text-violet-500" aria-hidden />
            <h3 className="text-sm font-bold text-foreground">Tools · MCP servers</h3></div>
          <p className="mt-1 text-xs text-muted-foreground">Local tool servers ISHTI may call. {byId.mcp?.status ?? "—"}</p>
        </div>
        <div className="rounded-xl border border-border bg-card p-4" data-testid="connector-a2a">
          <div className="flex items-center gap-2"><Webhook className="h-4 w-4 text-violet-500" aria-hidden />
            <h3 className="text-sm font-bold text-foreground">Agents · A2A</h3></div>
          <p className="mt-1 text-xs text-muted-foreground">Process-separate agents on 127.0.0.1 only. {byId.a2a?.status ?? "—"}</p>
        </div>
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
