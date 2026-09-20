// CalibrationTab — PH4-O5. Read-only: does an interviewer score the SAME
// candidates differently from the rest of the panel, on paired judgements.
//
// SUPPRESSION (security fix): a row with too few distinct candidates is
// suppressed server-side — mean/mean_delta/not_assessed_rate/flag/
// distribution come back null and by_competency empty, so a small number
// here can never be traced back to one or two candidates' scores. This tab
// says so in words and never falls back to showing a zero.

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getCalibration, type CalibrationRow } from '@/api/scheduling';
import { listRequisitions } from '@/api/requisitions';
import { getWorkflow, listWorkflows } from '@/api/workflows';
import { Loader2 } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';

type Period = 30 | 90 | 180;

const PERIODS: { key: Period; label: string }[] = [
  { key: 30, label: 'Last 30 days' },
  { key: 90, label: 'Last 90 days' },
  { key: 180, label: 'Last 180 days' },
];

function periodRange(days: Period): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - days);
  return { start: start.toISOString(), end: end.toISOString() };
}

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function signed(n: number): string {
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`;
}

function flagSentence(row: CalibrationRow): string | null {
  if (row.flag === 'higher') return 'Scores consistently higher than the panel on the same candidates.';
  if (row.flag === 'lower') return 'Scores consistently lower than the panel on the same candidates.';
  return null;
}

function useRoundOptions(requisitionId: string) {
  const workflows = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId),
    enabled: Boolean(requisitionId),
  });
  const publishedId = workflows.data?.find((w) => w.status === 'published')?.id ?? null;
  const workflow = useQuery({
    queryKey: ['hr', 'workflow', publishedId],
    queryFn: () => getWorkflow(publishedId as string),
    enabled: Boolean(publishedId),
  });
  return (workflow.data?.rounds ?? [])
    .filter((r) => r.kind === 'human_review')
    .map((r) => ({ id: r.id, title: r.title }));
}

function CalibrationRowCard({ row, minCandidates }: { row: CalibrationRow; minCandidates: number }) {
  if (row.suppressed) {
    return (
      <GlassCard className="p-4">
        <p className="text-[14px] font-medium text-foreground">{row.name}</p>
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">
          Too few candidates to compare ({row.candidates} of the {minCandidates} needed).
        </p>
      </GlassCard>
    );
  }

  const sentence = flagSentence(row);
  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-[14px] font-medium text-foreground">{row.name}</p>
        {row.flag ? (
          <StatusTag tone={row.flag === 'higher' ? 'amber' : 'lavender'}>
            {row.flag === 'higher' ? 'Scores higher' : 'Scores lower'}
          </StatusTag>
        ) : null}
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-4">
        <div>
          <dt className="text-[var(--ui-faint)]">Scorecards</dt>
          <dd className="text-foreground">{row.scorecards}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Mean score</dt>
          <dd className="text-foreground">{row.mean ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Shared judgements</dt>
          <dd className="text-foreground">{row.pairs}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">vs panel</dt>
          <dd className="text-foreground">{row.mean_delta === null ? '—' : signed(row.mean_delta)}</dd>
        </div>
      </dl>

      {row.not_assessed_rate !== null ? (
        <p className="mt-2 text-[12px] text-muted-foreground">
          Marked &quot;not assessed&quot; {Math.round(row.not_assessed_rate * 100)}% of the time.
        </p>
      ) : null}

      {row.distribution ? (
        <div className="mt-2.5 flex items-center gap-3 text-[11.5px] text-[var(--ui-soft)]">
          {(['1', '2', '3', '4', '5'] as const).map((k) => (
            <span key={k}>
              {k}: {row.distribution?.[k] ?? 0}
            </span>
          ))}
        </div>
      ) : null}

      {sentence ? <p className="mt-2.5 text-[12.5px] text-[var(--ui-warn)]">{sentence}</p> : null}
    </GlassCard>
  );
}

export default function CalibrationTab(): JSX.Element {
  const [period, setPeriod] = useState<Period>(90);
  const [requisitionId, setRequisitionId] = useState('');
  const [roundId, setRoundId] = useState('');
  // Memoized on `period` alone — see WorkloadTab's identical fix: recomputing
  // this on every render put a fresh millisecond in the query key each time,
  // which defeated caching and refetched in a tight loop.
  const { start, end } = useMemo(() => periodRange(period), [period]);

  const openings = useQuery({ queryKey: ['hr', 'requisitions'], queryFn: () => listRequisitions() });
  const rounds = useRoundOptions(requisitionId);

  const calibration = useQuery({
    queryKey: ['hr', 'panel', 'calibration', start, end, requisitionId, roundId],
    queryFn: () =>
      getCalibration({
        start,
        end,
        requisitionId: requisitionId || null,
        roundId: roundId || null,
      }),
  });

  return (
    <div>
      <GlassCard className="p-4">
        <p className="text-[13px] font-medium text-foreground">
          Read-only. Calibration never changes a submitted scorecard or a hiring decision.
        </p>
        <p className="mt-1 text-[12px] text-muted-foreground">
          {calibration.data
            ? `Interviewers are flagged at ±${calibration.data.rules.meaningful_delta} over at least ${calibration.data.rules.min_pairs} shared judgements.`
            : 'Interviewers are flagged only on a large, repeated difference from the rest of the panel.'}
        </p>
        <p className="mt-1 text-[12px] text-muted-foreground">
          Candidates you still have to score are left out until you submit your own scorecard for them.
        </p>
      </GlassCard>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="calib-period" className="block text-[12px] font-medium text-[var(--ui-soft)]">
            Period
          </label>
          <select
            id="calib-period"
            value={period}
            onChange={(e) => setPeriod(Number(e.target.value) as Period)}
            className="mt-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          >
            {PERIODS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="calib-opening" className="block text-[12px] font-medium text-[var(--ui-soft)]">
            Opening
          </label>
          <select
            id="calib-opening"
            value={requisitionId}
            onChange={(e) => {
              setRequisitionId(e.target.value);
              setRoundId('');
            }}
            className="mt-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          >
            <option value="">All openings</option>
            {(openings.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="calib-round" className="block text-[12px] font-medium text-[var(--ui-soft)]">
            Round
          </label>
          <select
            id="calib-round"
            value={roundId}
            onChange={(e) => setRoundId(e.target.value)}
            disabled={!requisitionId}
            className="mt-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
          >
            <option value="">All rounds</option>
            {rounds.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </select>
        </div>
      </div>

      {calibration.isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </div>
      ) : calibration.isError ? (
        <p className="mt-4 text-[13px] text-[var(--ui-danger)]">
          {errText(calibration.error, 'Could not load calibration')}
        </p>
      ) : (calibration.data?.interviewers.length ?? 0) === 0 ? (
        <p className="mt-4 text-[13px] text-muted-foreground">
          No submitted scorecards to compare in this period.
        </p>
      ) : (
        <div className="mt-4 flex flex-col gap-3">
          {calibration.data?.interviewers.map((row) => (
            <CalibrationRowCard
              key={row.user_id}
              row={row}
              minCandidates={calibration.data?.rules.min_candidates ?? 5}
            />
          ))}
        </div>
      )}
    </div>
  );
}
