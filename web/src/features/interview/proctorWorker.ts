// proctorWorker — runs MediaPipe FaceLandmarker inference OFF the main thread.
//
// The heavy ~2 fps face/gaze inference is the only CPU-significant part of
// proctoring; running it here keeps the interview UI (avatar video, buttons,
// animations) smooth on low-end devices. The main thread keeps ALL decision and
// state logic — this worker only turns a video frame into gaze signals.
//
// IMPORTANT: this is instantiated as a CLASSIC worker (see useProctoring), not a
// module worker. In a module worker MediaPipe loads its wasm loader via ESM
// import(), which Vite's dev server rejects for /public files; as a classic
// worker MediaPipe falls back to importScripts(), a plain static fetch of the
// self-hosted wasm. Vite still bundles the imports below into the worker.
//
// Protocol:
//   main → worker  { type: 'init', wasmUrl, modelUrl }
//   worker → main  { type: 'ready' } | { type: 'error', error }
//   main → worker  { type: 'frame', bitmap: ImageBitmap, ts: number }  (transferable)
//   worker → main  { type: 'signals', n, signals } | { type: 'error', error }
//
// The signal-parsing is the SAME pure function the main-thread fallback uses
// (proctorLogic.extractGazeSignals), so both paths produce identical results.

import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';
import { extractGazeSignals, type FrameData } from './proctorLogic';

let landmarker: FaceLandmarker | null = null;

const post = (msg: unknown, transfer?: Transferable[]) =>
  (self as unknown as Worker).postMessage(msg, transfer ?? []);

self.onmessage = async (e: MessageEvent) => {
  const msg = e.data as
    | { type: 'init'; wasmUrl: string; modelUrl: string }
    | { type: 'frame'; bitmap: ImageBitmap; ts: number };

  if (msg.type === 'init') {
    try {
      const fileset = await FilesetResolver.forVisionTasks(msg.wasmUrl);
      landmarker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: msg.modelUrl },
        runningMode: 'VIDEO',
        numFaces: 2,
        outputFacialTransformationMatrixes: true,
        outputFaceBlendshapes: true,
      });
      post({ type: 'ready' });
    } catch (err) {
      post({ type: 'error', error: String(err) });
    }
    return;
  }

  if (msg.type === 'frame') {
    const { bitmap, ts } = msg;
    if (!landmarker) {
      bitmap.close();
      return;
    }
    try {
      const result = landmarker.detectForVideo(bitmap, ts);
      const frame: FrameData = {
        faces: result.faceLandmarks ?? [],
        matrix: result.facialTransformationMatrixes?.[0]?.data as number[] | undefined,
        blendshapes: result.faceBlendshapes?.[0]?.categories,
      };
      const { n, signals } = extractGazeSignals(frame);
      post({ type: 'signals', n, signals });
    } catch {
      post({ type: 'error', error: 'detect' });
    } finally {
      bitmap.close();
    }
  }
};
