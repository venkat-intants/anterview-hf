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
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Briefcase } from '@/design/components/icons';

function localeFor(lang: string): string {
  if (lang.startsWith('hi')) return 'hi-IN';
  if (lang.startsWith('te')) return 'te-IN';
  return 'en-IN';
}

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
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

function OfferCard({ offer, locale }: { offer: CandidateOffer; locale: string }) {
  const { t } = useTranslation();
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

  return (
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
            <Briefcase size={14} className="shrink-0 text-muted-foreground" aria-hidden="true" />
            {offer.job_title}
          </h3>
          {offer.company ? (
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">{offer.company}</p>
          ) : null}
        </div>
        <StatusTag tone={STATUS_TONE[offer.status]} dot>
          {t(`myOffers.status.${offer.status}`)}
        </StatusTag>
      </div>

      {pay ? <p className="mt-3 text-[13.5px] text-foreground">{pay}</p> : null}

      {offer.status === 'sent' && offer.expires_at ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          {t('myOffers.expires', {
            date: new Date(offer.expires_at).toLocaleDateString(locale),
          })}
        </p>
      ) : null}
      {offer.responded_at ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          {t('myOffers.respondedOn', {
            date: new Date(offer.responded_at).toLocaleDateString(locale),
          })}
        </p>
      ) : null}
      {offer.preboarding_completed_at ? (
        <p className="mt-2 text-[12.5px] text-[var(--ui-ok)]">{t('myOffers.preboardingDone')}</p>
      ) : null}

      <button
        type="button"
        onClick={() => void open()}
        disabled={opening}
        className="mt-4 inline-flex items-center gap-1.5 rounded-[8px] bg-primary px-3.5 py-1.5 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {opening ? t('myOffers.opening') : t('myOffers.openOffer')}
      </button>
      {error ? <p className="mt-2 text-[12.5px] text-[var(--ui-danger)]">{error}</p> : null}
    </GlassCard>
  );
}

export default function YourOffers(): JSX.Element | null {
  const { t, i18n } = useTranslation();
  const locale = localeFor(i18n.language);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['me', 'offers'],
    queryFn: listMyOffers,
    staleTime: 30 * 1000,
    retry: false,
    throwOnError: false,
  });

  const rows = data ?? [];

  // Nothing to say when there is genuinely nothing and it never errored —
  // most candidates have no offer, and a permanent empty section on every
  // applications page would be noise, unlike the interviews section above it
  // (which explains itself once a candidate has any application at all).
  if (!isLoading && !isError && rows.length === 0) return null;

  return (
    <section aria-labelledby="your-offers-heading" className="mt-6">
      <h2 id="your-offers-heading" className="text-[18px] font-semibold text-foreground">
        {t('myOffers.heading')}
      </h2>

      {isLoading ? (
        <p className="mt-2 text-[13px] text-muted-foreground">{t('myOffers.loading')}</p>
      ) : isError ? (
        <p className="mt-2 text-[13px] text-[var(--ui-danger)]">
          {errText(error, t('myOffers.loadError'))}
        </p>
      ) : (
        <div className="mt-3 flex flex-col gap-3">
          {rows.map((o) => (
            <OfferCard key={o.id} offer={o} locale={locale} />
          ))}
        </div>
      )}
    </section>
  );
}
