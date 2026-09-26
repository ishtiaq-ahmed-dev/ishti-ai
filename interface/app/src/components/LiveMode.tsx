import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { PhoneOff } from "lucide-react";
import { AgentOrb, type OrbState } from "@/components/AgentOrb";

type VoiceState = "idle" | "listening" | "thinking" | "speaking";
const ORB: Record<VoiceState, OrbState> = { idle: "breathing", listening: "listening", thinking: "solving", speaking: "composing" };
const LABEL: Record<VoiceState, string> = { idle: "Connecting…", listening: "Listening", thinking: "Thinking", speaking: "Speaking" };

// 16 kHz mono float -> int16, posted in 640-sample (40 ms) blocks: the server's barge-in resolution
const WORKLET = `class Pcm extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(640); this.n = 0; }
  process(inputs) {
    const ch = inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = Math.max(-32768, Math.min(32767, ch[i] * 32768));
      if (this.n === 640) { this.port.postMessage(this.buf.slice().buffer, []); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("pcm", Pcm);`;

/** Mode C - Live agent: continuous two-way voice. The only mode in which ISHTI speaks (on this PC's speakers);
 *  talk over it to interrupt. Each finished turn is handed to onTurn so it lands in the open chat. */
export function LiveMode({ sessionId, onTurn, onClose }: {
  sessionId: string | null;
  onTurn: (user: string, reply: string, sessionId?: string) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<VoiceState>("idle");
  const [heard, setHeard] = useState("");
  const [step, setStep] = useState("");
  const [reply, setReply] = useState("");
  const [error, setError] = useState("");
  const [background, setBackground] = useState<string[]>([]);  // requests still running while the talk goes on
  const turn = useRef(onTurn);
  turn.current = onTurn;

  useEffect(() => {
    let ws: WebSocket | null = null, ctx: AudioContext | null = null, stream: MediaStream | null = null, closed = false;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
        ctx = new AudioContext({ sampleRate: 16000 });
        await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
        const node = new AudioWorkletNode(ctx, "pcm");
        ctx.createMediaStreamSource(stream).connect(node);
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${location.host}/ws/live${sessionId ? `?session=${encodeURIComponent(sessionId)}` : ""}`);
        ws.binaryType = "arraybuffer";
        ws.onopen = () => { node.port.onmessage = (e) => ws?.readyState === WebSocket.OPEN && ws.send(e.data); };
        ws.onclose = () => { if (!closed) setError("Live connection closed."); };
        ws.onmessage = (e) => {
          const { type, data } = JSON.parse(e.data);
          if (type === "voice_state") setState(data.state);
          else if (type === "stt_partial") setHeard(data.text);
          else if (type === "stt_final") { if (!data.ignored) { setHeard(data.text); setReply(""); setStep(""); } }
          else if (type === "trace") {
            const s = data.type === "route" ? `Using ${data.skill}` : data.type === "escalate" ? `Trying ${data.to} instead`
              : data.label ?? (data.type === "step" ? data.skill : null);
            if (s) setStep(String(s));
          }
          else if (type === "task_background") setBackground((b) => [...b, data.request].filter(Boolean));
          else if (type === "task_done") setBackground((b) => b.filter((r) => r !== data.request));
          else if (type === "reply") { setReply(data.reply); setStep(""); turn.current(data.user, data.reply, data.session_id); }
        };
      } catch (err) {
        setError(`Microphone unavailable: ${(err as Error).message}`);
      }
    })();
    return () => {
      closed = true;
      try { ws?.send("end"); } catch {}
      ws?.close();
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close();
    };
  }, [sessionId]);

  return (
    <motion.div role="dialog" aria-label="Live conversation" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }} onKeyDown={(e) => e.key === "Escape" && onClose()}
      className="fixed inset-0 z-[60] flex flex-col items-center justify-center gap-8 bg-background/95 px-4 backdrop-blur-md">
      <div className="relative flex items-center justify-center">
        <div className="pointer-events-none absolute inset-0 -m-16 rounded-full bg-accent/15 blur-3xl" />
        <AgentOrb state={ORB[state]} size={Math.min(260, typeof window !== "undefined" ? window.innerWidth * 0.6 : 260)} />
      </div>
      <div aria-live="polite" className="flex w-full max-w-xl flex-col items-center gap-3 text-center">
        <div className="text-sm font-semibold uppercase tracking-[0.2em] text-accent">{error ? "Stopped" : LABEL[state]}</div>
        {error && <p className="text-sm text-danger">{error}</p>}
        {heard && <p className="text-lg text-muted-foreground">“{heard}”</p>}
        <AnimatePresence mode="wait">
          {step && state === "thinking" && (
            <motion.p key={step} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className="text-sm font-medium">{step}</motion.p>
          )}
        </AnimatePresence>
        {reply && <p className="max-h-[30vh] overflow-y-auto text-base leading-relaxed">{reply}</p>}
      </div>
      {background.length > 0 && (
        <ul aria-label="Working in the background" className="flex max-w-xl flex-wrap justify-center gap-2">
          {background.map((r, i) => (
            <li key={`${i}-${r}`} className="flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" /> {r}
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={onClose} autoFocus
        className="flex items-center gap-2 rounded-full bg-danger px-6 py-3 text-sm font-semibold text-white shadow-lg transition-transform active:scale-95 cursor-pointer">
        <PhoneOff className="h-4 w-4" /> End Live
      </button>
      <p className="text-xs text-muted-foreground">Just talk. Speak over ISHTI to interrupt.</p>
    </motion.div>
  );
}
