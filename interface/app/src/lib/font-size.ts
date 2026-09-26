export type ChatFontSize = "sm" | "normal" | "lg" | "xl";

export const CHAT_FONT_SIZES: { key: ChatFontSize; label: string; desc: string; size: string }[] = [
  { key: "sm", label: "Small", desc: "13px - Compact view", size: "13px" },
  { key: "normal", label: "Normal", desc: "14px - Default", size: "14px" },
  { key: "lg", label: "Large", desc: "16px - Comfortable", size: "16px" },
  { key: "xl", label: "Extra Large", desc: "18px - High readability", size: "18px" },
];

export function getChatFontSize(): ChatFontSize {
  if (typeof window !== "undefined") {
    try {
      const saved = localStorage.getItem("ishti-chat-font-size") as ChatFontSize;
      if (saved && ["sm", "normal", "lg", "xl"].includes(saved)) {
        return saved;
      }
    } catch {}
  }
  return "normal";
}

export function setChatFontSize(size: ChatFontSize) {
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem("ishti-chat-font-size", size);
    } catch {}
    document.documentElement.setAttribute("data-chat-size", size);
    window.dispatchEvent(new CustomEvent("ishti-font-size-changed", { detail: size }));
  }
}

export function initChatFontSize() {
  if (typeof window !== "undefined") {
    const size = getChatFontSize();
    document.documentElement.setAttribute("data-chat-size", size);
  }
}
