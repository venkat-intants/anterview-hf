// NotFound — 404 page rendered for any unmatched route.
// Design: calm centred card on the canvas, one primary action.
// Unauthenticated users get a link to /; a signed-in user gets their own role's
// home (homePathFor), the same target login and a denied route use, so staff
// are not sent to the candidate dashboard (audit AD-F16).

import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { BrandMark } from '@/components/layout/AuthLayout';
import { PRIMARY_LINK_CLASS, TEXT_LINK_CLASS } from '@/components/layout/authStyles';
import { Home, ArrowLeft } from '@/design/components/icons';
import { useAuth } from '@/context/AuthContext';
import { homePathFor } from '@/components/layout/navSections';

export default function NotFound() {
  const { t } = useTranslation();
  const { isAuthenticated, user } = useAuth();
  const homePath = isAuthenticated ? homePathFor(user?.roles ?? []) : '/';

  return (
    <main
      className="flex min-h-screen flex-col items-center justify-center bg-background px-6 py-12 text-center font-sans text-foreground"
      aria-labelledby="not-found-heading"
    >
      <BrandMark size="lg" />
      <p
        className="mt-8 select-none font-display text-[96px] font-bold leading-none tracking-[-4px] text-[var(--ui-line-strong)] sm:text-[120px]"
        aria-hidden="true"
      >
        404
      </p>
      <h1 id="not-found-heading" className="mt-4 text-[28px] font-bold tracking-[-0.6px]">
        {t('error.pageNotFoundTitle')}
      </h1>
      <p className="mt-2 max-w-sm text-[15px] leading-relaxed text-muted-foreground">
        {t('error.pageNotFoundDesc')}
      </p>
      <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row">
        <Link to={homePath} className={PRIMARY_LINK_CLASS}>
          <Home size={16} aria-hidden="true" /> {t('error.goHome')}
        </Link>
        {!isAuthenticated && (
          <Link to="/login" className={TEXT_LINK_CLASS}>
            <ArrowLeft size={16} aria-hidden="true" /> {t('nav.login', 'Sign in')}
          </Link>
        )}
      </div>
    </main>
  );
}
