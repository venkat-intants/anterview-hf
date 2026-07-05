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
  },
});
