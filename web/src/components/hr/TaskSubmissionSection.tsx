// TaskSubmissionSection — PH4-D4. One application's job-simulation/portfolio
// submission(s): lifecycle state, the candidate's own content once it is
// readable, re-issue and withdraw.
//
// THERE IS NO REJECT PATH HERE, ON PURPOSE. A submission is evidence, not a
// decision (CLAUDE.md constraint 9 / D-05) — the round's pass/hold verdict is
// recorded on the decision queue, through the EXISTING round-review action,
// and reviewers are assigned in "Human interview" above through the SAME
// scorecard machinery. Nothing in this file advances, holds, scores or
// rejects anyone.
//
// WHAT THIS SHOWS (PH4-D4 gap 4 fix): `GET /hr/enrolments/{id}/tasks` now
// carries a submission's brief, item prompts and responses whenever they are
// readable — `status === 'submitted'` AND consent was not withdrawn (M1),
// the same rule the reviewer's own read follows (SubmissionPanel.tsx). A
// text answer renders as text, never HTML; an external link goes through
// the SAME interstitial that panel uses (`ExternalLinkGate`, re-exported
// from there — it is the last safeguard against a candidate-supplied URL, so
// there is only ever one implementation of it); a file is a download button
// through the existing `GET /hr/task-submissions/{id}/artifacts/{response_id}
// /download`. Reading a non-empty `responses` list here is audited
// server-side (`submission_viewed`), the same as a reviewer's own read. A
// withdrawn submission shows a plain tag instead of its content — never an
// empty or missing submission, since there IS one, it is just no longer
// visible or usable here.
//
// `is_current` (not list order) decides which row offers re-issue/withdraw —
// the field the server added to close the same gap.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  downloadTaskArtifact,
  listEnrolmentTasks,
  reissueTaskSubmission,
  withdrawTaskSubmission,
  type TaskItem,
  type TaskKind,
  type TaskResponseOut,
  type TaskSubmissionStatus,
} from '@/api/jobTasks';
import { downloadUrl } from '@/lib/safeUrl';
import { formatDate } from '@/lib/formatters';
import { toast } from '@/lib/toast';
import { ExternalLinkGate } from '@/components/interviewer/SubmissionPanel';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Download, Loader2 } from '@/design/components/icons';

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

/** A file response has no inline content — download it through the existing
 *  per-submission artifact route, the same pattern every other download in
 *  the codebase follows (`downloadUrl` guards the signed URL we get back). */
function TaskFileDownload({
  submissionId,
  response,
}: {
  submissionId: string;
  response: TaskResponseOut;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open(): Promise<void> {
    setPending(true);
    setError(null);
    try {
      const res = await downloadTaskArtifact(submissionId, response.id);
      const url = downloadUrl(res.url);
      if (!url) {
        setError('That download link could not be opened.');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (e) {
      setError(errText(e, 'Could not open this file'));
      toast.error(errText(e, 'Could not open this file'));
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => void open()}
        disabled={pending}
        className="inline-flex items-center gap-1.5 text-[12px] text-[var(--ui-info)] hover:underline disabled:opacity-50"
      >
        {pending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {response.original_name ?? 'Download file'}
      </button>
      {error ? <p className="mt-1 text-[11px] text-ember">{error}</p> : null}
    </div>
  );
}

function itemTypeLabel(artifact: TaskResponseOut): string {
  if (artifact.response_type === 'file') return 'File';
  if (artifact.link_kind) return artifact.link_kind.replace(/^\w/, (c) => c.toUpperCase());
  return 'Link';
}

/** One configured item's prompt, with whatever the candidate answered — or
 *  "No answer given" for an optional item left blank. */
function TaskItemAnswer({
  submissionId,
  item,
  response,
}: {
  submissionId: string;
  item: TaskItem;
  response: TaskResponseOut | undefined;
}) {
  return (
    <li className="rounded-[10px] border border-border p-3">
      <p className="whitespace-pre-wrap text-[12.5px] font-medium text-foreground">
        {item.prompt}
      </p>
      <div className="mt-1.5">
        {!response ? (
          <span className="text-[12px] text-[var(--ui-faint)]">No answer given.</span>
        ) : item.response_type === 'text' ? (
          <p className="whitespace-pre-wrap text-[13px] text-[var(--ui-soft)]">
            {response.text_value || (
              <span className="text-[var(--ui-faint)]">No answer given.</span>
            )}
          </p>
        ) : item.response_type === 'link' ? (
          response.link_url ? (
            <ExternalLinkGate href={response.link_url} />
          ) : (
            <span className="text-[12px] text-[var(--ui-faint)]">No link given.</span>
          )
        ) : (
          <TaskFileDownload submissionId={submissionId} response={response} />
        )}
      </div>
    </li>
  );
}

/** A free-form portfolio artifact — no item to answer, just a title,
 *  description and type the candidate gave it themselves. */
function TaskArtifactRow({
  submissionId,
  artifact,
}: {
  submissionId: string;
  artifact: TaskResponseOut;
}) {
  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 truncate text-[12.5px] font-medium text-foreground">
          {artifact.title || artifact.original_name || artifact.link_url || 'Portfolio artifact'}
        </p>
        <span className="shrink-0 text-[11px] text-[var(--ui-faint)]">
          {itemTypeLabel(artifact)}
        </span>
      </div>
      {artifact.description ? (
        <p className="mt-1 text-[12px] text-[var(--ui-soft)]">{artifact.description}</p>
      ) : null}
      <div className="mt-1.5">
        {artifact.response_type === 'link' && artifact.link_url ? (
          <ExternalLinkGate href={artifact.link_url} />
        ) : artifact.response_type === 'file' ? (
          <TaskFileDownload submissionId={submissionId} response={artifact} />
        ) : null}
      </div>
    </li>
  );
}

/** The brief, each item's prompt/answer, then free-form portfolio artifacts —
 *  shown only for a row the server has already decided is readable (`brief`
 *  set). Nothing here decides anything about the round; it is a read of what
 *  the candidate sent, for a human to weigh. */
function TaskSubmissionContent({
  submissionId,
  kind,
  brief,
  items,
  responses,
}: {
  submissionId: string;
  kind: TaskKind;
  brief: string;
  items: TaskItem[];
  responses: TaskResponseOut[];
}) {
  const byItemKey = new Map(
    responses.filter((r) => r.item_key).map((r) => [r.item_key as string, r]),
  );
  const artifacts = responses.filter((r) => !r.item_key);

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
      <p className="whitespace-pre-wrap text-[12.5px] text-[var(--ui-soft)]">{brief}</p>

      {items.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <TaskItemAnswer
              key={item.key}
              submissionId={submissionId}
              item={item}
              response={byItemKey.get(item.key)}
            />
          ))}
        </ul>
      ) : null}

      {kind === 'portfolio' ? (
        <div>
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--ui-faint)]">
            Portfolio
          </h4>
          {artifacts.length === 0 ? (
            <p className="mt-1 text-[12px] text-muted-foreground">No artifacts submitted.</p>
          ) : (
            <ul className="mt-1.5 flex flex-col gap-2">
              {artifacts.map((a) => (
                <TaskArtifactRow key={a.id} submissionId={submissionId} artifact={a} />
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

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
          {rows.map((row) => {
            // The live row for this (enrolment, round) — PH4-D4 gap 4 fix:
            // `is_current` from the server, never list position.
            const isCurrent = row.is_current;
            // M1: content is readable only once submitted AND consent for it
            // still stands — the same gate the server applies to `brief`.
            const readable = row.status === 'submitted' && !row.consent_withdrawn && row.brief;
            return (
              <div key={row.id} className="rounded-[10px] border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12.5px] font-medium text-foreground">
                    {row.round_title ?? 'Task round'}
                    {row.attempt_no > 1 ? ` · attempt ${row.attempt_no}` : ''}
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {/* PH4-D4 wave 5 — the candidate withdrew consent for
                        this submission (in progress or after submitting).
                        Say so plainly rather than letting the row read as an
                        empty or missing submission: there IS a submission,
                        it is just no longer visible or usable here. */}
                    {row.consent_withdrawn ? (
                      <StatusTag tone="ember" dot>
                        Consent withdrawn — content hidden
                      </StatusTag>
                    ) : null}
                    <StatusTag tone={STATUS_TONE[row.status]} dot>
                      {row.status.replace('_', ' ')}
                    </StatusTag>
                  </div>
                </div>
                <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11.5px] text-muted-foreground">
                  {row.due_at ? <div>Due {formatDate(row.due_at)}</div> : null}
                  {row.started_at ? <div>Started {formatDate(row.started_at)}</div> : null}
                  {row.submitted_at ? <div>Submitted {formatDate(row.submitted_at)}</div> : null}
                </dl>

                {readable ? (
                  <TaskSubmissionContent
                    submissionId={row.id}
                    kind={row.kind}
                    brief={row.brief as string}
                    items={row.items}
                    responses={row.responses}
                  />
                ) : null}

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
