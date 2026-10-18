// VerifyEmail — confirm an email address from the signup link.
// The single-use token arrives in the URL #fragment:
//   {APP_BASE_URL}/verify-email#<token>
// POST /auth/verify-email is fired once on mount; we show the outcome.

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { verifyEmail } from '@/api/auth';
import AuthLayout, { StatusPanel } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS, TEXT_LINK_CLASS } from '@/components/layout/authStyles';
import { CheckCircle2, AlertCircle, Loader2 } from '@/design/components/icons';
import { useFragmentToken } from '@/hooks/useFragmentToken';

type Status = 'verifying' | 'success' | 'error';

export default function VerifyEmail() {
  // Read once, then removed from the address bar and history entry.
  const token = useFragmentToken();
  const [status, setStatus] = useState<Status>('verifying');
  const [message, setMessage] = useState('');
  // Guard React 18 StrictMode double-invoke so we don't burn the single-use token.
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
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
  }, [token]);

  return (
    <AuthLayout>
      <div aria-live="polite">
        {status === 'verifying' && (
          <StatusPanel icon={Loader2} tone="info" spin title="Confirming your email…">
            <p>One moment.</p>
          </StatusPanel>
        )}

        {status === 'success' && (
          <StatusPanel
            icon={CheckCircle2}
            tone="ok"
            title="Email confirmed"
            actions={
              <Link to="/login" className={PRIMARY_LINK_CLASS}>
                Continue to sign in
              </Link>
            }
          >
            <p>Thanks - your email address is verified. You can sign in and get started.</p>
          </StatusPanel>
        )}

        {status === 'error' && (
          <StatusPanel
            icon={AlertCircle}
            tone="danger"
            title="Couldn’t verify your email"
            actions={
              <Link to="/login" className={TEXT_LINK_CLASS}>
                Back to sign in
              </Link>
            }
          >
            <p>{message}</p>
            <p className="text-[13px]">You can request a fresh link from your profile after signing in.</p>
          </StatusPanel>
        )}
      </div>
    </AuthLayout>
  );
}
