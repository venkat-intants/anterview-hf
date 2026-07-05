// ChangePassword — forced bootstrap-password reset (HR workflow).
// Shown when the account has must_change_password=true (e.g. an HR manager who
// just logged in with the default '1234'). Standalone page (no shell, no escape).
// Design: aurora-dark GlassCard + Field + strength meter. Logic UNCHANGED.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { changePassword } from '@/api/auth';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { AuroraField } from '@/design/components/AuroraField';
import { Field, Pill } from '@/design/components/primitives';
import { ShieldCheck, Lock } from '@/design/components/icons';

function landingForRoles(roles: string[]): string {
  if (roles.includes('platform_owner')) return '/platform';
  if (roles.includes('super_admin')) return '/superadmin';
  if (roles.includes('hr_manager')) return '/hr';
  if (roles.includes('admin')) return '/admin/overview';
  return '/dashboard';
}

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
      void navigate(landingForRoles(user?.roles ?? []), { replace: true });
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

  const strength = strengthOf(pw);

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-black px-4 py-12 font-sans text-white">
      <AuroraField />
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="relative z-10 w-full max-w-md"
      >
        <div className="rounded-[24px] border border-white/[0.08] bg-[#0f0f10] p-8">
          <div className="mb-6 flex flex-col items-center text-center">
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
              <ShieldCheck className="h-6 w-6" aria-hidden="true" />
            </span>
            <h1 className="mt-5 text-[20px] font-semibold tracking-[-0.4px] text-white">
              {t('changePassword.title')}
            </h1>
            <p className="mt-2 text-[13.5px] text-[#888b91]">{t('changePassword.desc')}</p>
          </div>

          <form onSubmit={onSubmit} noValidate className="space-y-4" aria-label="Change password form">
            <div className="space-y-2">
              <Field
                id="cp-new"
                label={t('changePassword.newPassword')}
                type="password"
                autoComplete="new-password"
                icon={<Lock size={15} aria-hidden="true" />}
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                placeholder={t('changePassword.newPlaceholder')}
              />
              {/* Strength meter — presentation only */}
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
              id="cp-confirm"
              label={t('changePassword.confirmPassword')}
              type="password"
              autoComplete="new-password"
              icon={<Lock size={15} aria-hidden="true" />}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={t('changePassword.confirmPlaceholder')}
            />

            {error && (
              <p role="alert" className="text-[13px] text-[#e6714f]">
                {error}
              </p>
            )}

            <Pill
              type="submit"
              disabled={mutation.isPending}
              aria-busy={mutation.isPending}
              className="w-full"
            >
              {mutation.isPending ? t('changePassword.saving') : t('changePassword.submit')}
            </Pill>
          </form>
        </div>
      </motion.div>
    </main>
  );
}
