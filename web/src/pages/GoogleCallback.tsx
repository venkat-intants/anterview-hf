// Google OAuth callback landing page — S5-003b / B-035
//
// Google redirects the browser here (configured as the OAuth redirect_uri) with
// ?code & ?state. We exchange them for an AntHire JWT, load the profile, store
// auth, and continue into the app. The exchange is guarded by a ref because the
// state token is single-use (Redis get-then-delete) and React StrictMode
// double-invokes effects in development — a second call would burn the token.
//
// Presentation = CenteredCard. OAuth mechanics are UNCHANGED.

import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { completeGoogleLogin } from '@/api/sso';
import { getMe } from '@/api/auth';
import { useAuth } from '@/context/AuthContext';
import { homePathFor } from '@/components/layout/navSections';
import { toast } from '@/lib/toast';
import { takeReturnPath } from '@/lib/returnTo';
import { CenteredCard } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS } from '@/components/layout/authStyles';
import { AlertCircle } from '@/design/components/icons';
import type { AuthUser } from '@/types/auth';

/** Server `detail` codes from /auth/sso/google/callback → i18n keys. */
const SSO_ERROR_KEYS: Record<string, string | undefined> = {
  GOOGLE_SIGNIN_CANDIDATES_ONLY: 'googleCallback.candidatesOnly',
  INVALID_OR_EXPIRED_STATE: 'googleCallback.expired',
  GOOGLE_EMAIL_UNVERIFIED: 'googleCallback.emailUnverified',
  GOOGLE_OAUTH_NOT_CONFIGURED: 'googleCallback.unavailable',
  GOOGLE_TOKEN_EXCHANGE_FAILED: 'googleCallback.unavailable',
  GOOGLE_USERINFO_FAILED: 'googleCallback.unavailable',
};

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
        // Same landing rule as the password login (Login.tsx): SSO is a second
        // door into the same app, not a second destination. Hardcoding
        // /dashboard here sent a Google-authenticated admin / HR manager /
        // super admin / platform owner to the candidate page, which their
        // role-scoped sidebar does not even link to. homePathFor already
        // returns /dashboard for candidates and for unknown/empty roles, so
        // the candidate path is unchanged.
        // Or the protected page that sent them to sign in (audit CA-M27).
        void navigate(takeReturnPath() ?? homePathFor(me.roles), { replace: true });
      } catch (err) {
        // The server answers with a machine code (GOOGLE_SIGNIN_CANDIDATES_ONLY).
        // Known codes get a sentence in the visitor's language; an unknown
        // one gets the generic line, never the code itself.
        const detail = err instanceof Error ? err.message : '';
        const key = SSO_ERROR_KEYS[detail];
        const message = key
          ? t(key)
          : !detail || /^[A-Z0-9_]+$/.test(detail)
            ? t('googleCallback.failed')
            : detail;
        setError(message);
        toast.error(message);
      }
    })();
  }, [params, navigate, setAuth, t]);

  return (
    <CenteredCard labelledBy="gcb-heading">
      {error ? (
        /* ── Error state ────────────────────────────────────────────────────── */
        <div role="alert" className="text-center">
          <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]">
            <AlertCircle className="h-6 w-6" aria-hidden="true" />
          </span>
          <h1 id="gcb-heading" className="mt-5 text-[24px] font-bold leading-tight tracking-[-0.5px] text-foreground">
            {t('googleCallback.failedTitle')}
          </h1>
          <p className="mt-2 text-[14px] leading-relaxed text-muted-foreground">{error}</p>
          <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
            <Link to="/login" className={PRIMARY_LINK_CLASS}>
              {t('googleCallback.backToSignIn')}
            </Link>
            {/* No "Retry" that reloads: the code and state in this URL are
                single use, so a reload always fails (audit CA-M8). A new
                attempt starts from the sign-in page. */}
          </div>
        </div>
      ) : (
        /* ── Loading state ──────────────────────────────────────────────────── */
        <div className="text-center">
          <div className="mb-4 flex justify-center">
            <div
              className="h-8 w-8 animate-spin rounded-full border-[3px] border-[var(--accent)] border-t-transparent"
              role="status"
              aria-label={t('googleCallback.completing')}
            />
          </div>
          <h1 id="gcb-heading" className="text-[18px] font-bold text-foreground">
            {t('googleCallback.completing')}
          </h1>
          <p className="mt-1 text-[13px] text-muted-foreground">{t('googleCallback.momentSub')}</p>
        </div>
      )}
    </CenteredCard>
  );
}
