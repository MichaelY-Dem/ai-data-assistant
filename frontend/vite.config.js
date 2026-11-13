import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, /chat and /health are forwarded to the FastAPI backend,
// so the browser sees one origin and FastAPI needs no CORS settings.
const BACKEND = "http://127.0.0.1:8000";
const proxyOptions = {
  target: BACKEND,
  changeOrigin: true,
  // The backend gives up on a question after 240 s; wait a little longer than that.
  timeout: 300_000,
  proxyTimeout: 300_000,
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/chat": proxyOptions,
      "/health": proxyOptions,
    },
  },
});
