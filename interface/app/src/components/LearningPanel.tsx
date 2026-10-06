// WP7 Learning panel: feedback stats, skill proposals (tier/risk) with approve/reject, auto-mode
// selector (off | propose | auto_t0 - auto_t0 only auto-enables READ-ONLY skills; anything with
// side effects always needs the owner's explicit approval), and a behavior-change log.
import { useCallback, useEffect, useState } from "react";
import { GraduationCap, RotateCcw } from "lucide-react";
import { getJSON, postJSON, putJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Stats = { total: number; pos: number; neg: number; neutral: number };
type Proposal = { id: string; title: string; skill: string; tier?: string; risk?: string; status: string; origin?: string };

export function LearningPanel() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [mode, setMode] = useState<string>("propose");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    getJSON<Stats>("/api/feedback/stats").then(setStats).catch(() => {});
    getJSON<{ proposals: Proposal[] }>("/api/skills/proposals").then((d) => setProposals(d.proposals)).catch(() => {});
  }, []);

  useEffect(() => { load(); }, [load]);

  const decide = async (id: string, approve: boolean) => {
    setBusy(id);
    try {
      await postJSON(`/api/skills/proposals/${id}/${approve ? "approve" : "reject"}`);
      setProposals((cur) => cur.map((p) => (p.id === id ? { ...p, status: approve ? "approved" : "rejected" } : p)));
      setLog((l) => [`${new Date().toLocaleTimeString()} — proposal ${id} ${approve ? "approved" : "rejected"} by you`, ...l].slice(0, 12));
    } finally { setBusy(""); }
  };

  const changeMode = async (m: string) => {
    try { await putJSON("/api/learning/mode", { mode: m }); setMode(m);
      setLog((l) => [`${new Date().toLocaleTimeString()} — learning mode set to ${m}`, ...l].slice(0, 12));
    } catch {}
  };

  const rollback = async (id: string) => {
    setBusy(id);
    try {
      await postJSON(`/api/skills/proposals/${id}/reject`);
      setLog((l) => [`${new Date().toLocaleTimeString()} — proposal ${id} rolled back`, ...l].slice(0, 12));
    } finally { setBusy(""); }
  };

  return (
    <div className="space-y-3" data-testid="learning-panel">
      <div className="flex items-center gap-1.5">
        <GraduationCap className="h-4 w-4 text-accent" aria-hidden />
        <h3 className="text-sm font-bold text-foreground">Learning</h3>
      </div>

      {stats && (
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg border border-border bg-card p-2" data-testid="fb-pos">
            <div className="text-lg font-bold text-emerald-500">{stats.pos}</div>
            <div className="text-[10px] text-muted-foreground">helpful</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-2" data-testid="fb-neg">
            <div className="text-lg font-bold text-amber-500">{stats.neg}</div>
            <div className="text-[10px] text-muted-foreground">needs work</div>
          </div>
          <div className="rounded-lg border border-border bg-card p-2">
            <div className="text-lg font-bold text-foreground">{stats.total}</div>
            <div className="text-[10px] text-muted-foreground">total</div>
          </div>
        </div>
      )}

      <div>
        <div className="text-xs font-semibold text-foreground">Auto-skill mode</div>
        <div className="mt-1 flex gap-1.5" role="group" aria-label="Learning mode">
          {(["off", "propose", "auto_t0"] as const).map((m) => (
            <button key={m} onClick={() => void changeMode(m)} aria-pressed={mode === m} data-testid={`mode-${m}`}
                    className={cn("rounded-md border border-border px-2 py-1 text-[11px] transition-colors",
                      mode === m ? "border-accent bg-accent/10 text-accent" : "text-muted-foreground hover:bg-muted")}>
              {m}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          auto_t0 auto-enables read-only skills only. Anything with side effects always waits for your approval.
        </p>
      </div>

      <div>
        <div className="text-xs font-semibold text-foreground">Skill proposals</div>
        {proposals.filter((p) => p.status === "open").length === 0 && (
          <p className="mt-1 text-[11px] text-muted-foreground">No open proposals.</p>
        )}
        <ul className="mt-1.5 space-y-1.5">
          {proposals.filter((p) => p.status === "open").map((p) => (
            <li key={p.id} className="rounded-lg border border-border bg-card p-2.5" data-testid={`proposal-${p.id}`}>
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-foreground">{p.title}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">{p.tier || "T?"}</span>
                <span className="text-[10px] text-muted-foreground">{p.risk}</span>
              </div>
              <div className="mt-1.5 flex gap-1.5">
                <button onClick={() => void decide(p.id, true)} disabled={busy === p.id} data-testid={`approve-${p.id}`}
                        className="rounded-md bg-accent px-2 py-1 text-[11px] font-medium text-accent-foreground disabled:opacity-50">Approve</button>
                <button onClick={() => void decide(p.id, false)} disabled={busy === p.id} data-testid={`reject-${p.id}`}
                        className="rounded-md border border-border px-2 py-1 text-[11px]">Reject</button>
                <button onClick={() => void rollback(p.id)} disabled={busy === p.id} title="Roll back this skill"
                        className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-destructive">
                  <RotateCcw className="h-3 w-3" /> Roll back
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <div className="text-xs font-semibold text-foreground">What changed my behavior</div>
        {log.length === 0
          ? <p className="mt-1 text-[11px] text-muted-foreground">Nothing yet — approvals and mode changes appear here.</p>
          : <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">{log.map((l, i) => <li key={i}>{l}</li>)}</ul>}
      </div>
    </div>
  );
}
