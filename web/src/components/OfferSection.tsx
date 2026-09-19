// OfferSection — PH4-A3. The candidate drawer's own view of this
// application's offer(s): every offer written against it, and a quick way to
// start one once the application has been decided as a hire (D-05: the
// decision itself is made elsewhere, never here).
//
// Full editing (compensation, terms, dates, approval, sending, withdrawal,
// documents) lives on the offer detail page — this is a launch pad, same
// relationship InterviewLoopsSection has to the interview-scheduling screens.

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

const inputCls =
  'mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

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
    <div className="mt-3 flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      <div>
        <label htmlFor={`${uid}-template`} className={labelCls}>
          Template (optional)
        </label>
        <select
          id={`${uid}-template`}
          value={templateId}
          onChange={(e) => setTemplateId(e.target.value)}
          className={inputCls}
        >
          <option value="">No template — start from scratch</option>
          {(templates.data ?? []).map((tpl) => (
            <option key={tpl.id} value={tpl.id}>
              {tpl.name}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label htmlFor={`${uid}-salary`} className={labelCls}>
            Base salary
          </label>
          <input
            id={`${uid}-salary`}
            type="number"
            min={1}
            value={baseSalary}
            onChange={(e) => setBaseSalary(e.target.value)}
            placeholder="e.g. 1200000"
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor={`${uid}-currency`} className={labelCls}>
            Currency
          </label>
          <input
            id={`${uid}-currency`}
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            maxLength={3}
            className={inputCls}
          />
        </div>
      </div>
      <p className="text-[11.5px] text-[var(--ui-faint)]">
        Job title defaults to {jobTitle ?? 'this opening'}. Everything else — dates, benefits,
        terms — is filled in on the offer itself.
      </p>
      <button
        type="button"
        disabled={!ready || createMut.isPending}
        onClick={() => createMut.mutate()}
        className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {createMut.isPending ? 'Creating…' : 'Create offer'}
      </button>
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
    <div className="mt-5">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="flex items-center gap-1.5 text-[13px] font-medium text-foreground"
        >
          <FileText size={13} aria-hidden="true" />
          Offer{rows.length > 0 ? ` (${rows.length})` : ''}
        </button>
        {canCreate ? (
          <button
            type="button"
            onClick={() => setCreating((v) => !v)}
            className="text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
          >
            {creating ? 'Close' : 'Create offer'}
          </button>
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
            <p className="mt-2 text-[12.5px] text-muted-foreground">Loading…</p>
          ) : offers.isError ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
              Could not load this application&apos;s offer.
            </p>
          ) : rows.length === 0 ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
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
                    className="flex items-center justify-between gap-2 rounded-[10px] border border-border p-3 hover:border-[var(--ui-line-strong)]"
                  >
                    <span className="min-w-0 truncate text-[12.5px] text-foreground">
                      {o.job_title}
                      {o.currency && o.base_salary
                        ? ` · ${o.currency} ${Number(o.base_salary).toLocaleString()}`
                        : ''}
                    </span>
                    <StatusTag tone={OFFER_TONE[o.status]} dot>
                      {o.status.replace('_', ' ')}
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
