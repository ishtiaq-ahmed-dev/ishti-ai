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
  glow?: boolean;
}

/**
 * Rich, vibrant theme palettes tailored to ISHTI's dark/light modes.
 * Maps each agent state to its signature theme color, glowing aura, and dot thickness.
 */
export const ORB_THEME_PALETTES: Record<
  OrbState,
  { color: string; glow: string; dotSize: number }
> = {
  // Thinking / Reasoning -> Electric Sapphire Blue
  solving: {
    color: "#38bdf8",
    glow: "rgba(56, 189, 248, 0.55)",
    dotSize: 1.35,
  },
  // Ready / Standby (Breathing) -> Brand Amber Gold
  breathing: {
    color: "#f59e0b",
    glow: "rgba(245, 158, 11, 0.4)",
    dotSize: 1.3,
  },
  // Voice / Listening -> Radiant Warm Gold
  listening: {
    color: "#fbbf24",
    glow: "rgba(251, 191, 36, 0.55)",
    dotSize: 1.4,
  },
  // Web Search / Browsing -> Electric Sky Cyan
  searching: {
    color: "#06b6d4",
    glow: "rgba(6, 182, 212, 0.5)",
    dotSize: 1.3,
  },
  // Memory / Databases / Tools -> Mint Emerald Green
  connecting: {
    color: "#10b981",
    glow: "rgba(16, 185, 129, 0.55)",
    dotSize: 1.3,
  },
  // Files / Folders -> Vibrant Teal / Aquamarine
  shaping: {
    color: "#14b8a6",
    glow: "rgba(20, 184, 166, 0.5)",
    dotSize: 1.35,
  },
  // Multi-step Plans -> Radiant Royal Violet
  weaving: {
    color: "#8b5cf6",
    glow: "rgba(139, 92, 246, 0.45)",
    dotSize: 1.3,
  },
  // Synthesizing / Replying -> Spring Emerald Green (completed verified answer)
  composing: {
    color: "#22c55e",
    glow: "rgba(34, 197, 94, 0.55)",
    dotSize: 1.35,
  },
  // General active work -> Vibrant Royal Blue
  working: {
    color: "#2563eb",
    glow: "rgba(37, 99, 235, 0.55)",
    dotSize: 1.3,
  },
};

export function AgentOrb({
  events,
  live,
  state: manualState,
  size = 42,
  theme = "auto",
  color,
  speed,
  className,
  showLabel = false,
  recording = false,
  glow = true,
}: AgentOrbProps) {
  const resolved = resolveAgentState(events, live, recording);
  const activeState = manualState ?? resolved.state;
  const palette = ORB_THEME_PALETTES[activeState] ?? ORB_THEME_PALETTES.solving;
  const effectiveColor = color ?? palette.color;

  const targetPx = typeof size === "number" ? size : size;
  // Choose highest density preset suitable for size
  const presetSize: OrbSize = targetPx >= 36 ? 64 : targetPx >= 24 ? 32 : 20;
  const scale = targetPx / presetSize;

  return (
    <div className={cn("inline-flex items-center gap-2 select-none", className)}>
      <div
        className="relative shrink-0 flex items-center justify-center"
        style={{ width: targetPx, height: targetPx }}
      >
        {/* Glowing aura for contrast & vibrant visibility on both dark and light modes */}
        {glow && (
          <div
            className="absolute inset-0 rounded-full pointer-events-none transition-all duration-500"
            style={{
              background: `radial-gradient(circle, ${palette.glow} 0%, rgba(0,0,0,0) 70%)`,
              transform: "scale(1.2)",
              filter: "blur(5px)",
            }}
          />
        )}
        <div
          style={
            scale !== 1
              ? {
                  transform: `scale(${scale})`,
                  transformOrigin: "center center",
                  width: presetSize,
                  height: presetSize,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }
              : undefined
          }
        >
          <ThinkingOrb
            state={activeState}
            size={presetSize}
            theme={theme}
            color={effectiveColor}
            speed={speed}
            dotSize={palette.dotSize}
          />
        </div>
      </div>
      {showLabel && (
        <div className="flex flex-col min-w-0">
          <span className="text-xs font-semibold text-foreground truncate">{resolved.label}</span>
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground opacity-75">
            {activeState}
          </span>
        </div>
      )}
    </div>
  );
}

export default AgentOrb;
