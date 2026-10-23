import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // In development the browser talks only to Vite; /api and /health are proxied to
  // FastAPI, so the session cookie is first-party (SameSite=Strict) and no CORS is needed.
  const apiTarget = env.REPORISK_API_PROXY || "http://127.0.0.1:8000";
  // Keep the browser's Host header (changeOrigin: false) so the API's same-origin
  // check sees the page's own origin. Vite's string shorthand would rewrite it.
  const proxy = { target: apiTarget, changeOrigin: false };
  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: { "/api": proxy, "/health": proxy },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.js"],
      css: false,
    },
  };
});
