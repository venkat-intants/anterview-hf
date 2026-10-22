// ForgotPassword — request a password-reset link.
// Public page. POST /auth/forgot-password always succeeds (anti-enumeration), so
// on success we show the same "check your inbox" confirmation regardless of
// whether the address exists. Design: AuthLayout + design primitives.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { Trans, useTranslation } from 'react-i18next';
import { forgotPassword } from '@/api/auth';
import AuthLayout, { FormError, StatusPanel } from '@/components/layout/AuthLayout';
import { TEXT_LINK_CLASS } from '@/components/layout/authStyles';
import { Field, Pill } from '@/design/components/primitives';
import { Mail, CheckCircle2, ArrowLeft } from '@/design/components/icons';
import { useCandidateError } from '@/lib/candidateCopy';

export default function ForgotPassword() {
  const { t } = useTranslation();
  const errorText = useCandidateError();
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => forgotPassword(email.trim()),
    onError: (err: unknown) => {
      setError(errorText(err, t('error.generic')));
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) {
      setError(t('auth.errEmail'));
      return;
    }
    mutation.mutate();
  }

  if (mutation.isSuccess) {
    return (
      <AuthLayout>
        <StatusPanel
          icon={CheckCircle2}
          tone="ok"
          title={t('auth.checkInboxTitle')}
          actions={
            <Link to="/login" className={TEXT_LINK_CLASS}>
              <ArrowLeft size={14} aria-hidden="true" /> {t('auth.backToSignIn')}
            </Link>
          }
        >
          <p>
            <Trans
              i18nKey="auth.checkInboxBody"
              values={{ email: email.trim() }}
              components={{ 1: <span className="font-semibold text-foreground" /> }}
            />
          </p>
        </StatusPanel>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <h1 className="text-[30px] font-bold leading-tight tracking-[-0.8px] text-foreground">
        {t('auth.forgotPassword')}
      </h1>
      <p className="mt-2 text-[15px] text-muted-foreground">{t('auth.forgotDesc')}</p>

      <form onSubmit={onSubmit} noValidate aria-label={t('auth.forgotFormAria')} className="mt-8 flex flex-col gap-2">
        <Field
          id="fp-email"
          label={t('auth.email')}
          type="email"
          autoComplete="email"
          placeholder="you@email.com"
          icon={<Mail size={15} aria-hidden="true" />}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'fp-error' : undefined}
        />

        <FormError id="fp-error">{error}</FormError>

        <Pill type="submit" disabled={mutation.isPending} aria-busy={mutation.isPending} className="min-h-11 w-full py-3">
          {mutation.isPending ? t('auth.sending') : t('auth.sendResetLink')}
        </Pill>
      </form>

      <p className="mt-6 text-center text-[13px] text-muted-foreground">
        {t('auth.rememberedIt')}{' '}
        <Link
          to="/login"
          className="inline-flex min-h-10 items-center rounded font-semibold text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {t('auth.backToSignIn')}
        </Link>
      </p>
    </AuthLayout>
  );
}
