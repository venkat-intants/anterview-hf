// OfferSection — PH4-A3. The candidate drawer's own view of this
// application's offer(s): every offer written against it, and a quick way to
// start one once the application has been decided as a hire (D-05: the
// decision itself is made elsewhere, never here).
//
// Full editing (compensation, terms, dates, approval, sending, withdrawal,
// documents) lives on the offer detail page — this is a launch pad, same
// relationship InterviewLoopsSection has to the interview-scheduling screens.

import { offerStatusWord } from '@/lib/offerStatus';
import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createEnrolmentOffer,
  listEnrolmentOffers,
  listOfferTemplates,
  type OfferOut,
  type OfferStatus,
} from '@/api/offers';
import { toast } from '@/lib/toast';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { FileText } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

// Same hairline-divided, 15px-titled block the candidate drawer uses for every
// section, so the offer reads as one more of them.
const SECTION = 'mt-6 border-t border-border pt-5';
const SECTION_TITLE = 'text-[15px] font-bold text-foreground';
const labelCls = 'text-[13px] font-semibold text-foreground';
const selectCls =
  'mt-1.5 h-11 w-full rounded-[12px] border border-input bg-card px-3.5 text-[14px] text-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

const OFFER_TONE: Record<OfferStatus, TagTone> = {
  draft: 'neutral',
  pending_approval: 'amber',
  approved: 'electric',
  rejected: 'ember',
  sent: 'electric',
  accepted: 'forest',
  declined: 'ember',
  expired: 'neutral',
  withdrawn: 'neutral',
};

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function CreateOfferForm({
  enrolmentId,
  jobTitle,
  onDone,
}: {
  enrolmentId: string;
  jobTitle: string | undefined;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const templates = useQuery({ queryKey: ['hr', 'offer-templates'], queryFn: listOfferTemplates });
  const [templateId, setTemplateId] = useState('');
  const [baseSalary, setBaseSalary] = useState('');
  const [currency, setCurrency] = useState('INR');

  const createMut = useMutation({
    mutationFn: () =>
      createEnrolmentOffer(enrolmentId, {
        template_id: templateId || null,
        base_salary: baseSalary,
        currency,
      }),
    onSuccess: () => {
      toast.success('Offer created as a draft');
      void qc.invalidateQueries({ queryKey: ['hr', 'enrolment', enrolmentId, 'offers'] });
      onDone();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create this offer')),
  });

  const ready = Number(baseSalary) > 0 && currency.trim().length === 3;

  return (
    <div className="mt-3 flex flex-col gap-4 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4">
      <div>
        <label htmlFor={`${uid}-template`} className={labelCls}>
          Template (optional)
        </label>
        <select
          id={`${uid}-template`}
          value={templateId}
          onChange={(e) => {
            // The form always sends a currency, and what it sends wins over the
            // template's — so take the template's the moment it is chosen, or a
            // USD template would quietly become an INR offer.
            const id = e.target.value;
            setTemplateId(id);
            const tpl = (templates.data ?? []).find((x) => x.id === id);
            if (tpl?.currency) setCurrency(tpl.currency);
          }}
          className={selectCls}
        >
          <option value="">No template — start from scratch</option>
          {(templates.data ?? []).map((tpl) => (
            <option key={tpl.id} value={tpl.id}>
              {tpl.name}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-1 gap-4 min-[420px]:grid-cols-2">
        <div>
          <label htmlFor={`${uid}-salary`} className={labelCls}>
            Base salary
          </label>
          <Input
            id={`${uid}-salary`}
            type="number"
            min={1}
            value={baseSalary}
            onChange={(e) => setBaseSalary(e.target.value)}
            placeholder="e.g. 1200000"
            className="mt-1.5"
          />
        </div>
        <div>
          <label htmlFor={`${uid}-currency`} className={labelCls}>
            Currency
          </label>
          <Input
            id={`${uid}-currency`}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            maxLength={3}
            className="mt-1.5"
          />
        </div>
      </div>
      <p className="text-[12.5px] text-muted-foreground">
        Job title defaults to {jobTitle ?? 'this opening'}. Everything else — dates, benefits, terms
        — is filled in on the offer itself.
      </p>
      <Button
        type="button"
        disabled={!ready || createMut.isPending}
        onClick={() => createMut.mutate()}
        className="self-start"
      >
        {createMut.isPending ? 'Creating…' : 'Create offer'}
      </Button>
    </div>
  );
}

export default function OfferSection({
  enrolmentId,
  jobTitle,
  applicationStatus,
}: {
  enrolmentId: string;
  jobTitle?: string;
  /** Only a hired application may start an offer (D-05: the decision itself
   *  is made elsewhere). Undefined hides the "create" control, not the list. */
  applicationStatus?: string;
}) {
  const [expanded, setExpanded] = useState(true);
  const [creating, setCreating] = useState(false);

  const offers = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'offers'],
    queryFn: () => listEnrolmentOffers(enrolmentId),
  });

  const rows = offers.data ?? [];
  const canCreate = applicationStatus === 'hired';

  return (
    <div className={SECTION}>
      <div className="flex items-center justify-between gap-2">
        <h3 className={SECTION_TITLE}>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="-ml-2 flex min-h-10 items-center gap-1.5 rounded-full px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <FileText size={14} aria-hidden="true" />
            Offer{rows.length > 0 ? ` (${rows.length})` : ''}
          </button>
        </h3>
        {canCreate ? (
          <Button type="button" variant="outline" onClick={() => setCreating((v) => !v)}>
            {creating ? 'Close' : 'Create offer'}
          </Button>
        ) : null}
      </div>

      {!expanded ? null : (
        <>
          {creating ? (
            <CreateOfferForm
              enrolmentId={enrolmentId}
              jobTitle={jobTitle}
              onDone={() => setCreating(false)}
            />
          ) : null}

          {offers.isLoading ? (
            <div className="mt-2 flex flex-col gap-2" aria-busy="true">
              <Skeleton className="h-12 rounded-[12px]" />
              <Skeleton className="h-12 rounded-[12px]" />
            </div>
          ) : offers.isError ? (
            <div role="alert" className="mt-2 flex flex-wrap items-center gap-3">
              <p className="text-[13px] text-muted-foreground">
                Could not load this application&apos;s offer.
              </p>
              <Button type="button" variant="outline" onClick={() => void offers.refetch()}>
                Try again
              </Button>
            </div>
          ) : rows.length === 0 ? (
            <p className="mt-2 text-[13px] text-muted-foreground">
              {canCreate
                ? 'No offer yet for this application.'
                : 'An offer can be created once this application is recorded as a hire.'}
            </p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {rows.map((o: OfferOut) => (
                <li key={o.id}>
                  <Link
                    to={`/hr/offers/${o.id}`}
                    className="flex min-h-12 items-center justify-between gap-3 rounded-[16px] border border-border p-3.5 hover:border-[var(--ui-line-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  >
                    <span className="min-w-0 truncate text-[13px] text-foreground">
                      {o.job_title}
                      {o.currency && o.base_salary
                        ? ` · ${o.currency} ${Number(o.base_salary).toLocaleString()}`
                        : ''}
                    </span>
                    <StatusTag tone={OFFER_TONE[o.status]} dot>
                      {offerStatusWord(o.status)}
                    </StatusTag>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
