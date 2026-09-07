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
import AuthLayout from '@/components/layout/AuthLayout';
import { Field, Pill } from '@/design/components/primitives';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Lock,
  UserPlus,
} from '@/design/components/icons';

/** 0–4 password-strength score (presentation only). Mirrors ResetPassword. */
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

function tokenFromHash(): string {
  if (typeof window === 'undefined') return '';
  return window.location.hash.replace(/^#/, '').trim();
}

type Phase = 'checking' | 'ready' | 'expired' | 'done';

export default function ActivateAccount() {
  const navigate = useNavigate();
  const [token] = useState(tokenFromHash);
  const [phase, setPhase] = useState<Phase>(token ? 'checking' : 'expired');
  const [target, setTarget] = useState<ActivationTarget | null>(null);
  const [linked, setLinked] = useState<'new' | 'existing'>('new');
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
          err instanceof Error ? err.message : 'This link is invalid or has expired.',
        );
        setPhase('expired');
      });
  }, [token]);

  const mutation = useMutation({
    mutationFn: () => activateAccount(token, pw),
    onSuccess: (result) => {
      setLinked(result.linked);
      setPhase('done');
      toast.success(
        result.linked === 'existing'
          ? 'Application added to your existing account.'
          : 'Your account is ready.',
      );
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : 'Could not set up your account.');
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

  if (phase === 'checking') {
    return (
      <AuthLayout>
        <div className="flex flex-col items-center text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
          </span>
          <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
            Checking your link…
          </h1>
          <p className="mt-2 text-[14px] text-[#888b91]">One moment.</p>
        </div>
      </AuthLayout>
    );
  }

  if (phase === 'expired') {
    return (
      <AuthLayout>
        <div className="flex flex-col items-center text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(230,113,79,0.14)] text-[#e6714f]">
            <AlertCircle className="h-6 w-6" aria-hidden="true" />
          </span>
          <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
            This link has expired
          </h1>
          <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">
            {checkError ??
              'This link is missing its token. Open the link from your application confirmation email.'}
          </p>
          {/* Deliberately no "request a new one": nothing on the public side
              can re-issue an applicant's activation link, and offering a button
              that cannot work is worse than saying who can help. */}
          <p className="mt-4 max-w-sm text-[13px] text-[#888b91]">
            Your application is safe — this only affects signing in. Reply to the
            confirmation email and the hiring team can send a new link.
          </p>
          <Link
            to="/login"
            className="mt-6 text-[13px] text-[#60a5fa] hover:underline underline-offset-4"
          >
            Already have an account? Sign in
          </Link>
        </div>
      </AuthLayout>
    );
  }

  if (phase === 'done') {
    return (
      <AuthLayout>
        <div className="flex flex-col items-center text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(39,201,63,0.14)] text-[#27c93f]">
            <CheckCircle2 className="h-6 w-6" aria-hidden="true" />
          </span>
          <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
            {linked === 'existing' ? 'Added to your account' : 'You’re all set'}
          </h1>
          <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">
            {linked === 'existing'
              ? 'This email already had an account, so we added the application to it. Sign in with your existing password.'
              : 'Sign in to see which stage your application is at and what happens next.'}
          </p>
          <Pill
            className="mt-6 w-full py-3"
            onClick={() => void navigate('/login', { replace: true })}
          >
            Sign in
          </Pill>
        </div>
      </AuthLayout>
    );
  }

  const strength = strengthOf(pw);

  return (
    <AuthLayout>
      <div className="mb-2 flex flex-col items-center text-center">
        <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
          <UserPlus className="h-6 w-6" aria-hidden="true" />
        </span>
      </div>
      <h1 className="text-center text-[24px] font-semibold tracking-[-0.6px] text-white">
        Track your application
      </h1>
      <p className="mt-1.5 text-center text-[14px] text-[#888b91]">
        {target?.job_title && target?.company_name
          ? `Set a password to follow your ${target.job_title} application at ${target.company_name}.`
          : 'Set a password to follow your application.'}
      </p>

      {target?.email && (
        <p className="mt-4 rounded-[12px] border border-white/8 bg-white/[0.03] px-4 py-3 text-center text-[13px] text-[#c6c8cc]">
          Your account will use{' '}
          <span className="font-medium text-white">{target.email}</span>
        </p>
      )}

      <form
        onSubmit={onSubmit}
        noValidate
        aria-label="Activate account form"
        className="mt-6 flex flex-col gap-4"
      >
        <div className="space-y-2">
          <Field
            id="ac-new"
            label="Choose a password"
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
                    style={{
                      background:
                        i < strength ? STRENGTH_COLOR[strength] : 'rgba(255,255,255,0.08)',
                    }}
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
          id="ac-confirm"
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

        <Pill
          type="submit"
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          className="w-full py-3"
        >
          {mutation.isPending ? 'Setting up…' : 'Create my account'}
        </Pill>
      </form>

      <p className="mt-6 text-center text-[13px] text-[#888b91]">
        <Link
          to="/login"
          className="font-medium text-white hover:underline focus:outline-none focus:underline underline-offset-4"
        >
          Already have an account? Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
