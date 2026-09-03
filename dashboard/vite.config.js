import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Proxies straight through to the orchestrator's real paths (P3-5:
  // /governance/inventory, /governance/accuracy-trend,
  // /governance/transparency-report). No /api prefix and no rewrite: the
  // orchestrator was never given one, so adding one here would be a proxy
  // config the backend contract does not actually have.
  server: { proxy: { "/governance": "http://localhost:8001" } },
});
