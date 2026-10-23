// ExceptionsSection — PH4-O1. What is blocking one application from moving
// through its stage normally, for a person to see and act on.
//
// Raising, resolving or reassigning an exception NEVER changes the
// candidate's status or round — that is the whole point of an exception
// rather than a status change, and it is said here in copy, not just in the
// endpoint's own comment, because a list of "blocked" items reads as a
// verdict if nothing says otherwise (D-05 in spirit, even though exceptions
// are not a hiring decision at all).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { AlertCircle } from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { QueryState } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';
import { formatDate } from '@/lib/formatters';
import { listStageOwners } from '@/api/stageSla';
import {
  listExceptions,
  raiseException,
  reassignException,
  resolveException,
  type StageException,
} from '@/api/stageSla';

const REASON_MIN = 10;

/** Open first, newest first within each group — the server already orders by
 *  raised_at DESC, so a stable sort on status alone keeps that. */
/** Everything that shows an exception or its count: this list, the decision
 *  queue's per-row badge and the HR console's at-risk widget. */
function refreshExceptionViews(qc: QueryClient, enrolmentId: string): void {
  void qc.invalidateQueries({ queryKey: ['hr', 'enrolment', enrolmentId, 'exceptions'] });
  void qc.invalidateQueries({ queryKey: ['hr', 'decision-queue'] });
  void qc.invalidateQueries({ queryKey: ['hr', 'stage-sla'] });
}

function openFirst(rows: StageException[]): StageException[] {
  return [...rows].sort((a, b) => {
    if (a.status === b.status) return 0;
    return a.status === 'open' ? -1 : 1;
  });
}

function RaiseForm({ enrolmentId, onRaised }: { enrolmentId: string; onRaised: () => void }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState('');
  const [ownerId, setOwnerId] = useState('');
  const owners = useQuery({
    queryKey: ['hr', 'stage-owners'],
    queryFn: listStageOwners,
    staleTime: 5 * 60_000,
  });

  const raiseMut = useMutation({
    mutationFn: () => raiseException(enrolmentId, { reason, owner_user_id: ownerId || null }),
    onSuccess: () => {
      toast.success('Exception raised');
      setReason('');
      setOwnerId('');
      refreshExceptionViews(qc, enrolmentId);
      onRaised();
    },
    onError: (e) => toast.error(errorMessage(e, 'Could not raise this exception.')),
  });

  const ready = reason.trim().length >= REASON_MIN;

  return (
    <form
      className="flex flex-col gap-2 rounded-[10px] border border-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) raiseMut.mutate();
      }}
    >
      <label htmlFor="exception-reason" className="text-[12px] font-medium text-[var(--ui-soft)]">
        What is blocking this application?
      </label>
      <Textarea
        id="exception-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={2}
        placeholder={`At least ${REASON_MIN} characters`}
      />
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="exception-owner" className="text-[12px] text-[var(--ui-soft)]">
          Owner
        </label>
        <SelectControl
          id="exception-owner"
          size="sm"
          className="w-auto"
          value={ownerId}
          onChange={(e) => setOwnerId(e.target.value)}
        >
          <option value="">You</option>
          {(owners.data ?? []).map((o) => (
            <option key={o.user_id} value={o.user_id}>
              {o.full_name}
            </option>
          ))}
        </SelectControl>
        <Button type="submit" disabled={!ready || raiseMut.isPending} className="ml-auto">
          {raiseMut.isPending ? 'Raising…' : 'Raise exception'}
        </Button>
      </div>
      {!ready && reason.length > 0 ? (
        <p className="text-[12px] text-[var(--ui-warn)]">Write at least {REASON_MIN} characters.</p>
      ) : null}
    </form>
  );
}

function ExceptionRow({ exc }: { exc: StageException }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const owners = useQuery({
    queryKey: ['hr', 'stage-owners'],
    queryFn: listStageOwners,
    staleTime: 5 * 60_000,
  });

  const invalidate = () => refreshExceptionViews(qc, exc.enrolment_id);

  const resolveMut = useMutation({
    mutationFn: () => resolveException(exc.exception_id, note),
    onSuccess: () => {
      toast.success('Exception resolved');
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e, 'Could not resolve this exception.')),
  });

  const reassignMut = useMutation({
    mutationFn: (ownerUserId: string) => reassignException(exc.exception_id, ownerUserId),
    onSuccess: () => {
      toast.success('Reassigned');
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e, 'Could not reassign this exception.')),
  });

  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusTag tone={exc.status === 'open' ? 'amber' : 'neutral'} dot>
          {exc.status === 'open' ? 'Open' : 'Resolved'}
        </StatusTag>
        <span className="text-[12px] text-muted-foreground">{exc.stage}</span>
      </div>
      <p className="mt-1.5 text-[12.5px] text-foreground">{exc.reason}</p>
      <p className="mt-1 text-[12px] text-muted-foreground">
        Raised by {exc.raised_by_name ?? 'someone no longer with the company'} on{' '}
        {formatDate(exc.raised_at)}
        {exc.owner_name ? ` · owner: ${exc.owner_name}` : ''}
      </p>
      {exc.status === 'resolved' ? (
        <p className="mt-1 text-[12px] text-muted-foreground">
          Resolved by {exc.resolved_by_name ?? 'someone no longer with the company'}
          {exc.resolved_at ? ` on ${formatDate(exc.resolved_at)}` : ''}
          {exc.resolution_note ? `: ${exc.resolution_note}` : ''}
        </p>
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <label
              htmlFor={`reassign-${exc.exception_id}`}
              className="text-[12px] text-[var(--ui-soft)]"
            >
              Reassign to
            </label>
            <SelectControl
              id={`reassign-${exc.exception_id}`}
              size="sm"
              className="w-auto"
              value={exc.owner_user_id ?? ''}
              onChange={(e) => {
                if (e.target.value) reassignMut.mutate(e.target.value);
              }}
              disabled={reassignMut.isPending}
            >
              <option value="">Unassigned</option>
              {(owners.data ?? []).map((o) => (
                <option key={o.user_id} value={o.user_id}>
                  {o.full_name}
                </option>
              ))}
            </SelectControl>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="text"
              size="sm"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Resolution note (optional)"
              aria-label={`Resolution note for ${exc.stage} exception`}
              className="w-auto min-w-0 flex-1"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => resolveMut.mutate()}
              disabled={resolveMut.isPending}
            >
              {resolveMut.isPending ? 'Resolving…' : 'Resolve'}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function ExceptionsSection({ enrolmentId }: { enrolmentId: string }): JSX.Element {
  const [raising, setRaising] = useState(false);
  const exceptions = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'exceptions'],
    queryFn: () => listExceptions(enrolmentId),
  });

  const rows = openFirst(exceptions.data ?? []);
  const openCount = rows.filter((r) => r.status === 'open').length;

  return (
    <div className="mt-6 border-t border-border pt-5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[15px] font-bold text-foreground">
          <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Exceptions{openCount > 0 ? ` (${openCount} open)` : ''}
        </h3>
        <Button type="button" variant="outline" size="sm" onClick={() => setRaising((v) => !v)}>
          {raising ? 'Cancel' : 'Raise exception'}
        </Button>
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
        An exception records what is blocking this application for a person to act on. It never
        changes the candidate&rsquo;s status.
      </p>

      {raising ? (
        <div className="mt-2">
          <RaiseForm enrolmentId={enrolmentId} onRaised={() => setRaising(false)} />
        </div>
      ) : null}

      <div className="mt-2">
        <QueryState
          query={exceptions}
          errorTitle="Could not load exceptions."
          loading={
            <div className="flex flex-col gap-2" aria-busy="true">
              <span className="sr-only">Loading…</span>
              <Skeleton className="h-14" />
            </div>
          }
          isEmpty={(d) => d.length === 0}
          empty={
            <p className="text-[12.5px] text-muted-foreground">
              No exceptions raised for this application.
            </p>
          }
        >
          {() => (
            <ul className="flex flex-col gap-2">
              {rows.map((exc) => (
                <ExceptionRow key={exc.exception_id} exc={exc} />
              ))}
            </ul>
          )}
        </QueryState>
      </div>
    </div>
  );
}
