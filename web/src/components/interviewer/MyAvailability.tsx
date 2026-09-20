// MyAvailability — PH4-A2/O5. The signed-in interviewer's own availability
// windows: what HR schedules against when they add a session, and what
// panel workload checks "outside availability" against. English-only by
// design, same as the console it lives on (CLAUDE.md).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Trash2 } from '@/design/components/icons';
import { GlassCard } from '@/design/components/primitives';
import { addMyAvailability, getMyAvailability, removeMyAvailability } from '@/api/scheduling';
import { localInputToIso } from '@/lib/localDatetime';
import { formatDayTime, browserTimezone } from '@/lib/timezone';
import { toast } from '@/lib/toast';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export default function MyAvailability(): JSX.Element {
  const qc = useQueryClient();
  const [startLocal, setStartLocal] = useState('');
  const [endLocal, setEndLocal] = useState('');

  const windows = useQuery({
    queryKey: ['interviewer', 'availability'],
    queryFn: () => getMyAvailability(),
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
    onError: (e: unknown) => toast.error(errText(e, 'Could not add that window')),
  });

  const removeMut = useMutation({
    mutationFn: (windowId: string) => removeMyAvailability(windowId),
    onSuccess: () => {
      toast.success('Removed');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not remove that window')),
  });

  const rows = windows.data ?? [];
  const ready = Boolean(startLocal) && Boolean(endLocal);

  return (
    <section className="mt-8">
      <h2 className="text-[16px] font-semibold text-foreground">My availability</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        When HR can book you for an interview — in your own local time ({browserTimezone()}).
      </p>

      <GlassCard className="mt-3 p-4">
        <form
          className="flex flex-wrap items-end gap-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) addMut.mutate();
          }}
        >
          <div>
            <label htmlFor="avail-start" className="block text-[12px] font-medium text-[var(--ui-soft)]">
              From
            </label>
            <input
              id="avail-start"
              type="datetime-local"
              value={startLocal}
              onChange={(e) => setStartLocal(e.target.value)}
              className="mt-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="avail-end" className="block text-[12px] font-medium text-[var(--ui-soft)]">
              To
            </label>
            <input
              id="avail-end"
              type="datetime-local"
              value={endLocal}
              onChange={(e) => setEndLocal(e.target.value)}
              className="mt-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={!ready || addMut.isPending}
            className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
          >
            {addMut.isPending ? 'Adding…' : 'Add window'}
          </button>
        </form>

        {windows.isLoading ? (
          <div className="mt-3 flex items-center gap-2 text-[13px] text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading…
          </div>
        ) : windows.isError ? (
          <p className="mt-3 text-[13px] text-[var(--ui-danger)]">
            {errText(windows.error, 'Could not load your availability')}
          </p>
        ) : rows.length === 0 ? (
          <p className="mt-3 text-[13px] text-muted-foreground">No windows set yet.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-1.5">
            {rows.map((w) => (
              <li
                key={w.id}
                className="flex items-center justify-between gap-2 rounded-[9px] border border-border px-3 py-2 text-[12.5px] text-foreground"
              >
                <span>
                  {formatDayTime(w.starts_at, browserTimezone())} &rarr;{' '}
                  {formatDayTime(w.ends_at, browserTimezone())}
                </span>
                <button
                  type="button"
                  aria-label="Remove this window"
                  disabled={removeMut.isPending}
                  onClick={() => removeMut.mutate(w.id)}
                  className="shrink-0 rounded-[7px] p-1 text-[var(--ui-faint)] hover:text-[var(--ui-danger)] disabled:opacity-40"
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </GlassCard>
    </section>
  );
}
