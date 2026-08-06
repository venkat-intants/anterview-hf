// Vendors the MediaPipe FaceLandmarker assets into web/public/mediapipe so the
// proctoring pipeline loads them from OUR origin instead of third-party CDNs
// (jsdelivr for the wasm, storage.googleapis.com for the model). This removes
// the runtime CDN dependency — important for offline / government / locked-down
// deployments. Assets are gitignored (≈37 MB); this script regenerates them.
//
// - wasm: copied from the installed npm package (no network needed).
// - model: downloaded once and pinned by SHA-256, then cached. A cached copy is
//   re-verified rather than trusted on size alone, because a connection reset
//   mid-body used to leave a truncated-but-non-empty file that every later
//   build accepted forever.
// - failure is LOUD but non-fatal by default, because CI runs `npm run build`
//   and must not depend on storage.googleapis.com being reachable. Set
//   MEDIAPIPE_REQUIRED=1 (the container build does this) to make it fatal, so
//   an image can never ship with proctoring silently dead.
//
// Idempotent: skips work that's already done. Runs automatically via the
// predev/prebuild npm hooks, or manually: `npm run setup:mediapipe`.

import {
  createReadStream,
  createWriteStream,
  existsSync,
  mkdirSync,
  readdirSync,
  copyFileSync,
  renameSync,
  rmSync,
  statSync,
} from 'node:fs';
import { createHash } from 'node:crypto';
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

// Pin of face_landmarker float16 v1. Google publishes this path as an immutable
// version, so a mismatch means a truncated download or a substituted file —
// either way the model must not be used. Update both values together if the
// pinned version is ever bumped.
const MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff';
const MODEL_BYTES = 3758596;

// Socket-inactivity budget per request. Without it a hung TCP connection stalls
// the Docker build with no upper bound.
const REQUEST_TIMEOUT_MS = 60_000;
const MAX_REDIRECTS = 5;

const required = process.env.MEDIAPIPE_REQUIRED === '1';

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

// 2. Download the model once, verifying the pin (skip the network entirely when
//    the cached copy already matches).
let modelReady = false;

if (existsSync(modelPath)) {
  const size = statSync(modelPath).size;
  const digest = await sha256File(modelPath);
  if (size === MODEL_BYTES && digest === MODEL_SHA256) {
    console.log('[mediapipe] model already present and verified, skipping download.');
    modelReady = true;
  } else {
    console.warn(
      `[mediapipe] cached model failed verification (${size} bytes, sha256 ${digest}) — re-downloading.`,
    );
    rmSync(modelPath, { force: true });
  }
}

if (!modelReady) {
  try {
    await downloadModel(MODEL_URL, modelPath);
    console.log('[mediapipe] model downloaded and verified.');
    modelReady = true;
  } catch (err) {
    console.error(`[mediapipe] MODEL DOWNLOAD FAILED: ${err.message}`);
    console.error('[mediapipe] proctoring (face + gaze tracking) will be DISABLED at runtime.');
    console.error(`[mediapipe] fix the network path to ${MODEL_URL}, or vendor the file to`);
    console.error(`[mediapipe]   ${modelPath}`);
    if (required) {
      console.error('[mediapipe] MEDIAPIPE_REQUIRED=1 — failing the build rather than shipping without it.');
      process.exit(1);
    }
  }
}

/** SHA-256 of a file, streamed so a 3.7 MB model never lands in memory twice. */
function sha256File(path) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    createReadStream(path)
      .on('error', reject)
      .on('data', (chunk) => hash.update(chunk))
      .on('end', () => resolve(hash.digest('hex')));
  });
}

/**
 * Download to a temp file, verify size + SHA-256, then rename into place.
 * Nothing partial is ever visible at `dest`, so a failed run cannot poison the
 * cache for every subsequent build.
 */
function downloadModel(url, dest, redirects = 0) {
  const tmp = `${dest}.download`;
  return new Promise((resolve, reject) => {
    let settled = false;
    const fail = (err) => {
      if (settled) return;
      settled = true;
      rmSync(tmp, { force: true });
      reject(err);
    };

    const req = https.get(url, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
        res.resume();
        if (redirects >= MAX_REDIRECTS) return fail(new Error('too many redirects'));
        if (settled) return;
        settled = true;
        return resolve(downloadModel(res.headers.location, dest, redirects + 1));
      }
      if (res.statusCode !== 200) {
        res.resume();
        return fail(new Error(`HTTP ${res.statusCode}`));
      }

      const hash = createHash('sha256');
      let bytes = 0;
      const file = createWriteStream(tmp);

      res.on('data', (chunk) => {
        hash.update(chunk);
        bytes += chunk.length;
      });
      res.on('error', fail);
      file.on('error', fail);
      res.pipe(file);

      file.on('finish', () => {
        file.close(() => {
          if (settled) return;
          const digest = hash.digest('hex');
          if (bytes !== MODEL_BYTES) {
            return fail(new Error(`truncated download: got ${bytes} bytes, expected ${MODEL_BYTES}`));
          }
          if (digest !== MODEL_SHA256) {
            return fail(new Error(`sha256 mismatch: got ${digest}, expected ${MODEL_SHA256}`));
          }
          settled = true;
          renameSync(tmp, dest);
          resolve();
        });
      });
    });

    req.setTimeout(REQUEST_TIMEOUT_MS, () => {
      req.destroy(new Error(`timed out after ${REQUEST_TIMEOUT_MS} ms`));
    });
    req.on('error', fail);
  });
}
