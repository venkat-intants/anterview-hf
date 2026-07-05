// LiveKitInterview — full-screen real-time interview (LiveKit + Simli avatar).
//
// Renders the avatar video full-bleed behind two overlay bars:
//   - Top bar: glassy status pill (left) + live elapsed-time pill (right).
//   - Bottom dock: mic-toggle + end button inside a floating glassy pill.
//
// All room lifecycle lives in useLiveKitInterview — this file is
// presentation-only (no connection / room / navigation logic changes).
//
// Mounted by Interview.tsx AFTER the intro gate (so the connect() call and the
// mic permission prompt are user-gesture initiated).

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Mic, MicOff, Video, VideoOff, PhoneOff, Loader2, AlertCircle, Clock, ShieldCheck, Maximize } from 'lucide-react';
import { useLiveKitInterview } from '@/hooks/useLiveKitInterview';
import { useProctoring } from '@/features/interview/useProctoring';
import { useFullscreen, requestFullscreen, exitFullscreen } from '@/features/interview/useFullscreen';
import type { LiveKitStatus } from '@/hooks/useLiveKitInterview';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface LiveKitInterviewProps {
  sessionId: string;
  /**
   * Whether the candidate consented to their camera being used (Phase A — 1:1
   * video). When true the hook publishes the webcam track and shows a self-view.
   * Defaults to false → audio-only, exactly the previous behaviour.
   */
  cameraConsented?: boolean;
}


// Zero-pad a number to 2 digits.
function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

// Format a total elapsed-seconds count as mm:ss.
function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${pad2(m)}:${pad2(s)}`;
}

export default function LiveKitInterview({ sessionId, cameraConsented = false }: LiveKitInterviewProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const {
    status,
    error,
    isMicEnabled,
    isCameraEnabled,
    videoRef,
    localVideoRef,
    connect,
    disconnect,
    toggleMic,
    toggleCamera,
  } = useLiveKitInterview(sessionId, cameraConsented);

  // Phase B — proctoring runs only when the candidate consented to the camera
  // and the room is connected. It reads the SAME local self-view video element,
  // detects gaze/face/tab signals in-browser, and emits events to the backend.
  const isConnectedForProctoring = status === 'connected';
  const { ready: proctoringReady, activeWarning, calibrating } = useProctoring({
    sessionId,
    videoRef: localVideoRef,
    enabled: cameraConsented && isConnectedForProctoring,
  });

  // Fullscreen guard — the interview runs in fullscreen (entered at the intro
  // gate). Leaving it mid-interview is a task-switch: useProctoring already emits
  // a `fullscreen_exit` integrity event on the same `fullscreenchange`, and we
  // block the session behind a re-entry overlay below until the candidate is back.
  const { isFullscreen, supported: fullscreenSupported } = useFullscreen();

  // Always drop out of fullscreen when leaving the interview (End, natural
  // completion, or any unmount) so the candidate isn't stranded fullscreen on
  // the results page.
  useEffect(() => {
    return () => {
      void exitFullscreen();
    };
  }, []);

  // Resolve translated status labels — memoised so the object is only rebuilt
  // when the active locale changes, not on every 1-second timer tick.
  const STATUS_LABEL = useMemo<Record<LiveKitStatus, string>>(() => ({
    idle: t('interview.statusIdle'),
    'fetching-token': t('interview.statusFetchingToken'),
    connecting: t('interview.statusConnecting'),
    connected: t('interview.statusConnected'),
    reconnecting: t('interview.statusReconnecting'),
    disconnected: t('interview.statusDisconnected'),
    error: t('interview.statusError'),
  }), [t]);

  const [ending, setEnding] = useState(false);

  // Avatar-ready gate — flips true the moment the avatar <video> renders its
  // first frame (onLoadedData / onPlaying). Kept false until then so we can
  // show a "Connecting to your interviewer…" overlay during the room-connected
  // → avatar-actually-visible window (~5–15 s with Tavus/Simli).
  const [avatarReady, setAvatarReady] = useState(false);

  // Reset avatarReady whenever the room leaves the connected state so that a
  // reconnect cycle always re-shows the avatar-joining overlay.
  // NOTE: must be its own effect — do NOT merge into the timer effect below.
  useEffect(() => {
    if (status !== 'connected') setAvatarReady(false);
  }, [status]);

  // Safety valve — if the avatar's first frame never fires onLoadedData/onPlaying
  // within 30 s of connecting (slow/cold/hung provider), force-clear the overlay
  // so the candidate is never stranded on "Connecting you to your interviewer…".
  // The video events still win in the normal path (they fire well before 30 s).
  useEffect(() => {
    if (status !== 'connected' || avatarReady) return;
    const id = setTimeout(() => setAvatarReady(true), 30_000);
    return () => clearTimeout(id);
  }, [status, avatarReady]);

  // Live timer — counts seconds from the moment the room reaches 'connected'.
  // The ONLY new state/effect added by this redesign pass.
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (status !== 'connected') return;
    // Reset on each new connection (handles retry scenarios).
    // NOTE: elapsed time reflects this connection window only (resets on reconnect).
    setElapsedSeconds(0);
    const id = setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(id);
  }, [status]);

  // Connect EXACTLY ONCE on mount. `connect` is intentionally NOT in the deps:
  // it's a useCallback whose identity changes, and re-running this effect would
  // tear down + rebuild the LiveKit room (the connect/leave/reconnect flapping
  // that prevents the WebRTC peer connection from ever stabilising). The hook's
  // own guards make connect() idempotent; we call it one time here.
  useEffect(() => {
    void connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleEnd = useCallback(async () => {
    setEnding(true);
    await disconnect();
    navigate(`/interview/${sessionId}/complete`, {
      state: { endedEarly: true },
      replace: true,
    });
  }, [disconnect, navigate, sessionId]);

  // Natural completion: backend closes the room (agent finished all questions).
  // When the room disconnects and we did NOT initiate it via the End button,
  // route to the completion/polling page without the endedEarly flag so
  // InterviewComplete will poll for the scorecard.
  useEffect(() => {
    if (status === 'disconnected' && !ending) {
      navigate(`/interview/${sessionId}/complete`, { replace: true });
    }
  }, [status, ending, navigate, sessionId]);

  const isConnected = status === 'connected';
  const isBusy =
    status === 'idle' ||
    status === 'fetching-token' ||
    status === 'connecting' ||
    status === 'reconnecting';

  return (
    <div className="fixed inset-0 overflow-hidden bg-black text-white">

      {/* ── Avatar video — fills the entire viewport ──────────────────────── */}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        onLoadedData={() => setAvatarReady(true)}
        onPlaying={() => setAvatarReady(true)}
        className={cn(
          'absolute inset-0 h-full w-full object-cover object-center',
          'transition-opacity duration-700',
          isConnected ? 'opacity-100' : 'opacity-0',
        )}
      />

      {/* ── Connecting / error overlay (z-10, below the HUD bars) ─────────── */}
      {!isConnected && (
        <div
          className={cn(
            'absolute inset-0 z-10 flex flex-col items-center justify-center',
            'gap-4 px-6 text-center bg-black/50 backdrop-blur-sm',
          )}
        >
          {status === 'error' ? (
            <>
              <AlertCircle className="h-10 w-10 text-destructive" aria-hidden="true" />
              <p className="max-w-sm text-body-sm text-mist">
                {error ?? t('interview.errorFallback')}
              </p>
              <Button onClick={() => void connect()} variant="secondary">
                {t('interview.tryAgain')}
              </Button>
            </>
          ) : (
            <>
              <Loader2 className="h-10 w-10 animate-spin text-electric-signal" aria-hidden="true" />
              <p className="text-body-sm text-ash">{STATUS_LABEL[status]}</p>
            </>
          )}
        </div>
      )}

      {/* ── Avatar-joining overlay (z-10, room connected but frame not yet rendered) ── */}
      {/* Sits ABOVE the video (which is still black at this point) but BELOW    */}
      {/* the HUD bars (z-20) so mic / End remain fully clickable.               */}
      {isConnected && !avatarReady && (
        <div
          className={cn(
            'absolute inset-0 z-10 flex flex-col items-center justify-center',
            'gap-4 px-6 text-center bg-black/80 backdrop-blur-sm',
          )}
        >
          <Loader2 className="h-10 w-10 animate-spin text-electric-signal" aria-hidden="true" />
          <p className="text-body-sm text-mist">{t('interview.connectingOverlayTitle')}</p>
          <p className="text-caption text-ash">{t('interview.connectingOverlaySub')}</p>
        </div>
      )}

      {/* ── Fullscreen re-entry gate (z-40, above EVERYTHING) ─────────────── */}
      {/* Leaving fullscreen mid-interview is treated as a task switch (logged as */}
      {/* a fullscreen_exit integrity event). The interview is blocked until the   */}
      {/* candidate returns to fullscreen — re-entry needs a user gesture, so we   */}
      {/* surface a button rather than forcing it programmatically.                */}
      {isConnected && fullscreenSupported && !isFullscreen && (
        <div
          className={cn(
            'absolute inset-0 z-40 flex flex-col items-center justify-center',
            'gap-4 px-6 text-center bg-black/90 backdrop-blur-md',
          )}
          role="alertdialog"
          aria-modal="true"
        >
          <AlertCircle className="h-10 w-10 text-amber-glow" aria-hidden="true" />
          <p className="max-w-md text-subheading font-semibold text-white">
            {t('interview.fullscreenExitTitle')}
          </p>
          <p className="max-w-md text-body-sm text-ash">{t('interview.fullscreenExitSub')}</p>
          <Button onClick={() => void requestFullscreen()} variant="secondary" className="gap-2">
            <Maximize className="h-4 w-4" aria-hidden="true" />
            {t('interview.returnFullscreen')}
          </Button>
        </div>
      )}

      {/* ── Candidate self-view (PiP) — only when camera was consented ─────── */}
      {/* Mirrored locally (scale-x-[-1]) so it feels like a mirror. Sits above  */}
      {/* the bottom dock. Hidden until the camera track is actually live.       */}
      {cameraConsented && (
        <div
          className={cn(
            'absolute bottom-24 right-4 z-20 sm:bottom-28 sm:right-6',
            'h-32 w-24 sm:h-40 sm:w-32 overflow-hidden rounded-xl',
            'border border-white/10 bg-obsidian/80 shadow-elevated',
            'transition-opacity duration-500',
            isCameraEnabled ? 'opacity-100' : 'opacity-0 pointer-events-none',
          )}
        >
          <video
            ref={localVideoRef}
            autoPlay
            playsInline
            muted
            className="h-full w-full object-cover scale-x-[-1]"
          />
        </div>
      )}

      {/* ── Top overlay bar (z-20) ────────────────────────────────────────── */}
      <div
        className={cn(
          'absolute inset-x-0 top-0 z-20',
          'bg-gradient-to-b from-black/60 to-transparent',
          'p-4 sm:p-6 flex items-center justify-between',
        )}
      >
        {/* Status pill */}
        <div
          className={cn(
            'rounded-full bg-white/[0.08] backdrop-blur border border-white/10 px-3 py-1.5',
            'text-body-sm text-mist flex items-center gap-2',
          )}
        >
          <span
            className={cn(
              'inline-block h-2.5 w-2.5 rounded-full flex-shrink-0',
              isConnected
                ? 'bg-vivid-mint'
                : status === 'error'
                  ? 'bg-destructive'
                  : 'bg-amber-glow animate-pulse',
            )}
            aria-hidden="true"
          />
          <span>{STATUS_LABEL[status]}</span>
        </div>

        <div className="flex items-center gap-2">
          {/* Proctoring indicator — shown when camera proctoring is consented + live */}
          {cameraConsented && isConnected && (
            <div
              className={cn(
                'rounded-full bg-white/[0.08] backdrop-blur border border-white/10 px-3 py-1.5',
                'text-caption text-mist flex items-center gap-1.5',
              )}
              title={
                proctoringReady
                  ? 'Interview integrity monitoring is active (on-device).'
                  : 'Starting integrity monitoring…'
              }
            >
              <ShieldCheck
                className={cn(
                  'h-3.5 w-3.5 flex-shrink-0',
                  proctoringReady ? 'text-vivid-mint' : 'text-amber-glow animate-pulse',
                )}
                aria-hidden="true"
              />
              <span className="hidden sm:inline">
                {proctoringReady ? t('interview.proctoringOn') : t('interview.proctoringStarting')}
              </span>
            </div>
          )}

          {/* Live timer pill — only shown once the avatar frame is visible */}
          {avatarReady && (
            <div
              className={cn(
                'rounded-full bg-white/[0.08] backdrop-blur border border-white/10 px-3 py-1.5',
                'text-body-sm text-white flex items-center gap-2',
              )}
            >
              <Clock className="h-3.5 w-3.5 flex-shrink-0 text-electric-signal" aria-hidden="true" />
              <span className="tabular-nums">{formatElapsed(elapsedSeconds)}</span>
            </div>
          )}
        </div>
      </div>

      {/* ── Live candidate nudge (z-30) — sustained proctoring lapse ─────── */}
      {isConnected && activeWarning && (
        <div className="absolute inset-x-0 top-20 z-30 flex justify-center px-4 pointer-events-none">
          <div
            role="status"
            aria-live="assertive"
            className={cn(
              'flex items-center gap-2.5 rounded-xl px-4 py-3 shadow-elevated',
              'bg-amber-glow/95 text-amber-950 max-w-md',
            )}
          >
            <AlertCircle className="h-5 w-5 flex-shrink-0" aria-hidden="true" />
            <p className="text-body-sm font-semibold">{t(`interview.warn_${activeWarning}`)}</p>
          </div>
        </div>
      )}

      {/* ── Calibration banner (z-30) — brief "hold still" at start ──────── */}
      {isConnected && calibrating && !activeWarning && (
        <div className="absolute inset-x-0 top-20 z-30 flex justify-center px-4 pointer-events-none">
          <div className="flex items-center gap-2.5 rounded-xl bg-white/[0.08] backdrop-blur border border-white/10 px-4 py-3 text-mist shadow-elevated max-w-md">
            <Loader2 className="h-5 w-5 flex-shrink-0 animate-spin text-electric-signal" aria-hidden="true" />
            <p className="text-body-sm font-medium">{t('interview.calibrating')}</p>
          </div>
        </div>
      )}

      {/* ── Bottom overlay / control dock (z-20) ─────────────────────────── */}
      <div
        className={cn(
          'absolute inset-x-0 bottom-0 z-20',
          'bg-gradient-to-t from-black/70 to-transparent',
          'p-4 sm:p-8 flex items-center justify-center',
        )}
      >
        {/* Floating glassy dock pill */}
        <div
          className={cn(
            'rounded-full bg-obsidian/70 backdrop-blur-md border border-white/10',
            'shadow-elevated px-3 py-2 flex items-center gap-3',
          )}
        >
          {/* Mic toggle */}
          <button
            type="button"
            onClick={() => void toggleMic()}
            disabled={!isConnected}
            className={cn(
              'h-11 w-11 rounded-full flex items-center justify-center',
              'transition-colors focus-visible:outline-none focus-visible:ring-2',
              'focus-visible:ring-white/40 disabled:pointer-events-none disabled:opacity-40',
              isMicEnabled
                ? 'bg-white/10 text-white hover:bg-white/20'
                : 'bg-destructive/25 hover:bg-destructive/40',
            )}
            aria-label={isMicEnabled ? t('interview.muteMic') : t('interview.unmuteMic')}
          >
            {isMicEnabled ? (
              <Mic className="h-5 w-5" aria-hidden="true" />
            ) : (
              <MicOff className="h-5 w-5 text-destructive" aria-hidden="true" />
            )}
          </button>

          {/* Camera toggle — only present when the candidate consented to video */}
          {cameraConsented && (
            <button
              type="button"
              onClick={() => void toggleCamera()}
              disabled={!isConnected}
              className={cn(
                'h-11 w-11 rounded-full flex items-center justify-center',
                'transition-colors focus-visible:outline-none focus-visible:ring-2',
                'focus-visible:ring-white/40 disabled:pointer-events-none disabled:opacity-40',
                isCameraEnabled
                  ? 'bg-white/10 text-white hover:bg-white/20'
                  : 'bg-destructive/25 hover:bg-destructive/40',
              )}
              aria-label={isCameraEnabled ? t('interview.cameraOff') : t('interview.cameraOn')}
            >
              {isCameraEnabled ? (
                <Video className="h-5 w-5" aria-hidden="true" />
              ) : (
                <VideoOff className="h-5 w-5 text-destructive" aria-hidden="true" />
              )}
            </button>
          )}

          {/* Divider */}
          <div className="h-6 w-px bg-white/10" aria-hidden="true" />

          {/* End interview */}
          <Button
            onClick={() => void handleEnd()}
            disabled={ending}
            variant="destructive"
            size="lg"
            className="rounded-full"
            aria-label={t('interview.endLabel')}
          >
            {ending ? (
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            ) : (
              <PhoneOff className="h-5 w-5" aria-hidden="true" />
            )}
            <span>{t('interview.endButton')}</span>
          </Button>
        </div>
      </div>

      {/* busy hint for screen readers */}
      {isBusy && (
        <span className="sr-only" role="status" aria-live="polite">
          {STATUS_LABEL[status]}
        </span>
      )}
    </div>
  );
}
