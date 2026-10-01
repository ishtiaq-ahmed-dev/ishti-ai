import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, del } from "@/lib/api";

type RemoteStatus = {
  telegram: {
    enabled: boolean;
    token_stored: boolean;
    paired_ids: number[];
    running: boolean;
    poller: { running: boolean; offset: number | null; updates_seen: number; last_error: string } | null;
  };
  whatsapp: { enabled: boolean; risk_ack: boolean; running: boolean; last_error: string };
  pin_set: boolean;
  locked: boolean;
  lockout_s: number;
  approvals_pending: number;
  privacy_notice: string;
  recent: { ts: number; action: string; detail: string; channel: string }[];
};

const Card = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => (
  <div
    className={`rounded-2xl border border-border bg-card/60 p-5 shadow-xs ${className}`}
  >
    {children}
  </div>
);

function Toggle({
  on,
  onChange,
  disabled,
}: {
  on: boolean;
  onChange: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      disabled={disabled}
      className={`relative h-6 w-11 rounded-full transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
        on ? "bg-accent" : "bg-muted"
      }`}
      aria-pressed={on}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-background shadow transition-all ${
          on ? "left-[22px]" : "left-0.5"
        }`}
      />
    </button>
  );
}

export function RemoteControl() {
  const [st, setSt] = useState<RemoteStatus | null>(null);
  const [pairCode, setPairCode] = useState("");
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPolicy, setShowPolicy] = useState(false);
  const [policyText, setPolicyText] = useState("");

  const refresh = useCallback(() => {
    getJSON<RemoteStatus>("/api/remote/status").then(setSt).catch(() => setSt(null));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const say = (m: string) => {
    setMsg(m);
    setTimeout(() => setMsg(""), 4000);
  };

  const storeToken = async () => {
    setBusy(true);
    try {
      await postJSON("/api/remote/telegram/token", { token: token.trim() });
      setToken("");
      say("Bot token stored (Windows Credential Manager).");
    } catch {
      say("That does not look like a Telegram bot token (expected digits:hash from @BotFather).");
    }
    setBusy(false);
    refresh();
  };

  const enable = async (channel: "telegram" | "whatsapp", enabled: boolean) => {
    setBusy(true);
    try {
      await postJSON("/api/remote/enable", { channel, enabled });
    } catch (e) {
      say("Cannot enable yet - store the bot token first.");
    }
    setBusy(false);
    refresh();
  };

  const startPairing = async () => {
    setBusy(true);
    try {
      const r = await postJSON<{ code: string; instructions: string }>("/api/remote/pair/start");
      setPairCode(r.code);
      say(r.instructions);
    } catch {
      say("Pairing could not start.");
    }
    setBusy(false);
    refresh();
  };

  const revoke = async () => {
    setBusy(true);
    try {
      await postJSON("/api/remote/revoke");
      say("All sessions revoked.");
    } catch {
      say("Revoke failed.");
    }
    setBusy(false);
    refresh();
  };

  const savePin = async () => {
    const pin = prompt("New PIN (4-12 characters):");
    if (!pin) return;
    try {
      await postJSON("/api/remote/pin", { pin });
      say("PIN saved (stored as a salted hash).");
    } catch {
      say("PIN must be 4-12 characters.");
    }
    refresh();
  };

  const clearPin = async () => {
    await del("/api/remote/pin").catch(() => undefined);
    say("PIN removed.");
    refresh();
  };

  const testSend = async () => {
    try {
      await postJSON("/api/remote/test");
      say("Test message sent to the paired chat.");
    } catch {
      say("Test failed - is the channel enabled?");
    }
    refresh();
  };

  const togglePolicy = async () => {
    if (showPolicy) {
      setShowPolicy(false);
      return;
    }
    try {
      const p = await getJSON<Record<string, unknown>>("/api/remote/policy");
      setPolicyText(JSON.stringify(p, null, 2));
      setShowPolicy(true);
    } catch {
      say("Policy could not be read.");
    }
  };

  const t = st?.telegram;
  const w = st?.whatsapp;

  return (
    <Card className="mb-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Remote control</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Control ISHTI from your phone. Everything runs through the same skills, gates and audit
            log as on the desktop. {st?.privacy_notice}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
            st?.telegram.running || st?.whatsapp.running
              ? "bg-emerald-500/15 text-emerald-600"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {st?.telegram.running ? "Telegram live" : st?.whatsapp.running ? "WhatsApp live" : "Off"}
        </span>
      </div>

      <div className="mt-4 space-y-3">
        {/* Telegram */}
        <div className="rounded-xl border border-border/60 p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-foreground">Telegram</p>
              <p className="text-[11px] text-muted-foreground">
                {t?.token_stored
                  ? `${t.paired_ids.length} account(s) paired · poller ${t.running ? "running" : "stopped"}`
                  : "Create a bot with @BotFather, paste its token here"}
              </p>
            </div>
            <Toggle
              on={!!t?.enabled}
              disabled={busy || (!!t?.enabled === false && !t?.token_stored)}
              onChange={() => enable("telegram", !t?.enabled)}
            />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456:ABC-DEF..."
              className="h-8 min-w-0 flex-1 rounded-lg border border-border bg-background px-2.5 text-xs outline-none focus:border-accent"
            />
            <button
              type="button"
              onClick={storeToken}
              disabled={busy || !token.trim()}
              className="h-8 rounded-lg bg-background px-3 text-xs font-medium text-foreground shadow-xs ring-1 ring-border transition hover:bg-muted cursor-pointer disabled:opacity-40"
            >
              Store token
            </button>
            <button
              type="button"
              onClick={startPairing}
              disabled={busy}
              className="h-8 rounded-lg bg-background px-3 text-xs font-medium text-foreground shadow-xs ring-1 ring-border transition hover:bg-muted cursor-pointer disabled:opacity-40"
            >
              Pair Telegram
            </button>
            {pairCode && (
              <span className="rounded-lg bg-accent/10 px-2.5 py-1 font-mono text-xs font-bold text-accent">
                {pairCode}
              </span>
            )}
            {t && t.paired_ids.length > 0 && (
              <button
                type="button"
                onClick={testSend}
                disabled={busy}
                className="h-8 rounded-lg px-3 text-xs text-muted-foreground transition hover:text-accent cursor-pointer"
              >
                Send test
              </button>
            )}
          </div>
        </div>

        {/* WhatsApp */}
        <div className="rounded-xl border border-border/60 p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-foreground">WhatsApp (self-chat)</p>
              <p className="text-[11px] text-muted-foreground">
                Default OFF: automating your own account can get it restricted. Reads/writes only
                your &quot;Message yourself&quot; chat, human-paced, nothing is sent to anyone else.
              </p>
            </div>
            <Toggle on={!!w?.enabled} disabled={busy} onChange={() => enable("whatsapp", !w?.enabled)} />
          </div>
          {w?.last_error && (
            <p className="mt-1 text-[11px] text-amber-600">{w.last_error}</p>
          )}
        </div>

        {/* Safety */}
        <div className="rounded-xl border border-border/60 p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-foreground">Panic PIN</p>
              <p className="text-[11px] text-muted-foreground">
                {st?.pin_set ? "Set - required for unlocks and high-risk approvals" : "Optional second factor"}
                {st?.locked ? ` · ISHTI is LOCKED${st.lockout_s ? ` (${st.lockout_s}s lockout)` : ""}` : ""}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={savePin}
                className="h-8 rounded-lg bg-background px-3 text-xs font-medium text-foreground shadow-xs ring-1 ring-border transition hover:bg-muted cursor-pointer"
              >
                {st?.pin_set ? "Change" : "Set"}
              </button>
              {st?.pin_set && (
                <button
                  type="button"
                  onClick={clearPin}
                  className="h-8 rounded-lg px-3 text-xs text-muted-foreground transition hover:text-accent cursor-pointer"
                >
                  Remove
                </button>
              )}
            </div>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={revoke}
              disabled={busy}
              className="h-8 rounded-lg bg-background px-3 text-xs font-medium text-red-600 shadow-xs ring-1 ring-red-500/30 transition hover:bg-red-500/5 cursor-pointer disabled:opacity-40"
            >
              Revoke all sessions
            </button>
            <button
              type="button"
              onClick={togglePolicy}
              className="h-8 rounded-lg px-3 text-xs text-muted-foreground transition hover:text-accent cursor-pointer"
            >
              {showPolicy ? "Hide policy" : "View remote_policy.yaml"}
            </button>
          </div>
          {showPolicy && (
            <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-muted/50 p-3 text-[11px] leading-relaxed text-muted-foreground">
              {policyText}
            </pre>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">
            remote_policy.yaml is edited only here on the PC - a remote message can never change it.
          </p>
        </div>

        {/* Recent remote activity */}
        {st?.recent && st.recent.length > 0 && (
          <div className="rounded-xl border border-border/60 p-3">
            <p className="text-xs font-semibold text-foreground">Recent remote activity</p>
            <ul className="mt-1.5 space-y-1">
              {st.recent.slice(0, 6).map((r, i) => (
                <li key={i} className="truncate text-[11px] text-muted-foreground">
                  <span className="font-mono text-accent">{r.channel}</span> · {r.action}
                  {r.detail ? ` — ${r.detail}` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {msg && <p className="mt-3 text-xs text-accent">{msg}</p>}
    </Card>
  );
}
