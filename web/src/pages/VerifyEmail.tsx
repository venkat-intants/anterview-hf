// VerifyEmail — confirm an email address from the signup link.
// The single-use token arrives in the URL #fragment:
//   {APP_BASE_URL}/verify-email#<token>
// POST /auth/verify-email is fired once on mount; we show the outcome.

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { verifyEmail } from '@/api/auth';
import AuthLayout from '@/components/layout/AuthLayout';
import { CheckCircle2, AlertCircle, Loader2 } from '@/design/components/icons';

type Status = 'verifying' | 'success' | 'error';

function tokenFromHash(): string {
  if (typeof window === 'undefined') return '';
  return window.location.hash.replace(/^#/, '').trim();
}

export default function VerifyEmail() {
  const [status, setStatus] = useState<Status>('verifying');
  const [message, setMessage] = useState('');
  // Guard React 18 StrictMode double-invoke so we don't burn the single-use token.
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    const token = tokenFromHash();
    if (!token) {
      setStatus('error');
      setMessage('This verification link is missing its token.');
      return;
    }
    verifyEmail(token)
      .then(() => setStatus('success'))
      .catch((err: unknown) => {
        setStatus('error');
        setMessage(
          err instanceof Error ? err.message : 'This verification link is invalid or has expired.',
        );
      });
  }, []);

  return (
    <AuthLayout>
      <div className="flex flex-col items-center text-center">
        {status === 'verifying' && (
          <>
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
            </span>
            <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
              Confirming your email…
            </h1>
            <p className="mt-2 text-[14px] text-[#888b91]">One moment.</p>
          </>
        )}

        {status === 'success' && (
          <>
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(39,201,63,0.14)] text-[#27c93f]">
              <CheckCircle2 className="h-6 w-6" aria-hidden="true" />
            </span>
            <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
              Email confirmed
            </h1>
            <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">
              Thanks — your email address is verified. You can sign in and get started.
            </p>
            <Link
              to="/login"
              className="mt-6 inline-flex items-center justify-center rounded-pill bg-white px-5 py-2.5 text-[14px] font-semibold text-black hover:bg-[#eaeaea]"
            >
              Continue to sign in
            </Link>
          </>
        )}

        {status === 'error' && (
          <>
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(230,113,79,0.14)] text-[#e6714f]">
              <AlertCircle className="h-6 w-6" aria-hidden="true" />
            </span>
            <h1 className="mt-5 text-[22px] font-semibold tracking-[-0.6px] text-white">
              Couldn’t verify your email
            </h1>
            <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">{message}</p>
            <p className="mt-4 max-w-sm text-[13px] text-[#70757c]">
              You can request a fresh link from your profile after signing in.
            </p>
            <Link
              to="/login"
              className="mt-6 text-[13px] text-[#60a5fa] hover:underline underline-offset-4"
            >
              Back to sign in
            </Link>
          </>
        )}
      </div>
    </AuthLayout>
  );
}
