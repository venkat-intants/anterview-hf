// Interview — LiveKit voice + video interview page.
//
// Flow:
//   1. Interview renders InterviewIntro (full-screen intro / consent video).
//   2. Candidate clicks "Begin interview" or "Skip" → introDone becomes true.
//   3. Interview renders LiveKitInterview which auto-connects to the LiveKit room.
//      The user gesture from step 2 satisfies browser autoplay policies for the
//      avatar video and mic permission prompt.
//
// sessionId is read from the URL param (:sessionId).
// If the route is reached without a sessionId (shouldn't happen in normal flow)
// the page renders a graceful "missing session" error rather than crashing.

import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import InterviewIntro from '@/components/InterviewIntro';
import LiveKitInterview from '@/features/interview/LiveKitInterview';
import { postConsent } from '@/api/consent';
import type { Language } from '@/types/interview';
import { StatusTag } from '@/design/components/primitives';
import { XCircle } from '@/design/components/icons';
import { cn } from '@/lib/utils';

// Language preference written to localStorage by StartInterview / JobsList
// before createSession is called. Resolved once at page mount.
const LANGUAGE_STORAGE_KEY = 'intants:interview-language';

function resolveLanguage(): Language {
  const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (stored === 'en' || stored === 'hi' || stored === 'te') return stored;
  return 'en';
}

export default function Interview() {
  const { t } = useTranslation();
  const { sessionId } = useParams<{ sessionId: string }>();
  const [introDone, setIntroDone] = useState(false);
  // Phase A — whether the candidate consented to their camera in the intro.
  const [cameraConsented, setCameraConsented] = useState(false);

  // Resolved once at mount — language cannot change mid-session.
  const [sessionLanguage] = useState<Language>(resolveLanguage);

  // Intro complete: persist the camera-consent decision (best-effort — a ledger
  // failure must never block the interview) and advance to the live session.
  function handleIntroDone(consented: boolean) {
    setCameraConsented(consented);
    if (consented) {
      void postConsent(undefined, 'video_capture').catch(() => {
        // Non-fatal: the DPDP video_capture row failed to write. We still honour
        // the candidate's in-session choice; the ledger write can be retried.
      });
    }
    setIntroDone(true);
  }

  // Guard: sessionId must be present — the route definition guarantees it
  // but we handle the defensive case explicitly.
  if (!sessionId) {
    return (
      <main
        className={cn(
          'relative min-h-screen flex flex-col items-center justify-center bg-black px-4 text-white',
          'overflow-hidden',
        )}
      >
        {/* Aurora ambient glow — presentational only */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 overflow-hidden"
        >
          <div
            className="av-aurora-blob absolute left-1/2 top-1/2 h-[60vh] w-[60vh] -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              background:
                'radial-gradient(circle,rgba(var(--accent-rgb),0.18),transparent 65%)',
              filter: 'blur(80px)',
            }}
          />
        </div>

        {/* Glass card */}
        <div
          className={cn(
            'relative z-10 flex flex-col items-center gap-4 rounded-[24px] border',
            'border-[rgba(230,113,79,0.2)] bg-[rgba(15,15,16,0.85)] backdrop-blur-md',
            'px-8 py-10 shadow-[0_0_40px_rgba(0,0,0,0.6)] max-w-sm w-full text-center',
          )}
          role="alert"
        >
          <XCircle
            className="h-10 w-10 text-[#e6714f]"
            aria-hidden="true"
          />
          <StatusTag tone="ember" className="text-[13px]">
            {t('interviewPage.missingSession')}
          </StatusTag>
        </div>
      </main>
    );
  }

  if (!introDone) {
    return (
      <InterviewIntro
        language={sessionLanguage}
        onDone={handleIntroDone}
      />
    );
  }

  return <LiveKitInterview sessionId={sessionId} cameraConsented={cameraConsented} />;
}
