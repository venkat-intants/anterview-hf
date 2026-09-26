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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listMyRediscovery,
  rediscoveryErrorMessage,
  setMyRediscovery,
  type MyRediscoveryCompany,
  type MyRediscoveryResponse,
} from '@/api/rediscovery';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Search } from '@/design/components/icons';

function localeFor(lang: string): string {
  if (lang.startsWith('hi')) return 'hi-IN';
  if (lang.startsWith('te')) return 'te-IN';
  return 'en-IN';
}

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
    <div className="rounded-[10px] border border-border p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13.5px] font-medium text-foreground">
            {t('rediscovery.toggleLabel', { company: company.company_name })}
          </p>
          <p className="mt-1 max-w-[52ch] text-[12px] leading-relaxed text-muted-foreground">
            {t('rediscovery.notice', { company: company.company_name, months: consentMonths })}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <StatusTag tone={company.state === 'on' ? 'forest' : 'neutral'} dot>
            {company.state === 'on'
              ? company.expires_at
                ? t('rediscovery.onUntil', {
                    date: new Date(company.expires_at).toLocaleDateString(locale),
                  })
                : t('rediscovery.on')
              : t('rediscovery.off')}
          </StatusTag>
          {company.state === 'on' ? (
            <button
              type="button"
              onClick={() => setConfirmingOff(true)}
              className="rounded-[8px] border border-border px-3 py-1.5 text-[12.5px] text-foreground"
            >
              {t('rediscovery.turnOff')}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => mut.mutate(true)}
              disabled={mut.isPending}
              className="rounded-[8px] bg-primary px-3 py-1.5 text-[12.5px] font-semibold text-primary-foreground disabled:opacity-60"
            >
              {mut.isPending ? '…' : t('rediscovery.turnOn')}
            </button>
          )}
        </div>
      </div>

      {confirmingOff ? (
        <div
          role="group"
          aria-label={t('rediscovery.turnOffConfirmTitle')}
          className="mt-3 rounded-[10px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger)]/[0.06] p-3"
        >
          <p className="text-[12.5px] font-medium text-foreground">
            {t('rediscovery.turnOffConfirmTitle')}
          </p>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            {t('rediscovery.turnOffConfirmDesc', { company: company.company_name })}
          </p>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => mut.mutate(false)}
              disabled={mut.isPending}
              className="rounded-[8px] bg-[#e6714f]/20 px-3 py-1.5 text-[12px] font-semibold text-[#ff8a66] disabled:opacity-50"
            >
              {mut.isPending ? '…' : t('rediscovery.turnOffConfirmYes')}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingOff(false)}
              className="rounded-[8px] bg-[var(--ui-inset)] px-3 py-1.5 text-[12px] text-[var(--ui-soft)]"
            >
              {t('rediscovery.cancel')}
            </button>
          </div>
        </div>
      ) : null}

      {err ? <p className="mt-2 text-[12px] text-[var(--ui-danger)]">{err}</p> : null}
    </div>
  );
}

export default function YourRediscovery(): JSX.Element | null {
  const { t, i18n } = useTranslation();
  const locale = localeFor(i18n.language);

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
    <section aria-labelledby="your-rediscovery-heading" className="mt-6">
      <GlassCard className="p-5">
        <h2
          id="your-rediscovery-heading"
          className="flex items-center gap-2 text-[15px] font-semibold text-foreground"
        >
          <Search size={15} className="shrink-0 text-muted-foreground" aria-hidden="true" />
          {t('rediscovery.heading')}
        </h2>

        {isLoading ? (
          <p className="mt-2 text-[13px] text-muted-foreground">{t('rediscovery.loading')}</p>
        ) : isError ? (
          <p className="mt-2 text-[13px] text-[var(--ui-danger)]">
            {rediscoveryErrorMessage(error, t('rediscovery.loadError'))}
          </p>
        ) : (
          <div className="mt-3 flex flex-col gap-2.5">
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
      </GlassCard>
    </section>
  );
}
