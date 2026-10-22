// VerifyEmail — confirm an email address from the signup link.
// The single-use token arrives in the URL #fragment:
//   {APP_BASE_URL}/verify-email#<token>
// POST /auth/verify-email is fired once on mount; we show the outcome.

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { verifyEmail } from '@/api/auth';
import AuthLayout, { StatusPanel } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS, TEXT_LINK_CLASS } from '@/components/layout/authStyles';
import { CheckCircle2, AlertCircle, Loader2 } from '@/design/components/icons';
import { useFragmentToken } from '@/hooks/useFragmentToken';
import { useCandidateError } from '@/lib/candidateCopy';

type Status = 'verifying' | 'success' | 'error';

export default function VerifyEmail() {
  const { t } = useTranslation();
  const errorText = useCandidateError();
  // Read once, then removed from the address bar and history entry.
  const token = useFragmentToken();
  const [status, setStatus] = useState<Status>('verifying');
  // The failure itself, so its sentence follows the UI language.
  const [failure, setFailure] = useState<unknown>(null);
  // Guard React 18 StrictMode double-invoke so we don't burn the single-use token.
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    if (!token) {
      setStatus('error');
      setFailure('missing');
      return;
    }
    verifyEmail(token)
      .then(() => setStatus('success'))
      .catch((err: unknown) => {
        setStatus('error');
        setFailure(err);
      });
  }, [token]);

  return (
    <AuthLayout>
      <div aria-live="polite">
        {status === 'verifying' && (
          <StatusPanel icon={Loader2} tone="info" spin title={t('auth.verifyingTitle')}>
            <p>{t('auth.oneMoment')}</p>
          </StatusPanel>
        )}

        {status === 'success' && (
          <StatusPanel
            icon={CheckCircle2}
            tone="ok"
            title={t('auth.verifiedTitle')}
            actions={
              <Link to="/login" className={PRIMARY_LINK_CLASS}>
                {t('auth.continueToSignIn')}
              </Link>
            }
          >
            <p>{t('auth.verifiedBody')}</p>
          </StatusPanel>
        )}

        {status === 'error' && (
          <StatusPanel
            icon={AlertCircle}
            tone="danger"
            title={t('auth.verifyFailedTitle')}
            actions={
              <Link to="/login" className={TEXT_LINK_CLASS}>
                {t('auth.backToSignIn')}
              </Link>
            }
          >
            <p>
              {failure === 'missing'
                ? t('auth.verifyMissingToken')
                : errorText(failure, t('auth.verifyInvalid'))}
            </p>
            {/* No page offers a "resend" control; signing in with an
                unconfirmed address re-sends the link (audit CA-M9). */}
            <p className="text-[13px]">{t('auth.verifyHelp')}</p>
          </StatusPanel>
        )}
      </div>
    </AuthLayout>
  );
}
