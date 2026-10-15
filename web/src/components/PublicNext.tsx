// PublicNext — the closing "What happens next" block on a token page's finish
// state: plainly what happens now and who acts next, then, for someone who is
// not signed in, one way to follow the application from an account.
//
// The account link follows three rules (UX round 3):
//   - no session at all   -> "Track this in My applications" -> /login
//   - a signed-in account -> the same words -> /applications
//   - a guest session (`guest_candidate` only, minted from an interview link)
//     -> NO account link. Every account page refuses a guest token on the
//     server, so offering one would send them to a wall.
// While the silent-refresh probe is still running we do not know which of the
// three applies, so no link is drawn yet.

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { WhatNext } from '@/design/components/next-step';
import { useAuth } from '@/context/AuthContext';
import { isGuestOnly } from '@/lib/guest';
import { cn } from '@/lib/utils';

interface PublicNextProps {
  /** What happens now and who acts next, in the candidate's language. */
  children: ReactNode;
  /** Offer the "Track this in My applications" link. Default true; pages that
   *  are not about an application (a decline, a withdrawn offer) pass false. */
  track?: boolean;
  className?: string;
}

export function PublicNext({ children, track = true, className }: PublicNextProps): JSX.Element {
  const { t } = useTranslation();
  const { isAuthenticated, isInitializing, user } = useAuth();
  const guest = isGuestOnly(user?.roles);
  const target = guest || isInitializing ? null : isAuthenticated ? '/applications' : '/login';
  return (
    <WhatNext title={t('public.whatNext')} className={className}>
      <p>{children}</p>
      {track && target ? (
        <Link
          to={target}
          className={cn(
            'mt-3 inline-flex min-h-[44px] items-center rounded-[6px] text-[14px] font-medium text-[var(--ui-info)] underline-offset-2',
            'hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          )}
        >
          {t('public.track')}
        </Link>
      ) : null}
    </WhatNext>
  );
}
