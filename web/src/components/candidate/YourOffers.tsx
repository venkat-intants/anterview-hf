// YourOffers — PH4-A3. A signed-in candidate's own offers: status, expiry
// (while it can still be answered), what they were paid, and a button that
// mints a fresh link into the same public /offer page anyone reaches from
// their email — rotating the token, exactly like "Open offer" in a fresh
// tab would from the email itself.
//
// Candidate-facing: every string goes through i18n (EN/HI/TE per CLAUDE.md).
//
// SECURITY: the link the server returns is validated as same-origin with
// pathname `/offer` before this ever navigates to it — an open redirect
// would otherwise let a compromised or buggy server send a signed-in
// candidate anywhere.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { getMyOfferLink, listMyOffers, type CandidateOffer, type OfferStatus } from '@/api/offers';
import { sameOriginUrl } from '@/lib/safeUrl';
import { useCandidateError } from '@/lib/candidateCopy';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/design/components/page';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Briefcase } from '@/design/components/icons';

function localeFor(lang: string): string {
  if (lang.startsWith('hi')) return 'hi-IN';
  if (lang.startsWith('te')) return 'te-IN';
  return 'en-IN';
}

const STATUS_TONE: Record<OfferStatus, TagTone> = {
  draft: 'neutral',
  pending_approval: 'neutral',
  approved: 'neutral',
  rejected: 'neutral',
  sent: 'amber',
  accepted: 'forest',
  declined: 'ember',
  expired: 'neutral',
  withdrawn: 'neutral',
};

function money(o: CandidateOffer, locale: string): string | null {
  if (!o.base_salary || !o.currency) return null;
  const amount = Number(o.base_salary).toLocaleString(locale);
  return `${o.currency} ${amount}`;
}

/**
 * One offer. `embedded` is for an offer shown ON its application card, where the
 * card already names the role and company: only the status is repeated.
 */
function OfferCard({
  offer,
  locale,
  embedded = false,
}: {
  offer: CandidateOffer;
  locale: string;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const errText = useCandidateError();
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);

  async function open(): Promise<void> {
    setError(null);
    setOpening(true);
    try {
      const link = await getMyOfferLink(offer.id);
      const safe = sameOriginUrl(link.url, '/offer');
      if (!safe) {
        setError(t('myOffers.openError'));
        return;
      }
      window.location.assign(safe);
    } catch (e: unknown) {
      setError(errText(e, t('myOffers.openError')));
    } finally {
      setOpening(false);
    }
  }

  const pay = money(offer, locale);
  const covered =
    embedded &&
    (offer.status === 'sent' || (offer.status === 'accepted' && !offer.preboarding_completed_at));

  return (
    <div className="rounded-[14px] bg-[var(--ui-inset-soft)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {embedded ? (
            <h3 className="text-[15px] font-bold text-foreground">{t('myOffers.heading')}</h3>
          ) : (
            <>
              <h3 className="flex items-center gap-2 text-[15px] font-bold text-foreground">
                <Briefcase
                  size={15}
                  className="shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
                {offer.job_title}
              </h3>
              {offer.company ? (
                <p className="mt-0.5 text-[13px] text-muted-foreground">{offer.company}</p>
              ) : null}
            </>
          )}
        </div>
        <StatusTag tone={STATUS_TONE[offer.status]} dot>
          {t(`myOffers.status.${offer.status}`)}
        </StatusTag>
      </div>

      {pay ? <p className="mt-3 text-[14px] font-semibold text-foreground">{pay}</p> : null}

      {offer.status === 'sent' && offer.expires_at ? (
        <p className="mt-2 text-[13px] text-muted-foreground">
          {t('myOffers.expires', {
            date: new Date(offer.expires_at).toLocaleDateString(locale),
          })}
        </p>
      ) : null}
      {offer.responded_at ? (
        <p className="mt-2 text-[13px] text-muted-foreground">
          {t('myOffers.respondedOn', {
            date: new Date(offer.responded_at).toLocaleDateString(locale),
          })}
        </p>
      ) : null}
      {offer.preboarding_completed_at ? (
        <p className="mt-2 text-[13px] font-medium text-[var(--ui-ok)]">
          {t('myOffers.preboardingDone')}
        </p>
      ) : null}

      {/* On an application card the action row above already opens an offer that
          is waiting on the candidate; the button here would be a second copy. */}
      {covered ? null : (
        <Button
          type="button"
          size="sm"
          onClick={() => void open()}
          disabled={opening}
          className="mt-4"
        >
          {opening ? t('myOffers.opening') : t('myOffers.openOffer')}
        </Button>
      )}
      {error ? (
        <p role="alert" className="mt-2 text-[13px] text-[var(--ui-danger)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Offers shown on their application card (the server sent an `application_id`). */
export function EmbeddedOffers({ offers }: { offers: CandidateOffer[] }): JSX.Element {
  const { i18n } = useTranslation();
  const locale = localeFor(i18n.language);
  return (
    <div className="flex flex-col gap-3">
      {offers.map((o) => (
        <OfferCard key={o.id} offer={o} locale={locale} embedded />
      ))}
    </div>
  );
}

interface YourOffersProps {
  /** Offer ids already shown on an application card; they are left out here. */
  exclude?: ReadonlySet<string>;
}

export default function YourOffers({ exclude }: YourOffersProps = {}): JSX.Element | null {
  const { t, i18n } = useTranslation();
  const errText = useCandidateError();
  const locale = localeFor(i18n.language);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['me', 'offers'],
    queryFn: listMyOffers,
    staleTime: 30 * 1000,
    retry: false,
    throwOnError: false,
  });

  const rows = (data ?? []).filter((o) => !exclude?.has(o.id));

  // Nothing to say when there is genuinely nothing and it never errored —
  // most candidates have no offer, and a permanent empty section on every
  // applications page would be noise, unlike the interviews section above it
  // (which explains itself once a candidate has any application at all).
  if (!isLoading && !isError && rows.length === 0) return null;

  return (
    <SectionCard title={t('myOffers.heading')} icon={Briefcase}>
      {isLoading ? (
        <div aria-busy="true" className="space-y-2">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-1/3" />
          <span className="sr-only">{t('myOffers.loading')}</span>
        </div>
      ) : isError ? (
        <p role="alert" className="text-[14px] text-[var(--ui-danger)]">
          {errText(error, t('myOffers.loadError'))}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((o) => (
            <OfferCard key={o.id} offer={o} locale={locale} />
          ))}
        </div>
      )}
    </SectionCard>
  );
}
