/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      // The MediaPipe proctoring assets (~37 MB of wasm + the model) are large
      // on-demand runtime files loaded only during an interview — never part of
      // the app shell. Exclude them from the service-worker precache manifest
      // (otherwise workbox fails the build on the >2 MB precache size limit).
      // They still load fine from our own origin when proctoring starts.
      workbox: {
        globIgnores: ['**/mediapipe/**'],
        // The app-shell navigation fallback must NOT swallow navigations the
        // SERVER handles: /auth/sso/* are the OAuth redirect endpoints that
        // 302 the browser to Google. With the default fallback the service
        // worker serves the cached index.html instead, and "Sign in with
        // Google" dead-ends on the SPA's 404 page (seen live on the Space).
        navigateFallbackDenylist: [/^\/auth\/sso\//],
      },
      manifest: {
        name: 'Intants AI Interview',
        short_name: 'Intants',
        description: 'AI-powered voice interview platform',
        theme_color: '#4f46e5',
        background_color: '#ffffff',
        display: 'standalone',
        icons: [
          {
            src: '/icon-192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: '/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5174,
    strictPort: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    // Vitest defaults to 5s. Several userEvent-driven tests already sit in the
    // 2.4-3.1s band on a fast laptop, and under full-suite parallelism one has
    // been observed at 7.6s — a real 1-in-6 flake locally, which on a slower
    // CI runner would be worse. These tests type character-by-character through
    // jsdom with i18n and react-query mounted, so the cost is structural rather
    // than a hang; a longer ceiling is the honest fix. A genuinely stuck test
    // still fails, 15s later.
    testTimeout: 15_000,
  },
});
