// MyAvailability — PH4-A2/O5. The signed-in interviewer's own availability
// windows: what HR schedules against when they add a session, and what
// panel workload checks "outside availability" against. English-only by
// design, same as the console it lives on (CLAUDE.md).

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { GlassCard } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { addMyAvailability, getMyAvailability, removeMyAvailability } from '@/api/scheduling';
import { localInputToIso } from '@/lib/localDatetime';
import { formatDayTime, browserTimezone } from '@/lib/timezone';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';

/** The furthest ahead the list can show: the server's maximum range (92 days). */
const HORIZON_DAYS = 92;
/** The server's limit on one window (interview_scheduling.add_availability). */
const MAX_WINDOW_DAYS = 14;
const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Why this window cannot be added, or null when it can (audit AD-F11).
 *
 * The 14-day limit is the server's, said up front instead of learned from a
 * 422. The horizon is this list's: a window further out than it can show used
 * to save, toast "Availability added" and then never appear, so it could not be
 * seen or removed.
 */
function windowProblem(startIso: string, endIso: string, now: number): string | null {
  const start = Date.parse(startIso);
  const end = Date.parse(endIso);
  if (Number.isNaN(start) || Number.isNaN(end)) return 'Enter both times.';
  if (end <= start) return 'The end must be after the start.';
  if (end - start > MAX_WINDOW_DAYS * DAY_MS) {
    return `A window can be at most ${MAX_WINDOW_DAYS} days. Add more than one for longer.`;
  }
  if (start > now + HORIZON_DAYS * DAY_MS) {
    return `You can add windows up to ${HORIZON_DAYS} days ahead.`;
  }
  return null;
}

export default function MyAvailability(): JSX.Element {
  const qc = useQueryClient();
  const [startLocal, setStartLocal] = useState('');
  const [endLocal, setEndLocal] = useState('');

  // The whole range the server allows, so every window that can be added is
  // also listed (it used to fetch 28 days; later windows saved but vanished).
  const range = useMemo(() => {
    const now = Date.now();
    return {
      start: new Date(now).toISOString(),
      end: new Date(now + HORIZON_DAYS * DAY_MS).toISOString(),
    };
  }, []);
  const windows = useQuery({
    queryKey: ['interviewer', 'availability'],
    queryFn: () => getMyAvailability(range),
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['interviewer', 'availability'] });

  const addMut = useMutation({
    mutationFn: () =>
      addMyAvailability({
        starts_at: localInputToIso(startLocal),
        ends_at: localInputToIso(endLocal),
      }),
    onSuccess: () => {
      toast.success('Availability added');
      setStartLocal('');
      setEndLocal('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add that window.')),
  });

  const removeMut = useMutation({
    mutationFn: (windowId: string) => removeMyAvailability(windowId),
    onSuccess: () => {
      toast.success('Window deleted');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete that window.')),
  });

  const rows = windows.data ?? [];
  const problem =
    startLocal && endLocal
      ? windowProblem(localInputToIso(startLocal), localInputToIso(endLocal), Date.now())
      : null;
  const ready = Boolean(startLocal) && Boolean(endLocal) && problem === null;

  return (
    <section>
      <h2 className="text-[16px] font-semibold text-foreground">My availability</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        When HR can book you for an interview - in your own local time ({browserTimezone()}). Each
        window is at most {MAX_WINDOW_DAYS} days; the list shows the next {HORIZON_DAYS} days.
      </p>

      <GlassCard className="mt-3 p-4">
        <form
          className="flex flex-wrap items-end gap-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) addMut.mutate();
          }}
        >
          <FormField id="avail-start" label="From">
            {(p) => (
              <Input
                {...p}
                type="datetime-local"
                className="w-auto"
                value={startLocal}
                onChange={(e) => setStartLocal(e.target.value)}
              />
            )}
          </FormField>
          <FormField id="avail-end" label="To">
            {(p) => (
              <Input
                {...p}
                type="datetime-local"
                className="w-auto"
                value={endLocal}
                onChange={(e) => setEndLocal(e.target.value)}
              />
            )}
          </FormField>
          <Button
            type="submit"
            disabled={!ready || addMut.isPending}
            aria-describedby={problem ? 'avail-problem' : undefined}
          >
            {addMut.isPending ? 'Adding…' : 'Add window'}
          </Button>
        </form>
        {problem ? (
          <p id="avail-problem" role="alert" className="mt-2 text-[12.5px] text-[var(--ui-warn)]">
            {problem}
          </p>
        ) : null}

        {windows.isLoading ? (
          <div className="mt-3 flex flex-col gap-1.5" aria-busy="true" aria-label="Loading your availability">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : windows.isError ? (
          <QueryErrorPanel
            className="mt-3"
            title="Could not load your availability."
            error={windows.error}
            onRetry={() => void windows.refetch()}
            retrying={windows.isFetching}
          />
        ) : rows.length === 0 ? (
          <p className="mt-3 text-[13px] text-muted-foreground">No windows set yet.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-1.5">
            {rows.map((w) => {
              const from = formatDayTime(w.starts_at, browserTimezone());
              const to = formatDayTime(w.ends_at, browserTimezone());
              return (
                <li
                  key={w.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-[9px] border border-border px-3 py-2 text-[12.5px] text-foreground"
                >
                  <span>
                    {from} &rarr; {to}
                  </span>
                  {/* Named for its window (AD-F33), and a confirm before it is
                      deleted, like every other delete (consistency B3). */}
                  <ConfirmDeleteButton
                    title={`Delete the window ${from} to ${to}`}
                    confirmText="Delete"
                    confirmGroupLabel={`Delete the window ${from} to ${to}`}
                    pending={removeMut.isPending && removeMut.variables === w.id}
                    onConfirm={() => removeMut.mutate(w.id)}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </GlassCard>
    </section>
  );
}
