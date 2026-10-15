// ForgotPassword — request a password-reset link.
// Public page. POST /auth/forgot-password always succeeds (anti-enumeration), so
// on success we show the same "check your inbox" confirmation regardless of
// whether the address exists. Design: AuthLayout + design primitives.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { forgotPassword } from '@/api/auth';
import AuthLayout, { FormError, StatusPanel } from '@/components/layout/AuthLayout';
import { TEXT_LINK_CLASS } from '@/components/layout/authStyles';
import { Field, Pill } from '@/design/components/primitives';
import { Mail, CheckCircle2, ArrowLeft } from '@/design/components/icons';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => forgotPassword(email.trim()),
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) {
      setError('Enter a valid email address.');
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
          title="Check your inbox"
          actions={
            <Link to="/login" className={TEXT_LINK_CLASS}>
              <ArrowLeft size={14} aria-hidden="true" /> Back to sign in
            </Link>
          }
        >
          <p>
            If an account exists for <span className="font-semibold text-foreground">{email.trim()}</span>, we’ve sent
            a password-reset link. It expires soon — check spam if you don’t see it.
          </p>
        </StatusPanel>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <h1 className="text-[30px] font-bold leading-tight tracking-[-0.8px] text-foreground">Forgot password?</h1>
      <p className="mt-2 text-[15px] text-muted-foreground">
        Enter your email and we’ll send you a secure link to reset it.
      </p>

      <form onSubmit={onSubmit} noValidate aria-label="Forgot password form" className="mt-8 flex flex-col gap-2">
        <Field
          id="fp-email"
          label="Email"
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
          {mutation.isPending ? 'Sending…' : 'Send reset link'}
        </Pill>
      </form>

      <p className="mt-6 text-center text-[13px] text-muted-foreground">
        Remembered it?{' '}
        <Link
          to="/login"
          className="inline-flex min-h-10 items-center rounded font-semibold text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          Back to sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
