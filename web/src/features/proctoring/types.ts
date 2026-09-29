// Shared proctoring event/transport types.
//
// The camera module (proctorLogic + useProctoring + proctorWorker) is used by
// two surfaces — the interview (features/interview/LiveKitInterview.tsx) and
// the exam (pages/exam/useExamProctor.ts) — that talk to two different
// backends with two different wire shapes. This module stays deliberately
// transport-agnostic: it produces `ProctorEvent`s and hands them to whatever
// `ProctorEventSubmitter` the caller injects. Neither the event shape nor the
// submitter type says anything about HTTP, batching, or which service is on
// the other end — that is entirely the caller's concern.

import type { ProctorCondition } from './proctorLogic';

/**
 * The full vocabulary this module can emit. `ProctorCondition` (the three
 * camera-derived ranged conditions) plus the cheap browser signals emitted
 * only when the caller opts into `browserEvents` (see useProctoring), plus
 * the `proctor_error` diagnostic (the detection pipeline itself failed —
 * zero score impact, exists so "no camera flags" is distinguishable from
 * "detection never ran").
 *
 * NOT every surface's backend accepts every one of these — e.g. the exam's
 * `/exam/integrity-event` ingest only recognises the three camera
 * conditions plus fullscreen_exit/tab_blur (see the camera-proctoring
 * contract). A submitter is expected to filter to whatever its own backend's
 * vocabulary actually is and reject/drop the rest client-side rather than
 * send something the server would reject anyway.
 */
export type ProctorEventType =
  | ProctorCondition
  | 'tab_blur'
  | 'fullscreen_exit'
  | 'copy'
  | 'paste'
  | 'proctor_error';

/** One event this module produces, in transport-agnostic wire shape. */
export interface ProctorEvent {
  type: ProctorEventType;
  /** ISO-8601 UTC start timestamp. */
  started_at: string;
  /** ISO-8601 UTC end timestamp for ranged events; omitted for instantaneous ones. */
  ended_at?: string;
  metadata?: Record<string, unknown>;
}

/** What a submitter reports back after sending a batch (or one event). */
export interface ProctorSubmitResult {
  /** Latest known cumulative integrity score (0-100), or null when the
   *  transport has none to report (e.g. an exam event the backend rejected,
   *  or a batch it has not finished scoring). */
  integrityScore: number | null;
}

/**
 * Injected by the caller — how detected events actually leave the browser.
 * Called with the full flushed batch, which MAY be empty (the interview uses
 * an empty call as a "proctoring is active" heartbeat). Must never throw:
 * proctoring must never break the interview or the exam, so a submitter
 * should swallow its own transport errors and resolve to `null`.
 */
export type ProctorEventSubmitter = (events: ProctorEvent[]) => Promise<ProctorSubmitResult | null>;
