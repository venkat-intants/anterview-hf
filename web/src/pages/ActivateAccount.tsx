// ActivateAccount — an applicant claims the account their application created.
//
// Reached from the confirmation email:  {APP_BASE_URL}/activate#<token>
//
// The token is in the #fragment, so it never reaches a server log — the same
// discipline as the reset, exam and interview links.
//
// The link is checked BEFORE the form is shown. An applicant who opens a
// week-old email should be told the link has lapsed straight away, not after
// choosing a password and pressing a button. It also lets the page greet them
// by name and show the address the account will use, which is the one thing
// they need to confirm: people apply with more than one address.
//
// Two outcomes, because the server has two branches. `linked: 'new'` means the
// password they just chose is their password. `linked: 'existing'` means this
// address already had an account and the application was attached to it — the
// password they typed was not used, and saying "your account is ready" there
// would send them to sign in with a credential that does not work.

import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import {
  activateAccount,
  getActivationTarget,
  type ActivationTarget,
} from '@/api/publicApply';
import { toast } from '@/lib/toast';
import AuthLayout, { FormError, PasswordField, PasswordStrength, StatusPanel } from '@/components/layout/AuthLayout';
import { TEXT_LINK_CLASS } from '@/components/layout/authStyles';
import { Pill } from '@/design/components/primitives';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Lock,
  UserPlus,
} from '@/design/components/icons';
import { useFragmentToken } from '@/hooks/useFragmentToken';
import { useTranslation } from 'react-i18next';

type Phase = 'checking' | 'ready' | 'expired' | 'done';

export default function ActivateAccount() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Read once, then removed from the address bar and history entry.
  const token = useFragmentToken();
  const [phase, setPhase] = useState<Phase>(token ? 'checking' : 'expired');
  const [target, setTarget] = useState<ActivationTarget | null>(null);
  const [linked, setLinked] = useState<'new' | 'existing'>('new');
  // What the server said happened. For an address that already had an account
  // there are two outcomes with opposite instructions — "use your existing
  // password" and, when that account had never been verified, "use the one you
  // just chose" — and only the server knows which, so its sentence is shown
  // rather than one guessed from `linked`.
  const [serverMessage, setServerMessage] = useState('');
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  // The preview is a GET and does not consume the token, but it IS rate
  // limited — StrictMode's double-invoke would spend two of five allowed
  // requests a minute for nothing.
  const checked = useRef(false);

  useEffect(() => {
    if (!token || checked.current) return;
    checked.current = true;
    getActivationTarget(token)
      .then((t) => {
        setTarget(t);
        setPhase('ready');
      })
      .catch((err: unknown) => {
        setCheckError(
          err instanceof Error ? err.message : t('public.activate.invalidLink'),
        );
        setPhase('expired');
      });
  }, [token, t]);

  const mutation = useMutation({
    mutationFn: () => activateAccount(token, pw),
    onSuccess: (result) => {
      setLinked(result.linked);
      setServerMessage(result.message.trim());
      setPhase('done');
      toast.success(
        result.linked === 'existing'
          ? t('public.activate.toastExisting')
          : t('public.activate.toastNew'),
      );
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : t('public.activate.setupError'));
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 8) {
      setError(t('public.activate.pwShort'));
      return;
    }
    if (pw !== confirm) {
      setError(t('public.activate.pwMismatch'));
      return;
    }
    mutation.mutate();
  }

  if (phase === 'checking') {
    return (
      <AuthLayout>
        <StatusPanel icon={Loader2} tone="info" spin title={t('public.activate.checking')}>
          <p>{t('public.activate.oneMoment')}</p>
        </StatusPanel>
      </AuthLayout>
    );
  }

  if (phase === 'expired') {
    return (
      <AuthLayout>
        <StatusPanel
          icon={AlertCircle}
          tone="danger"
          title={t('public.activate.expiredTitle')}
          actions={
            <Link to="/login" className={TEXT_LINK_CLASS}>
              {t('public.activate.signInExisting')}
            </Link>
          }
        >
          <p>{checkError ?? t('public.activate.missingToken')}</p>
          {/* Deliberately no "request a new one": nothing on the public side
              can re-issue an applicant's activation link, and offering a button
              that cannot work is worse than saying who can help. */}
          <p className="text-[13px]">{t('public.activate.expiredSafe')}</p>
        </StatusPanel>
      </AuthLayout>
    );
  }

  if (phase === 'done') {
    return (
      <AuthLayout>
        <StatusPanel
          icon={CheckCircle2}
          tone="ok"
          title={
            linked === 'existing'
              ? t('public.activate.doneExistingTitle')
              : t('public.activate.doneNewTitle')
          }
          actions={
            <Pill className="min-h-11 w-full py-3" onClick={() => void navigate('/login', { replace: true })}>
              {t('public.activate.signIn')}
            </Pill>
          }
        >
          {linked === 'existing' ? (
            <>
              {/* The server's own sentence wins: only it knows whether the
                  existing password or the one just chosen now applies. */}
              <p>{serverMessage || t('public.activate.existingFallback')}</p>
              <p>{t('public.activate.inApplications')}</p>
            </>
          ) : (
            <p>{t('public.activate.doneNew')}</p>
          )}
        </StatusPanel>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[var(--ui-info-wash)] text-[var(--ui-info)]">
        <UserPlus className="h-6 w-6" aria-hidden="true" />
      </span>
      <h1 className="mt-5 text-[30px] font-bold leading-tight tracking-[-0.8px] text-foreground">
        {t('public.activate.heading')}
      </h1>
      <p className="mt-2 text-[15px] text-muted-foreground">
        {target?.job_title && target?.company_name
          ? t('public.activate.subtitleFull', {
              job: target.job_title,
              company: target.company_name,
            })
          : t('public.activate.subtitle')}
      </p>

      {target?.email && (
        <p className="mt-4 rounded-[12px] bg-[var(--ui-inset-soft)] px-4 py-3 text-[13px] text-muted-foreground">
          {t('public.activate.accountUses')}{' '}
          <span className="break-all font-semibold text-foreground">{target.email}</span>
        </p>
      )}

      <form
        onSubmit={onSubmit}
        noValidate
        aria-label={t('public.activate.formAria')}
        className="mt-6 flex flex-col gap-4"
      >
        <div className="space-y-2">
          <PasswordField
            id="ac-new"
            label={t('public.activate.choosePw')}
            autoComplete="new-password"
            placeholder="••••••••"
            icon={<Lock size={15} aria-hidden="true" />}
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'ac-error' : undefined}
          />
          <PasswordStrength password={pw} />
        </div>

        <PasswordField
          id="ac-confirm"
          label={t('public.activate.confirmPw')}
          autoComplete="new-password"
          placeholder="••••••••"
          icon={<Lock size={15} aria-hidden="true" />}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'ac-error' : undefined}
        />

        <FormError id="ac-error">{error}</FormError>

        <Pill
          type="submit"
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          className="min-h-11 w-full py-3"
        >
          {mutation.isPending ? t('public.activate.settingUp') : t('public.activate.create')}
        </Pill>
      </form>

      <p className="mt-6 text-center">
        <Link to="/login" className={TEXT_LINK_CLASS}>
          {t('public.activate.signInExisting')}
        </Link>
      </p>
    </AuthLayout>
  );
}
