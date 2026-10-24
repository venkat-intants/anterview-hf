// YourRediscovery — PH5-E3 (D5-1). A candidate's own rediscovery opt-in, one
// row per company they have applied to. This is the "My applications" side of
// the pair described in the design: the other door is the SECOND, independent
// checkbox on the public apply form (`PublicApply.tsx`), and the two must
// never be confused with each other or with the application consent itself.
//
// Candidate-facing: every string goes through i18n (EN/HI/TE per CLAUDE.md).
// These are CONSENT keys — see `lib/i18n.ts`'s header note: HI/TE want a
// targeted native-speaker and legal pass before this is candidate-facing in
// production, and ship unreviewed now.
//
// TURNING OFF IS A TWO-STEP CONFIRM, on the `offer.withdraw*` /
// `task.withdrawConsent*` precedent — this withdraws a DPDP consent, not a
// preference toggle, so a single misclick must not do it.
//
// `PUT /users/me/rediscovery/{company_id}` always succeeds for the signed-in
// owner, even after a prior withdrawal (the "not re-granted after
// withdrawal" refusal is a public-apply-form-only rule — see api/rediscovery.ts).

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDate, uiLocale } from '@/lib/formatters';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listMyRediscovery,
  rediscoveryErrorMessage,
  setMyRediscovery,
  type MyRediscoveryCompany,
  type MyRediscoveryResponse,
} from '@/api/rediscovery';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Section } from '@/design/components/rows';
import { StatusTag } from '@/design/components/primitives';

function CompanyRow({
  company,
  consentMonths,
  locale,
}: {
  company: MyRediscoveryCompany;
  consentMonths: number;
  locale: string;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [confirmingOff, setConfirmingOff] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (on: boolean) => setMyRediscovery(company.company_id, on),
    onSuccess: (updated) => {
      setConfirmingOff(false);
      // Patch the cached list in place rather than refetching — the response
      // to the write IS the single row's new state.
      qc.setQueryData<MyRediscoveryResponse | undefined>(['me', 'rediscovery'], (prev) =>
        prev
          ? {
              ...prev,
              companies: prev.companies.map((c) =>
                c.company_id === updated.company_id ? updated : c,
              ),
            }
          : prev,
      );
    },
    onError: (e: unknown) => {
      // Surfaced inline, not a toast: this is a consent decision, and the
      // reason it failed belongs next to the control someone just used.
      setConfirmingOff(false);
      setErr(rediscoveryErrorMessage(e, t('rediscovery.updateError')));
    },
  });

  return (
    <div className="rounded-[14px] bg-[var(--ui-inset-soft)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[14px] font-semibold text-foreground">
            {t('rediscovery.toggleLabel', { company: company.company_name })}
          </p>
          <p className="mt-1 max-w-[52ch] text-[13px] leading-relaxed text-muted-foreground">
            {t('rediscovery.notice', { company: company.company_name, months: consentMonths })}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <StatusTag tone={company.state === 'on' ? 'forest' : 'neutral'} dot>
            {company.state === 'on'
              ? company.expires_at
                ? t('rediscovery.onUntil', {
                    date: formatDate(company.expires_at, locale),
                  })
                : t('rediscovery.on')
              : t('rediscovery.off')}
          </StatusTag>
          {company.state === 'on' ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setConfirmingOff(true)}
            >
              {t('rediscovery.turnOff')}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={() => mut.mutate(true)}
              disabled={mut.isPending}
            >
              {mut.isPending ? '…' : t('rediscovery.turnOn')}
            </Button>
          )}
        </div>
      </div>

      {confirmingOff ? (
        <div
          role="group"
          aria-label={t('rediscovery.turnOffConfirmTitle')}
          className="mt-3 rounded-[14px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-4"
        >
          <p className="text-[14px] font-semibold text-foreground">
            {t('rediscovery.turnOffConfirmTitle')}
          </p>
          <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
            {t('rediscovery.turnOffConfirmDesc', { company: company.company_name })}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => mut.mutate(false)}
              disabled={mut.isPending}
            >
              {mut.isPending ? '…' : t('rediscovery.turnOffConfirmYes')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setConfirmingOff(false)}
            >
              {t('rediscovery.cancel')}
            </Button>
          </div>
        </div>
      ) : null}

      {err ? (
        <p role="alert" className="mt-2 text-[13px] text-[var(--ui-danger)]">
          {err}
        </p>
      ) : null}
    </div>
  );
}

export default function YourRediscovery(): JSX.Element | null {
  const { t, i18n } = useTranslation();
  const locale = uiLocale(i18n.language);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['me', 'rediscovery'],
    queryFn: listMyRediscovery,
    staleTime: 30 * 1000,
    retry: false,
    throwOnError: false,
  });

  const companies = data?.companies ?? [];

  // Nothing to say when there is genuinely nothing to opt into — a candidate
  // who has applied nowhere (or nowhere that could feed this list) sees no
  // permanent empty card, matching YourOffers.tsx's own rule.
  if (!isLoading && !isError && companies.length === 0) return null;

  return (
    // The last block on /applications: a consent setting, not a status, so it
    // sits below every application and is titled for what it is.
    <Section title={t('track.privacy')} data-testid="rediscovery-section">
      {isLoading ? (
        <div aria-busy="true" className="space-y-2">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-1/3" />
          <span className="sr-only">{t('rediscovery.loading')}</span>
        </div>
      ) : isError ? (
        <p role="alert" className="text-[14px] text-[var(--ui-danger)]">
          {rediscoveryErrorMessage(error, t('rediscovery.loadError'))}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {companies.map((c) => (
            <CompanyRow
              key={c.company_id}
              company={c}
              consentMonths={data?.consent_months ?? 12}
              locale={locale}
            />
          ))}
        </div>
      )}
    </Section>
  );
}
