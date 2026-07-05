// Google OAuth callback landing page — S5-003b / B-035
//
// Google redirects the browser here (configured as the OAuth redirect_uri) with
// ?code & ?state. We exchange them for an Intants JWT, load the profile, store
// auth, and continue into the app. The exchange is guarded by a ref because the
// state token is single-use (Redis get-then-delete) and React StrictMode
// double-invokes effects in development — a second call would burn the token.
//
// Presentation = aurora-dark design language. OAuth mechanics are UNCHANGED.

import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import { completeGoogleLogin } from '@/api/sso';
import { getMe } from '@/api/auth';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { AuroraField } from '@/design/components/AuroraField';
import { Pill } from '@/design/components/primitives';
import { AlertCircle } from '@/design/components/icons';
import type { AuthUser } from '@/types/auth';

/** Brand dot-in-rounded-square mark (matches the design auth shell). */
function BrandMark() {
  return (
    <span className="inline-flex h-12 w-12 items-center justify-center rounded-[11px] bg-[linear-gradient(135deg,#112d72,#a887dc)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]">
      <span className="h-3.5 w-3.5 rounded-full bg-white" />
    </span>
  );
}

export default function GoogleCallback() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setAuth } = useAuth();
  const [params] = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const exchangedRef = useRef(false);

  useEffect(() => {
    // Run the single-use exchange exactly once (StrictMode double-effect guard).
    if (exchangedRef.current) return;
    exchangedRef.current = true;

    const code = params.get('code');
    const state = params.get('state');
    const oauthError = params.get('error');

    if (oauthError) {
      const msg = t('googleCallback.cancelled', { error: oauthError });
      setError(msg);
      return;
    }
    if (!code || !state) {
      setError(t('googleCallback.missingCode'));
      return;
    }

    void (async () => {
      try {
        const tokens = await completeGoogleLogin(code, state);
        const me = await getMe(tokens.access_token);
        const user: AuthUser = {
          user_id: me.user_id,
          full_name: me.full_name,
          email: me.email,
          roles: me.roles,
        };
        setAuth(tokens.access_token, user);
        void navigate('/dashboard', { replace: true });
      } catch (err) {
        const message = err instanceof Error ? err.message : t('googleCallback.failed');
        setError(message);
        toast.error(message);
      }
    })();
  }, [params, navigate, setAuth, t]);

  return (
    <main className="relative flex min-h-screen items-center justify-center bg-black px-4 py-12 font-sans text-white">
      <AuroraField />
      {error ? (
        /* ── Error state ────────────────────────────────────────────────────── */
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
          className="relative z-10 w-full max-w-md"
        >
          <div className="mb-8 flex justify-center">
            <Link
              to="/"
              className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded-[11px]"
            >
              <BrandMark />
            </Link>
          </div>

          <div
            role="alert"
            className="rounded-[24px] border border-[rgba(230,113,79,0.2)] bg-[#0f0f10] p-8 text-center"
          >
            <div className="mb-4 flex justify-center">
              <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-[rgba(230,113,79,0.16)]">
                <AlertCircle className="h-6 w-6 text-[#e6714f]" aria-hidden="true" />
              </span>
            </div>
            <h1 className="mb-2 text-[20px] font-semibold tracking-[-0.4px] text-white">
              {t('googleCallback.failedTitle')}
            </h1>
            <p className="mb-6 text-[13.5px] text-[#888b91]">{error}</p>
            <div className="flex flex-col justify-center gap-3 sm:flex-row">
              <Link to="/login">
                <Pill className="w-full sm:w-auto">{t('googleCallback.backToSignIn')}</Pill>
              </Link>
              <Pill variant="ghost" type="button" onClick={() => window.location.reload()}>
                {t('googleCallback.retry')}
              </Pill>
            </div>
          </div>
        </motion.div>
      ) : (
        /* ── Loading state ──────────────────────────────────────────────────── */
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="relative z-10 text-center"
        >
          <div className="mb-6 flex justify-center">
            <BrandMark />
          </div>
          <div className="mb-4 flex justify-center">
            <div
              className="h-8 w-8 animate-spin rounded-full border-[3px] border-[var(--accent)] border-t-transparent"
              role="status"
              aria-label={t('googleCallback.completing')}
            />
          </div>
          <p className="text-[14px] font-medium text-white">{t('googleCallback.completing')}</p>
          <p className="mt-1 text-[12px] text-[#888b91]">{t('googleCallback.momentSub')}</p>
        </motion.div>
      )}
    </main>
  );
}
