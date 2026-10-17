/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production assets are written into the Python package so the installed application serves
// them without Node.js. VR_DFIR_UI_OUT_DIR overrides the location (used by scripts/frontend.sh).
const outDir = process.env.VR_DFIR_UI_OUT_DIR ?? "../src/vr_dfir/web/static";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: "assets",
    // Never inline assets as data: URIs; the UI's Content-Security-Policy allows fonts and
    // images only from the same origin (font-src 'self').
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 900,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // Development only: the API runs separately on 127.0.0.1:8766.
    proxy: { "/api": { target: "http://127.0.0.1:8766", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/__tests__/**/*.test.tsx", "src/__tests__/**/*.test.ts"],
  },
});
