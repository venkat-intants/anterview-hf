// ResetPassword — set a new password from an emailed reset link.
// The single-use token arrives in the URL #fragment (kept out of server logs):
//   {APP_BASE_URL}/reset-password#<token>
// POST /auth/reset-password consumes it. On success → redirect to /login.

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { resetPassword } from '@/api/auth';
import { toast } from '@/lib/toast';
import AuthLayout, { FormError, PasswordField, PasswordStrength, StatusPanel } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS } from '@/components/layout/authStyles';
import { Pill } from '@/design/components/primitives';
import { Lock, KeyRound, AlertCircle } from '@/design/components/icons';
import { useFragmentToken } from '@/hooks/useFragmentToken';

export default function ResetPassword() {
  const navigate = useNavigate();
  // Read once, then removed from the address bar and history entry.
  const token = useFragmentToken();
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => resetPassword(token, pw),
    onSuccess: () => {
      toast.success('Password updated — please sign in.');
      void navigate('/login', { replace: true });
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : 'Could not reset your password.');
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (pw !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    mutation.mutate();
  }

  if (!token) {
    return (
      <AuthLayout>
        <StatusPanel
          icon={AlertCircle}
          tone="danger"
          title="Invalid reset link"
          actions={
            <Link to="/forgot-password" className={PRIMARY_LINK_CLASS}>
              Request a new link
            </Link>
          }
        >
          <p>This link is missing its token. Please request a new password-reset email.</p>
        </StatusPanel>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[var(--ui-info-wash)] text-[var(--ui-info)]">
        <KeyRound className="h-6 w-6" aria-hidden="true" />
      </span>
      <h1 className="mt-5 text-[30px] font-bold leading-tight tracking-[-0.8px] text-foreground">
        Set a new password
      </h1>
      <p className="mt-2 text-[15px] text-muted-foreground">Choose a strong password you’ll remember.</p>

      <form onSubmit={onSubmit} noValidate aria-label="Reset password form" className="mt-8 flex flex-col gap-4">
        <div className="space-y-2">
          <PasswordField
            id="rp-new"
            label="New password"
            autoComplete="new-password"
            placeholder="••••••••"
            icon={<Lock size={15} aria-hidden="true" />}
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'rp-error' : undefined}
          />
          <PasswordStrength password={pw} />
        </div>

        <PasswordField
          id="rp-confirm"
          label="Confirm password"
          autoComplete="new-password"
          placeholder="••••••••"
          icon={<Lock size={15} aria-hidden="true" />}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'rp-error' : undefined}
        />

        <FormError id="rp-error">{error}</FormError>

        <Pill type="submit" disabled={mutation.isPending} aria-busy={mutation.isPending} className="min-h-11 w-full py-3">
          {mutation.isPending ? 'Updating…' : 'Update password'}
        </Pill>
      </form>

      <p className="mt-6 text-center text-[13px] text-muted-foreground">
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
