// ChangePassword — forced bootstrap-password reset (HR workflow).
// Shown when the account has must_change_password=true (e.g. an HR manager who
// just logged in with the default '1234'). Standalone page (no shell, no escape).
// Design: CenteredCard + PasswordField + strength meter. Logic UNCHANGED.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation } from '@tanstack/react-query';
import { changePassword } from '@/api/auth';
import { homePathFor } from '@/components/layout/navSections';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { CenteredCard, FormError, PasswordField, PasswordStrength } from '@/components/layout/AuthLayout';
import { Pill } from '@/design/components/primitives';
import { ShieldCheck, Lock } from '@/design/components/icons';

export default function ChangePassword() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { accessToken, user, setAuth } = useAuth();
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => changePassword(pw),
    onSuccess: () => {
      if (accessToken && user) {
        setAuth(accessToken, { ...user, must_change_password: false });
      }
      toast.success(t('changePassword.successToast'));
      void navigate(homePathFor(user?.roles ?? []), { replace: true });
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : t('changePassword.errGeneric'));
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 8) {
      setError(t('changePassword.errMinLength'));
      return;
    }
    if (pw !== confirm) {
      setError(t('changePassword.errMismatch'));
      return;
    }
    mutation.mutate();
  }

  return (
    <CenteredCard>
      <div className="mb-6 flex flex-col items-center text-center">
        <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[var(--ui-info-wash)] text-[var(--ui-info)]">
          <ShieldCheck className="h-6 w-6" aria-hidden="true" />
        </span>
        <h1 className="mt-5 text-[24px] font-bold leading-tight tracking-[-0.5px] text-foreground">
          {t('changePassword.title')}
        </h1>
        <p className="mt-2 text-[14px] leading-relaxed text-muted-foreground">{t('changePassword.desc')}</p>
      </div>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4" aria-label="Change password form">
        <div className="space-y-2">
          <PasswordField
            id="cp-new"
            label={t('changePassword.newPassword')}
            autoComplete="new-password"
            icon={<Lock size={15} aria-hidden="true" />}
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder={t('changePassword.newPlaceholder')}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'cp-error' : undefined}
          />
          {/* Strength meter — presentation only */}
          <PasswordStrength password={pw} />
        </div>

        <PasswordField
          id="cp-confirm"
          label={t('changePassword.confirmPassword')}
          autoComplete="new-password"
          icon={<Lock size={15} aria-hidden="true" />}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={t('changePassword.confirmPlaceholder')}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'cp-error' : undefined}
        />

        <FormError id="cp-error">{error}</FormError>

        <Pill
          type="submit"
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          className="min-h-11 w-full py-3"
        >
          {mutation.isPending ? t('changePassword.saving') : t('changePassword.submit')}
        </Pill>
      </form>
    </CenteredCard>
  );
}
