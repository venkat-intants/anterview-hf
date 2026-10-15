// WorkflowReviews — PH4-O6, decision D4-2. The company super admin's queue of
// workflow versions waiting for approval, and one version in full.
//
// Two views, one file: `/superadmin/workflow-reviews` (the queue) and
// `/superadmin/workflow-reviews/:workflowId` (one version, with everything a
// reviewer needs to decide — rounds, branches, criteria, the validation
// report, the dry run, stage owners/SLAs, and the history). The notification
// the backend sends when a version is submitted links straight to the detail
// route.
//
// Same account boundary as ApprovalQueue: only a super admin can reach these
// endpoints, and a version cannot be approved by the person who wrote it —
// enforced server-side, surfaced here as a 403 if it is ever reached anyway.

import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, ChevronDown, XCircle } from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { Breadcrumbs, DataList, Page, PageHeader } from '@/design/components/page';
import { Section, Well } from '@/design/components/rows';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { cn } from '@/lib/utils';
import { ROUND_KIND_META } from '@/components/workflow/roundKinds';
import type { RoundKind } from '@/api/workflows';
import { toast } from '@/lib/toast';
import {
  DecisionPanel,
  NothingToDecide,
  ReviewDetailError,
  ReviewDetailLayout,
  ReviewDetailLoading,
  ReviewQueueItem,
  ReviewQueueState,
  ReviewTrack,
  WhatChanges,
  type DecisionMode,
} from '@/components/superadmin/ReviewParts';
import { waitingText } from '@/components/superadmin/waitingText';
import {
  approveWorkflowReview,
  getWorkflowReviewDetail,
  listPendingWorkflowReviews,
  requestWorkflowChanges,
  type ReviewAction,
  type SimulationResult,
  type SimulationRound,
  type WorkflowReviewDetail,
} from '@/api/workflowReview';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const CHANGES_NOTE_MIN = 10;

/* ── The queue ──────────────────────────────────────────────────────────── */

function ReviewQueue(): JSX.Element {
  const queue = useQuery({
    queryKey: ['admin', 'workflow-reviews'],
    queryFn: listPendingWorkflowReviews,
  });
  const rows = queue.data ?? [];

  return (
    <Page width="form">
      <PageHeader
        title="Workflow reviews"
        description="Hiring workflows your HR managers have submitted for approval before they can go live. Oldest request first."
      />
      <ReviewQueueState
        loading={queue.isLoading}
        error={queue.isError}
        errorText="Could not load the workflow review queue."
        onRetry={() => void queue.refetch()}
        count={rows.length}
        emptyDescription="A workflow your HR managers submit for review appears here."
      >
        {rows.map((row) => (
          <ReviewQueueItem
            key={row.workflow_id}
            to={`/superadmin/workflow-reviews/${row.workflow_id}`}
            title={`${row.opening_title} · v${row.version}`}
            summary={`${row.rounds} round${row.rounds === 1 ? '' : 's'}`}
            submittedBy={row.submitted_by_name}
            waiting={waitingText(row.submitted_at)}
            note={row.note}
            cta="Review this version"
          />
        ))}
      </ReviewQueueState>
    </Page>
  );
}

/* ── The dry run, read-only (the version's own submitted result) ─────────── */

const STATE_WORD: Record<SimulationRound['state'], string> = {
  ok: 'OK',
  warning: 'Warning',
  error: 'Error',
};

function SimulationReadout({ simulation }: { simulation: SimulationResult | null }): JSX.Element {
  const [scenariosOpen, setScenariosOpen] = useState(false);
  if (!simulation) {
    return (
      <p className="text-[12.5px] text-muted-foreground">
        No dry run has been recorded for this version.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {simulation.stale ? (
        <Well role="status" className="flex items-center gap-1.5 py-2 text-[12px] text-[var(--ui-soft)]">
          <AlertTriangle
            className="h-3.5 w-3.5 shrink-0 text-[var(--ui-warn)]"
            aria-hidden="true"
          />
          Stale — the workflow changed since this run.
        </Well>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground">
        <StatusTag
          tone={
            simulation.status === 'passed'
              ? 'forest'
              : simulation.status === 'warnings'
                ? 'amber'
                : 'ember'
          }
          dot
        >
          {simulation.status === 'passed'
            ? 'Passed'
            : simulation.status === 'warnings'
              ? 'Needs attention'
              : 'Failed'}
        </StatusTag>
        <span>
          {simulation.errors} error{simulation.errors === 1 ? '' : 's'} &middot;{' '}
          {simulation.warnings} warning
          {simulation.warnings === 1 ? '' : 's'}
        </span>
        {simulation.run_by_name ? <span>by {simulation.run_by_name}</span> : null}
      </div>
      <ul className="divide-y divide-border">
        {simulation.rounds.map((r) => (
          <li key={r.round_id} className="py-2.5 first:pt-0 last:pb-0">
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className="font-medium text-foreground">
                {r.position + 1}. {r.title}
              </span>
              <span className="text-muted-foreground">{STATE_WORD[r.state]}</span>
            </div>
            {r.findings.length > 0 ? (
              <ul className="mt-1.5 flex flex-col gap-1 pl-2">
                {r.findings.map((f, i) => (
                  <li
                    key={i}
                    className={cn(
                      'text-[12px]',
                      f.severity === 'error' ? 'text-[var(--ui-danger)]' : 'text-[var(--ui-warn)]',
                    )}
                  >
                    <span className="mr-1 text-[12px] font-semibold uppercase">{f.severity}</span>
                    {f.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
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
          {scenariosOpen ? 'Hide' : 'Show'} {simulation.scenarios.length} scenario
          {simulation.scenarios.length === 1 ? '' : 's'}
        </button>
        {scenariosOpen ? (
          <ol className="mt-2 divide-y divide-border">
            {simulation.scenarios.map((s) => (
              <li key={s.id} className="py-2 text-[12px] text-[var(--ui-soft)]">
                <span className="font-medium text-foreground">{s.id}</span> — {s.description} —{' '}
                {s.end === 'decision'
                  ? 'reaches a decision'
                  : s.end === 'held'
                    ? 'is held'
                    : s.end === 'waiting'
                      ? 'waits for a person to move them on'
                      : 'could not finish'}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </div>
  );
}

/* ── One version, in full ──────────────────────────────────────────────── */

const ACTION_WORDS: Record<ReviewAction, string> = {
  submitted: 'Submitted for review',
  withdrawn: 'Withdrawn from review',
  approved: 'Approved',
  changes_requested: 'Changes requested',
  reopened: 'Reopened for edits',
};

function ReviewDetail({ workflowId }: { workflowId: string }): JSX.Element {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<DecisionMode>(null);
  const historyKey = useScopedPersistKey('workflow-review-history');

  const detail = useQuery({
    queryKey: ['admin', 'workflow-reviews', workflowId],
    queryFn: () => getWorkflowReviewDetail(workflowId),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['admin', 'workflow-reviews', workflowId] });
    void qc.invalidateQueries({ queryKey: ['admin', 'workflow-reviews'] });
  };

  const approveMut = useMutation({
    // The fingerprint of the version on screen, so the server can refuse an
    // approval of something that changed after this page loaded.
    mutationFn: () =>
      approveWorkflowReview(workflowId, note, detail.data?.review_fingerprint ?? null),
    onSuccess: () => {
      setMode(null);
      setNote('');
      invalidate();
      toast.success('Approved — HR can publish it now');
    },
    onError: (e) => toast.error(errText(e, 'Could not approve this version')),
  });

  const changesMut = useMutation({
    mutationFn: () => requestWorkflowChanges(workflowId, note),
    onSuccess: () => {
      setMode(null);
      setNote('');
      invalidate();
      toast.success('Sent back with your note');
    },
    onError: (e) => toast.error(errText(e, 'Could not send this back')),
  });

  const crumbs = (
    <Breadcrumbs
      items={[
        { label: 'Workflow reviews', to: '/superadmin/workflow-reviews' },
        { label: detail.data ? `Review v${detail.data.version}` : 'Review version' },
      ]}
    />
  );

  if (detail.isLoading) {
    return (
      <Page width="default">
        {crumbs}
        <ReviewDetailLoading />
      </Page>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <Page width="default">
        {crumbs}
        <ReviewDetailError
          text="Could not load this workflow version."
          backTo="/superadmin/workflow-reviews"
          backLabel="Back to workflow reviews"
        />
      </Page>
    );
  }

  const wf: WorkflowReviewDetail = detail.data;
  const canDecide = wf.review.review_status === 'in_review';
  const newestEntry = wf.review.history.reduce<(typeof wf.review.history)[number] | null>(
    (n, h) => (n === null || h.at > n.at ? h : n),
    null,
  );

  return (
    <ReviewDetailLayout
      width="default"
      breadcrumbs={crumbs}
      title={`${wf.opening_title} · v${wf.version}`}
      description={`${wf.rounds.length} round${wf.rounds.length === 1 ? '' : 's'} to review before this version can go live.`}
      whatChanges={
        canDecide ? (
          <WhatChanges>
            approving lets HR publish this version, and every candidate in this opening is routed
            by the rounds below
            {shortlistSettings(wf.settings).auto
              ? ' — including applicants above the ATS bar, who will start round 1 without anyone confirming them'
              : ''}
            ; sending it back returns it to HR as a draft to edit.
          </WhatChanges>
        ) : undefined
      }
      track={
        <ReviewTrack
          label="Workflow review progress"
          submittedLabel={`Version ${wf.version} submitted`}
          submittedBy={wf.review.submitted_by_name ?? 'someone no longer with the company'}
          submittedAt={wf.review.submitted_at}
          waiting={waitingText(wf.review.submitted_at)}
          pending={canDecide}
          afterLabel="HR publishes it"
        />
      }
      evidence={
        <>
          <Section title="Submission">
            <DataList
              items={[
                { label: 'Opening', value: wf.opening_title },
                { label: 'Version', value: `v${wf.version}` },
                { label: 'Note from HR', value: wf.review.note ?? undefined },
              ]}
            />
          </Section>

          <ShortlistingSection settings={wf.settings} />

          <Section
            title="Rounds & routing"
            description="Each round, who advances, and where everyone else goes."
          >
            <ol className="divide-y divide-border">
              {wf.rounds.map((r) => (
                <li key={r.round_id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-medium text-foreground">
                      {r.position + 1}. {r.title}
                    </span>
                    <StatusTag tone="neutral">{kindLabel(r.kind)}</StatusTag>
                    {r.pass_threshold !== null ? (
                      <span className="text-[12px] text-muted-foreground">
                        advance at {r.pass_threshold}%
                      </span>
                    ) : null}
                  </div>
                  {r.criteria.length > 0 ? (
                    <p className="mt-1.5 text-[12px] text-[var(--ui-soft)]">
                      Assesses: {r.criteria.map((c) => c.name).join(', ')}
                    </p>
                  ) : null}
                  {/* PH4-D4 — what goes live is what the approver approves, the
                      same reason exam content is frozen for review. */}
                  {r.task ? (
                    <Well className="mt-2 p-2.5">
                      <p className="whitespace-pre-wrap text-[12px] text-[var(--ui-soft)]">
                        {r.task.brief}
                      </p>
                      {r.task.items.length > 0 ? (
                        <ul className="mt-1.5 flex flex-col gap-1">
                          {r.task.items.map((it) => (
                            <li key={it.key} className="text-[12px] text-muted-foreground">
                              {it.prompt}{' '}
                              <span className="text-[var(--ui-faint)]">
                                ({it.response_type}
                                {it.required ? ', required' : ''})
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {r.task.kind === 'portfolio' ? (
                        <p className="mt-1.5 text-[12px] text-muted-foreground">
                          {r.task.min_artifacts}–{r.task.max_artifacts} artifacts &middot;{' '}
                          {r.task.allow_files && r.task.allow_links
                            ? 'files or links'
                            : r.task.allow_files
                              ? 'files only'
                              : 'links only'}
                        </p>
                      ) : null}
                    </Well>
                  ) : null}
                  <div className="mt-1.5 flex flex-col gap-0.5 text-[12px] text-muted-foreground">
                    <span>
                      If they pass &rarr;{' '}
                      <span className="text-foreground">{r.on_pass ?? 'Final decision'}</span>
                    </span>
                    <span>
                      If below the threshold &rarr;{' '}
                      <span className="text-foreground">{r.on_fail ?? 'Hold for a person'}</span>
                    </span>
                    {r.on_fast_track ? (
                      <span>
                        Fast-track at {r.fast_track_min_percent}% &rarr;{' '}
                        <span className="text-foreground">{r.on_fast_track}</span>
                      </span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </Section>

          <Section title="Validation">
            {wf.validation.errors.length === 0 && wf.validation.warnings.length === 0 ? (
              <p className="flex items-center gap-1.5 text-[12.5px] text-[var(--ui-ok)]">
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                Nothing to fix.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {wf.validation.errors.map((e) => (
                  <li
                    key={e}
                    className="flex items-start gap-1.5 text-[12.5px] text-[var(--ui-danger)]"
                  >
                    <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    {e}
                  </li>
                ))}
                {wf.validation.warnings.map((w) => (
                  <li
                    key={w}
                    className="flex items-start gap-1.5 text-[12.5px] text-[var(--ui-warn)]"
                  >
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    {w}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Dry run" description="The result HR saw when they submitted this version.">
            <SimulationReadout simulation={wf.simulation} />
          </Section>

          <Section title="Stage owners & SLAs">
            {wf.stages.length === 0 ? (
              <p className="text-[12.5px] text-muted-foreground">No stages recorded.</p>
            ) : (
              <ul className="divide-y divide-border">
                {wf.stages.map((s) => (
                  <li
                    key={s.round_id ?? '__decision__'}
                    className="flex flex-wrap items-center justify-between gap-2 py-2 text-[12.5px] first:pt-0 last:pb-0"
                  >
                    <span className="text-foreground">{s.stage}</span>
                    <span className="text-muted-foreground">
                      {s.owner_name ?? 'Unassigned'}
                      {s.sla_hours ? ` · ${s.sla_hours}h SLA` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Collapsible
            variant="card"
            title="History"
            count={wf.review.history.length}
            noun="entry"
            nounPlural="entries"
            latest={
              newestEntry
                ? `${ACTION_WORDS[newestEntry.action]}${newestEntry.actor_name ? ` by ${newestEntry.actor_name}` : ''}, ${new Date(newestEntry.at).toLocaleDateString()}`
                : undefined
            }
            persistKey={historyKey}
            data-testid="workflow-review-history"
          >
            {wf.review.history.length === 0 ? (
              <p className="text-[12.5px] text-muted-foreground">No activity recorded yet.</p>
            ) : (
              <ProgressiveList
                as="ol"
                items={wf.review.history}
                initial={5}
                noun="entries"
                getKey={(h, i) => `${h.at}-${i}`}
                className="flex flex-col gap-1.5 border-l border-border pl-3"
                renderItem={(h) => (
                  <li className="text-[12.5px] text-[var(--ui-soft)]">
                    {ACTION_WORDS[h.action]}
                    {h.actor_name ? ` — ${h.actor_name}` : ''}
                    <span className="ml-1.5 text-[12px] text-[var(--ui-faint)]">
                      {new Date(h.at).toLocaleString()}
                    </span>
                    {h.note ? (
                      <span className="block text-[12px] text-muted-foreground">{h.note}</span>
                    ) : null}
                  </li>
                )}
              />
            )}
          </Collapsible>
        </>
      }
      decision={
        canDecide ? (
          <DecisionPanel
            sticky
            mode={mode}
            onMode={setMode}
            note={note}
            onNote={setNote}
            noteId="review-decision-note"
            rejectNoteMin={CHANGES_NOTE_MIN}
            rejectLabel="Request changes"
            approvePending={approveMut.isPending}
            rejectPending={changesMut.isPending}
            onApprove={() => approveMut.mutate()}
            onReject={() => changesMut.mutate()}
          />
        ) : (
          <NothingToDecide>
            This version is {wf.review.review_status.replace('_', ' ')} — nothing for you to decide
            right now.
          </NothingToDecide>
        )
      }
    />
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

/** A kind the server sends as a bare identifier, in words an approver reads. */
function kindLabel(kind: string): string {
  return ROUND_KIND_META[kind as RoundKind]?.label ?? kind.replace(/_/g, ' ');
}

/* ── Shortlisting — the gate a reviewer is actually approving ─────────────── */

/** Read the two shortlist settings out of the untyped `settings` bag. */
function shortlistSettings(settings: Record<string, unknown>): {
  auto: boolean;
  bar: number | null;
} {
  const raw = settings.shortlist_ats_threshold;
  return {
    auto: settings.auto_shortlist === true,
    bar: typeof raw === 'number' ? raw : null,
  };
}

/**
 * WHY THIS SECTION EXISTS. Approval is the safeguard on removing the human
 * shortlist gate: settings are draft-only, so turning automatic shortlisting
 * on means a new version, and a super admin approves it before it can go
 * live. That safeguard is only real if the approver is SHOWN the setting.
 * Until this section existed this page rendered none of a version's
 * automation settings at all.
 */
function ShortlistingSection({ settings }: { settings: Record<string, unknown> }) {
  const { auto, bar } = shortlistSettings(settings);
  const barPct = bar === null ? null : bar * 10;

  return (
    <Section
      title="Shortlisting"
      description="Who decides that an applicant starts round 1."
    >
      {auto ? (
        <div
          role="alert"
          data-testid="review-auto-shortlist"
          className="flex items-start gap-2.5 rounded-[12px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn)]/[0.08] p-3"
        >
          <AlertTriangle
            className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]"
            aria-hidden="true"
          />
          <div className="text-[13px] leading-relaxed text-foreground">
            <p className="font-semibold">This version shortlists automatically.</p>
            <p className="mt-1 text-muted-foreground">
              Applicants with an ATS score of{' '}
              <strong className="text-foreground">{barPct ?? '—'}/100 or higher</strong> start
              round 1 with no one confirming them. Below that score nobody is moved — they stay in
              HR&rsquo;s queue to shortlist by hand. The final hire or reject is still a person.
            </p>
          </div>
        </div>
      ) : (
        <p className="text-[13px] text-muted-foreground" data-testid="review-manual-shortlist">
          A person confirms every shortlist.
          {barPct !== null ? ` Applicants scoring ${barPct}/100 or higher are suggested for it.` : ''}
        </p>
      )}
    </Section>
  );
}

export default function WorkflowReviews(): JSX.Element {
  const { workflowId } = useParams();
  return workflowId ? <ReviewDetail workflowId={workflowId} /> : <ReviewQueue />;
}
