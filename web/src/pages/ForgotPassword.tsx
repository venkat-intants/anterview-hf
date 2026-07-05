// ForgotPassword — request a password-reset link.
// Public page. POST /auth/forgot-password always succeeds (anti-enumeration), so
// on success we show the same "check your inbox" confirmation regardless of
// whether the address exists. Design: AuthLayout + design primitives.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { forgotPassword } from '@/api/auth';
import AuthLayout from '@/components/layout/AuthLayout';
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
        <div className="flex flex-col items-center text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(39,201,63,0.14)] text-[#27c93f]">
            <CheckCircle2 className="h-6 w-6" aria-hidden="true" />
          </span>
          <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
            Check your inbox
          </h1>
          <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">
            If an account exists for <span className="text-white">{email.trim()}</span>, we’ve sent
            a password-reset link. It expires soon — check spam if you don’t see it.
          </p>
          <Link
            to="/login"
            className="mt-6 inline-flex items-center gap-1.5 text-[13px] text-[#60a5fa] hover:underline underline-offset-4"
          >
            <ArrowLeft size={14} aria-hidden="true" /> Back to sign in
          </Link>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-white">Forgot password?</h1>
      <p className="mt-1.5 text-[14px] text-[#888b91]">
        Enter your email and we’ll send you a secure link to reset it.
      </p>

      <form onSubmit={onSubmit} noValidate aria-label="Forgot password form" className="mt-8 flex flex-col gap-4">
        <Field
          id="fp-email"
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@email.com"
          icon={<Mail size={15} aria-hidden="true" />}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        {error && (
          <p role="alert" className="text-[12.5px] text-[#e6714f]">
            {error}
          </p>
        )}

        <Pill type="submit" disabled={mutation.isPending} aria-busy={mutation.isPending} className="w-full py-3">
          {mutation.isPending ? 'Sending…' : 'Send reset link'}
        </Pill>
      </form>

      <p className="mt-6 text-center text-[13px] text-[#888b91]">
        Remembered it?{' '}
        <Link
          to="/login"
          className="font-medium text-white hover:underline focus:outline-none focus:underline underline-offset-4"
        >
          Back to sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
