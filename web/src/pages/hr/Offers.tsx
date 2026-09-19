// Offers — PH4-A3/A4. Every offer the company has written, newest activity
// first, with each accepted one's document progress — the one screen that
// answers "who is mid-preboarding, and what are they waiting on?" across
// every candidate at once.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listCompanyOffers, type OfferListFilter, type OfferStatus } from '@/api/offers';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { Loader2 } from '@/design/components/icons';

const PAGE_SIZE = 25;

const STATUS_TONE: Record<OfferStatus, TagTone> = {
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

const FILTERS: { value: OfferListFilter | ''; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'draft', label: 'Draft' },
  { value: 'pending_approval', label: 'Pending approval' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Sent back' },
  { value: 'sent', label: 'Sent' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'declined', label: 'Declined' },
  { value: 'expired', label: 'Expired' },
  { value: 'withdrawn', label: 'Withdrawn' },
  { value: 'preboarding', label: 'In preboarding' },
];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : '—';
}

export default function Offers() {
  const [status, setStatus] = useState<OfferListFilter | ''>('');
  const [offset, setOffset] = useState(0);

  const list = useQuery({
    queryKey: ['hr', 'offers', status, offset],
    queryFn: () => listCompanyOffers({ status: status || null, limit: PAGE_SIZE, offset }),
  });

  const rows = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  return (
    <div className="mx-auto w-full max-w-[980px] px-4 py-8">
      <Reveal>
        <h1 className="text-[24px] font-semibold tracking-[-0.6px] text-foreground">Offers</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Every offer written for this company, and how far each one has got.
        </p>
      </Reveal>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <label htmlFor="offers-status" className="text-[12.5px] font-medium text-[var(--ui-soft)]">
          Status
        </label>
        <select
          id="offers-status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as OfferListFilter | '');
            setOffset(0);
          }}
          className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
        >
          {FILTERS.map((f) => (
            <option key={f.value || 'all'} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>

      {list.isLoading ? (
        <p className="mt-6 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      ) : list.isError ? (
        <p className="mt-6 text-[13px] text-[var(--ui-danger)]">
          {errText(list.error, 'Could not load offers.')}
        </p>
      ) : rows.length === 0 ? (
        <GlassCard className="mt-6 p-6 text-center">
          <p className="text-[13.5px] text-foreground">No offers here.</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            Create one from a candidate&apos;s drawer once their application is a hire.
          </p>
        </GlassCard>
      ) : (
        <div className="mt-6 flex flex-col gap-2.5">
          {rows.map((o) => (
            <Link
              key={o.id}
              to={`/hr/offers/${o.id}`}
              className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-border bg-card p-4 hover:border-[var(--ui-line-strong)]"
            >
              <div className="min-w-0">
                <p className="truncate text-[14px] font-medium text-foreground">
                  {o.candidate_name}
                </p>
                <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                  {o.job_title}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted-foreground">
                <span>Expires {fmtDate(o.expires_at)}</span>
                {o.documents.mandatory_total > 0 ? (
                  <span>
                    Documents {o.documents.mandatory_verified}/{o.documents.mandatory_total}
                    {o.documents.awaiting_review > 0
                      ? ` · ${o.documents.awaiting_review} awaiting review`
                      : ''}
                  </span>
                ) : null}
                <StatusTag tone={STATUS_TONE[o.status]} dot>
                  {o.status.replace('_', ' ')}
                </StatusTag>
              </div>
            </Link>
          ))}
        </div>
      )}

      {total > PAGE_SIZE ? (
        <div className="mt-5 flex items-center justify-between text-[12.5px] text-muted-foreground">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            className="rounded-[9px] border border-border px-3 py-1.5 disabled:opacity-40"
          >
            Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            type="button"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            className="rounded-[9px] border border-border px-3 py-1.5 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      ) : null}
    </div>
  );
}
