// WorkloadTab — PH4-O5. Who is carrying the interview load this period, and
// each interviewer's daily/weekly limit. Over-allocation is a FLAG for HR,
// never a refusal (see panel_workload.py's own note) — this tab only ever
// shows numbers and sentences; it cannot move or refuse anything.

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getWorkload, setInterviewerCapacity, type WorkloadRow } from '@/api/scheduling';
import {
  capacityProblem,
  flagSentences,
  presetRange,
  WORK_TIMEZONE_LABEL,
  type WorkloadPeriodPreset,
} from '@/lib/workloadPanel';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { formatNumber, plural } from '@/lib/formatters';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

/** Interview hours to one decimal: "1.3", "4.5", "6". */
function formatHours(hours: number): string {
  return formatNumber(Math.round(hours * 10) / 10);
}

type Preset = WorkloadPeriodPreset;

const PRESETS: { key: Preset; label: string }[] = [
  { key: 'this_week', label: 'This week' },
  { key: 'next_week', label: 'Next week' },
  { key: 'next_30_days', label: 'Next 30 days' },
];

const ROLE_LABEL: Record<WorkloadRow['role'], string> = {
  interviewer: 'Interviewer',
  hr_manager: 'HR manager',
};

/** The stored override as the field shows it: blank means the company default. */
function overrideText(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value);
}

function CapacityEditor({
  row,
  defaults,
}: {
  row: WorkloadRow;
  defaults: { max_per_day: number; max_per_week: number };
}) {
  const qc = useQueryClient();
  // Pre-filled with the stored OVERRIDE only. The effective limit used to go
  // in here, so the field was never blank and saving an untouched row pinned
  // the company default as a custom limit (IV-M13).
  const [perDay, setPerDay] = useState(overrideText(row.custom_max_per_day));
  const [perWeek, setPerWeek] = useState(overrideText(row.custom_max_per_week));
  // Follow the server after a refetch (a colleague's change, or our own save).
  useEffect(() => setPerDay(overrideText(row.custom_max_per_day)), [row.custom_max_per_day]);
  useEffect(() => setPerWeek(overrideText(row.custom_max_per_week)), [row.custom_max_per_week]);

  const dayErr = capacityProblem(perDay, 24, 'Daily');
  const weekErr = capacityProblem(perWeek, 100, 'Weekly');
  const unchanged =
    perDay === overrideText(row.custom_max_per_day) &&
    perWeek === overrideText(row.custom_max_per_week);
  const hasOverride = row.custom_max_per_day != null || row.custom_max_per_week != null;

  const saveMut = useMutation({
    mutationFn: (vars: { day: string; week: string }) =>
      setInterviewerCapacity(row.user_id, {
        max_sessions_per_day: vars.day.trim() ? Number(vars.day) : null,
        max_sessions_per_week: vars.week.trim() ? Number(vars.week) : null,
      }),
    onSuccess: () => {
      toast.success(`Updated ${row.name}'s limits`);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update the limits.')),
  });

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-[12px] text-[var(--ui-soft)]">
          Daily limit
          <Input
            aria-label={`Daily limit for ${row.name}`}
            type="number"
            size="sm"
            min={1}
            max={24}
            value={perDay}
            onChange={(e) => setPerDay(e.target.value)}
            placeholder={`Default (${defaults.max_per_day})`}
            invalid={dayErr !== null}
            className="mt-0.5 block w-[130px]"
          />
        </label>
        <label className="text-[12px] text-[var(--ui-soft)]">
          Weekly limit
          <Input
            aria-label={`Weekly limit for ${row.name}`}
            type="number"
            size="sm"
            min={1}
            max={100}
            value={perWeek}
            onChange={(e) => setPerWeek(e.target.value)}
            placeholder={`Default (${defaults.max_per_week})`}
            invalid={weekErr !== null}
            className="mt-0.5 block w-[130px]"
          />
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={saveMut.isPending || unchanged || dayErr !== null || weekErr !== null}
          onClick={() => saveMut.mutate({ day: perDay, week: perWeek })}
        >
          {saveMut.isPending ? 'Saving…' : 'Save'}
        </Button>
        {hasOverride ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={saveMut.isPending}
            onClick={() => {
              setPerDay('');
              setPerWeek('');
              saveMut.mutate({ day: '', week: '' });
            }}
          >
            Reset to company default
          </Button>
        ) : null}
      </div>
      {dayErr ? <p className="text-[12px] text-[var(--ui-danger)]">{dayErr}</p> : null}
      {weekErr ? <p className="text-[12px] text-[var(--ui-danger)]">{weekErr}</p> : null}
    </div>
  );
}

function WorkloadRowCard({
  row,
  defaults,
}: {
  row: WorkloadRow;
  defaults: { max_per_day: number; max_per_week: number };
}) {
  const flags = flagSentences(row);
  const custom = row.custom_max_per_day != null || row.custom_max_per_week != null;
  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[14px] font-medium text-foreground">{row.name}</p>
          <p className="text-[12px] text-muted-foreground">{ROLE_LABEL[row.role] ?? row.role}</p>
        </div>
        {flags.length > 0 ? (
          <StatusTag tone="amber">
            {plural(flags.length, 'flag')}
          </StatusTag>
        ) : null}
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Interviews</dt>
          <dd className="text-foreground">{row.sessions}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Hours</dt>
          {/* The server sends a raw float (80 minutes is 1.333...): one
              decimal, through the shared formatter. */}
          <dd className="text-foreground">{formatHours(row.hours)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Loops</dt>
          <dd className="text-foreground">{row.loops}</dd>
        </div>
        <div>
          {/* Not for the chosen period: what they owe today, whenever the
              interview was (IV-L11). */}
          <dt className="text-muted-foreground">Open scorecards (now)</dt>
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
        <p className="mb-1.5 text-[12px] text-muted-foreground">
          {custom
            ? `Custom limits: daily ${row.max_per_day}, weekly ${row.max_per_week}. `
            : 'Using the company default. '}
          Leave a field blank to use the company default (daily {defaults.max_per_day}, weekly{' '}
          {defaults.max_per_week}).
        </p>
        <CapacityEditor row={row} defaults={defaults} />
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

  const workload = useQuery({
    queryKey: ['hr', 'panel', 'workload', start, end],
    queryFn: () => getWorkload(start, end),
  });
  const { data, isLoading, isError } = workload;

  return (
    <div>
      {/* Pressed buttons in a labelled group: these pick a period for one
          list, they are not tabs over separate panels. */}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Period">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            aria-pressed={preset === p.key}
            onClick={() => setPreset(p.key)}
            className={
              preset === p.key
                ? 'rounded-pill bg-foreground px-3.5 py-1.5 text-[12.5px] font-medium text-background'
                : 'rounded-pill border border-border px-3.5 py-1.5 text-[12.5px] font-medium text-[var(--ui-soft)] hover:text-foreground'
            }
          >
            {p.label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[12px] text-muted-foreground">
        Days and weeks are counted in {WORK_TIMEZONE_LABEL}, Monday to Sunday.
      </p>

      {isLoading ? (
        <div className="mt-4 flex flex-col gap-3" aria-busy="true" aria-label="Loading the panel workload">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : isError && !data ? (
        <QueryErrorPanel
          className="mt-4"
          title="Could not load the panel workload."
          error={workload.error}
          onRetry={() => void workload.refetch()}
          retrying={workload.isFetching}
        />
      ) : (data?.interviewers.length ?? 0) === 0 ? (
        <p className="mt-4 text-[13px] text-muted-foreground">
          No one can be booked for interviews yet. Your super admin adds interviewers under Team.
        </p>
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
