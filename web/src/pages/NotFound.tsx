// NotFound — 404 page rendered for any unmatched route.
// Design: branded gradient 404 + aurora background.
// Unauthenticated users get a link to /; authenticated users get /dashboard.

import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AuroraField } from '@/design/components/AuroraField';
import { Pill } from '@/design/components/primitives';
import { Home, ArrowLeft } from '@/design/components/icons';
import { useAuth } from '@/context/AuthContext';

export default function NotFound() {
  const { t } = useTranslation();
  const { isAuthenticated } = useAuth();
  const homePath = isAuthenticated ? '/dashboard' : '/';

  return (
    <main
      className="relative flex min-h-screen flex-col items-center justify-center bg-black px-6 text-center font-sans text-white"
      aria-labelledby="not-found-heading"
    >
      <AuroraField />
      <div className="relative z-10 flex flex-col items-center">
        <div className="bg-[linear-gradient(160deg,#fff,#a887dc,#e6c4e7)] bg-clip-text text-[120px] font-semibold leading-none tracking-[-4px] text-transparent select-none">
          404
        </div>
        <h1 id="not-found-heading" className="mt-4 text-[24px] font-semibold tracking-[-0.6px]">
          {t('error.pageNotFoundTitle')}
        </h1>
        <p className="mt-2 max-w-sm text-[14px] text-[#888b91]">{t('error.pageNotFoundDesc')}</p>
        <div className="mt-8 flex items-center gap-3">
          <Link to={homePath}>
            <Pill className="px-5 py-3">
              <Home size={16} aria-hidden="true" /> {t('error.goHome')}
            </Pill>
          </Link>
          {!isAuthenticated && (
            <Link to="/login">
              <Pill variant="ghost" className="px-5 py-3">
                <ArrowLeft size={16} aria-hidden="true" /> {t('nav.login', 'Sign in')}
              </Pill>
            </Link>
          )}
        </div>
      </div>
    </main>
  );
}
