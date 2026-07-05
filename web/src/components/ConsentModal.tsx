// ConsentModal — DPDP Act 2023 consent gate overlay
// Full-screen backdrop with centred card. Keyboard accessible: Esc = Decline,
// focus trapped inside the modal, "I Agree" receives focus on mount.
//
// IMPORTANT (i18n legal note): HI/TE translations of this modal are in
// lib/i18n.ts under the 'consent.*' keys. They are a FIRST PASS and must
// receive native-speaker and legal/compliance review before any government-bid
// or production launch. Provider names (Sarvam, Google Gemini, Groq) are
// kept as proper nouns and are not translated.

import { useEffect, useRef, useCallback, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

interface ConsentModalProps {
  /** Called when the user clicks "I Agree" and postConsent has resolved */
  onAgree: () => Promise<void>;
  /** Called when the user declines (Esc or "Decline" button) */
  onDecline: () => void;
  /** Show spinner on the Agree button while postConsent is in-flight */
  isSubmitting: boolean;
  /** If postConsent failed, show this message inside the modal */
  error: string | null;
}

const HEADING_ID = 'consent-modal-heading';

export default function ConsentModal({
  onAgree,
  onDecline,
  isSubmitting,
  error,
}: ConsentModalProps) {
  const { t } = useTranslation();
  const agreeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Move focus to "I Agree" on mount
  useEffect(() => {
    agreeRef.current?.focus();
  }, []);

  // Trap focus inside the modal
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
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
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
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4"
      aria-hidden="false"
    >
      {/* Dialog card */}
      <Card
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={HEADING_ID}
        onKeyDown={handleKeyDown}
        className="relative w-full max-w-lg rounded-2xl shadow-elevated overflow-y-auto max-h-[90vh] focus:outline-none"
        tabIndex={-1}
      >
        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b border-border">
          <h2
            id={HEADING_ID}
            className="text-subheading font-semibold text-foreground"
          >
            {t('consent.title')}
          </h2>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4 text-body-sm text-muted-foreground leading-relaxed">
          <p>{t('consent.intro')}</p>

          <ul className="list-disc list-inside space-y-1 ml-1 marker:text-muted-foreground">
            <li>{t('consent.bullet1')}</li>
            <li>{t('consent.bullet2')}</li>
            <li>{t('consent.bullet3')}</li>
            <li>{t('consent.bullet4')}</li>
          </ul>

          <dl className="space-y-2">
            <div>
              <dt className="font-semibold text-foreground inline">{t('consent.languagesLabel')} </dt>
              <dd className="inline">{t('consent.languagesValue')}</dd>
            </div>
            <div>
              <dt className="font-semibold text-foreground inline">{t('consent.retentionLabel')} </dt>
              <dd className="inline">
                {t('consent.retentionValue')}{' '}
                <strong className="text-foreground">{t('consent.retentionDays')}</strong>
              </dd>
            </div>
            <div>
              <dt className="font-semibold text-foreground inline">
                {t('consent.rightsLabel')}{' '}
              </dt>
              <dd className="inline">
                {t('consent.rightsValue')}{' '}
                <a
                  href="mailto:support@intants.com"
                  className="text-primary underline underline-offset-2 hover:text-primary/80 focus:outline-none focus:ring-2 focus:ring-ring rounded"
                >
                  support@intants.com
                </a>
              </dd>
            </div>
          </dl>

          {/* DPDP cross-border transparency — data currently processed outside India */}
          <p className="text-body-sm text-foreground">
            {t('consent.dataFlowNote')}
          </p>

          <p className="text-muted-foreground text-caption">
            {t('consent.footerNote')}
          </p>

          {/* API error */}
          {error && (
            <div
              role="alert"
              className="rounded-xl bg-destructive/10 border border-destructive/30 px-4 py-3 text-caption text-destructive"
            >
              {error}
            </div>
          )}
        </div>

        {/* Footer — buttons */}
        <div className="px-6 pb-6 flex flex-col-reverse sm:flex-row gap-3 sm:justify-end">
          {/* Decline */}
          <Button
            type="button"
            variant="outline"
            onClick={onDecline}
            disabled={isSubmitting}
            className="w-full sm:w-auto"
          >
            {t('consent.decline')}
          </Button>

          {/* I Agree — primary CTA, receives focus on mount */}
          <Button
            ref={agreeRef}
            type="button"
            variant="default"
            onClick={handleAgreeClick}
            disabled={isSubmitting}
            aria-busy={isSubmitting}
            // Keep the accessible name stable across idle/submitting states
            // so screen readers (and tests) can always locate the button by
            // its semantic intent. Visible text still swaps to "Saving…".
            aria-label="I Agree"
            className="w-full sm:w-auto"
          >
            {isSubmitting ? (
              <span className="flex items-center justify-center gap-2">
                <span
                  className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent"
                  aria-hidden="true"
                />
                {t('consent.saving')}
              </span>
            ) : (
              t('consent.agree')
            )}
          </Button>
        </div>
      </Card>
    </div>
  );
}
