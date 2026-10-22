// Login page — email + password form → POST /auth/login → redirect to /dashboard
// Design: AuthLayout split shell. Password sign-in leads; the consent-gated
//         the Google SSO option (conic-G badge) follows it.
// Logic: RHF + zodResolver, login()→getMe()→setAuth, role-aware redirect,
//        must_change_password guard, isPending/aria-busy, toast errors, all t() keys.

import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useMutation } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { login, getMe } from '@/api/auth';
import { googleLoginUrl } from '@/api/sso';
import { useAuth } from '@/context/AuthContext';
import { homePathFor } from '@/components/layout/navSections';
import { toast } from '@/lib/toast';
import { useCandidateError } from '@/lib/candidateCopy';
import { rememberReturnPath, safeReturnPath } from '@/lib/returnTo';
import { ConsentDisclosure } from '@/components/layout/ConsentDisclosure';
import type { AuthUser } from '@/types/auth';
import AuthLayout, { FormError, PasswordField } from '@/components/layout/AuthLayout';
import { Field, Pill } from '@/design/components/primitives';
import { Mail, Lock } from '@/design/components/icons';

// ── Zod schema ────────────────────────────────────────────────────────────────
// Built per render language so field errors are localised (audit CA-M17).
function loginSchema(t: TFunction) {
  return z.object({
    email: z.string().email(t('auth.errEmail')),
    password: z.string().min(8, t('auth.errPasswordMin')),
  });
}

type LoginFormValues = z.infer<ReturnType<typeof loginSchema>>;

function statusOf(err: unknown): number | null {
  const status: unknown = err instanceof Error ? (err as { status?: unknown }).status : null;
  return typeof status === 'number' ? status : null;
}

// ── Google "G" badge (conic gradient — matches design SsoButtons) ──────────────
function GoogleBadge() {
  return (
    <span
      aria-hidden="true"
      className="flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold text-foreground"
      style={{ background: 'conic-gradient(from -45deg,#ea4335,#fbbc05,#34a853,#4285f4,#ea4335)' }}
    >
      G
    </span>
  );
}

// ── Base button classes reused across SSO buttons ─────────────────────────────
const ssoBase =
  'flex min-h-11 w-full items-center justify-center gap-2.5 rounded-full px-5 py-3 text-[14px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background';

// ─────────────────────────────────────────────────────────────────────────────

export default function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { setAuth } = useAuth();
  const errorText = useCandidateError();
  // The protected page that sent the visitor here, if any (audit CA-M27).
  const returnTo = safeReturnPath((location.state as { from?: unknown } | null)?.from);

  // DPDP consent — gates the account-creating Google sign-in (DPDP §7). Signing
  // in with Google can create a new candidate account, so we capture consent
  // here and pass it through; the password form below is for existing accounts.
  const [agreedToDpdp, setAgreedToDpdp] = useState(false);

  const schema = useMemo(() => loginSchema(t), [t]);
  const form = useForm<LoginFormValues>({
    resolver: zodResolver(schema),
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
      } else {
        // homePathFor, not a local if-chain: this branch previously had no
        // `admin` case and fell through to /dashboard, stranding admin-only
        // accounts on a candidate page their sidebar does not link to.
        // Back to the page that asked for a sign-in, when there was one.
        void navigate(returnTo ?? homePathFor(me.roles), { replace: true });
      }
    },
    onError: (err: unknown) => {
      // The server writes English; a Hindi or Telugu page gets its own words
      // for the cases a person can act on (audit CA-M21).
      const status = statusOf(err);
      const fallback =
        status === 401
          ? t('auth.errBadCredentials')
          : status === 403
            ? t('auth.errEmailUnconfirmed')
            : t('error.generic');
      toast.error(errorText(err, fallback));
    },
  });

  function onSubmit(values: LoginFormValues) {
    mutation.mutate(values);
  }

  return (
    <AuthLayout>
      {/* Heading */}
      <h1 className="text-[30px] font-bold leading-tight tracking-[-0.8px] text-foreground">
        {t('auth.welcomeBack')}
      </h1>
      <p className="mt-2 text-[15px] text-muted-foreground">{t('auth.signInSubtitle')}</p>

      {/* Email + password form — RHF + Zod + inline FormMessage */}
      <form
        onSubmit={(e) => void handleSubmit(onSubmit)(e)}
        noValidate
        aria-label={t('auth.loginFormAria')}
        className="mt-8 flex flex-col gap-2"
      >
        <div>
          <Field
            label={t('auth.email')}
            type="email"
            autoComplete="email"
            placeholder="you@email.com"
            data-testid="login-email"
            icon={<Mail size={15} aria-hidden="true" />}
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? 'login-email-error' : undefined}
            {...register('email')}
          />
          <FormError id="login-email-error">{errors.email?.message}</FormError>
        </div>

        <div>
          <PasswordField
            label={t('auth.password')}
            autoComplete="current-password"
            placeholder="••••••••"
            data-testid="login-password"
            icon={<Lock size={15} aria-hidden="true" />}
            invalid={Boolean(errors.password)}
            aria-describedby={errors.password ? 'login-password-error' : undefined}
            {...register('password')}
          />
          <FormError id="login-password-error">{errors.password?.message}</FormError>
        </div>

        {/* Forgot password link */}
        <div className="flex justify-end">
          <Link
            to="/forgot-password"
            className="inline-flex min-h-10 items-center rounded text-[13px] font-medium text-[var(--ui-info)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {t('auth.forgotPassword')}
          </Link>
        </div>

        {/* Submit */}
        <Pill
          type="submit"
          data-testid="login-submit"
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          className="min-h-11 w-full py-3"
        >
          {mutation.isPending ? t('auth.signingIn') : t('auth.signIn')}
        </Pill>
      </form>

      {/* Divider */}
      <div className="my-6 flex items-center gap-3 text-[13px] text-muted-foreground">
        <span className="h-px flex-1 bg-[var(--ui-inset-strong)]" aria-hidden="true" />
        {t('auth.orContinueWith')}
        <span className="h-px flex-1 bg-[var(--ui-inset-strong)]" aria-hidden="true" />
      </div>

      {/* DPDP consent — gates the account-creating Google sign-in (DPDP §7). */}
      <label className="flex cursor-pointer items-start gap-3 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3.5 text-[13px] leading-relaxed text-muted-foreground">
        <input
          type="checkbox"
          checked={agreedToDpdp}
          onChange={(e) => { setAgreedToDpdp(e.target.checked); }}
          className="mt-0.5 h-4 w-4 flex-none cursor-pointer accent-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          aria-describedby={agreedToDpdp ? 'login-consent-disclosure' : 'login-consent-help login-consent-disclosure'}
        />
        <span>{t('auth.consentLabel')}</span>
      </label>
      <ConsentDisclosure id="login-consent-disclosure" />

      {!agreedToDpdp && (
        <p id="login-consent-help" className="mt-2 text-[12.5px] text-muted-foreground">
          {t('auth.consentNeededGoogle')}
        </p>
      )}

      {/* SSO buttons */}
      <div className="mt-3 flex flex-col gap-2.5">
        {/* Google — gated on DPDP consent; passes the consent signal through. */}
        <button
          type="button"
          disabled={!agreedToDpdp}
          aria-disabled={!agreedToDpdp}
          aria-describedby={agreedToDpdp ? undefined : 'login-consent-help'}
          className={`${ssoBase} border border-[var(--ui-line-strong)] bg-card text-foreground hover:bg-[var(--ui-inset)] disabled:cursor-not-allowed disabled:opacity-50`}
          onClick={() => {
            rememberReturnPath(returnTo);
            window.location.assign(googleLoginUrl('/dashboard', agreedToDpdp, 1));
          }}
        >
          <GoogleBadge />
          {t('auth.signInWithGoogle')}
        </button>
      </div>

      {/* Footer link */}
      <p className="mt-6 text-center text-[13px] text-muted-foreground">
        {t('auth.noAccount')}{' '}
        <Link
          to="/register"
          className="inline-flex min-h-10 items-center rounded font-semibold text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {t('auth.createOne')}
        </Link>
      </p>
    </AuthLayout>
  );
}
