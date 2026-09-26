import { ThinkingOrb, type OrbState, type OrbSize, type OrbTheme } from "thinking-orbs";
import type { TraceEvent } from "@/lib/api";
import { cn } from "@/lib/utils";
import { resolveAgentState } from "@/lib/agent-state";

export type { OrbState, OrbSize, OrbTheme };

export interface AgentOrbProps {
  events?: TraceEvent[];
  live?: boolean;
  state?: OrbState;
  size?: number | OrbSize;
  theme?: OrbTheme;
  color?: string;
  speed?: number;
  className?: string;
  showLabel?: boolean;
  recording?: boolean;
}

export function AgentOrb({
  events,
  live,
  state: manualState,
  size = 32,
  theme = "auto",
  color,
  speed,
  className,
  showLabel = false,
  recording = false,
}: AgentOrbProps) {
  const resolved = resolveAgentState(events, live, recording);
  const activeState = manualState ?? resolved.state;

  // Normalize size to hand-tuned presets: 64 | 32 | 20
  const normalizedSize: OrbSize = size >= 48 ? 64 : size >= 26 ? 32 : 20;

  return (
    <div className={cn("inline-flex items-center gap-2 select-none", className)}>
      <div
        className="relative shrink-0 flex items-center justify-center"
        style={{ width: normalizedSize, height: normalizedSize }}
      >
        <ThinkingOrb
          state={activeState}
          size={normalizedSize}
          theme={theme}
          color={color}
          speed={speed}
        />
      </div>
      {showLabel && (
        <div className="flex flex-col min-w-0">
          <span className="text-xs font-medium text-foreground truncate">{resolved.label}</span>
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground opacity-75">
            {activeState}
          </span>
        </div>
      )}
    </div>
  );
}

export default AgentOrb;
