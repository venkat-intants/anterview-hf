// CalibrationTab — PH4-O5, extended PH5-E4. Read-only: does an interviewer
// score the SAME candidates differently from the rest of the panel, on
// paired judgements — now also broken down per frozen criterion, with the
// panel's OWN per-criterion baseline and a drill-down to the judgements
// themselves.
//
// SUPPRESSION (security fix): a row or criterion cell with too few distinct
// candidates is suppressed server-side — mean/mean_delta/not_assessed_rate/
// flag/distribution come back null and by_competency/by_criterion empty, so a
// small number here can never be traced back to one or two candidates'
// scores. This tab says so in words and never falls back to showing a zero.
//
// BANNED WORDS (PH5-E4): bias, harsh, lenient, outlier, poor, recommend.
// Interviewers are never ranked — the list below is always alphabetical.

import { useMemo, useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import {
  getCalibration,
  type CalibrationCriterion,
  type CalibrationResponse,
  type CalibrationRow,
} from '@/api/scheduling';
import { listRequisitions } from '@/api/requisitions';
import {
  getWorkflow,
  listWorkflows,
  HUMAN_EVALUATED_KINDS,
  type WorkflowSummary,
} from '@/api/workflows';
import { Loader2 } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import CalibrationInfoDialog from './CalibrationInfoDialog';
import CalibrationJudgementsDialog from './CalibrationJudgementsDialog';

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

/** The overall paired-gap sentence (PH5-E4 §4.3 wording) — every number in
 *  it comes straight from the server; nothing here is derived or rounded
 *  beyond display precision. Null when the row carries no signal to state.
 *
 *  `same_direction_count` is the server's own count behind
 *  `same_direction_share` (never `Math.round(same_direction_share * pairs)`,
 *  which can silently disagree with it) — printed directly as "in N of
 *  pairs". Falls back to the share as a percentage only when the server
 *  omits the count. */
function flagSentence(row: CalibrationRow): string | null {
  if (!row.flag || row.mean_delta === null || row.same_direction_share === null) return null;
  const direction = row.flag === 'higher' ? 'higher than' : 'lower than';
  const sameWay =
    row.same_direction_count !== null
      ? `in ${row.same_direction_count} of ${row.pairs} the gap was the same way`
      : `the gap pointed the same way in about ${Math.round(row.same_direction_share * 100)}% of them`;
  return (
    `Scores ${direction} the rest of the panel on the same candidates, by ` +
    `${Math.abs(row.mean_delta).toFixed(1)} on average across ${row.pairs} shared ` +
    `judgements; ${sameWay}.`
  );
}

/** The narrower, one-criterion version of the same sentence — the API does
 *  not carry a per-criterion same-direction share, so this states only what
 *  it actually returns (the gap and the judgement count), never a number the
 *  server did not send. */
function criterionGapSentence(
  label: string,
  direction: 'higher' | 'lower',
  gap: number,
  shared: number,
): string {
  const word = direction === 'higher' ? 'higher than' : 'lower than';
  return `On ${label}, ${word} the rest of the panel by ${Math.abs(gap).toFixed(1)} on average across ${shared} shared judgements.`;
}

function disagreementSentence(c: CalibrationCriterion): string | null {
  if (c.signal !== 'wide_disagreement' || c.disagreement === null) return null;
  return (
    `On this criterion, interviewers scoring the same candidate were ${c.disagreement.toFixed(1)} ` +
    'points apart on average. The anchors may need discussing.'
  );
}

interface RoundOption {
  id: string;
  title: string;
  workflowVersion: number;
  archived: boolean;
}

function roundOptionLabel(o: RoundOption): string {
  return o.archived ? `${o.title} (v${o.workflowVersion}, archived)` : o.title;
}

/** A requisition cloned and archived many times must not turn one dropdown
 *  into this many parallel `GET /workflows/{id}` requests — the published
 *  version plus the most recent this-many archived ones, newest first. */
const MAX_ARCHIVED_ROUND_VERSIONS = 5;

/** Every scorable round (human_review, job_simulation, portfolio) across the
 *  published workflow AND the most recent archived versions (capped — see
 *  `MAX_ARCHIVED_ROUND_VERSIONS`) — PH5-E4 fix to the O5 gap that only
 *  listed human_review rounds of the published workflow. */
function useRoundOptions(requisitionId: string): {
  options: RoundOption[];
  isLoading: boolean;
  hiddenArchivedCount: number;
} {
  const workflows = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId),
    enabled: Boolean(requisitionId),
  });
  const published = (workflows.data ?? []).filter((w) => w.status === 'published');
  const archived = (workflows.data ?? [])
    .filter((w) => w.status === 'archived')
    .sort((a, b) => b.version - a.version);
  const cappedArchived = archived.slice(0, MAX_ARCHIVED_ROUND_VERSIONS);
  const hiddenArchivedCount = archived.length - cappedArchived.length;
  const relevant: WorkflowSummary[] = [...published, ...cappedArchived].sort(
    (a, b) => b.version - a.version,
  );
  const workflowQueries = useQueries({
    queries: relevant.map((w) => ({
      queryKey: ['hr', 'workflow', w.id],
      queryFn: () => getWorkflow(w.id),
      enabled: Boolean(requisitionId),
    })),
  });

  const options: RoundOption[] = [];
  const seen = new Set<string>();
  relevant.forEach((w, i) => {
    const wf = workflowQueries[i]?.data;
    if (!wf) return;
    for (const r of wf.rounds) {
      if (!(HUMAN_EVALUATED_KINDS as readonly string[]).includes(r.kind)) continue;
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      options.push({
        id: r.id,
        title: r.title,
        workflowVersion: w.version,
        archived: w.status === 'archived',
      });
    }
  });
  return {
    options,
    isLoading: workflows.isLoading || workflowQueries.some((q) => q.isLoading),
    hiddenArchivedCount,
  };
}

function CriteriaTable({
  criteria,
  minCandidates,
}: {
  criteria: CalibrationCriterion[];
  minCandidates: number;
}): JSX.Element | null {
  if (criteria.length === 0) return null;
  return (
    <GlassCard className="p-4">
      <h3 className="text-[14px] font-semibold text-foreground">Criteria</h3>
      <p className="mt-1 text-[12px] text-muted-foreground">
        The panel&apos;s own distribution on each frozen criterion — never an interviewer&apos;s
        figure.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-left text-[12.5px]">
          <thead>
            <tr className="border-b border-border">
              <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">Criterion</th>
              <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">Round</th>
              <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">Distribution (1–5)</th>
              <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">Mean</th>
              <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">Disagreement</th>
            </tr>
          </thead>
          <tbody>
            {criteria.map((c) => (
              <tr key={c.criterion_key} className="border-b border-border last:border-0 align-top">
                <td className="py-2 pr-3 text-foreground">{c.competency_name}</td>
                <td className="py-2 pr-3 text-muted-foreground">
                  {c.round_title ?? '—'}
                  {c.workflow_version ? ` (v${c.workflow_version})` : ''}
                </td>
                {c.suppressed ? (
                  <td colSpan={3} className="py-2 pr-3 text-[var(--ui-faint)]">
                    too few to compare ({c.candidates} of the {minCandidates} needed)
                  </td>
                ) : (
                  <>
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2 text-[11.5px] text-[var(--ui-soft)]">
                        {(['1', '2', '3', '4', '5'] as const).map((k) => (
                          <span key={k}>
                            {k}: {c.distribution?.[k] ?? 0}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-2 pr-3 text-foreground">{c.mean ?? '—'}</td>
                    <td className="py-2 pr-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-foreground">{c.disagreement ?? '—'}</span>
                        {c.signal === 'wide_disagreement' ? (
                          <StatusTag tone="amber">wide disagreement</StatusTag>
                        ) : null}
                      </div>
                      {disagreementSentence(c) ? (
                        <p className="mt-1 max-w-[260px] text-[11px] leading-relaxed text-muted-foreground">
                          {disagreementSentence(c)}
                        </p>
                      ) : null}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}

function CalibrationRowCard({
  row,
  minCandidates,
  criterionLabels,
  onShowJudgements,
}: {
  row: CalibrationRow;
  minCandidates: number;
  criterionLabels: Record<string, string>;
  onShowJudgements: (criterionKey?: string) => void;
}) {
  if (row.suppressed) {
    return (
      <GlassCard className="p-4">
        <p className="text-[14px] font-medium text-foreground">{row.name}</p>
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">
          too few candidates to compare ({row.candidates} of the {minCandidates} needed)
        </p>
      </GlassCard>
    );
  }

  const sentence = flagSentence(row);
  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-[14px] font-medium text-foreground">{row.name}</p>
        <div className="flex items-center gap-2">
          {row.flag ? (
            <StatusTag tone={row.flag === 'higher' ? 'amber' : 'lavender'}>
              {row.flag === 'higher' ? 'Scores higher' : 'Scores lower'}
            </StatusTag>
          ) : null}
          <button
            type="button"
            onClick={() => onShowJudgements(undefined)}
            className="text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
          >
            Show judgements
          </button>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-4">
        <div>
          <dt className="text-[var(--ui-faint)]">Scorecards</dt>
          <dd className="text-foreground">{row.scorecards}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Average of everything scored</dt>
          <dd className="text-foreground">{row.mean ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">Shared judgements</dt>
          <dd className="text-foreground">{row.pairs}</dd>
        </div>
        <div>
          <dt className="text-[var(--ui-faint)]">vs panel (paired)</dt>
          <dd className="text-foreground">
            {row.mean_delta === null ? '—' : signed(row.mean_delta)}
          </dd>
        </div>
      </dl>

      {row.same_direction_share !== null ? (
        <p className="mt-2 text-[12px] text-muted-foreground">
          Same direction in {Math.round(row.same_direction_share * 100)}% of shared judgements.
        </p>
      ) : null}

      {row.not_assessed_rate !== null ? (
        <p className="mt-1 text-[12px] text-muted-foreground">
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

      {row.by_criterion.length > 0 ? (
        <div className="mt-3 border-t border-border pt-2.5">
          <p className="text-[11px] uppercase tracking-wide text-[var(--ui-faint)]">
            Per criterion
          </p>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {row.by_criterion.map((g) => {
              const label = criterionLabels[g.criterion_key] ?? g.criterion_key;
              return (
                <li
                  key={g.criterion_key}
                  className="flex items-start justify-between gap-3 text-[12px]"
                >
                  <span className="text-[var(--ui-soft)]">
                    {g.signal
                      ? criterionGapSentence(label, g.signal, g.gap, g.shared_judgements)
                      : `${label}: ${signed(g.gap)} across ${g.shared_judgements} shared judgements`}
                  </span>
                  <button
                    type="button"
                    onClick={() => onShowJudgements(g.criterion_key)}
                    className="shrink-0 text-[11.5px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
                  >
                    Show
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </GlassCard>
  );
}

export default function CalibrationTab(): JSX.Element {
  const [period, setPeriod] = useState<Period>(90);
  const [requisitionId, setRequisitionId] = useState('');
  const [roundId, setRoundId] = useState('');
  const [showInfo, setShowInfo] = useState(false);
  const [judgementsTarget, setJudgementsTarget] = useState<{
    interviewerId: string;
    interviewerName: string;
    criterionKey?: string;
  } | null>(null);
  // Memoized on `period` alone — see WorkloadTab's identical fix: recomputing
  // this on every render put a fresh millisecond in the query key each time,
  // which defeated caching and refetched in a tight loop.
  const { start, end } = useMemo(() => periodRange(period), [period]);

  const openings = useQuery({
    queryKey: ['hr', 'requisitions'],
    queryFn: () => listRequisitions(),
  });
  const { options: rounds, hiddenArchivedCount } = useRoundOptions(requisitionId);

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

  const data: CalibrationResponse | undefined = calibration.data;
  const criterionLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const c of data?.criteria ?? []) {
      const parts = [c.competency_name];
      if (c.round_title) parts.push(c.round_title);
      map[c.criterion_key] = parts.join(' · ');
    }
    return map;
  }, [data]);

  const sortedInterviewers = useMemo(
    () => [...(data?.interviewers ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [data],
  );

  return (
    <div>
      <GlassCard className="p-4">
        <p className="text-[13px] font-medium text-foreground">
          These are patterns in scoring, not assessments of any interviewer. Differences can have
          good reasons. Use them to start a calibration conversation. Nothing here changes a
          scorecard or a decision.
        </p>
        <p className="mt-1 text-[12px] text-muted-foreground">
          {data
            ? `Interviewers are flagged at ±${data.rules.meaningful_delta} over at least ${data.rules.min_pairs} shared judgements, pointing the same way at least ${Math.round(data.rules.min_same_direction_share * 100)}% of the time.`
            : 'Interviewers are flagged only on a large, repeated difference from the rest of the panel.'}
        </p>
        <p className="mt-1 text-[12px] text-muted-foreground">
          Candidates you still have to score are left out until you submit your own scorecard for
          them.
        </p>
        {data ? (
          <button
            type="button"
            onClick={() => setShowInfo(true)}
            className="mt-2 text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
          >
            How is this calculated?
          </button>
        ) : null}
      </GlassCard>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label
            htmlFor="calib-period"
            className="block text-[12px] font-medium text-[var(--ui-soft)]"
          >
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
          <label
            htmlFor="calib-opening"
            className="block text-[12px] font-medium text-[var(--ui-soft)]"
          >
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
          <label
            htmlFor="calib-round"
            className="block text-[12px] font-medium text-[var(--ui-soft)]"
          >
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
                {roundOptionLabel(r)}
              </option>
            ))}
          </select>
          {hiddenArchivedCount > 0 ? (
            <p className="mt-1 text-[11px] text-[var(--ui-faint)]">
              {hiddenArchivedCount} older archived{' '}
              {hiddenArchivedCount === 1 ? 'version is' : 'versions are'} not listed.
            </p>
          ) : null}
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
      ) : (data?.interviewers.length ?? 0) === 0 ? (
        <p className="mt-4 text-[13px] text-muted-foreground">
          No submitted scorecards to compare in this period.
        </p>
      ) : (
        <>
          <div className="mt-4">
            <CriteriaTable
              criteria={data?.criteria ?? []}
              minCandidates={data?.rules.min_candidates ?? 5}
            />
          </div>

          <div className="mt-4 flex flex-col gap-3">
            {sortedInterviewers.map((row) => (
              <CalibrationRowCard
                key={row.user_id}
                row={row}
                minCandidates={data?.rules.min_candidates ?? 5}
                criterionLabels={criterionLabels}
                onShowJudgements={(criterionKey) =>
                  setJudgementsTarget({
                    interviewerId: row.user_id,
                    interviewerName: row.name,
                    criterionKey,
                  })
                }
              />
            ))}
          </div>
        </>
      )}

      {showInfo && data ? (
        <CalibrationInfoDialog calibration={data} onClose={() => setShowInfo(false)} />
      ) : null}

      {judgementsTarget ? (
        <CalibrationJudgementsDialog
          interviewerId={judgementsTarget.interviewerId}
          interviewerName={judgementsTarget.interviewerName}
          start={start}
          end={end}
          requisitionId={requisitionId || null}
          roundId={roundId || null}
          criterionKey={judgementsTarget.criterionKey ?? null}
          onClose={() => setJudgementsTarget(null)}
        />
      ) : null}
    </div>
  );
}
