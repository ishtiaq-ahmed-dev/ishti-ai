// WP4 Personal/Business mode UI: header switch (with pending-run confirmation), per-chat badge,
// namespace indicator, and the read-only prompt-layer viewer (core locked, profile visible).
import { useEffect, useState } from "react";
import { Briefcase, Heart, Layers } from "lucide-react";
import { getJSON } from "@/lib/api";
import { chatStore, type Profile } from "@/lib/chat-store";
import { cn } from "@/lib/utils";

type Layers = {
  layers: { name: string; chars: number; preview?: string; count?: number }[];
  total_chars: number;
  core_digest: string;
};

export function useActiveProfile(): Profile {
  const [profile, setProfileState] = useState<Profile>(chatStore.activeProfile);
  useEffect(() => chatStore.subscribe(() => setProfileState(chatStore.activeProfile)), []);
  return profile;
}

export function ProfileSwitcher({ pendingRuns = 0 }: { pendingRuns?: number }) {
  const active = useActiveProfile();
  const [confirming, setConfirming] = useState<Profile | null>(null);
  const [error, setError] = useState("");

  const switchTo = async (p: Profile) => {
    if (p === active) return;
    if (pendingRuns > 0) { setConfirming(p); return; } // runs keep their profile; confirm first
    await doSwitch(p);
  };

  const doSwitch = async (p: Profile) => {
    setConfirming(null);
    setError("");
    try {
      await chatStore.setProfile(p);
      window.dispatchEvent(new Event("ishti-profile-changed"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="flex items-center gap-1" data-testid="profile-switcher">
      <div className="flex overflow-hidden rounded-lg border border-border" role="group" aria-label="Active profile">
        {(["personal", "business"] as const).map((p) => (
          <button
            key={p}
            onClick={() => void switchTo(p)}
            aria-pressed={active === p}
            data-testid={`profile-${p}`}
            className={cn("inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors",
              active === p ? "bg-accent/15 text-accent" : "text-muted-foreground hover:bg-muted")}
          >
            {p === "personal" ? <Heart className="h-3.5 w-3.5" aria-hidden /> : <Briefcase className="h-3.5 w-3.5" aria-hidden />}
            {p === "personal" ? "Personal" : "Business"}
          </button>
        ))}
      </div>
      {error && <span role="alert" className="max-w-48 truncate text-[10px] text-danger" title={error}>{error}</span>}

      {confirming && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40" role="dialog" aria-modal="true" data-testid="profile-confirm">
          <div className="w-[380px] rounded-xl border border-border bg-card p-4 shadow-xl">
            <h3 className="text-sm font-bold text-foreground">Switch to {confirming} mode?</h3>
            <p className="mt-1.5 text-xs text-muted-foreground">
              {pendingRuns} run{pendingRuns === 1 ? "" : "s"} still in progress. They keep their
              current profile and memory namespace — nothing is interrupted.
            </p>
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => setConfirming(null)} className="rounded-md border border-border px-3 py-1.5 text-xs">Cancel</button>
              <button onClick={() => void doSwitch(confirming)} data-testid="profile-confirm-yes"
                      className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground">
                Switch
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function ChatProfileBadge({ profile }: { profile: Profile }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px]",
      profile === "business" ? "border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-300"
                             : "border-rose-400/40 bg-rose-400/10 text-rose-600 dark:text-rose-300")}
          data-testid={`chat-badge-${profile}`}>
      {profile === "business" ? <Briefcase className="h-2.5 w-2.5" aria-hidden /> : <Heart className="h-2.5 w-2.5" aria-hidden />}
      {profile}
    </span>
  );
}

export function PromptLayersViewer() {
  const [layers, setLayers] = useState<Layers | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    getJSON<Layers>("/api/prompt/layers").then(setLayers).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="text-xs text-muted-foreground">Prompt layers unavailable: {err}</div>;
  if (!layers) return <div className="text-xs text-muted-foreground">Loading prompt layers…</div>;

  return (
    <div className="space-y-2" data-testid="prompt-layers">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
        <Layers className="h-3.5 w-3.5 text-accent" aria-hidden />
        System prompt layers — {layers.total_chars} chars
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">core {layers.core_digest}</span>
      </div>
      {layers.layers.map((l) => (
        <div key={l.name} className="rounded-lg border border-border bg-card/60 p-2.5 text-xs">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-accent">{l.name}</span>
            <span className="text-muted-foreground">{l.chars} chars{l.count ? ` · ${l.count} skills` : ""}</span>
            {l.name === "core" && (
              <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">locked · byte-stable</span>
            )}
          </div>
          {l.preview && <p className="mt-1 line-clamp-2 text-muted-foreground">{l.preview}</p>}
        </div>
      ))}
      <p className="text-[11px] text-muted-foreground">Read-only: the prompt is compiled by ISHTI, never edited here.</p>
    </div>
  );
}
