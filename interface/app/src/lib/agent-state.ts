import type { OrbState } from "thinking-orbs";
import type { TraceEvent } from "@/lib/api";

export type { OrbState };

/**
 * Resolves a tool or step name/description directly to its matching orb animation state:
 * - searching: web search, crawling, browsing, fetching URLs
 * - solving: planning, reasoning, code generation, decision evaluation
 * - connecting: SQLite memory, ChromaDB vectors, external MCP servers, weather, integrations
 * - weaving: multi-agent / multi-step plan orchestration
 * - shaping: folder scanning/organizing, file transformation, video processing
 * - composing: drafting text, synthesizing final response, streaming tokens
 * - listening: microphone active, speech-to-text
 * - working: general active command / tool execution
 * - breathing: idle standby
 */
export function resolveToolState(textOrName: string): OrbState {
  const t = (textOrName || "").toLowerCase();

  // Searching: Sweeping scan meridian over dotted globe
  if (
    t.includes("search") ||
    t.includes("browse") ||
    t.includes("fetch") ||
    t.includes("crawl") ||
    t.includes("searx") ||
    t.includes("google") ||
    t.includes("find") ||
    t.includes("lookup") ||
    t.includes("shopping")
  ) {
    return "searching";
  }

  // Solving: Scrambling bands that snap together in quarter turns
  if (
    t.includes("plan") ||
    t.includes("code") ||
    t.includes("solve") ||
    t.includes("reason") ||
    t.includes("math") ||
    t.includes("decision") ||
    t.includes("eval") ||
    t.includes("think")
  ) {
    return "solving";
  }

  // Connecting: Interconnecting constellation with pulse packets
  if (
    t.includes("memory") ||
    t.includes("fact") ||
    t.includes("mcp") ||
    t.includes("weather") ||
    t.includes("whatsapp") ||
    t.includes("remind") ||
    t.includes("owner") ||
    t.includes("chroma") ||
    t.includes("sqlite") ||
    t.includes("connect")
  ) {
    return "connecting";
  }

  // Shaping: Morphing geometric outlines
  if (
    t.includes("folder") ||
    t.includes("scan") ||
    t.includes("organize") ||
    t.includes("video") ||
    t.includes("ffmpeg") ||
    t.includes("file") ||
    t.includes("convert")
  ) {
    return "shaping";
  }

  // Weaving: Three interlacing braided strands
  if (t.includes("weave") || t.includes("orchestrat") || t.includes("pipeline") || t.includes("multi")) {
    return "weaving";
  }

  // Composing: Undulating multi-band ribbon/sash
  if (t.includes("compose") || t.includes("draft") || t.includes("reply") || t.includes("chat") || t.includes("stream")) {
    return "composing";
  }

  // Listening: Waveform rolling through latitude rings
  if (t.includes("mic") || t.includes("voice") || t.includes("listen") || t.includes("speech") || t.includes("stt")) {
    return "listening";
  }

  // Working: Tilted orbital particles
  return "working";
}

/**
 * Intelligently resolves the current animation state and human-readable label from live execution events.
 */
export function resolveAgentState(
  events?: TraceEvent[],
  live?: boolean,
  recording?: boolean
): { state: OrbState; label: string } {
  if (recording) {
    return { state: "listening", label: "Listening…" };
  }

  if (!live) {
    return { state: "breathing", label: "Ready" };
  }

  if (!events || events.length === 0) {
    return { state: "solving", label: "Thinking…" };
  }

  const activeTool = events.findLast((e) => e.type === "tool" && e.status === "start");
  const activeStep = events.findLast((e) => e.type === "step" && e.status === "start");
  const route = events.find((e) => e.type === "route");
  const toolName = activeTool?.name || "";
  const toolLabel = activeTool?.label || "";
  const skillName = activeStep?.skill || route?.skill || "";

  // 1. If there's an active running tool, resolve by tool name and label
  if (activeTool) {
    const combined = `${toolName} ${toolLabel} ${skillName}`;
    const state = resolveToolState(combined);
    const label = toolLabel || `Using ${toolName}`;
    return { state, label };
  }

  // 2. Multi-step plan orchestration
  if (route?.steps && route.steps.length > 1) {
    const startedSteps = events.filter((e) => e.type === "step" && e.status === "start").length;
    return {
      state: "weaving",
      label: `Executing plan (${startedSteps || 1}/${route.steps.length})`,
    };
  }

  // 3. Active step without a tool yet
  if (activeStep) {
    const state = resolveToolState(skillName);
    return { state, label: `Working · ${skillName}` };
  }

  // 4. Route determined but no tool active
  if (route?.skill) {
    const state = resolveToolState(route.skill);
    return { state, label: `Thinking · ${route.skill}` };
  }

  // 5. Default when live
  return { state: "solving", label: "Thinking…" };
}
