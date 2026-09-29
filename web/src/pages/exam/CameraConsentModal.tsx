// CameraConsentModal — the dedicated camera-proctoring consent gate for the
// exam-taking flow (camera-proctoring contract §3).
//
// DPDP §6(1): consent is freely given or it is not consent — so this is its
// OWN step, never bundled with the exam's separate recording/scoring consent
// checkbox on the intro card (PublicExam.tsx). It tells the candidate, in
// plain words and before they decide: that the camera is on during the exam,
// what is detected, that no video or image is ever recorded or sent
// anywhere, and that only these detection events are stored.
//
// Accessible the same way components/ConsentModal.tsx already is: role
// "dialog", aria-modal, focus trapped inside, Escape declines, "Turn on
// camera monitoring" receives focus on mount.
//
// i18n: this copy is registered on the "higher bar" list at the top of
// lib/i18n.ts (see publicExam.cameraConsent* below) — HI/TE ship unreviewed.

import { useEffect, useRef, useCallback, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldCheck, Eye, Users, Video } from '@/design/components/icons';
import { GlassCard, Pill } from '@/design/components/primitives';

interface CameraConsentModalProps {
  /** The candidate agreed — calls `POST /exam/camera-consent` (grantCameraConsent)
   *  and only resolves once the ledger write has actually happened; the modal
   *  stays up (isSubmitting) until then, matching ConsentModal's pattern. */
  onAgree: () => Promise<void>;
  /** The candidate declined (Esc, "Decline" button, or backdrop is inert —
   *  there is no dismiss-without-deciding path, matching ConsentModal). */
  onDecline: () => void;
  /** True while the grantCameraConsent call is in flight. */
  isSubmitting: boolean;
  /** Set if grantCameraConsent failed — shown inline, modal stays open. */
  error: string | null;
}

const HEADING_ID = 'camera-consent-heading';

export default function CameraConsentModal({
  onAgree,
  onDecline,
  isSubmitting,
  error,
}: CameraConsentModalProps) {
  const { t } = useTranslation();
  const agreeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    agreeRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Escape') {
        onDecline();
        return;
      }
      if (e.key === 'Tab') {
        const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (!focusable || focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    },
    [onDecline],
  );

  function handleAgreeClick() {
    if (isSubmitting) return;
    void onAgree();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-midnight/90 px-4 backdrop-blur-md"
      data-testid="camera-consent-backdrop"
    >
      {/* GlassCard has a closed prop set (no arbitrary attribute pass-through),
          so the dialog role/keyboard-trap lives on this plain wrapper and
          GlassCard only supplies the visual surface inside it. */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={HEADING_ID}
        onKeyDown={handleKeyDown}
        tabIndex={-1}
        className="w-full max-w-md focus:outline-none"
      >
        <GlassCard className="p-6">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[rgba(39,201,63,0.15)] text-vivid-mint">
              <Video className="h-5 w-5" aria-hidden="true" />
            </span>
            <h2 id={HEADING_ID} className="text-subheading font-semibold text-foreground">
              {t('publicExam.cameraConsentTitle')}
            </h2>
          </div>

          <p className="mt-4 text-body-sm text-muted-foreground">
            {t('publicExam.cameraConsentIntro')}
          </p>

          <ul className="mt-4 space-y-3">
            <li className="flex items-start gap-2.5 text-body-sm text-muted-foreground">
              <Eye size={16} className="mt-0.5 shrink-0 text-electric" aria-hidden="true" />
              <span>{t('publicExam.cameraConsentDetects')}</span>
            </li>
            <li className="flex items-start gap-2.5 text-body-sm text-muted-foreground">
              <Users size={16} className="mt-0.5 shrink-0 text-electric" aria-hidden="true" />
              <span>{t('publicExam.cameraConsentMultiple')}</span>
            </li>
            <li className="flex items-start gap-2.5 text-body-sm font-medium text-foreground">
              <ShieldCheck
                size={16}
                className="mt-0.5 shrink-0 text-vivid-mint"
                aria-hidden="true"
              />
              <span>{t('publicExam.cameraConsentNoVideo')}</span>
            </li>
          </ul>

          <p className="mt-4 text-caption text-fog">{t('publicExam.cameraConsentDeclineNote')}</p>

          {error && (
            <div
              role="alert"
              className="mt-4 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-caption text-destructive"
            >
              {error}
            </div>
          )}

          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Pill
              type="button"
              variant="outline"
              onClick={onDecline}
              disabled={isSubmitting}
              className="w-full sm:w-auto"
            >
              {t('publicExam.cameraConsentDecline')}
            </Pill>
            <Pill
              ref={agreeRef}
              type="button"
              onClick={handleAgreeClick}
              disabled={isSubmitting}
              aria-busy={isSubmitting}
              // Keep the accessible name stable across idle/submitting states,
              // matching ConsentModal — the visible text still swaps.
              aria-label={t('publicExam.cameraConsentAgree')}
              className="w-full sm:w-auto"
            >
              {isSubmitting ? (
                <span className="flex items-center justify-center gap-2">
                  <span
                    className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent"
                    aria-hidden="true"
                  />
                  {t('publicExam.cameraConsentSaving')}
                </span>
              ) : (
                t('publicExam.cameraConsentAgree')
              )}
            </Pill>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
