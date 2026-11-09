import { useEffect, useRef } from "react";
import {
  ThinkingOrb,
  MODE_FRAMES,
  resolvePreset,
  scaleRadii,
  type OrbState,
  type OrbSize,
  type OrbTheme,
} from "thinking-orbs";
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

function parseTint(color?: string) {
  if (!color) return undefined;
  const hex = color.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    let h = hex[1];
    if (h.length === 3) h = h.replace(/./g, (c) => c + c);
    const n = parseInt(h, 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  const fn = color.trim().match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i);
  if (fn) return { r: Number(fn[1]), g: Number(fn[2]), b: Number(fn[3]) };
  return undefined;
}

function inkColor(w: number, alpha: number, dark: boolean, tint?: { r: number; g: number; b: number }) {
  if (!tint) {
    const g = Math.round((dark ? 1 - w : w) * 255);
    return `rgba(${g},${g},${g},${alpha})`;
  }
  const ramp = (c: number) => Math.round(dark ? c * (1 - w) : c + (255 - c) * w);
  return `rgba(${ramp(tint.r)},${ramp(tint.g)},${ramp(tint.b)},${alpha})`;
}

/**
 * High-DPI Native Canvas Renderer for large and custom-sized orbs.
 * Renders directly at device pixel resolution without bitmap scaling or CSS blur.
 */
function CrispCanvas({
  state,
  size,
  theme = "auto",
  color,
  speed = 1,
  dotSize = 1,
}: {
  state: OrbState;
  size: number;
  theme?: OrbTheme;
  color?: string;
  speed?: number;
  dotSize?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dpr = Math.min(2.5, typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);
    const pxSize = Math.round(size * dpr);
    canvas.width = pxSize;
    canvas.height = pxSize;
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const isDark =
      theme === "dark" ||
      (theme !== "light" &&
        typeof document !== "undefined" &&
        (document.documentElement.classList.contains("dark") ||
          document.body.classList.contains("dark") ||
          (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches)));

    const { mode, speed: baseSpeed, opts: presetOpts } = resolvePreset(state, 64);
    let opts = { ...presetOpts };
    if (dotSize !== 1) {
      opts = scaleRadii(opts, dotSize);
    }
    const frameFn = MODE_FRAMES[mode];
    const tint = parseTint(color);
    const effSpeed = baseSpeed * speed;

    let rafId = 0;
    let running = false;

    const render = (timeSec: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);

      const frame = frameFn(size, timeSec, opts);
      if (frame.lines && frame.lines.length) {
        for (const l of frame.lines) {
          const alpha = l.a ?? 1;
          const w = Math.min(1, Math.max(0, l.white));
          ctx.strokeStyle = inkColor(w, alpha, isDark, tint);
          ctx.lineWidth = l.w;
          ctx.beginPath();
          ctx.moveTo(l.x1, l.y1);
          ctx.lineTo(l.x2, l.y2);
          ctx.stroke();
        }
      }
      for (const d of frame.dots) {
        const alpha = d.a ?? 1;
        const w = Math.min(1, Math.max(0, d.white));
        ctx.fillStyle = inkColor(w, alpha, isDark, tint);
        ctx.beginPath();
        ctx.arc(d.x, d.y, Math.max(0.4, d.r), 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const loop = (timestamp: number) => {
      render((timestamp / 1000) * effSpeed);
      if (running) {
        rafId = requestAnimationFrame(loop);
      }
    };

    const start = () => {
      if (running) return;
      running = true;
      rafId = requestAnimationFrame(loop);
    };

    const stop = () => {
      running = false;
      cancelAnimationFrame(rafId);
    };

    // reduced motion: one still frame, no loop
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      render(0);
      return;
    }
    start();

    const onVis = () => {
      if (document.visibilityState === "hidden") stop();
      else start();
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [state, size, theme, color, speed, dotSize]);

  return <canvas ref={canvasRef} className="block pointer-events-none" />;
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
    glow: "rgba(56, 189, 248, 0.4)",
    dotSize: 1.35,
  },
  // Ready / Standby (Breathing) -> Brand Amber Gold
  breathing: {
    color: "#f59e0b",
    glow: "rgba(245, 158, 11, 0.35)",
    dotSize: 1.3,
  },
  // Voice / Listening -> Radiant Warm Gold
  listening: {
    color: "#fbbf24",
    glow: "rgba(251, 191, 36, 0.4)",
    dotSize: 1.4,
  },
  // Web Search / Browsing -> Electric Sky Cyan
  searching: {
    color: "#06b6d4",
    glow: "rgba(6, 182, 212, 0.4)",
    dotSize: 1.3,
  },
  // Memory / Databases / Tools -> Mint Emerald Green
  connecting: {
    color: "#10b981",
    glow: "rgba(16, 185, 129, 0.4)",
    dotSize: 1.3,
  },
  // Files / Folders -> Vibrant Teal / Aquamarine
  shaping: {
    color: "#14b8a6",
    glow: "rgba(20, 184, 166, 0.4)",
    dotSize: 1.35,
  },
  // Multi-step Plans -> Radiant Royal Violet
  weaving: {
    color: "#8b5cf6",
    glow: "rgba(139, 92, 246, 0.35)",
    dotSize: 1.3,
  },
  // Synthesizing / Replying -> Spring Emerald Green (completed verified answer)
  composing: {
    color: "#22c55e",
    glow: "rgba(34, 197, 94, 0.4)",
    dotSize: 1.35,
  },
  // General active work -> Vibrant Royal Blue
  working: {
    color: "#2563eb",
    glow: "rgba(37, 99, 235, 0.4)",
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

  // For exact native preset sizes (20, 32, 64), use ThinkingOrb
  const isPresetSize = targetPx === 20 || targetPx === 32 || targetPx === 64;

  return (
    <div className={cn("inline-flex items-center gap-2 select-none", className)}>
      <div
        className="relative shrink-0 flex items-center justify-center"
        style={{ width: targetPx, height: targetPx }}
      >
        {/* Crisp radial aura without raster blur filter */}
        {glow && (
          <div
            className="absolute inset-0 rounded-full pointer-events-none transition-all duration-500"
            style={{
              background: `radial-gradient(circle, ${palette.glow} 0%, transparent 72%)`,
              transform: "scale(1.15)",
            }}
          />
        )}
        {isPresetSize ? (
          <ThinkingOrb
            state={activeState}
            size={targetPx as OrbSize}
            theme={theme}
            color={effectiveColor}
            speed={speed}
            dotSize={palette.dotSize}
          />
        ) : (
          <CrispCanvas
            state={activeState}
            size={targetPx}
            theme={theme}
            color={effectiveColor}
            speed={speed}
            dotSize={palette.dotSize}
          />
        )}
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
