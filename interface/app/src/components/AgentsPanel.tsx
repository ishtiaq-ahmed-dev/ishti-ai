// WP7 Agents panel: registry (/api/agents), live statuses (agent_status), inter-agent messages
// (agent_message), and INPUT_REQUIRED prompts with an answer box (the owner is the only T2
// authority). Data flows through the shared chatStore event sink - never per-panel sockets.
import { useEffect, useState } from "react";
import { Bot, HelpCircle, Send } from "lucide-react";
import { getJSON, postJSON } from "@/lib/api";
import { chatStore } from "@/lib/chat-store";
import { cn } from "@/lib/utils";

type Agent = { id: string; skills: string[]; goal: string; state: string; detail?: string };
type AgentMessage = { seq?: number; sender?: string; text: string; ts?: number };
type InputRequired = { agent_id: string; question: string; run_id?: string };

export function AgentsPanel() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [input, setInput] = useState<InputRequired | null>(null);
  const [answer, setAnswer] = useState("");
  const [answered, setAnswered] = useState("");

  useEffect(() => {
    const load = () => {
      getJSON<{ agents: Agent[] }>("/api/agents").then((d) => setAgents(d.agents)).catch(() => {});
      for (const a of agents.slice(0, 1)) {
        getJSON<{ messages: AgentMessage[] }>(`/api/agents/${a.id}/messages`)
          .then((d) => setMessages((cur) => dedupe([...cur, ...d.messages]))).catch(() => {});
      }
    };
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [agents.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // INPUT_REQUIRED arrives via the shared event stream; the latest unanswered one shows the box
    const sink = (ev: { type: string; data: any }) => {
      if (ev.type === "agent_status" && ev.data?.state === "input_required") {
        setInput({ agent_id: ev.data.agent_id, question: ev.data.detail, run_id: ev.data.run_id });
      }
      if (ev.type === "input_required") {
        // the durable event from agent_bus.request_input: {agent_id, question, run_id}
        setInput({ agent_id: ev.data?.agent_id, question: ev.data?.question, run_id: ev.data?.run_id });
      }
      if (ev.type === "agent_message") {
        setMessages((cur) => dedupe([...cur, { sender: ev.data?.from || ev.data?.agent_id, text: ev.data?.text }]));
      }
    };
    const off = chatStore.addSink(sink);
    return () => { off(); };
  }, []);

  const submitAnswer = async () => {
    if (!input || !answer.trim()) return;
    try {
      await postJSON("/api/agents/events", { type: "input_response", agent_id: input.agent_id,
                                             run_id: input.run_id, answer: answer.trim() });
      setAnswered(input.question);
      setInput(null);
      setAnswer("");
    } catch { /* visible via store connection state */ }
  };

  return (
    <div className="space-y-3" data-testid="agents-panel">
      <h3 className="text-sm font-bold text-foreground">Agents</h3>
      <div className="grid gap-2 md:grid-cols-2">
        {agents.map((a) => (
          <div key={a.id} className="rounded-lg border border-border bg-card p-3" data-testid={`agent-${a.id}`}>
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-accent" aria-hidden />
              <span className="text-sm font-semibold text-foreground">{a.id}</span>
              <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[10px]",
                a.state === "running" ? "bg-amber-500/15 text-amber-600 dark:text-amber-300"
                  : a.state === "input_required" ? "bg-sky-500/15 text-sky-600 dark:text-sky-300"
                  : "bg-muted text-muted-foreground")}>{a.state}</span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{a.goal}</p>
            <p className="mt-1 font-mono text-[10px] text-muted-foreground">{(a.skills || []).join(" · ")}</p>
          </div>
        ))}
      </div>

      {input && (
        <div className="rounded-lg border border-sky-500/40 bg-sky-500/10 p-3" data-testid="input-required">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <HelpCircle className="h-4 w-4 text-sky-500" aria-hidden /> {input.agent_id} needs your input
          </div>
          <p className="mt-1 text-xs text-foreground">{input.question}</p>
          <div className="mt-2 flex gap-1.5">
            <input value={answer} onChange={(e) => setAnswer(e.target.value)} data-testid="input-required-answer"
                   placeholder="Your answer…" className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs" />
            <button onClick={submitAnswer} className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground">
              <Send className="h-3 w-3" /> Answer
            </button>
          </div>
        </div>
      )}
      {answered && !input && <p className="text-[11px] text-muted-foreground">Answered: “{answered}”</p>}

      {messages.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-3">
          <h4 className="text-xs font-semibold text-foreground">Inter-agent messages</h4>
          <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto text-xs text-muted-foreground">
            {messages.slice(-20).map((m, i) => (
              <li key={i} className="font-mono text-[11px]">[{m.sender || "agent"}] {m.text}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function dedupe(list: AgentMessage[]): AgentMessage[] {
  const seen = new Set<string>();
  return list.filter((m) => {
    const k = `${m.sender}:${m.text}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(-100);
}
