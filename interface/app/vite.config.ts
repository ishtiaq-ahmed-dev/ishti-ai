import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Built into dist/ and served by the FastAPI server at "/". `npm run dev` proxies the API to it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8899", "/ws": { target: "ws://127.0.0.1:8899", ws: true } } },
});
