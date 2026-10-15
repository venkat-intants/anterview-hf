// InterviewIntro — full-screen intro video gate shown before the voice-first
// interview session begins.
//
// Because browser autoplay-with-audio requires a user gesture, we show a
// "Begin interview" button. Clicking it calls video.play() (gesture-backed,
// so audio is allowed) and hides the button. A "Skip" link bypasses the video
// entirely. Both paths call onDone() to hand control back to Interview.
//
// Graceful fallback: if the video fails to load or decode (e.g. missing clip),
// onDone() is called immediately so a broken asset NEVER blocks the interview.

import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { requestFullscreen, isFullscreenSupported } from '@/features/interview/useFullscreen';
import { Button } from '@/components/ui/button';
import { Play } from '@/design/components/icons';
import type { Language } from '../types/interview';

interface InterviewIntroProps {
  language: Language;
  /** Called when the intro completes. cameraConsented reflects the checkbox. */
  onDone: (cameraConsented: boolean) => void;
}

export default function InterviewIntro({ language, onDone }: InterviewIntroProps) {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);
  // Whether the user has tapped "Begin interview" — hides the CTA once started.
  const [started, setStarted] = useState(false);
  // Phase A — explicit opt-in for the candidate camera (DPDP video_capture).
  // Default ON so the 1:1 video experience is the norm, but the candidate can
  // untick it; an unticked box → audio-only, exactly the previous behaviour.
  const [cameraConsent, setCameraConsent] = useState(true);
  // Read the latest checkbox value inside event-driven safeDone calls.
  const cameraConsentRef = useRef(cameraConsent);
  cameraConsentRef.current = cameraConsent;
  // Guard: prevent onDone being called more than once if multiple events fire.
  const doneCalledRef = useRef(false);
  // True when a fullscreen request was denied — the interview is gated on
  // fullscreen, so we surface a retry hint instead of proceeding.
  const [fsDenied, setFsDenied] = useState(false);

  const safeDone = useCallback(() => {
    if (doneCalledRef.current) return;
    doneCalledRef.current = true;
    onDone(cameraConsentRef.current);
  }, [onDone]);

  // Begin: the interview runs in fullscreen, which can ONLY be requested from a
  // user gesture (this click). If the candidate denies it, we do not start —
  // they must allow fullscreen and click again.
  async function handleBegin() {
    const entered = await requestFullscreen();
    // Block only if fullscreen is supported but the candidate refused it. On a
    // browser without the Fullscreen API we proceed (can't enforce it).
    if (!entered && isFullscreenSupported()) {
      setFsDenied(true);
      return;
    }
    setFsDenied(false);
    setStarted(true);
    videoRef.current?.play().catch(() => {
      // If play() rejects (e.g. jsdom or permission policy), the video won't
      // play but the UI stays in "started" state. onEnded or onError will
      // still fire if appropriate; otherwise the candidate can click "Skip".
    });
  }

  // Skip: same fullscreen gate, then go straight to the live session.
  async function handleSkip() {
    const entered = await requestFullscreen();
    if (!entered && isFullscreenSupported()) {
      setFsDenied(true);
      return;
    }
    setFsDenied(false);
    safeDone();
  }

  // Src: default to English if the language is not one of the known clips.
  const validLang: Language = language === 'hi' || language === 'te' ? language : 'en';
  const videoSrc = `/intro/intro_${validLang}.mp4`;

  return (
    <div
      className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-background px-4 py-8"
      data-testid="interview-intro"
    >
      <div className="flex w-full max-w-2xl flex-col items-center gap-6">
        {/* Header */}
        <div className="text-center">
          <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--ui-info)]">
            {t('interviewIntro.brand')}
          </span>
          <h1 className="mt-1 text-[28px] font-bold tracking-[-0.6px] text-foreground sm:text-[32px]">
            {t('interviewIntro.meetTitle')}
          </h1>
        </div>

        {/* Video container */}
        <div className="w-full overflow-hidden rounded-card border border-border bg-card shadow-elevated">
          {/*
           * onError: video 404, decode failure, or codec unsupported — call
           *   onDone so a broken clip never blocks the interview.
           * onEnded: natural end of video playback — transition to the session.
           * playsInline: required on iOS to prevent fullscreen hijack.
           * preload="auto": buffer eagerly so playback starts without delay
           *   after the user gesture.
           */}
          <video
            ref={videoRef}
            src={videoSrc}
            playsInline
            preload="auto"
            onEnded={safeDone}
            onError={safeDone}
            aria-label={t('interviewIntro.videoAria')}
            className="aspect-video w-full bg-[var(--ui-inset-strong)] object-cover"
            data-testid="intro-video"
          />
        </div>

        {/* Camera consent — Phase A (DPDP video_capture) */}
        <label className="flex w-full max-w-md cursor-pointer items-start gap-3 rounded-[14px] border border-border bg-card px-4 py-3.5">
          <input
            type="checkbox"
            checked={cameraConsent}
            onChange={(e) => setCameraConsent(e.target.checked)}
            className="mt-0.5 h-5 w-5 flex-none rounded border-[var(--ui-line-strong)] accent-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            data-testid="camera-consent-checkbox"
          />
          <span className="text-[14px] leading-snug text-foreground">
            {t('interviewIntro.cameraConsent')}
          </span>
        </label>

        {/* CTA buttons */}
        <div className="flex w-full max-w-xs flex-col items-center gap-3">
          {!started && (
            <Button
              type="button"
              size="lg"
              onClick={() => void handleBegin()}
              aria-label={t('interviewIntro.beginLabel')}
              className="w-full"
              data-testid="begin-button"
            >
              {t('interviewIntro.beginButton')}
              <Play aria-hidden="true" />
            </Button>
          )}

          <button
            type="button"
            onClick={() => void handleSkip()}
            aria-label={t('interviewIntro.skipLabel')}
            className="min-h-[40px] rounded px-2 text-[14px] font-semibold text-muted-foreground underline underline-offset-2 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            data-testid="skip-button"
          >
            {t('interviewIntro.skipButton')}
          </button>

          {/* Fullscreen gate: required to start; denial shows a retry hint. */}
          {fsDenied ? (
            <p
              className="rounded-[12px] bg-[var(--ui-warn-wash)] px-3 py-2 text-center text-[14px] text-foreground"
              role="alert"
              data-testid="fullscreen-denied"
            >
              {t('interviewIntro.fullscreenRequired')}
            </p>
          ) : (
            <p className="text-center text-[13px] text-muted-foreground">
              {t('interviewIntro.fullscreenNote')}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
