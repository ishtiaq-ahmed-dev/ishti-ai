import { ChevronDown, Languages } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Lang = { id: string; name: string; note?: string; enabled: boolean; locked: boolean };

/** Settings -> Language: English is always on; Urdu and Pashto are switches, OFF by default (ishti/languages.py).
 *  Collapsible with the same chevron header as the other Settings cards; default collapsed. */
export function LanguageSettings({ isOpen = false, onToggle }: { isOpen?: boolean; onToggle?: () => void }) {
  const [langs, setLangs] = useState<Lang[]>();
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    getJSON<{ languages: Lang[] }>("/api/settings/languages")
      .then((d) => setLangs(d.languages))
      .catch((e) => setMsg(String(e.message || e)));
  }, []);
  useEffect(load, [load]);

  const flip = async (l: Lang) => {
    setMsg("");
    try {
      const d = await postJSON<{ languages: Lang[] }>("/api/settings/languages", { [l.id]: !l.enabled });
      setLangs(d.languages);
    } catch (e) {
      setMsg(String((e as Error).message || e));
    }
  };

  const on = (langs ?? []).filter((l) => l.enabled).map((l) => l.name).join(", ");

  return (
    <div className="rounded-2xl border border-border/80 bg-card/40 shadow-xs overflow-hidden" data-testid="language-settings">
      <div
        className="flex items-center justify-between gap-3 p-4 cursor-pointer hover:bg-muted/30 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-sky-500/15 text-sky-500">
            <Languages className="h-4.5 w-4.5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-foreground text-sm">Language</span>
              <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] font-mono text-muted-foreground">
                {on || "English"}
              </span>
            </div>
            <div className="text-xs text-muted-foreground">
              ISHTI uses English only, unless you switch another language on. A language that is off is not listened for, not translated and not spoken.
            </div>
          </div>
        </div>
        <button
          type="button"
          title={isOpen ? "Collapse" : "Expand"}
          onClick={(e) => { e.stopPropagation(); onToggle?.(); }}
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          <ChevronDown className={cn("h-4 w-4 transition-transform duration-200", isOpen ? "rotate-180 text-accent" : "")} />
        </button>
      </div>
      {isOpen && (
        <>
          <ul className="divide-y divide-border/60 border-t border-border/60 bg-background/50">
            {(langs ?? []).map((l) => (
              <li key={l.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-foreground">{l.name}</div>
                  <div className="text-[11px] text-muted-foreground">{l.locked ? "Always on" : l.note ?? (l.enabled ? "On" : "Off")}</div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={l.enabled}
                  aria-label={`${l.name} ${l.enabled ? "on" : "off"}`}
                  disabled={l.locked}
                  onClick={() => flip(l)}
                  className={cn(
                    "relative h-6 w-11 shrink-0 rounded-full transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-60",
                    l.enabled ? "bg-accent" : "bg-muted-foreground/30",
                  )}
                >
                  <span className={cn("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all", l.enabled ? "left-[22px]" : "left-0.5")} />
                </button>
              </li>
            ))}
          </ul>
          {msg && <div role="alert" className="border-t border-danger/40 bg-danger/10 px-4 py-2 text-xs text-danger">{msg}</div>}
        </>
      )}
    </div>
  );
}
