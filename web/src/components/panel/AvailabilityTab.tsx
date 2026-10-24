// AvailabilityTab — PH4-A2/O5. HR reading and editing ANY interviewer's
// availability — what `addSession`/`rescheduleSession` check "outside
// availability" against, and what "Show free times" searches.

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addInterviewerAvailability,
  getInterviewerAvailability,
  removeInterviewerAvailability,
} from '@/api/scheduling';
import { listInterviewers } from '@/api/scorecards';
import { localInputToIso, toLocalInputValue } from '@/lib/localDatetime';
import { formatDayTime, browserTimezone } from '@/lib/timezone';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { GlassCard } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';

/** How far ahead the list reaches. The server caps a read at 92 days. */
const RANGES = [
  { weeks: 4, label: 'Next 4 weeks' },
  { weeks: 8, label: 'Next 8 weeks' },
  { weeks: 12, label: 'Next 12 weeks' },
] as const;

type RangeWeeks = (typeof RANGES)[number]['weeks'];

export default function AvailabilityTab(): JSX.Element {
  const qc = useQueryClient();
  const interviewers = useQuery({ queryKey: ['hr', 'interviewers'], queryFn: listInterviewers });
  const [userId, setUserId] = useState('');
  const [startLocal, setStartLocal] = useState('');
  const [endLocal, setEndLocal] = useState('');
  const [weeks, setWeeks] = useState<RangeWeeks>(4);
  const tz = browserTimezone();

  // Memoised on the range alone, for the same reason as WorkloadTab: a fresh
  // `new Date()` on every render would put a new key in the query each time.
  const range = useMemo(() => {
    const start = new Date();
    const end = new Date(start.getTime() + weeks * 7 * 24 * 60 * 60 * 1000);
    return { start: start.toISOString(), end: end.toISOString() };
  }, [weeks]);

  const windows = useQuery({
    queryKey: ['hr', 'panel', 'availability', userId, weeks],
    queryFn: () => getInterviewerAvailability(userId, range),
    enabled: Boolean(userId),
  });

  // Availability feeds the workload flags and the free-time search too.
  const invalidate = () => void invalidateAfterHrMutation(qc);

  const addMut = useMutation({
    mutationFn: () =>
      addInterviewerAvailability(userId, {
        starts_at: localInputToIso(startLocal),
        ends_at: localInputToIso(endLocal),
      }),
    onSuccess: () => {
      // A window that starts after the period shown is saved but would not
      // appear below, which read as "it did not save".
      const beyond = new Date(localInputToIso(startLocal)).getTime() >= Date.parse(range.end);
      toast.success(
        beyond
          ? 'Availability added. It starts after the period shown; choose a longer range to see it.'
          : 'Availability added',
      );
      setStartLocal('');
      setEndLocal('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the window.')),
  });

  const removeMut = useMutation({
    mutationFn: (windowId: string) => removeInterviewerAvailability(windowId),
    onSuccess: () => {
      toast.success('Window removed');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not remove the window.')),
  });

  const rows = windows.data ?? [];
  const ready = Boolean(userId) && Boolean(startLocal) && Boolean(endLocal);
  const chosen = (interviewers.data ?? []).find((iv) => iv.user_id === userId);
  const nowLocal = toLocalInputValue(new Date().toISOString());

  return (
    <div>
      <FormField id="panel-avail-who" label="Interviewer" className="max-w-[320px]">
        {(p) => (
          <SelectControl
            {...p}
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            disabled={interviewers.isError && !interviewers.data}
          >
            <option value="">Choose an interviewer…</option>
            {(interviewers.data ?? []).map((iv) => (
              <option key={iv.user_id} value={iv.user_id}>
                {iv.full_name}
              </option>
            ))}
          </SelectControl>
        )}
      </FormField>

      {interviewers.isError && !interviewers.data ? (
        // A failed list is not an empty one (IV-L10).
        <QueryErrorPanel
          className="mt-3"
          title="Could not load interviewers."
          error={interviewers.error}
          onRetry={() => void interviewers.refetch()}
          retrying={interviewers.isFetching}
        />
      ) : !userId ? (
        <p className="mt-3 text-[13px] text-muted-foreground">
          Choose an interviewer to see and edit their availability.
        </p>
      ) : (
        <GlassCard className="mt-3 p-4">
          <p className="text-[12.5px] text-muted-foreground">
            Times below are shown in your own local time ({tz}).
          </p>
          <form
            className="mt-2 flex flex-wrap items-end gap-2.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (ready) addMut.mutate();
            }}
          >
            <FormField id="panel-avail-start" label="From">
              {(p) => (
                <Input
                  {...p}
                  type="datetime-local"
                  size="sm"
                  className="w-auto"
                  min={nowLocal}
                  value={startLocal}
                  onChange={(e) => setStartLocal(e.target.value)}
                />
              )}
            </FormField>
            <FormField id="panel-avail-end" label="To">
              {(p) => (
                <Input
                  {...p}
                  type="datetime-local"
                  size="sm"
                  className="w-auto"
                  min={startLocal || nowLocal}
                  value={endLocal}
                  onChange={(e) => setEndLocal(e.target.value)}
                />
              )}
            </FormField>
            <Button type="submit" size="sm" disabled={!ready || addMut.isPending}>
              {addMut.isPending ? 'Adding…' : `Add window for ${chosen?.full_name ?? 'them'}`}
            </Button>
          </form>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <label
              htmlFor="panel-avail-range"
              className="text-[12px] font-medium text-[var(--ui-soft)]"
            >
              Showing
            </label>
            <SelectControl
              id="panel-avail-range"
              size="sm"
              className="w-auto"
              value={weeks}
              onChange={(e) => setWeeks(Number(e.target.value) as RangeWeeks)}
            >
              {RANGES.map((r) => (
                <option key={r.weeks} value={r.weeks}>
                  {r.label}
                </option>
              ))}
            </SelectControl>
          </div>

          {windows.isLoading ? (
            <div className="mt-3 flex flex-col gap-1.5" aria-busy="true" aria-label="Loading availability">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : windows.isError ? (
            <QueryErrorPanel
              className="mt-3"
              title="Could not load their availability."
              error={windows.error}
              onRetry={() => void windows.refetch()}
              retrying={windows.isFetching}
            />
          ) : rows.length === 0 ? (
            <p className="mt-3 text-[13px] text-muted-foreground">
              No windows set in the {RANGES.find((r) => r.weeks === weeks)?.label.toLowerCase()}.
            </p>
          ) : (
            <ul className="mt-3 flex flex-col gap-1.5">
              {rows.map((w) => {
                const label = `${formatDayTime(w.starts_at, tz)} to ${formatDayTime(w.ends_at, tz)}`;
                return (
                  <li
                    key={w.id}
                    className="flex items-center justify-between gap-2 rounded-[9px] border border-border px-3 py-1 text-[12.5px] text-foreground"
                  >
                    <span>{label}</span>
                    <ConfirmDeleteButton
                      title={`Remove the window ${label}`}
                      confirmText="Remove window"
                      pending={removeMut.isPending && removeMut.variables === w.id}
                      onConfirm={() => removeMut.mutate(w.id)}
                    />
                  </li>
                );
              })}
            </ul>
          )}
        </GlassCard>
      )}
    </div>
  );
}
