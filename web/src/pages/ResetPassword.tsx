// ResetPassword — set a new password from an emailed reset link.
// The single-use token arrives in the URL #fragment (kept out of server logs):
//   {APP_BASE_URL}/reset-password#<token>
// POST /auth/reset-password consumes it. On success → redirect to /login.

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { resetPassword } from '@/api/auth';
import { toast } from '@/lib/toast';
import AuthLayout from '@/components/layout/AuthLayout';
import { Field, Pill } from '@/design/components/primitives';
import { Lock, KeyRound, AlertCircle } from '@/design/components/icons';

/** 0–4 password-strength score (presentation only). */
function strengthOf(pw: string): number {
  let s = 0;
  if (pw.length >= 8) s++;
  if (pw.length >= 12) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) s++;
  return Math.min(4, s);
}

const STRENGTH_COLOR = ['#e6714f', '#e6714f', '#ffb764', 'var(--accent)', '#27c93f'];
const STRENGTH_LABEL = ['Too weak', 'Weak', 'Fair', 'Good', 'Strong'];

// Token lives in the URL #fragment.
function tokenFromHash(): string {
  if (typeof window === 'undefined') return '';
  return window.location.hash.replace(/^#/, '').trim();
}

export default function ResetPassword() {
  const navigate = useNavigate();
  const [token] = useState(tokenFromHash);
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
        <div className="flex flex-col items-center text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(230,113,79,0.14)] text-[#e6714f]">
            <AlertCircle className="h-6 w-6" aria-hidden="true" />
          </span>
          <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
            Invalid reset link
          </h1>
          <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">
            This link is missing its token. Please request a new password-reset email.
          </p>
          <Link
            to="/forgot-password"
            className="mt-6 text-[13px] text-[#60a5fa] hover:underline underline-offset-4"
          >
            Request a new link
          </Link>
        </div>
      </AuthLayout>
    );
  }

  const strength = strengthOf(pw);

  return (
    <AuthLayout>
      <div className="mb-2 flex flex-col items-center text-center">
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
          <KeyRound className="h-6 w-6" aria-hidden="true" />
        </span>
      </div>
      <h1 className="text-center text-[24px] font-semibold tracking-[-0.6px] text-white">
        Set a new password
      </h1>
      <p className="mt-1.5 text-center text-[14px] text-[#888b91]">Choose a strong password you’ll remember.</p>

      <form onSubmit={onSubmit} noValidate aria-label="Reset password form" className="mt-8 flex flex-col gap-4">
        <div className="space-y-2">
          <Field
            id="rp-new"
            label="New password"
            type="password"
            autoComplete="new-password"
            placeholder="••••••••"
            icon={<Lock size={15} aria-hidden="true" />}
            value={pw}
            onChange={(e) => setPw(e.target.value)}
          />
          {pw.length > 0 && (
            <div className="flex items-center gap-2">
              <div className="flex flex-1 gap-1">
                {[0, 1, 2, 3].map((i) => (
                  <span
                    key={i}
                    className="h-1 flex-1 rounded-full transition-colors"
                    style={{ background: i < strength ? STRENGTH_COLOR[strength] : 'rgba(255,255,255,0.08)' }}
                  />
                ))}
              </div>
              <span className="text-[11px]" style={{ color: STRENGTH_COLOR[strength] }}>
                {STRENGTH_LABEL[strength]}
              </span>
            </div>
          )}
        </div>

        <Field
          id="rp-confirm"
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          placeholder="••••••••"
          icon={<Lock size={15} aria-hidden="true" />}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />

        {error && (
          <p role="alert" className="text-[12.5px] text-[#e6714f]">
            {error}
          </p>
        )}

        <Pill type="submit" disabled={mutation.isPending} aria-busy={mutation.isPending} className="w-full py-3">
          {mutation.isPending ? 'Updating…' : 'Update password'}
        </Pill>
      </form>

      <p className="mt-6 text-center text-[13px] text-[#888b91]">
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
