// ResetPassword — set a new password from an emailed reset link.
// The single-use token arrives in the URL #fragment (kept out of server logs):
//   {APP_BASE_URL}/reset-password#<token>
// POST /auth/reset-password consumes it. On success → redirect to /login.

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { resetPassword } from '@/api/auth';
import { toast } from '@/lib/toast';
import AuthLayout, { FormError, PasswordField, PasswordStrength, StatusPanel } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS } from '@/components/layout/authStyles';
import { Lock, KeyRound, AlertCircle } from '@/design/components/icons';
import { useCandidateError } from '@/lib/candidateCopy';
import { useFragmentToken } from '@/hooks/useFragmentToken';
import { Button } from '@/components/ui/button';

export default function ResetPassword() {
  const { t } = useTranslation();
  const errorText = useCandidateError();
  const navigate = useNavigate();
  // Read once, then removed from the address bar and history entry, and held
  // in memory only. This token sets a password: kept in tab storage, a
  // reopened tab on a shared machine would hand the next person a working form
  // for somebody else's account. A reload costs the form; the emailed link
  // still works until it is used.
  const token = useFragmentToken();
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => resetPassword(token, pw),
    onSuccess: () => {
      toast.success(t('auth.resetSuccess'));
      void navigate('/login', { replace: true });
    },
    onError: (err: unknown) => {
      setError(errorText(err, t('auth.resetError')));
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 8) {
      setError(t('auth.errPasswordMin'));
      return;
    }
    if (pw !== confirm) {
      setError(t('auth.errPasswordMismatch'));
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
          title={t('auth.resetInvalidTitle')}
          actions={
            <Link to="/forgot-password" className={PRIMARY_LINK_CLASS}>
              {t('auth.requestNewLink')}
            </Link>
          }
        >
          {/* The emailed link still works if it has not been used: say so
              before sending them to request another (audit CA-H3). */}
          <p>{t('auth.resetInvalidBody')}</p>
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
        {t('auth.resetTitle')}
      </h1>
      <p className="mt-2 text-[15px] text-muted-foreground">{t('auth.resetDesc')}</p>

      <form onSubmit={onSubmit} noValidate aria-label={t('auth.resetFormAria')} className="mt-8 flex flex-col gap-4">
        <div className="space-y-2">
          <PasswordField
            id="rp-new"
            label={t('auth.newPassword')}
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
          label={t('auth.confirmPassword')}
          autoComplete="new-password"
          placeholder="••••••••"
          icon={<Lock size={15} aria-hidden="true" />}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'rp-error' : undefined}
        />

        <FormError id="rp-error">{error}</FormError>

        <Button type="submit" disabled={mutation.isPending} aria-busy={mutation.isPending} size="lg" className="w-full">
          {mutation.isPending ? t('auth.updating') : t('auth.updatePassword')}
        </Button>
      </form>

      <p className="mt-6 text-center text-[13px] text-muted-foreground">
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
