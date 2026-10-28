import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => {
  // The backend used by `npm run dev` (see src/config.ts): the S-0 mock unless STATSEAT_BACKEND says otherwise.
  const backend = loadEnv(mode, ".", "STATSEAT_").STATSEAT_BACKEND || "http://127.0.0.1:8000";

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["icon-192.png", "icon-512.png", "apple-touch-icon.png", "archie.svg", "archie-character.svg", "pitch-bg.svg"],
        manifest: {
          name: "statseat",
          short_name: "statseat",
          description: "Every stat from your seat: live data, games and Archie for the match on your ticket.",
          theme_color: "#12081F",
          background_color: "#12081F",
          display: "standalone",
          orientation: "portrait",
          start_url: "/",
          // PNG icons; scripts/gen_pwa_icons.py makes the 192, maskable and iPhone ones from icon-512.png.
          icons: [
            { src: "icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
            { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
            { src: "icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
          ],
        },
        workbox: {
          navigateFallbackDenylist: [/^\/api\//],
          // API answers change with the match clock, so they are not cached; the one exception is the match
          // overview (line-ups, officials, stadium), which never changes: with a weak signal in the stadium the
          // Pre-match section still opens from the last copy (L-2e).
          runtimeCaching: [{
            urlPattern: ({ url }) => url.pathname === "/api/match",
            handler: "NetworkFirst",
            options: { cacheName: "match-overview", networkTimeoutSeconds: 4, expiration: { maxEntries: 2 } },
          }],
        },
      }),
    ],
    server: {
      host: true,
      port: 5173,
      proxy: { "/api": { target: backend, changeOrigin: true } },
    },
    preview: {
      proxy: { "/api": { target: backend, changeOrigin: true } },
    },
  };
});
