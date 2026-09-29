// ConfirmReapplication — a candidate proves the address behind a second
// application, and only then does it reach the hiring team (PH3-B4b).
//
// Reached from the email:  {APP_BASE_URL}/reapply#<token>
//
// The token is in the #fragment, so it never reaches a server log — the same
// discipline as the activation, reset, exam and interview links.
//
// WHY THERE IS A PAGE HERE AT ALL
// The public apply form is anonymous and identifies a person by an address
// typed into it. A second application to a role somebody was turned down for
// therefore cannot be acted on when it arrives: that would let anyone holding
// the link move a real candidate's status, replace the CV on their
// application and spend an exception the hiring team had granted them.
// Following this link is the only evidence that the person who typed the
// address is the person who reads mail at it.
//
// ONE BUTTON, AND IT IS NOT AUTOMATIC
// The confirmation fires on a click rather than on load. Mail clients and
// security scanners fetch links in the background, and a GET that silently
// changed a candidate's application would be confirmed by a scanner rather
// than by a person — which is the whole thing this page exists to prevent.
//
// The copy never mentions the earlier rejection. Somebody who did not apply
// may be reading this, and another person's employment history is not ours to
// put in their inbox.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { confirmReapplication } from '@/api/publicApply';
import AuthLayout from '@/components/layout/AuthLayout';
import { Pill } from '@/design/components/primitives';
import { AlertCircle, CheckCircle2, Loader2 } from '@/design/components/icons';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export default function ConfirmReapplication(): JSX.Element {
  const { t } = useTranslation();
  // Read once: a later navigation must not swap the credential under us.
  const [token] = useState(() => window.location.hash.replace(/^#/, '').trim());

  useEffect(() => {
    // Take it out of the address bar once held. It is single-use and about to
    // be spent, but a link left on screen is a link that gets screenshotted.
    if (window.location.hash) {
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, []);

  const confirm = useMutation({
    mutationFn: () => confirmReapplication(token),
  });

  if (!token) {
    return (
      <AuthLayout>
        <h1 className="text-[22px] font-semibold tracking-[-0.6px] text-foreground">
          {t('reapply.title')}
        </h1>
        <p className="mt-4 flex items-start gap-2 text-[13.5px] text-[var(--ui-danger)]">
          <AlertCircle size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
          {t('reapply.noToken')}
        </p>
      </AuthLayout>
    );
  }

  if (confirm.isSuccess) {
    return (
      <AuthLayout>
        <div className="text-center">
          <CheckCircle2
            className="mx-auto h-9 w-9 text-[var(--ui-ok)]"
            aria-hidden="true"
          />
          <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-foreground">
            {t('reapply.title')}
          </h1>
          <p className="mt-3 text-[14px] text-foreground">{confirm.data.message}</p>
          <p className="mt-2 text-[13px] text-[var(--ui-soft)]">
            {t('reapply.whatNext')}
          </p>
          <Link
            to="/applications"
            className="mt-5 inline-block text-[13px] text-[var(--accent)] underline underline-offset-2"
          >
            {t('reapply.viewApplications')}
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <h1 className="text-[22px] font-semibold tracking-[-0.6px] text-foreground">
        {t('reapply.title')}
      </h1>
      <p className="mt-3 text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
        {t('reapply.lead')}
      </p>
      {confirm.isError ? (
        <p className="mt-3 flex items-start gap-2 text-[13px] text-[var(--ui-danger)]">
          <AlertCircle size={15} aria-hidden="true" className="mt-0.5 shrink-0" />
          {errText(confirm.error, t('reapply.errConfirm'))}
        </p>
      ) : null}
      <Pill
        onClick={() => confirm.mutate()}
        disabled={confirm.isPending}
        className="mt-5 w-full justify-center px-4 py-2.5"
      >
        {confirm.isPending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t('reapply.confirming')}
          </>
        ) : (
          t('reapply.confirmCta')
        )}
      </Pill>
      <p className="mt-4 text-[12px] leading-relaxed text-[var(--ui-faint)]">
        {t('reapply.ignore')}
      </p>
    </AuthLayout>
  );
}
