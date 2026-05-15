import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Node 18 needs `--experimental-global-webcrypto` for workbox-build to
// access `globalThis.crypto`. The npm `build` script sets NODE_OPTIONS
// accordingly. Node 20+ exposes the global natively.
//
// Phase 6d B4: Vite proxy + same-origin cookie sharing.
//
// The session cookie is host-only in dev (no SESSION_COOKIE_DOMAIN env var
// in api/config.py), so the SPA must be on the SAME origin as the API to
// access it. We proxy /api/* to localhost:8000 so the browser sees a
// single origin (localhost:5173). Prod uses a real shared parent domain.
//
// Phase 6e B13: vite-plugin-pwa wires the install banner, manifest, and
// service worker.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/icon-192.png", "icons/icon-512.png"],
      manifest: {
        name: "Centro Financiero",
        short_name: "Centro",
        description: "Asistente financiero personal — Costa Rica.",
        theme_color: "#1e3a8a",
        background_color: "#f8fafc",
        display: "standalone",
        scope: "/",
        start_url: "/",
        lang: "es-CR",
        icons: [
          {
            src: "icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            src: "icons/icon-512-maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // App shell (HTML/CSS/JS bundled by Vite): cache-first with
        // revalidation, exactly the spec's strategy. vite-plugin-pwa
        // precaches the build artifacts; the runtime rules below handle
        // everything else.
        globPatterns: ["**/*.{js,css,html,ico,svg,png,webp}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // API JSON: network-first with 24h cache fallback so the
            // dashboard works briefly offline (decision: read-only).
            urlPattern: ({ url }) =>
              url.pathname.startsWith("/api/v1/dashboard") ||
              url.pathname.startsWith("/api/v1/accounts") ||
              url.pathname.startsWith("/api/v1/transactions") ||
              url.pathname.startsWith("/api/v1/debts") ||
              url.pathname.startsWith("/api/v1/goals") ||
              url.pathname.startsWith("/api/v1/recurring-bills") ||
              url.pathname.startsWith("/api/v1/recurring-incomes") ||
              url.pathname.startsWith("/api/v1/users/me/insights") ||
              url.pathname.startsWith("/api/v1/categories"),
            handler: "NetworkFirst",
            options: {
              cacheName: "centro-api-cache",
              networkTimeoutSeconds: 4,
              expiration: {
                maxEntries: 200,
                maxAgeSeconds: 60 * 60 * 24, // 24h
              },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Images: cache-first, 7-day TTL.
            urlPattern: ({ request }) => request.destination === "image",
            handler: "CacheFirst",
            options: {
              cacheName: "centro-image-cache",
              expiration: {
                maxEntries: 50,
                maxAgeSeconds: 60 * 60 * 24 * 7, // 7d
              },
            },
          },
        ],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
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
