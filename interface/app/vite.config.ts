import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Built into dist/ and served by the FastAPI server at "/". `npm run dev` proxies the API to it.
// changeOrigin stays false so the app's own Host reaches the backend: the API's CSRF rule requires
// an Origin that matches Host, and browsers always send Origin on POST/PUT/DELETE. Rewriting the
// Host made every state-changing call fail with 403 "foreign origin" in dev.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: {
    port: 5174,
    proxy: {
      "/api": { target: "http://127.0.0.1:8888", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8888", ws: true, changeOrigin: false },
    },
  },
});
