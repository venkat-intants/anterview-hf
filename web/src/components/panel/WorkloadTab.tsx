// WorkloadTab — PH4-O5. Who is carrying the interview load this period, and
// each interviewer's daily/weekly limit. Over-allocation is a FLAG for HR,
// never a refusal (see panel_workload.py's own note) — this tab only ever
// shows numbers and sentences; it cannot move or refuse anything.

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getWorkload, setInterviewerCapacity, type WorkloadRow } from '@/api/scheduling';
import { flagSentences, presetRange, type WorkloadPeriodPreset } from '@/lib/workloadPanel';
import { toast } from '@/lib/toast';
import { Loader2 } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';

type Preset = WorkloadPeriodPreset;

const PRESETS: { key: Preset; label: string }[] = [
  { key: 'this_week', label: 'This week' },
  { key: 'next_week', label: 'Next week' },
  { key: 'next_30_days', label: 'Next 30 days' },
];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function CapacityEditor({ row }: { row: WorkloadRow }) {
  const qc = useQueryClient();
  const [perDay, setPerDay] = useState(String(row.max_per_day));
  const [perWeek, setPerWeek] = useState(String(row.max_per_week));

  const saveMut = useMutation({
    mutationFn: (vars: { day: string; week: string }) =>
      setInterviewerCapacity(row.user_id, {
        max_sessions_per_day: vars.day.trim() ? Number(vars.day) : null,
        max_sessions_per_week: vars.week.trim() ? Number(vars.week) : null,
      }),
    onSuccess: () => {
      toast.success(`Updated ${row.name}'s limits`);
      void qc.invalidateQueries({ queryKey: ['hr', 'panel', 'workload'] });
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not update this limit')),
  });

  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="text-[11.5px] text-[var(--ui-soft)]">
        Daily limit
        <input
          aria-label={`Daily limit for ${row.name}`}
          type="number"
          min={1}
          max={24}
          value={perDay}
          onChange={(e) => setPerDay(e.target.value)}
          placeholder="company default"
          className="mt-0.5 block w-[110px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12px] text-foreground focus:border-[var(--accent)] focus:outline-none"
        />
      </label>
      <label className="text-[11.5px] text-[var(--ui-soft)]">
        Weekly limit
        <input
          aria-label={`Weekly limit for ${row.name}`}
          type="number"
          min={1}
          max={100}
          value={perWeek}
          onChange={(e) => setPerWeek(e.target.value)}
          placeholder="company default"
          className="mt-0.5 block w-[110px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12px] text-foreground focus:border-[var(--accent)] focus:outline-none"
        />
      </label>
      <button
        type="button"
        disabled={saveMut.isPending}
        onClick={() => saveMut.mutate({ day: perDay, week: perWeek })}
        className="rounded-[8px] border border-border px-3 py-1.5 text-[11.5px] font-medium text-foreground disabled:opacity-40"
      >
        {saveMut.isPending ? 'Saving…' : 'Save'}
      </button>
      <button
        type="button"
        disabled={saveMut.isPending}
        onClick={() => {
          setPerDay('');
          setPerWeek('');
          saveMut.mutate({ day: '', week: '' });
        }}
        className="text-[11.5px] text-muted-foreground hover:text-foreground disabled:opacity-40"
      >
        Reset to company default
      </button>
    </div>
  );
}

function WorkloadRowCard({ row, defaults }: { row: WorkloadRow; defaults: { max_per_day: number; max_per_week: number } }) {
  const flags = flagSentences(row);
  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[14px] font-medium text-foreground">{row.name}</p>
          <p className="text-[11.5px] text-muted-foreground">{row.role.replace('_', ' ')}</p>
        </div>
        {flags.length > 0 ? <StatusTag tone="amber">{flags.length} flag{flags.length === 1 ? '' : 's'}</StatusTag> : null}
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-4">
        <div>
          <dt className="text-[var(--ui-faint)]">Sessions</dt>
          <dd className="text-foreground">{row.sessions}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Hours</dt>
          <dd className="text-foreground">{row.hours}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Loops</dt>
          <dd className="text-foreground">{row.loops}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Open scorecards</dt>
          <dd className="text-foreground">{row.open_scorecards}</dd>
        </div>
      </dl>

      {flags.length > 0 ? (
        <ul className="mt-2.5 flex flex-col gap-1">
          {flags.map((f) => (
            <li key={f} className="text-[12px] text-[var(--ui-warn)]">
              {f}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2.5 text-[12px] text-muted-foreground">No flags this period.</p>
      )}

      <div className="mt-3 border-t border-border pt-3">
        <p className="mb-1.5 text-[11px] text-[var(--ui-faint)]">
          Blank uses the company default (daily {defaults.max_per_day}, weekly {defaults.max_per_week}).
        </p>
        <CapacityEditor row={row} />
      </div>
    </GlassCard>
  );
}

export default function WorkloadTab(): JSX.Element {
  const [preset, setPreset] = useState<Preset>('this_week');
  // Memoized on `preset` alone: presetRange(preset) defaults to `new Date()`,
  // and recomputing it on every render put a fresh millisecond in the query
  // key each time — react-query saw a "new" query every render and refetched
  // in a tight loop instead of caching on the period the reader chose.
  const { start, end } = useMemo(() => presetRange(preset), [preset]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['hr', 'panel', 'workload', start, end],
    queryFn: () => getWorkload(start, end),
  });

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Period">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            aria-pressed={preset === p.key}
            onClick={() => setPreset(p.key)}
            className={
              preset === p.key
                ? 'rounded-pill bg-primary px-3.5 py-1.5 text-[12.5px] font-medium text-primary-foreground'
                : 'rounded-pill border border-border px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--ui-soft)] hover:text-foreground'
            }
          >
            {p.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </div>
      ) : isError ? (
        <p className="mt-4 text-[13px] text-[var(--ui-danger)]">
          {errText(error, 'Could not load panel workload')}
        </p>
      ) : (data?.interviewers.length ?? 0) === 0 ? (
        <p className="mt-4 text-[13px] text-muted-foreground">No one can be booked for interviews yet. Your super admin adds interviewers under Team.</p>
      ) : (
        <div className="mt-4 flex flex-col gap-3">
          {data?.interviewers.map((row) => (
            <WorkloadRowCard key={row.user_id} row={row} defaults={data.defaults} />
          ))}
        </div>
      )}
    </div>
  );
}
