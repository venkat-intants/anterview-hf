// Offers — PH4-A3/A4. Every offer the company has written, newest activity
// first, with each accepted one's document progress — the one screen that
// answers "who is mid-preboarding, and what are they waiting on?" across
// every candidate at once.

import { OFFER_STATUS_TONE } from '@/lib/offerStatus';
import { statusLabel } from '@/lib/statusLabel';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listCompanyOffers, type OfferListFilter } from '@/api/offers';
import { StatusTag } from '@/design/components/primitives';
import { Page, PageHeader, EmptyState, Toolbar } from '@/design/components/page';
import { effectiveOfferStatus, isLapsed, offerNeeds } from '@/lib/offerTrack';
import { QueryErrorPanel } from '@/components/QueryState';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { FileText } from '@/design/components/icons';
import { formatDate, plural } from '@/lib/formatters';

const PAGE_SIZE = 25;

const TH =
  'sticky top-0 z-[1] h-auto whitespace-nowrap border-b border-border bg-card px-5 py-3 text-[12px] font-semibold uppercase tracking-[0.5px] text-muted-foreground';
const TD = 'border-b border-border px-5 py-3 align-middle group-last:border-0';

const FILTERS: { value: OfferListFilter | ''; label: string }[] = [
  { value: '', label: 'All' },
  // The same words the status pills use, so a filter and a pill never disagree.
  ...(
    [
      'draft',
      'pending_approval',
      'approved',
      'rejected',
      'sent',
      'accepted',
      'declined',
      'expired',
      'withdrawn',
    ] as const
  ).map((value) => ({ value, label: statusLabel(value, 'offer') })),
  { value: 'preboarding', label: 'In preboarding' },
];

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
    <Page width="default">
      <PageHeader
        title="Offers"
        description="Every offer written for this company, and how far each one has got."
      />

      <Toolbar
        trailing={
          list.data ? (
            <span className="text-[13px] tabular-nums text-muted-foreground" role="status">
              {plural(total, 'offer')}
            </span>
          ) : null
        }
      >
        <label htmlFor="offers-status" className="text-[13px] font-medium text-[var(--ui-soft)]">
          Status
        </label>
        <SelectControl
          id="offers-status"
          className="w-auto"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as OfferListFilter | '');
            setOffset(0);
          }}
        >
          {FILTERS.map((f) => (
            <option key={f.value || 'all'} value={f.value}>
              {f.label}
            </option>
          ))}
        </SelectControl>
      </Toolbar>

      {list.isLoading ? (
        <div className="flex flex-col gap-2.5" role="status" aria-label="Loading offers">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[56px] w-full rounded-card" />
          ))}
        </div>
      ) : list.isError ? (
        <QueryErrorPanel
          title="Could not load offers."
          error={list.error}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No offers here."
          description="Create one from a candidate's drawer once their application is a hire."
        />
      ) : (
        // Table's own wrapper is the scroll box (both axes), so the 70vh cap goes
        // on it: the sticky header then sticks inside the box that scrolls.
        <div className="overflow-hidden rounded-card border border-border bg-card shadow-card [&>div]:max-h-[70vh]">
          <Table className="min-w-[720px] border-collapse text-left">
            <TableCaption className="sr-only">Offers</TableCaption>
            <TableHeader className="[&_tr]:border-0">
              <TableRow className="hover:bg-transparent">
                <TableHead scope="col" className={TH}>
                  Candidate
                </TableHead>
                <TableHead scope="col" className={TH}>
                  Role
                </TableHead>
                <TableHead scope="col" className={TH}>
                  Progress
                </TableHead>
                <TableHead scope="col" className={TH}>
                  Status
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((o) => {
                // A row loaded before the deadline passed still says "sent";
                // it is read as expired from then on (audit OF-F4).
                const status = effectiveOfferStatus(o);
                const needs = offerNeeds(o);
                return (
                  <TableRow
                    key={o.id}
                    className="group border-0 transition-colors hover:bg-[var(--ui-inset-soft)]"
                  >
                    <TableCell className={TD}>
                      <Link
                        to={`/hr/offers/${o.id}`}
                        className="rounded-[6px] text-[14px] font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                      >
                        {o.candidate_name}
                      </Link>
                    </TableCell>
                    <TableCell className={cn(TD, 'text-[13px] text-muted-foreground')}>
                      {o.job_title}
                    </TableCell>
                    <TableCell className={cn(TD, 'text-[12.5px] text-muted-foreground')}>
                      <div className="flex flex-col gap-0.5">
                        {o.status === 'sent' && o.expires_at ? (
                          <span>
                            {isLapsed(o) ? 'Expired' : 'Expires'} {formatDate(o.expires_at)}
                          </span>
                        ) : null}
                        {o.preboarding_completed_at ? <span>Preboarding complete</span> : null}
                        {o.documents.mandatory_total > 0 ? (
                          <span className="tabular-nums">
                            Documents {o.documents.mandatory_verified}/{o.documents.mandatory_total}
                            {o.documents.awaiting_review > 0
                              ? ` · ${o.documents.awaiting_review} awaiting review`
                              : ''}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className={TD}>
                      <StatusTag tone={OFFER_STATUS_TONE[status]} dot>
                        {statusLabel(status, 'offer')}
                      </StatusTag>
                      <span
                        data-testid="offer-needs"
                        className={cn(
                          'mt-1 block text-[12.5px]',
                          needs.mine ? 'font-medium text-foreground' : 'text-muted-foreground',
                        )}
                      >
                        {needs.text}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {total > PAGE_SIZE ? (
        <div className="flex items-center justify-between text-[12.5px] text-muted-foreground">
          <Button
            type="button"
            variant="outline"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
          >
            Previous
          </Button>
          <span>
            {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <Button
            type="button"
            variant="outline"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      ) : null}
    </Page>
  );
}
