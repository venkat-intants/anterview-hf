// Register page — full_name + email + password → POST /auth/register → /dashboard
// Design: AuthLayout split shell. The form leads; the DPDP consent checkbox
//         sits directly above its submit, and the SSO options (conic-G Google
//         badge) follows, gated by the same checkbox.
// Logic: RHF + zodResolver (full_name 2–100, email, password 8–128),
//        registerUser()→getMe()→setAuth→/dashboard, isPending/aria-busy,
//        toast errors, all t() keys. No role tabs (role is server-assigned).

import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useMutation } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { register as registerUser, getMe } from '@/api/auth';
import { googleLoginUrl } from '@/api/sso';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { useCandidateError } from '@/lib/candidateCopy';
import { ConsentDisclosure } from '@/components/layout/ConsentDisclosure';
import type { AuthUser } from '@/types/auth';
import AuthLayout, { FormError, PasswordField, StatusPanel } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS } from '@/components/layout/authStyles';
import { Field } from '@/design/components/primitives';
import { User, Mail, Lock, CheckCircle2 } from '@/design/components/icons';
import { Button } from '@/components/ui/button';

// ── Zod schema ────────────────────────────────────────────────────────────────
// Built per render language so field errors are localised (audit CA-M17).
function registerSchema(t: TFunction) {
  return z.object({
    full_name: z.string().min(2, t('auth.errNameMin')).max(100, t('auth.errNameMax')),
    email: z.string().email(t('auth.errEmail')),
    password: z.string().min(8, t('auth.errPasswordMin')).max(128, t('auth.errPasswordMax')),
  });
}

type RegisterFormValues = z.infer<ReturnType<typeof registerSchema>>;

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

export default function Register() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setAuth } = useAuth();
  const errorText = useCandidateError();

  // DPDP consent (DPDP §7) — gates BOTH sign-up paths. For Google SSO the
  // consent signal is threaded through initiate → callback, which records the
  // ledger row atomically with account creation. The local register API does
  // not yet capture it server-side, so the checkbox still gates that submit.
  const [agreedToDpdp, setAgreedToDpdp] = useState(false);
  // When the backend requires email confirmation, registration creates the
  // account but does NOT log in — we show a "check your inbox" panel instead.
  const [verifyEmailSent, setVerifyEmailSent] = useState<string | null>(null);

  const schema = useMemo(() => registerSchema(t), [t]);
  const form = useForm<RegisterFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: '', email: '', password: '' },
  });

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = form;

  // ── Mutation: registerUser → (auto-login → /dashboard) | (verify-email panel) ─
  const mutation = useMutation({
    mutationFn: async (values: RegisterFormValues) => {
      const regRes = await registerUser(values);
      // No token when verification is required — skip the getMe call.
      const me =
        regRes.verification_required || !regRes.access_token ? null : await getMe();
      return { regRes, me, email: values.email };
    },
    onSuccess: ({ regRes, me, email }) => {
      if (regRes.verification_required || !me || !regRes.access_token) {
        // Account created, not signed in — prompt the user to confirm their email.
        setVerifyEmailSent(email);
        return;
      }
      const user: AuthUser = {
        user_id: me.user_id,
        full_name: me.full_name,
        email: me.email,
        roles: me.roles,
      };
      setAuth(regRes.access_token, user);
      // Straight to personalization, not via /dashboard. A self-registered
      // account is always a self-serve candidate, so the dashboard would only
      // render a generic page and then bounce them here anyway — the bounce is
      // visible, and it is the first thing they ever see of the product.
      void navigate('/onboarding', { replace: true });
    },
    onError: (err: unknown) => {
      // English server text gives way to the page's language (audit CA-M21).
      const status = statusOf(err);
      const fallback =
        status === 409
          ? t('auth.errAccountExists')
          : status === 400 || status === 422
            ? t('auth.errRegisterInvalid')
            : t('error.generic');
      toast.error(errorText(err, fallback));
    },
  });

  function onSubmit(values: RegisterFormValues) {
    mutation.mutate(values);
  }

  // Email-confirmation gate: account created but sign-in withheld until verified.
  if (verifyEmailSent) {
    return (
      <AuthLayout>
        <StatusPanel
          icon={CheckCircle2}
          tone="ok"
          title={t('auth.confirmEmailTitle')}
          actions={
            <Link to="/login" className={PRIMARY_LINK_CLASS}>
              {t('auth.goToSignIn')}
            </Link>
          }
        >
          <p>
            <Trans
              i18nKey="auth.confirmEmailBody"
              values={{ email: verifyEmailSent }}
              components={{ 1: <span className="font-semibold text-foreground" /> }}
            />
          </p>
        </StatusPanel>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      {/* Heading */}
      <h1 className="text-[30px] font-bold leading-tight tracking-[-0.8px] text-foreground">
        {t('auth.createYourAccount')}
      </h1>
      <p className="mt-2 text-[15px] text-muted-foreground">{t('auth.startJourney')}</p>

      {/* Full name + email + password form — RHF + Zod + inline FormMessage */}
      <form
        onSubmit={(e) => void handleSubmit(onSubmit)(e)}
        noValidate
        aria-label={t('auth.registerFormAria')}
        className="mt-8 flex flex-col gap-2"
      >
        <div>
          <Field
            label={t('auth.fullName')}
            type="text"
            autoComplete="name"
            placeholder={t('auth.namePlaceholder')}
            icon={<User size={15} aria-hidden="true" />}
            aria-invalid={errors.full_name ? true : undefined}
            aria-describedby={errors.full_name ? 'reg-full_name-error' : undefined}
            {...register('full_name')}
          />
          <FormError id="reg-full_name-error">{errors.full_name?.message}</FormError>
        </div>

        <div>
          <Field
            label={t('auth.email')}
            type="email"
            autoComplete="email"
            placeholder="you@email.com"
            icon={<Mail size={15} aria-hidden="true" />}
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? 'reg-email-error' : undefined}
            {...register('email')}
          />
          <FormError id="reg-email-error">{errors.email?.message}</FormError>
        </div>

        <div>
          <PasswordField
            label={t('auth.password')}
            autoComplete="new-password"
            placeholder={t('auth.passwordPlaceholder')}
            icon={<Lock size={15} aria-hidden="true" />}
            // Says what is enforced (length) and only advises the rest (audit CA-L8).
            hint={t('auth.passwordHint')}
            invalid={Boolean(errors.password)}
            aria-describedby={errors.password ? 'reg-password-error' : undefined}
            {...register('password')}
          />
          <FormError id="reg-password-error">{errors.password?.message}</FormError>
        </div>

        {/* DPDP consent — gates BOTH sign-up methods (Google SSO + the form). */}
        <label className="mt-2 flex cursor-pointer items-start gap-3 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3.5 text-[13px] leading-relaxed text-muted-foreground">
          <input
            type="checkbox"
            checked={agreedToDpdp}
            onChange={(e) => { setAgreedToDpdp(e.target.checked); }}
            className="mt-0.5 h-4 w-4 flex-none cursor-pointer accent-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            aria-describedby={agreedToDpdp ? 'reg-consent-disclosure' : 'reg-consent-help reg-consent-disclosure'}
          />
          <span>{t('auth.consentLabel')}</span>
        </label>
        <ConsentDisclosure id="reg-consent-disclosure" />

        {!agreedToDpdp && (
          <p id="reg-consent-help" className="text-[12.5px] text-muted-foreground">
            {t('auth.consentNeededSignup')}
          </p>
        )}

        {/* Submit — gated by the DPDP consent checkbox directly above it */}
        <Button
          type="submit"
          disabled={mutation.isPending || !agreedToDpdp}
          aria-busy={mutation.isPending}
          size="lg"
          className="w-full"
        >
          {mutation.isPending ? t('auth.creatingAccount') : t('auth.createAccount')}
        </Button>
      </form>

      {/* Divider */}
      <div className="my-6 flex items-center gap-3 text-[13px] text-muted-foreground">
        <span className="h-px flex-1 bg-[var(--ui-inset-strong)]" aria-hidden="true" />
        {t('auth.orContinueWithFull')}
        <span className="h-px flex-1 bg-[var(--ui-inset-strong)]" aria-hidden="true" />
      </div>

      {/* SSO buttons */}
      <div className="flex flex-col gap-2.5">
        {/* Google — gated on DPDP consent; passes the consent signal through. */}
        <button
          type="button"
          disabled={!agreedToDpdp}
          aria-disabled={!agreedToDpdp}
          aria-describedby={agreedToDpdp ? undefined : 'reg-consent-help'}
          className={`${ssoBase} border border-[var(--ui-line-strong)] bg-card text-foreground hover:bg-[var(--ui-inset)] disabled:cursor-not-allowed disabled:opacity-50`}
          onClick={() => { window.location.assign(googleLoginUrl('/dashboard', agreedToDpdp, 1)); }}
        >
          <GoogleBadge />
          {t('auth.signUpWithGoogle')}
        </button>
      </div>

      {/* Footer link */}
      <p className="mt-6 text-center text-[13px] text-muted-foreground">
        {t('auth.haveAccount')}{' '}
        <Link
          to="/login"
          className="inline-flex min-h-10 items-center rounded font-semibold text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {t('auth.signIn2')}
        </Link>
      </p>
    </AuthLayout>
  );
}
