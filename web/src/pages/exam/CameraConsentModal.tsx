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
//
// WHAT `onAgree` ACTUALLY DOES — corrected 2026-09-29 (code review FIX 4).
// This used to claim onAgree calls POST /exam/camera-consent
// (grantCameraConsent) directly and that the modal stays open until that
// ledger write completes. That is not what ships: consent recording was
// rewired to happen at Start instead — PublicExam.tsx's onAgree is a
// synchronous local state flip (`setCameraConsent('granted')`) that closes
// this modal immediately, and the actual `grantCameraConsent(token)` call
// happens later, awaited inside the "Start exam" button's own mutation,
// immediately before `POST /exam/start` — which is what actually gates on
// the dpdp_consent_ledger row, never on this modal's local state. A failure
// there surfaces below the Start button (`startMut.isError`), not here. This
// modal therefore has nothing to submit and no error of its own to show.

import { useEffect, useRef, useCallback, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldCheck, Eye, Users, Video } from '@/design/components/icons';
import { GlassCard, Pill } from '@/design/components/primitives';

interface CameraConsentModalProps {
  /** The candidate agreed. Synchronous from this component's point of view —
   *  see the module note above for where the actual consent call happens. */
  onAgree: () => void;
  /** The candidate declined (Esc, "Decline" button, or backdrop is inert —
   *  there is no dismiss-without-deciding path, matching ConsentModal). */
  onDecline: () => void;
}

const HEADING_ID = 'camera-consent-heading';

export default function CameraConsentModal({ onAgree, onDecline }: CameraConsentModalProps) {
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
    onAgree();
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

          <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Pill
              type="button"
              variant="outline"
              onClick={onDecline}
              className="w-full sm:w-auto"
            >
              {t('publicExam.cameraConsentDecline')}
            </Pill>
            <Pill
              ref={agreeRef}
              type="button"
              onClick={handleAgreeClick}
              aria-label={t('publicExam.cameraConsentAgree')}
              className="w-full sm:w-auto"
            >
              {t('publicExam.cameraConsentAgree')}
            </Pill>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
