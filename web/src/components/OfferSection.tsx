// OfferSection — PH4-A3. The candidate drawer's own view of this
// application's offer(s): every offer written against it, and a quick way to
// start one once the application has been decided as a hire (D-05: the
// decision itself is made elsewhere, never here).
//
// Full editing (compensation, terms, dates, approval, sending, withdrawal,
// documents) lives on the offer detail page — this is a launch pad, same
// relationship InterviewLoopsSection has to the interview-scheduling screens.

import { OFFER_STATUS_TONE } from '@/lib/offerStatus';
import { statusLabel } from '@/lib/statusLabel';
import { effectiveOfferStatus } from '@/lib/offerTrack';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { useId, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createEnrolmentOffer,
  listEnrolmentOffers,
  listOfferTemplates,
  type OfferOut,
  type OfferStatus,
} from '@/api/offers';
import { toast } from '@/lib/toast';
import { StatusTag } from '@/design/components/primitives';
import { FileText } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { formatMoney } from '@/lib/formatters';

// Same hairline-divided, 15px-titled block the candidate drawer uses for every
// section, so the offer reads as one more of them.
const SECTION = 'mt-6 border-t border-border pt-5';
const SECTION_TITLE = 'text-[15px] font-bold text-foreground';

/** States in which an application already has its one offer in play: the
 *  server allows a single live offer per application and refuses a second with
 *  a 409 (uq_offers_live_per_enrolment). */
const LIVE: readonly OfferStatus[] = [
  'draft',
  'pending_approval',
  'approved',
  'rejected',
  'sent',
  'accepted',
];

/** The server's own ceiling for base_salary, NUMERIC(14,2). */
const SALARY_MAX = 1e12;

function CreateOfferForm({ enrolmentId }: { enrolmentId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
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
    onSuccess: (offer) => {
      toast.success('Offer created as a draft');
      // Refreshes the drawer's list, the company offers list and the pipeline.
      void invalidateAfterHrMutation(qc);
      // Everything but the salary is filled in on the offer itself, so that is
      // the next stop (audit L9).
      void navigate(`/hr/offers/${offer.id}`);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not create this offer.')),
  });

  const salary = Number(baseSalary);
  const salaryOk = baseSalary.trim() !== '' && salary > 0 && salary < SALARY_MAX;
  const currencyOk = /^[A-Za-z]{3}$/.test(currency.trim());
  const ready = salaryOk && currencyOk;

  return (
    <div className="mt-3 flex flex-col gap-4 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4">
      <div>
        <FormField id={`${uid}-template`} label="Template (optional)">
          {(p) => (
            <SelectControl
              {...p}
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
            >
              <option value="">No template, start from scratch</option>
              {(templates.data ?? []).map((tpl) => (
                <option key={tpl.id} value={tpl.id}>
                  {tpl.name}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        {templates.isError ? (
          <p role="alert" className="mt-1.5 text-[12.5px] text-[var(--ui-danger)]">
            {errorMessage(templates.error, 'Could not load the offer templates.')} You can still
            start from scratch.
          </p>
        ) : null}
      </div>
      <div className="grid grid-cols-1 gap-4 min-[420px]:grid-cols-2">
        <FormField id={`${uid}-salary`} label="Base salary">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={0.01}
              step={0.01}
              value={baseSalary}
              onChange={(e) => setBaseSalary(e.target.value)}
              placeholder="e.g. 1200000"
            />
          )}
        </FormField>
        <FormField id={`${uid}-currency`} label="Currency">
          {(p) => (
            <Input
              {...p}
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
              maxLength={3}
            />
          )}
        </FormField>
      </div>
      {baseSalary.trim() !== '' && !salaryOk ? (
        <p className="text-[12.5px] text-[var(--ui-warn)]">
          The base salary must be more than zero and under 1,00,00,00,00,000.
        </p>
      ) : null}
      {currency.trim() !== '' && !currencyOk ? (
        <p className="text-[12.5px] text-[var(--ui-warn)]">
          Currency is a three-letter code, such as INR.
        </p>
      ) : null}
      <p className="text-[12.5px] text-muted-foreground">
        The job title is taken from the opening. Everything else (dates, benefits, terms) is filled
        in on the offer itself, which opens next.
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
  applicationStatus,
}: {
  enrolmentId: string;
  /** Accepted for callers but not shown: the title an offer gets is decided by
   *  the server, which prefers the opening's title to the application's, so
   *  naming this one could promise the wrong title (audit L9). */
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
  const hired = applicationStatus === 'hired';
  // One offer in play per application: the server refuses a second, so it is
  // not offered (audit OF-L8). A sent offer past its deadline is not in play.
  const inPlay = rows.some((o) => LIVE.includes(effectiveOfferStatus(o)));
  // An accepted offer is final: it can never be declined, withdrawn or expire,
  // so the note must not promise a new one after any of those.
  const accepted = rows.some((o) => effectiveOfferStatus(o) === 'accepted');
  const canCreate = hired && offers.isSuccess && !inPlay;

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
          <Button
            type="button"
            variant={creating ? 'ghost' : 'outline'}
            onClick={() => setCreating((v) => !v)}
          >
            {creating ? 'Cancel' : 'New offer'}
          </Button>
        ) : null}
      </div>

      {!expanded ? null : (
        <>
          {creating && canCreate ? <CreateOfferForm enrolmentId={enrolmentId} /> : null}

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
              {hired
                ? 'No offer yet for this application.'
                : 'An offer can be created once this application is recorded as a hire.'}
            </p>
          ) : (
            <>
              {hired && accepted ? (
                <p className="mt-2 text-[12.5px] text-muted-foreground">
                  This application already has an accepted offer, so no other offer can be written
                  for it.
                </p>
              ) : hired && inPlay ? (
                <p className="mt-2 text-[12.5px] text-muted-foreground">
                  This application already has an offer in progress. A new one can be written once
                  it is declined, withdrawn or expired.
                </p>
              ) : null}
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
                          ? ` · ${formatMoney(o.base_salary, o.currency)}`
                          : ''}
                      </span>
                      <StatusTag tone={OFFER_STATUS_TONE[effectiveOfferStatus(o)]} dot>
                        {statusLabel(effectiveOfferStatus(o), 'offer')}
                      </StatusTag>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
