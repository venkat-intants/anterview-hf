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
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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

/** Every calibration figure is printed to one decimal place, the same as the
 *  sentences beside it (IV-L13): the table used to show 3 decimals, the gap 2
 *  and the sentences 1, so one number appeared three ways on one card. */
function oneDp(n: number | null | undefined): string {
  return n === null || n === undefined ? '-' : n.toFixed(1);
}

function signed(n: number): string {
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`;
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

/** The criteria table keeps its compact rhythm inside the card. */
const HEAD = 'h-auto px-0 py-2 pr-3 font-medium text-muted-foreground';
const CELL = 'px-0 py-2 pr-3';

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
        The panel&apos;s own distribution on each frozen criterion - never an interviewer&apos;s
        figure.
      </p>
      <div className="mt-3">
        <Table className="min-w-[640px] border-collapse text-left text-[12.5px]">
          <TableHeader>
            <TableRow className="border-border hover:bg-transparent">
              <TableHead scope="col" className={HEAD}>Criterion</TableHead>
              <TableHead scope="col" className={HEAD}>Round</TableHead>
              <TableHead scope="col" className={HEAD}>Distribution (1-5)</TableHead>
              <TableHead scope="col" className={HEAD}>Mean</TableHead>
              <TableHead scope="col" className={HEAD}>Disagreement</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {criteria.map((c) => (
              <TableRow key={c.criterion_key} className="border-border align-top hover:bg-transparent">
                <TableCell className={`${CELL} text-foreground`}>{c.competency_name}</TableCell>
                <TableCell className={`${CELL} text-muted-foreground`}>
                  {c.round_title ?? '-'}
                  {c.workflow_version ? ` (v${c.workflow_version})` : ''}
                </TableCell>
                {c.suppressed ? (
                  <TableCell colSpan={3} className={`${CELL} text-muted-foreground`}>
                    too few to compare ({c.candidates} of the {minCandidates} needed)
                  </TableCell>
                ) : (
                  <>
                    <TableCell className={CELL}>
                      <div className="flex items-center gap-2 text-[12px] text-[var(--ui-soft)]">
                        {(['1', '2', '3', '4', '5'] as const).map((k) => (
                          <span key={k}>
                            {k}: {c.distribution?.[k] ?? 0}
                          </span>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className={`${CELL} text-foreground`}>{oneDp(c.mean)}</TableCell>
                    <TableCell className={CELL}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-foreground">{oneDp(c.disagreement)}</span>
                        {c.signal === 'wide_disagreement' ? (
                          <StatusTag tone="amber">wide disagreement</StatusTag>
                        ) : null}
                      </div>
                      {disagreementSentence(c) ? (
                        <p className="mt-1 max-w-[260px] text-[12px] leading-relaxed text-muted-foreground">
                          {disagreementSentence(c)}
                        </p>
                      ) : null}
                    </TableCell>
                  </>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </GlassCard>
  );
}

/** A text action sitting in running copy: the link button without its box. */
const TEXT_LINK = 'h-auto px-0 py-0 text-[12px] font-normal';

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
          <Button
            type="button"
            variant="link"
            size="sm"
            onClick={() => onShowJudgements(undefined)}
            className={TEXT_LINK}
          >
            Show judgements
          </Button>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12.5px] sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Scorecards</dt>
          <dd className="text-foreground">{row.scorecards}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Average of everything scored</dt>
          <dd className="text-foreground">{oneDp(row.mean)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Shared judgements</dt>
          <dd className="text-foreground">{row.pairs}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">vs panel (paired)</dt>
          <dd className="text-foreground">
            {row.mean_delta === null ? '-' : signed(row.mean_delta)}
          </dd>
        </div>
      </dl>

      {/* Only when the flag sentence above does not already say it. */}
      {row.same_direction_share !== null && !sentence ? (
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
        <div className="mt-2.5 flex items-center gap-3 text-[12px] text-[var(--ui-soft)]">
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
          <p className="text-[12px] uppercase tracking-wide text-muted-foreground">Per criterion</p>
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
                  <Button
                    type="button"
                    variant="link"
                    size="sm"
                    onClick={() => onShowJudgements(g.criterion_key)}
                    className={`shrink-0 ${TEXT_LINK}`}
                  >
                    Show
                  </Button>
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
          <Button
            type="button"
            variant="link"
            size="sm"
            onClick={() => setShowInfo(true)}
            className={`mt-2 ${TEXT_LINK}`}
          >
            How is this calculated?
          </Button>
        ) : null}
      </GlassCard>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <FormField id="calib-period" label="Period" className="min-w-[180px]">
          {(p) => (
            <SelectControl
              {...p}
              size="sm"
              value={period}
              onChange={(e) => setPeriod(Number(e.target.value) as Period)}
            >
              {PERIODS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <div className="min-w-[200px]">
          <FormField id="calib-opening" label="Opening">
            {(p) => (
              <SelectControl
                {...p}
                size="sm"
                value={requisitionId}
                onChange={(e) => {
                  setRequisitionId(e.target.value);
                  setRoundId('');
                }}
              >
                <option value="">All openings</option>
                {(openings.data ?? []).map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title}
                  </option>
                ))}
              </SelectControl>
            )}
          </FormField>
          {openings.isError ? (
            // Without this a failed list reads as "you have no openings"
            // (IV-L10); "All openings" still works.
            <p className="mt-1 text-[12px] text-[var(--ui-danger)]" role="alert">
              Could not load openings.{' '}
              <button
                type="button"
                onClick={() => void openings.refetch()}
                className="underline hover:no-underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                Try again
              </button>
            </p>
          ) : null}
        </div>
        <div className="min-w-[200px]">
          <FormField id="calib-round" label="Round">
            {(p) => (
              <SelectControl
                {...p}
                size="sm"
                value={roundId}
                onChange={(e) => setRoundId(e.target.value)}
                disabled={!requisitionId}
              >
                <option value="">All rounds</option>
                {rounds.map((r) => (
                  <option key={r.id} value={r.id}>
                    {roundOptionLabel(r)}
                  </option>
                ))}
              </SelectControl>
            )}
          </FormField>
          {hiddenArchivedCount > 0 ? (
            <p className="mt-1 text-[12px] text-muted-foreground">
              {hiddenArchivedCount} older archived{' '}
              {hiddenArchivedCount === 1 ? 'version is' : 'versions are'} not listed.
            </p>
          ) : null}
        </div>
      </div>

      {calibration.isLoading ? (
        <div className="mt-4 flex flex-col gap-3" aria-busy="true" aria-label="Loading calibration">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
        </div>
      ) : calibration.isError && !calibration.data ? (
        <QueryErrorPanel
          className="mt-4"
          title="Could not load calibration."
          error={calibration.error}
          onRetry={() => void calibration.refetch()}
          retrying={calibration.isFetching}
        />
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
