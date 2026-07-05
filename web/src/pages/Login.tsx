// Login page — email + password form → POST /auth/login → redirect to /dashboard
// Design: AuthSplit skin (dark canvas, conic-G Google badge, Naipunyam disabled).
// Logic: RHF + zodResolver, login()→getMe()→setAuth, role-aware redirect,
//        must_change_password guard, isPending/aria-busy, toast errors, all t() keys.

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useMutation } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { login, getMe } from '@/api/auth';
import { googleLoginUrl } from '@/api/sso';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import type { AuthUser } from '@/types/auth';
import AuthLayout from '@/components/layout/AuthLayout';
import { Field, Pill } from '@/design/components/primitives';
import { Mail, Lock } from '@/design/components/icons';

// ── Zod schema ────────────────────────────────────────────────────────────────
const loginSchema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
});

type LoginFormValues = z.infer<typeof loginSchema>;

// ── Google "G" badge (conic gradient — matches design SsoButtons) ──────────────
function GoogleBadge() {
  return (
    <span
      aria-hidden="true"
      className="flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold text-white"
      style={{ background: 'conic-gradient(from -45deg,#ea4335,#fbbc05,#34a853,#4285f4,#ea4335)' }}
    >
      G
    </span>
  );
}

// ── Naipunyam badge ───────────────────────────────────────────────────────────
function NaipunyamBadge() {
  return (
    <span
      aria-hidden="true"
      className="flex h-5 w-5 items-center justify-center rounded-[6px] text-[11px] font-bold text-white"
      style={{ background: 'linear-gradient(135deg,#16c253,var(--accent))' }}
    >
      न
    </span>
  );
}

// ── Base button classes reused across SSO buttons ─────────────────────────────
const ssoBase =
  'flex w-full items-center justify-center gap-2.5 rounded-[12px] px-4 py-3 text-[14px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black';

// ─────────────────────────────────────────────────────────────────────────────

export default function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setAuth } = useAuth();

  // DPDP consent — gates the account-creating Google sign-in (DPDP §7). Signing
  // in with Google can create a new candidate account, so we capture consent
  // here and pass it through; the password form below is for existing accounts.
  const [agreedToDpdp, setAgreedToDpdp] = useState(false);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  });

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = form;

  // ── Mutation: login → getMe → setAuth → role-aware redirect ──────────────────
  const mutation = useMutation({
    mutationFn: async (values: LoginFormValues) => {
      const loginRes = await login(values);
      const me = await getMe(loginRes.access_token);
      return { loginRes, me };
    },
    onSuccess: ({ loginRes, me }) => {
      const user: AuthUser = {
        user_id: me.user_id,
        full_name: me.full_name,
        email: me.email,
        roles: me.roles,
        must_change_password: me.must_change_password,
      };
      setAuth(loginRes.access_token, user);
      if (me.must_change_password) {
        void navigate('/change-password', { replace: true });
      } else if (me.roles.includes('platform_owner')) {
        void navigate('/platform', { replace: true });
      } else if (me.roles.includes('super_admin')) {
        void navigate('/superadmin', { replace: true });
      } else if (me.roles.includes('hr_manager')) {
        void navigate('/hr', { replace: true });
      } else {
        void navigate('/dashboard', { replace: true });
      }
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : t('error.generic');
      toast.error(message);
    },
  });

  function onSubmit(values: LoginFormValues) {
    mutation.mutate(values);
  }

  return (
    <AuthLayout>
      {/* Mobile compact logo (hidden on desktop where the brand panel shows) */}
      <Link
        to="/"
        className="mb-8 flex items-center gap-2.5 lg:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded-lg"
        aria-label="Anterview home"
      >
        <span
          className="flex h-8 w-8 items-center justify-center rounded-[9px]"
          style={{ background: 'linear-gradient(135deg,#112d72,#a887dc)' }}
        >
          <span className="h-2.5 w-2.5 rounded-full bg-white" />
        </span>
        <span className="text-[15px] font-semibold text-white">Anterview</span>
      </Link>

      {/* Heading */}
      <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-white">
        {t('auth.welcomeBack')}
      </h1>
      <p className="mt-1.5 text-[14px] text-[#888b91]">{t('auth.signInSubtitle')}</p>

      {/* DPDP consent — gates the account-creating Google sign-in (DPDP §7). */}
      <label className="mt-8 flex cursor-pointer items-start gap-2.5 text-[12.5px] text-[#888b91]">
        <input
          type="checkbox"
          checked={agreedToDpdp}
          onChange={(e) => { setAgreedToDpdp(e.target.checked); }}
          className="mt-0.5 h-4 w-4 flex-none cursor-pointer accent-[var(--accent)]"
          aria-label="I agree to the Terms and DPDP-compliant Privacy Policy"
        />
        <span>
          I agree to the Terms and the DPDP-compliant Privacy Policy, including
          interview recording for scoring.
        </span>
      </label>

      {/* SSO buttons */}
      <div className="mt-4 flex flex-col gap-2.5">
        {/* Google — gated on DPDP consent; passes the consent signal through. */}
        <button
          type="button"
          disabled={!agreedToDpdp}
          aria-disabled={!agreedToDpdp}
          className={`${ssoBase} bg-white text-black hover:bg-[#eaeaea] disabled:cursor-not-allowed disabled:opacity-50`}
          onClick={() => { window.location.assign(googleLoginUrl('/dashboard', agreedToDpdp, 1)); }}
        >
          <GoogleBadge />
          {t('auth.signInWithGoogle')}
        </button>

        {/* Naipunyam — disabled (no live handler; see gap report) */}
        <button
          type="button"
          disabled
          aria-disabled="true"
          className={`${ssoBase} border border-white/15 bg-white/[0.04] text-white opacity-50 cursor-not-allowed`}
        >
          <NaipunyamBadge />
          Sign in with Naipunyam SSO
        </button>
      </div>

      {/* Divider */}
      <div className="my-6 flex items-center gap-3 text-[12px] text-[#5a5f66]">
        <span className="h-px flex-1 bg-white/10" aria-hidden="true" />
        {t('auth.orContinueWith')}
        <span className="h-px flex-1 bg-white/10" aria-hidden="true" />
      </div>

      {/* Email + password form — RHF + Zod + inline FormMessage */}
      <form
        onSubmit={(e) => void handleSubmit(onSubmit)(e)}
        noValidate
        aria-label="Login form"
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-1">
          <Field
            label={t('auth.email')}
            type="email"
            autoComplete="email"
            placeholder="you@email.com"
            icon={<Mail size={15} aria-hidden="true" />}
            {...register('email')}
          />
          {errors.email && (
            <span role="alert" className="text-[11.5px] text-[#e6714f]">
              {errors.email.message}
            </span>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <Field
            label={t('auth.password')}
            type="password"
            autoComplete="current-password"
            placeholder="••••••••"
            icon={<Lock size={15} aria-hidden="true" />}
            {...register('password')}
          />
          {errors.password && (
            <span role="alert" className="text-[11.5px] text-[#e6714f]">
              {errors.password.message}
            </span>
          )}
        </div>

        {/* Forgot password link */}
        <div className="flex justify-end">
          <Link
            to="/forgot-password"
            className="text-[12.5px] text-[#60a5fa] hover:underline focus-visible:outline-none focus-visible:underline underline-offset-4"
          >
            {t('auth.forgotPassword')}
          </Link>
        </div>

        {/* Submit */}
        <Pill
          type="submit"
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          className="w-full py-3"
        >
          {mutation.isPending ? t('auth.signingIn') : t('auth.signIn')}
        </Pill>
      </form>

      {/* Footer link */}
      <p className="mt-6 text-center text-[13px] text-[#888b91]">
        {t('auth.noAccount')}{' '}
        <Link
          to="/register"
          className="font-medium text-white hover:underline focus:outline-none focus:underline underline-offset-4"
        >
          {t('auth.createOne')}
        </Link>
      </p>
    </AuthLayout>
  );
}
