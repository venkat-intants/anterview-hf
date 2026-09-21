// TaskSubmissionSection — PH4-D4. One application's job-simulation/portfolio
// submission(s): lifecycle state, re-issue and withdraw.
//
// THERE IS NO REJECT PATH HERE, ON PURPOSE. A submission is evidence, not a
// decision (CLAUDE.md constraint 9 / D-05) — the round's pass/hold verdict is
// recorded on the decision queue, through the EXISTING round-review action,
// and reviewers are assigned in "Human interview" above through the SAME
// scorecard machinery. Nothing in this file advances, holds, scores or
// rejects anyone.
//
// WHAT THIS CANNOT SHOW (a backend gap, not a UI omission): the server's own
// `GET /hr/enrolments/{id}/tasks` returns each submission's lifecycle only
// (status, dates, attempt number) — there is no HR endpoint that lists a
// submission's actual responses or artifacts by submission id, only a
// per-file download that needs a response id HR has no way to learn without
// one. A submission's content is reachable today only through the reviewer
// path (an assigned interviewer's own scorecard, InterviewerScorecard's
// Submission tab). Nor does the list say which row is the current, live one
// — `_submission_out` carries no `superseded_at` — so this infers it from
// recency (`for_enrolment` orders newest first) and re-issue/withdraw are
// offered only on that newest row.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listEnrolmentTasks,
  reissueTaskSubmission,
  withdrawTaskSubmission,
  type TaskSubmissionStatus,
} from '@/api/jobTasks';
import { formatDate } from '@/lib/formatters';
import { toast } from '@/lib/toast';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Loader2 } from '@/design/components/icons';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const STATUS_TONE: Record<TaskSubmissionStatus, TagTone> = {
  assigned: 'neutral',
  in_progress: 'electric',
  submitted: 'forest',
  expired: 'ember',
  withdrawn: 'neutral',
};

const OPEN_STATUSES: TaskSubmissionStatus[] = ['assigned', 'in_progress'];

export default function TaskSubmissionSection({
  enrolmentId,
}: {
  enrolmentId: string;
}): JSX.Element | null {
  const qc = useQueryClient();
  const [reason, setReason] = useState('');

  const list = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'tasks'],
    queryFn: () => listEnrolmentTasks(enrolmentId),
    retry: false,
  });

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ['hr', 'enrolment', enrolmentId, 'tasks'] });

  const reissueMut = useMutation({
    mutationFn: (submissionId: string) => reissueTaskSubmission(submissionId),
    onSuccess: () => {
      toast.success('A fresh link was issued');
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'Could not re-issue this task')),
  });

  const withdrawMut = useMutation({
    mutationFn: (submissionId: string) => withdrawTaskSubmission(submissionId, reason),
    onSuccess: () => {
      toast.success('Submission withdrawn');
      setReason('');
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'Could not withdraw this submission')),
  });

  // Nothing to show for an application with no task round — most
  // applications, today. A failed read is said plainly rather than looking
  // like "no task round".
  if (!list.isLoading && !list.isError && (list.data ?? []).length === 0) return null;

  const rows = list.data ?? [];

  return (
    <div className="mt-5">
      <h3 className="text-[13px] font-medium text-foreground">Job simulation / portfolio</h3>

      {list.isLoading ? (
        <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      ) : list.isError ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          Could not load this application&rsquo;s task submissions.
        </p>
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          {rows.map((row, idx) => {
            // Newest first (the server's own order) — the current, live
            // attempt is the first row; the rest are kept history.
            const isCurrent = idx === 0;
            return (
              <div key={row.id} className="rounded-[10px] border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12.5px] font-medium text-foreground">
                    {row.round_title ?? 'Task round'}
                    {row.attempt_no > 1 ? ` · attempt ${row.attempt_no}` : ''}
                  </span>
                  <StatusTag tone={STATUS_TONE[row.status]} dot>
                    {row.status.replace('_', ' ')}
                  </StatusTag>
                </div>
                <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11.5px] text-muted-foreground">
                  {row.due_at ? <div>Due {formatDate(row.due_at)}</div> : null}
                  {row.started_at ? <div>Started {formatDate(row.started_at)}</div> : null}
                  {row.submitted_at ? <div>Submitted {formatDate(row.submitted_at)}</div> : null}
                </dl>

                {isCurrent ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {OPEN_STATUSES.includes(row.status) ? (
                      <>
                        <input
                          type="text"
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          placeholder="Withdraw reason (optional)"
                          aria-label={`Withdraw reason for ${row.round_title ?? 'this task'}`}
                          className="w-full max-w-[220px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[11.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
                        />
                        <button
                          type="button"
                          onClick={() => withdrawMut.mutate(row.id)}
                          disabled={withdrawMut.isPending}
                          className="rounded-[8px] border border-[var(--ui-danger)]/30 px-2.5 py-1 text-[11.5px] text-[var(--ui-danger)] hover:bg-[var(--ui-danger)]/10 disabled:opacity-40"
                        >
                          Withdraw
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => reissueMut.mutate(row.id)}
                        disabled={reissueMut.isPending}
                        className="rounded-[8px] border border-[var(--ui-line-strong)] px-2.5 py-1 text-[11.5px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
                      >
                        {reissueMut.isPending ? 'Issuing…' : 'Re-issue a link'}
                      </button>
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
