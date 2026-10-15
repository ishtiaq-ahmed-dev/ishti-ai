// Read-only prompt-layer viewer (core locked, profile visible).
import { useEffect, useState } from "react";
import { Layers } from "lucide-react";
import { getJSON } from "@/lib/api";
import { chatStore, type Profile } from "@/lib/chat-store";

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
