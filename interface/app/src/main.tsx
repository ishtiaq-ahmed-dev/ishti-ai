import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import "./index.css";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";

// F11 = full screen in the desktop window (pywebview bridge; MainApi.toggle_fullscreen). No-op in a normal browser tab.
window.addEventListener("keydown", (e) => {
  const api = (window as unknown as { pywebview?: { api?: { toggle_fullscreen?: () => void } } }).pywebview?.api;
  if (e.key === "F11" && api?.toggle_fullscreen) {
    e.preventDefault();
    api.toggle_fullscreen();
  }
});

// role="button" elements that are not real <button>s get the keyboard behaviour of one (Enter / Space).
window.addEventListener("keydown", (e) => {
  const t = e.target as HTMLElement | null;
  if ((e.key === "Enter" || e.key === " ") && t?.getAttribute?.("role") === "button" && t.tagName !== "BUTTON") {
    e.preventDefault();
    t.click();
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      {/* every motion component honours the OS "reduce motion" setting */}
      <MotionConfig reducedMotion="user">
        <App />
      </MotionConfig>
    </ErrorBoundary>
  </StrictMode>
);
