// BankQuestionDetailPane - one bank question, read first and edited on purpose.
//
// The right-hand side of the bank's master-detail. It shows the question as a
// reader sees it (an MCQ's options with the right one marked, or the whole
// coding question through BankCodingQuestionView), and keeps every action the
// old two-column page had:
//
//   draft       Edit, Submit for review, Delete
//   in review   Withdraw - only the person who submitted it; the server 403s
//               anyone else, so the control is not offered to them
//   approved    New version, Retire
//   retired     New version
//
// plus the version history and the exams that use it. Content is editable only
// while the question is a draft; anything else is changed by making a new
// version, which is a new draft.
//
// An AI draft says so, and says that a person decides: the model's output is a
// suggestion and these questions go on to judge people.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useEffect, useRef, useState, type RefObject } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Check, Loader2, Pencil } from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { STATUS_TONE, STATUS_WORD } from './bankStatus';
import { Well } from '@/design/components/rows';
import { Button } from '@/components/ui/button';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { BankQuestionEditor } from '@/components/bank/BankQuestionEditor';
import { QuestionBadges } from '@/components/bank/QuestionBadges';
import type { UnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { BankCodingQuestionView } from '@/components/hr/bank/BankCodingQuestionView';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  deleteBankQuestion,
  getBankQuestion,
  newBankQuestionVersion,
  retireBankQuestion,
  submitBankQuestion,
  updateBankQuestion,
  withdrawBankQuestion,
  type BankQuestion,
  type BankQuestionInput,
} from '@/api/questionBanks';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const SECONDARY =
  'min-h-10 rounded-full border border-[var(--ui-line-strong)] px-5 py-1.5 text-[13px] font-medium text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50';
const QUIET_DANGER =
  'min-h-10 rounded-full px-4 py-1.5 text-[13px] font-medium text-muted-foreground hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)] focus-visible:bg-[var(--ui-danger-wash)] focus-visible:text-[var(--ui-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50';

/** An MCQ read the way a candidate sees it, with the right option named. */
function McqReadView({ question }: { question: BankQuestion }): JSX.Element {
  return (
    <div className="flex flex-col gap-3" data-testid="mcq-read-view">
      <p className="whitespace-pre-wrap break-words text-[14px] font-medium leading-relaxed text-foreground">
        {question.prompt}
      </p>
      <ol className="flex flex-col gap-1.5" aria-label="Options">
        {(question.options ?? []).map((o, i) => {
          const correct = i === question.correct_index;
          return (
            <li
              key={i}
              className={cn(
                'flex items-start gap-2 rounded-[10px] px-3 py-2 text-[13.5px]',
                correct
                  ? 'bg-[var(--ui-ok-wash)] text-foreground'
                  : 'bg-[var(--ui-inset-soft)] text-foreground',
              )}
            >
              <span className="font-semibold tabular-nums text-muted-foreground">
                {String.fromCharCode(65 + i)}.
              </span>
              <span className="min-w-0 flex-1 break-words">{o}</span>
              {correct ? (
                <span className="inline-flex flex-none items-center gap-1 text-[12.5px] font-semibold text-[var(--ui-ok)]">
                  <Check size={13} aria-hidden="true" />
                  Correct answer
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
      <p className="text-[12.5px] text-muted-foreground">
        {question.points} point{question.points === 1 ? '' : 's'}
        {question.competencies.length > 0
          ? ` · ${question.competencies.map((c) => c.name).join(', ')}`
          : ''}
      </p>
    </div>
  );
}

export interface BankQuestionDetailPaneProps {
  /** The row from the list, used until (and instead of) a fresher fetch. */
  question: BankQuestion;
  guard: UnsavedGuard;
  /** Gives the pane's heading to the parent, which focuses it after a swap on small screens. */
  headingRef?: RefObject<HTMLHeadingElement>;
  /** A new version was created as a draft; the parent selects it. */
  onSelectVersion: (id: string) => void;
  /** The question no longer exists in this view (deleted). */
  onGone: () => void;
  /** Below `lg` the list and the pane take turns; this is the way back. */
  onBack?: () => void;
}

export function BankQuestionDetailPane({
  question: listed,
  guard,
  headingRef,
  onSelectVersion,
  onGone,
  onBack,
}: BankQuestionDetailPaneProps): JSX.Element {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [editing, setEditing] = useState(false);
  const [newVersionMode, setNewVersionMode] = useState(false);
  const localHeading = useRef<HTMLHeadingElement>(null);
  const heading = headingRef ?? localHeading;

  // Which version of this lineage is on screen. It starts as the row that was
  // chosen and moves when a version is picked from the history, whether or not
  // the parent's list happens to contain that version.
  const [viewId, setViewId] = useState(listed.id);
  const detail = useQuery({
    queryKey: ['hr', 'bank-questions', viewId],
    queryFn: () => getBankQuestion(viewId),
  });
  const lastVersions = useRef<BankQuestion[]>([]);
  if (detail.data) lastVersions.current = detail.data.versions;
  const versions = detail.data?.versions ?? lastVersions.current;
  const q: BankQuestion = detail.data?.question ?? versions.find((v) => v.id === viewId) ?? listed;

  // A different question starts read-only, whatever the last one was doing.
  useEffect(() => {
    setEditing(false);
    setNewVersionMode(false);
  }, [viewId]);

  const invalidateAll = (): void => {
    void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-review-queue'] });
  };

  const updateMut = useMutation({
    mutationFn: (input: BankQuestionInput) => updateBankQuestion(q.id, input),
    onSuccess: () => {
      toast.success('Saved');
      guard.setDirty(false);
      setEditing(false);
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not save this question')),
  });

  const newVersionMut = useMutation({
    mutationFn: (input: BankQuestionInput) => newBankQuestionVersion(q.id, input),
    onSuccess: (created) => {
      toast.success(`Version ${created.version} created as a draft`);
      guard.setDirty(false);
      setNewVersionMode(false);
      invalidateAll();
      onSelectVersion(created.id);
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create a new version')),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteBankQuestion(q.id),
    onSuccess: () => {
      toast.success('Question deleted');
      invalidateAll();
      onGone();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not delete this question')),
  });

  const submitMut = useMutation({
    mutationFn: () => submitBankQuestion(q.id),
    onSuccess: () => {
      toast.success('Submitted for review');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not submit this question')),
  });

  const withdrawMut = useMutation({
    mutationFn: () => withdrawBankQuestion(q.id),
    onSuccess: () => {
      toast.success('Withdrawn to draft');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not withdraw this question')),
  });

  const retireMut = useMutation({
    mutationFn: () => retireBankQuestion(q.id),
    onSuccess: () => {
      toast.success('Retired');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not retire this question')),
  });

  const isDraft = q.status === 'draft';
  const reading = !editing && !newVersionMode;
  const usedIn = detail.data?.used_in_exams ?? [];
  const aiDraft = q.origin === 'ai_draft' && isDraft;

  function cancelEdit(): void {
    if (!guard.confirmLeave()) return;
    setEditing(false);
    setNewVersionMode(false);
  }

  return (
    <div className="flex flex-col gap-4" data-testid="bank-question-detail">
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          className="inline-flex min-h-10 items-center gap-1.5 self-start rounded-[10px] text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] lg:hidden"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          Back to the list
        </button>
      ) : null}

      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3
            ref={heading}
            tabIndex={-1}
            className="text-[15px] font-semibold text-foreground focus-visible:outline-none"
          >
            {q.kind === 'mcq' ? 'MCQ' : 'Coding'} question · v{q.version}
          </h3>
          <QuestionBadges question={q} showKind={false} showUnverified={false} className="mt-1.5" />
        </div>
        <StatusTag tone={STATUS_TONE[q.status]}>{STATUS_WORD[q.status]}</StatusTag>
      </div>

      {q.review_note ? (
        <Well className="border border-[var(--ui-warn)]/40" data-testid="review-note">
          <p className="text-[13px] font-semibold text-foreground">
            {isDraft ? 'Sent back by a reviewer' : 'Reviewer note'}
          </p>
          <p className="mt-0.5 whitespace-pre-wrap break-words text-[13.5px] text-foreground">
            {q.review_note}
          </p>
        </Well>
      ) : null}

      {aiDraft && reading ? (
        <Well data-testid="ai-draft-note">
          <p className="text-[13px] leading-relaxed text-foreground">
            <span className="font-semibold">Written by AI, saved as a draft.</span> Read it and fix
            what is wrong before you send it for review. A person decides whether it is ever used.
          </p>
        </Well>
      ) : null}

      {newVersionMode ? (
        <Well className="border border-[var(--accent)]" data-testid="new-version-note">
          <p className="text-[13px] text-foreground">
            Saving creates version {q.version + 1} as a new draft - this{' '}
            {STATUS_WORD[q.status].toLowerCase()} version is unchanged.
          </p>
        </Well>
      ) : null}

      {editing || newVersionMode ? (
        <BankQuestionEditor
          question={q}
          saving={updateMut.isPending || newVersionMut.isPending}
          forceEditable={newVersionMode}
          onCancel={cancelEdit}
          onDirtyChange={guard.setDirty}
          onSave={(input) =>
            newVersionMode ? newVersionMut.mutate(input) : updateMut.mutate(input)
          }
        />
      ) : (
        <>
          {!isDraft ? (
            <p className="text-[13px] text-muted-foreground">
              This question is {STATUS_WORD[q.status].toLowerCase()} - only a draft can be edited.
              {q.status === 'approved' || q.status === 'retired'
                ? ' Start a new version to change it.'
                : ''}
            </p>
          ) : null}
          {q.kind === 'coding' ? (
            <BankCodingQuestionView question={q} headingAs="h4" />
          ) : (
            <McqReadView question={q} />
          )}
        </>
      )}

      {reading ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          {isDraft ? (
            <>
              <Button type="button" variant="outline" onClick={() => setEditing(true)}>
                <Pencil aria-hidden="true" />
                Edit
              </Button>
              <button
                type="button"
                disabled={submitMut.isPending}
                onClick={() => submitMut.mutate()}
                className={SECONDARY}
              >
                {submitMut.isPending ? (
                  <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : null}
                Submit for review
              </button>
              <ConfirmDeleteButton
                label="Delete"
                pending={deleteMut.isPending}
                onConfirm={() => deleteMut.mutate()}
              />
            </>
          ) : null}
          {q.status === 'in_review' && q.submitted_by_user_id === user?.user_id ? (
            // Only the submitter may withdraw. Gating the control means that
            // refusal is never something another reviewer runs into by surprise.
            <button
              type="button"
              disabled={withdrawMut.isPending}
              onClick={() => withdrawMut.mutate()}
              className={SECONDARY}
            >
              Withdraw
            </button>
          ) : null}
          {q.status === 'approved' || q.status === 'retired' ? (
            <button type="button" onClick={() => setNewVersionMode(true)} className={SECONDARY}>
              New version
            </button>
          ) : null}
          {q.status === 'approved' ? (
            <button
              type="button"
              disabled={retireMut.isPending}
              onClick={() => retireMut.mutate()}
              className={cn(QUIET_DANGER, 'ml-auto')}
            >
              Retire
            </button>
          ) : null}
        </div>
      ) : null}

      {versions.length > 1 ? (
        <div className="border-t border-border pt-3">
          <h4 className="mb-2 text-[12.5px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
            Version history
          </h4>
          <ul className="flex flex-col gap-1.5">
            {versions.map((v) => (
              <li key={v.id}>
                <button
                  type="button"
                  aria-pressed={v.id === q.id}
                  onClick={() => {
                    if (v.id !== q.id && guard.confirmLeave()) setViewId(v.id);
                  }}
                  className={cn(
                    'flex min-h-10 w-full items-center justify-between rounded-[12px] border px-3 py-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                    v.id === q.id
                      ? 'border-[var(--accent)] bg-[var(--ui-inset-soft)] text-foreground ring-1 ring-[var(--accent)]'
                      : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                >
                  <span>v{v.version}</span>
                  <StatusTag tone={STATUS_TONE[v.status]} className="text-[12px]">
                    {STATUS_WORD[v.status]}
                  </StatusTag>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {usedIn.length > 0 ? (
        <div className="border-t border-border pt-3">
          <h4 className="mb-2 text-[12.5px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
            Used in {usedIn.length} exam{usedIn.length === 1 ? '' : 's'}
          </h4>
          <ul className="flex flex-col gap-1">
            {usedIn.map((u) => (
              <li key={u.exam_id}>
                <Link
                  to={`/hr/exams/${u.exam_id}`}
                  className="inline-flex min-h-10 items-center text-[13px] text-[var(--ui-info)] hover:underline"
                >
                  {u.exam_title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
