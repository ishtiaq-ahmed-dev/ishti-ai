import { ChevronDown, ShieldCheck, Lock, Trash2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Row = { ts: string; host: string; port: number; who: string; allowed: boolean; reason: string };
type Found = { name: string; to: string; data: string; status: string };
type Privacy = {
  internet: boolean;
  blocked_total: number;
  allowed_total: number;
  recent: Row[];
  skills: { name: string; hosts: string[] }[];
  third_parties_found: Found[];
};

type VaultStats = {
  total_items?: number;
  file_count: number;
  total_bytes: number;
  vault_dir: string;
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

/** Settings -> Privacy: nothing leaves this PC unless a web skill is asked to; every outside connection is listed here. */
export function PrivacyPanel() {
  const [p, setP] = useState<Privacy>();
  const [vault, setVault] = useState<VaultStats | null>(null);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const [vaultMsg, setVaultMsg] = useState("");
  const [vaultBusy, setVaultBusy] = useState(false);

  const load = useCallback(() => {
    getJSON<Privacy>("/api/privacy").then(setP).catch((e) => setMsg(String(e.message || e)));
    getJSON<VaultStats>("/api/vault/status").then(setVault).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => clearInterval(t);
  }, [load]);

  const setInternet = async (on: boolean) => {
    setMsg("");
    try {
      await postJSON("/api/privacy", { internet: on });
      load();
    } catch (e) {
      setMsg(String((e as Error).message || e));
    }
  };

  const handleVaultCleanup = async () => {
    setVaultBusy(true);
    setVaultMsg("");
    try {
      const res = await postJSON<{ ok: boolean; cleaned: number }>("/api/vault/cleanup", {});
      setVaultMsg(`Cleanup complete: removed ${res.cleaned} expired files.`);
      load();
    } catch (e) {
      setVaultMsg(String((e as Error).message || e));
    } finally {
      setVaultBusy(false);
    }
  };

  const handleVaultPurge = async () => {
    if (!window.confirm("Purge Media Vault: All temporary voice notes, audio, and media files will be immediately shredded. Continue?")) {
      return;
    }
    setVaultBusy(true);
    setVaultMsg("");
    try {
      const approval = await postJSON<{ approval_token: string }>("/api/vault/purge/prepare", {});
      const res = await postJSON<{ ok: boolean; purged: number }>("/api/vault/purge", {
        approval_token: approval.approval_token,
      });
      setVaultMsg(`Vault purged: permanently removed ${res.purged} files.`);
      load();
    } catch (e) {
      setVaultMsg(String((e as Error).message || e));
    } finally {
      setVaultBusy(false);
    }
  };

  const outside = (p?.recent ?? []).filter((r) => r.allowed).length;
  return (
    <div className="space-y-4" data-testid="privacy-panel">
      <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden">
        <div className="flex items-center justify-between gap-3 p-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-500/15 text-emerald-500">
              <ShieldCheck className="h-4.5 w-4.5" />
            </div>
            <div className="min-w-0">
              <div className="font-semibold text-foreground text-sm">Privacy</div>
              <div className="text-xs text-muted-foreground">
                Your data stays on this PC. Nothing is sent anywhere unless you ask a web skill (search, weather, markets...) to look something up.
              </div>
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={!!p?.internet}
            aria-label={`Internet for skills ${p?.internet ? "on" : "off"}`}
            onClick={() => p && setInternet(!p.internet)}
            className={cn("relative h-6 w-11 shrink-0 rounded-full transition-colors cursor-pointer", p?.internet ? "bg-accent" : "bg-muted-foreground/30")}
          >
            <span className={cn("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all", p?.internet ? "left-[22px]" : "left-0.5")} />
          </button>
        </div>
        <div className="border-t border-border/60 bg-background/50 px-4 py-3 text-xs text-muted-foreground">
          {p?.internet
            ? "Internet for skills is ON: web skills can reach the sites you ask for. Everything else is blocked, and every outside connection is listed below."
            : "Internet for skills is OFF: ISHTI cannot reach anything outside this PC."}
          <span className="ml-2 font-medium text-foreground">
            {p ? `${p.allowed_total} allowed, ${p.blocked_total} blocked so far` : ""}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center justify-between border-t border-border/60 px-4 py-2.5 text-xs font-medium text-accent hover:bg-muted/30 cursor-pointer"
        >
          <span>{open ? "Hide details" : "Show what connects to the internet"}</span>
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        </button>
        {open && p && (
          <div className="space-y-4 border-t border-border/60 bg-background/50 p-4 text-xs">
            <section>
              <h4 className="mb-1.5 font-semibold uppercase tracking-wider text-muted-foreground">Programs that can reach the internet</h4>
              <ul className="space-y-2">
                {p.third_parties_found.map((f) => (
                  <li key={f.name} className="rounded-lg border border-border/60 bg-card/50 p-2.5">
                    <div className="font-medium text-foreground">{f.name}</div>
                    <div className="text-muted-foreground">To: {f.to}. Carries: {f.data}.</div>
                    <div className="text-foreground/80">Status: {f.status}</div>
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <h4 className="mb-1.5 font-semibold uppercase tracking-wider text-muted-foreground">Latest outside connections ({outside} allowed in this list)</h4>
              {p.recent.length === 0 ? (
                <p className="text-muted-foreground">None yet.</p>
              ) : (
                <ul className="max-h-56 space-y-1 overflow-y-auto scroll-thin font-mono text-[11px]">
                  {[...p.recent].reverse().slice(0, 30).map((r, i) => (
                    <li key={i} className={cn("flex gap-2", r.allowed ? "text-foreground" : "text-danger")}>
                      <span className="shrink-0">{r.ts.slice(11, 19)}</span>
                      <span className="shrink-0">{r.allowed ? "allowed" : "BLOCKED"}</span>
                      <span className="truncate">{r.host}:{r.port} ({r.who})</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
        {msg && <div role="alert" className="border-t border-danger/40 bg-danger/10 px-4 py-2 text-xs text-danger">{msg}</div>}
      </div>

      {/* Media Vault Card */}
      <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden">
        <div className="flex items-center justify-between gap-3 p-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-500/15 text-blue-500">
              <Lock className="h-4.5 w-4.5" />
            </div>
            <div className="min-w-0">
              <div className="font-semibold text-foreground text-sm">Media Vault</div>
              <div className="text-xs text-muted-foreground">
                Ephemeral encrypted storage for voice notes, audio messages, and uploaded files. Automatically expires and shreds files after their TTL.
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={vaultBusy}
              onClick={handleVaultCleanup}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors cursor-pointer disabled:opacity-50"
              title="Remove expired files"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", vaultBusy && "animate-spin")} />
              <span>Clean Expired</span>
            </button>
            <button
              type="button"
              disabled={vaultBusy || !(vault?.total_items ?? vault?.file_count)}
              onClick={handleVaultPurge}
              className="flex items-center gap-1.5 rounded-lg border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-xs font-medium text-danger hover:bg-danger/20 transition-colors cursor-pointer disabled:opacity-50"
              title="Permanently shred all media files in vault"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span>Purge All</span>
            </button>
          </div>
        </div>
        <div className="border-t border-border/60 bg-background/50 px-4 py-3 text-xs text-muted-foreground flex items-center justify-between">
          <div>
            Status: <span className="font-medium text-foreground">{vault ? `${vault.total_items ?? vault.file_count ?? 0} file(s) stored (${formatBytes(vault.total_bytes)})` : "Checking vault..."}</span>
          </div>
          {vault?.vault_dir && (
            <div className="truncate text-[11px] text-muted-foreground font-mono max-w-[280px]" title={vault.vault_dir}>
              {vault.vault_dir}
            </div>
          )}
        </div>
        {vaultMsg && (
          <div className="border-t border-border/60 bg-muted/20 px-4 py-2 text-xs text-accent">
            {vaultMsg}
          </div>
        )}
      </div>
    </div>
  );
}
