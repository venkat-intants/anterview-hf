// DryRunPanel — PH4-O2. Walks synthetic candidates through the routing logic
// that will actually run (route_after_result), and checks every round for
// what would stop it running or deserves a second look.
//
// A passing dry run publishes nothing — it writes only its own result. Said
// here explicitly, because "Run" buttons on a hiring product default to
// reading as consequential.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Loader2,
  XCircle,
} from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { cn } from '@/lib/utils';
import { toast } from '@/lib/toast';
import {
  getSimulation,
  runSimulation,
  type SimulationResult,
  type SimulationRound,
  type SimulationScenario,
} from '@/api/workflowReview';

const STATUS_META: Record<SimulationResult['status'], { label: string; tone: 'forest' | 'amber' | 'ember' }> = {
  passed: { label: 'Passed', tone: 'forest' },
  warnings: { label: 'Needs attention', tone: 'amber' },
  failed: { label: 'Failed', tone: 'ember' },
};

function RoundStateIcon({ state }: { state: SimulationRound['state'] }) {
  if (state === 'ok') {
    return <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--ui-ok)]" aria-hidden="true" />;
  }
  if (state === 'warning') {
    return <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />;
  }
  return <XCircle className="h-4 w-4 shrink-0 text-[var(--ui-danger)]" aria-hidden="true" />;
}

const STATE_WORD: Record<SimulationRound['state'], string> = {
  ok: 'OK',
  warning: 'Warning',
  error: 'Error',
};

function RoundRow({ round }: { round: SimulationRound }) {
  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex items-center gap-2">
        <RoundStateIcon state={round.state} />
        <span className="text-[13px] font-medium text-foreground">
          {round.position + 1}. {round.title}
        </span>
        {/* The word, not only the icon — a colour-blind reviewer must not have
            to guess what a check mark and a triangle disagree about. */}
        <span className="text-[11px] text-muted-foreground">{STATE_WORD[round.state]}</span>
      </div>
      {round.findings.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1 pl-6">
          {round.findings.map((f, i) => (
            <li
              key={i}
              className={cn(
                'flex items-start gap-1.5 text-[12px]',
                f.severity === 'error' ? 'text-[var(--ui-danger)]' : 'text-[var(--ui-warn)]',
              )}
            >
              <span className="mt-0.5 shrink-0 text-[10px] font-semibold uppercase tracking-wide">
                {f.severity}
              </span>
              {f.message}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

const OUTCOME_WORD: Record<string, string> = {
  pass: 'passes',
  fast_track: 'fast-tracks',
  fail: 'falls below the threshold',
};

function ScenarioRow({ scenario }: { scenario: SimulationScenario }) {
  const [open, setOpen] = useState(false);
  const endLabel =
    scenario.end === 'decision'
      ? 'reaches a decision'
      : scenario.end === 'held'
        ? 'is held for you'
        : 'could not finish';
  return (
    <li className="rounded-[10px] border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="min-w-0 truncate text-[12.5px] text-foreground">
          {scenario.id} — {scenario.description}
        </span>
        <span className="flex shrink-0 items-center gap-2 text-[11.5px] text-muted-foreground">
          {endLabel}
          <ChevronDown
            className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')}
            aria-hidden="true"
          />
        </span>
      </button>
      {open ? (
        <ol className="flex flex-col gap-1.5 border-t border-border p-3">
          {scenario.steps.map((s, i) => (
            <li key={i} className="text-[12px] text-[var(--ui-soft)]">
              <span className="text-foreground">{s.round_title ?? s.round_id}</span>
              {s.outcome ? ` ${OUTCOME_WORD[s.outcome] ?? s.outcome}` : ''}
              {s.next ? (
                <span className="text-[var(--ui-faint)]">
                  {' '}
                  &rarr;{' '}
                  {s.next.kind === 'round'
                    ? (s.next.round_title ?? 'another round')
                    : s.next.kind === 'complete'
                      ? 'final decision'
                      : 'held for a person'}
                </span>
              ) : null}
              {s.error ? <span className="text-[var(--ui-danger)]"> — {s.error}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}

export default function DryRunPanel({ workflowId }: { workflowId: string }): JSX.Element {
  const qc = useQueryClient();
  const [scenariosOpen, setScenariosOpen] = useState(false);

  const latest = useQuery({
    queryKey: ['hr', 'workflow', workflowId, 'simulation'],
    queryFn: () => getSimulation(workflowId),
  });

  const runMut = useMutation({
    mutationFn: () => runSimulation(workflowId),
    onSuccess: (result) => {
      qc.setQueryData(['hr', 'workflow', workflowId, 'simulation'], result);
      toast.success(
        result.status === 'passed'
          ? 'Dry run passed'
          : result.status === 'warnings'
            ? 'Dry run passed, with warnings to look at'
            : 'Dry run found errors',
      );
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not run the dry run'),
  });

  const result = latest.data;

  return (
    <div className="flex flex-col gap-3" data-testid="dry-run-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-[13px] font-medium text-foreground">Dry run</h3>
          <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted-foreground">
            Walks synthetic candidates through this version&rsquo;s actual routing. A passing
            dry run publishes nothing.
          </p>
        </div>
        <button
          type="button"
          onClick={() => runMut.mutate()}
          disabled={runMut.isPending}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-[10px] bg-primary px-3.5 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
        >
          {runMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
          {result ? 'Run again' : 'Run dry run'}
        </button>
      </div>

      {latest.isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading the last dry run…</p>
      ) : latest.isError ? (
        // Not "never run": a failed read says nothing about whether one exists.
        <p role="alert" className="text-[12px] text-[var(--ui-danger)]">
          Could not load the last dry run. Try again, or run a new one.
        </p>
      ) : !result ? (
        <p className="text-[12px] text-muted-foreground">Not run yet for this version.</p>
      ) : (
        <>
          {result.stale ? (
            <p
              role="status"
              className="flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn)]/[0.06] px-3 py-2 text-[12px] text-[var(--ui-soft)]"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
              Stale — the workflow changed since this run.
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <StatusTag tone={STATUS_META[result.status].tone} dot>
              {STATUS_META[result.status].label}
            </StatusTag>
            <span className="text-[12px] text-muted-foreground">
              {result.errors} error{result.errors === 1 ? '' : 's'} &middot; {result.warnings}{' '}
              warning{result.warnings === 1 ? '' : 's'}
            </span>
            {result.run_by_name ? (
              <span className="text-[11.5px] text-[var(--ui-faint)]">by {result.run_by_name}</span>
            ) : null}
          </div>

          {result.workflow_findings.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {result.workflow_findings.map((f, i) => (
                <li
                  key={i}
                  className={cn(
                    'flex items-start gap-1.5 text-[12px]',
                    f.severity === 'error' ? 'text-[var(--ui-danger)]' : 'text-[var(--ui-warn)]',
                  )}
                >
                  <span className="mt-0.5 shrink-0 text-[10px] font-semibold uppercase tracking-wide">
                    {f.severity}
                  </span>
                  {f.message}
                </li>
              ))}
            </ul>
          ) : null}

          <ul className="flex flex-col gap-2">
            {result.rounds.map((r) => (
              <RoundRow key={r.round_id} round={r} />
            ))}
          </ul>

          <div>
            <button
              type="button"
              onClick={() => setScenariosOpen((o) => !o)}
              aria-expanded={scenariosOpen}
              className="flex items-center gap-1.5 text-[12.5px] text-[var(--accent)] hover:underline"
            >
              <ChevronDown
                className={cn('h-3.5 w-3.5 transition-transform', scenariosOpen && 'rotate-180')}
                aria-hidden="true"
              />
              {scenariosOpen ? 'Hide' : 'Show'} {result.scenarios.length} scenario
              {result.scenarios.length === 1 ? '' : 's'}
            </button>
            {scenariosOpen ? (
              <ol className="mt-2 flex flex-col gap-2">
                {result.scenarios.map((s) => (
                  <ScenarioRow key={s.id} scenario={s} />
                ))}
              </ol>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
