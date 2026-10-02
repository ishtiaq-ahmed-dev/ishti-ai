import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
);
