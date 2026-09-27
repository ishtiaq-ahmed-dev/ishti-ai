export type VoiceState = "idle" | "listening" | "thinking" | "speaking";
export type Live = { text: string; step: string; done: boolean; voice?: VoiceState } | null;

/** Disabled: chat shows its own clean inline thinking/results, avoiding intrusive floating popups. */
export function LiveNarration(_props?: { live?: Live; onDismiss?: () => void }) {
  return null;
}
