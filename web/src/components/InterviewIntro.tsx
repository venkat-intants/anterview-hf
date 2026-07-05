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
      className="h-screen flex flex-col items-center justify-center bg-aurora relative overflow-hidden"
      data-testid="interview-intro"
    >
      {/* Midnight veil — keeps content legible over the darker upper region */}
      <div
        className="absolute inset-0 bg-gradient-to-b from-black/85 via-black/55 to-black/30 pointer-events-none"
        aria-hidden="true"
      />

      {/* Video player */}
      <div className="relative z-10 w-full max-w-2xl px-4 flex flex-col items-center gap-6">
        {/* Header */}
        <div className="text-center">
          <span className="text-caption font-semibold text-electric-signal tracking-wider uppercase">
            {t('interviewIntro.brand')}
          </span>
          <h1 className="mt-1 text-heading font-semibold text-white">{t('interviewIntro.meetTitle')}</h1>
        </div>

        {/* Video container */}
        <div className="w-full rounded-3xl overflow-hidden shadow-elevated border border-white/10 bg-black">
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
            aria-label="AI interviewer introduction video"
            className="w-full aspect-video object-cover"
            data-testid="intro-video"
          />
        </div>

        {/* Camera consent — Phase A (DPDP video_capture) */}
        <label className="flex items-start gap-3 w-full max-w-md rounded-xl bg-obsidian/70 backdrop-blur-sm border border-white/10 px-4 py-3 cursor-pointer">
          <input
            type="checkbox"
            checked={cameraConsent}
            onChange={(e) => setCameraConsent(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-white/30 bg-transparent accent-electric-signal"
            data-testid="camera-consent-checkbox"
          />
          <span className="text-body-sm text-mist leading-snug">
            {t('interviewIntro.cameraConsent')}
          </span>
        </label>

        {/* CTA buttons */}
        <div className="flex flex-col items-center gap-3 w-full max-w-xs">
          {!started && (
            <button
              type="button"
              onClick={() => void handleBegin()}
              aria-label={t('interviewIntro.beginLabel')}
              className="w-full flex items-center justify-center gap-2 rounded-[9px] bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-6 py-3.5 text-base shadow-elevated transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-black"
              data-testid="begin-button"
            >
              {t('interviewIntro.beginButton')}
              {/* Play triangle */}
              <svg
                aria-hidden="true"
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="currentColor"
                className="h-5 w-5"
              >
                <path
                  fillRule="evenodd"
                  d="M4.5 5.653c0-1.427 1.529-2.33 2.779-1.643l11.54 6.347c1.295.712 1.295 2.573 0 3.286L7.28 19.99c-1.25.687-2.779-.217-2.779-1.643V5.653Z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          )}

          <button
            type="button"
            onClick={() => void handleSkip()}
            aria-label={t('interviewIntro.skipLabel')}
            className="text-body-sm text-mist hover:text-white underline underline-offset-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-black rounded"
            data-testid="skip-button"
          >
            {t('interviewIntro.skipButton')}
          </button>

          {/* Fullscreen gate: required to start; denial shows a retry hint. */}
          {fsDenied ? (
            <p
              className="text-body-sm text-amber-glow text-center"
              role="alert"
              data-testid="fullscreen-denied"
            >
              {t('interviewIntro.fullscreenRequired')}
            </p>
          ) : (
            <p className="text-caption text-ash text-center">
              {t('interviewIntro.fullscreenNote')}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
