// Vendors the MediaPipe FaceLandmarker assets into web/public/mediapipe so the
// proctoring pipeline loads them from OUR origin instead of third-party CDNs
// (jsdelivr for the wasm, storage.googleapis.com for the model). This removes
// the runtime CDN dependency — important for offline / government / locked-down
// deployments. Assets are gitignored (≈37 MB); this script regenerates them.
//
// - wasm: copied from the installed npm package (no network needed).
// - model: downloaded once (cached thereafter). Download failure is NON-FATAL —
//   the build/dev still succeed; proctoring just degrades until the model is
//   present (the hook already handles a missing model gracefully).
//
// Idempotent: skips work that's already done. Runs automatically via the
// predev/prebuild npm hooks, or manually: `npm run setup:mediapipe`.

import { createWriteStream, existsSync, mkdirSync, readdirSync, copyFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import https from 'node:https';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '..');
const wasmSrc = join(webRoot, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm');
const outDir = join(webRoot, 'public', 'mediapipe');
const wasmOut = join(outDir, 'wasm');
const modelPath = join(outDir, 'face_landmarker.task');

const MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';

mkdirSync(wasmOut, { recursive: true });

// 1. Copy wasm loader/binaries from the installed package.
if (existsSync(wasmSrc)) {
  let copied = 0;
  for (const f of readdirSync(wasmSrc)) {
    const dest = join(wasmOut, f);
    if (!existsSync(dest)) {
      copyFileSync(join(wasmSrc, f), dest);
      copied += 1;
    }
  }
  console.log(`[mediapipe] wasm ready (${copied} file(s) copied)`);
} else {
  console.warn('[mediapipe] wasm source missing — run `npm install` first; skipping.');
}

// 2. Download the model once (skip if already present and non-empty).
if (existsSync(modelPath) && statSync(modelPath).size > 0) {
  console.log('[mediapipe] model already present, skipping download.');
} else {
  await downloadModel(MODEL_URL, modelPath).catch((err) => {
    console.warn(`[mediapipe] model download failed (non-fatal): ${err.message}`);
  });
}

function downloadModel(url, dest, redirects = 0) {
  return new Promise((resolve, reject) => {
    https
      .get(url, (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          if (redirects > 5) return reject(new Error('too many redirects'));
          res.resume();
          return resolve(downloadModel(res.headers.location, dest, redirects + 1));
        }
        if (res.statusCode !== 200) {
          res.resume();
          return reject(new Error(`HTTP ${res.statusCode}`));
        }
        const file = createWriteStream(dest);
        res.pipe(file);
        file.on('finish', () => file.close(() => {
          console.log('[mediapipe] model downloaded.');
          resolve();
        }));
        file.on('error', reject);
      })
      .on('error', reject);
  });
}
