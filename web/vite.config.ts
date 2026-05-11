import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Phase 6d B4: Vite proxy + same-origin cookie sharing.
//
// The session cookie is host-only in dev (no SESSION_COOKIE_DOMAIN env var
// in api/config.py), so the SPA must be on the SAME origin as the API to
// access it. We proxy /api/* to localhost:8000 so the browser sees a
// single origin (localhost:5173). Prod uses a real shared parent domain.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
