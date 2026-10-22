// McqSection: the MCQ question list for one exam section, with the bank
// picker and the composer (McqComposer) below it. Moved out of
// pages/hr/ExamEditor.tsx.
// English-only by design (CLAUDE.md: staff consoles are not translated).

import BankProvenanceChip from '@/components/bank/BankProvenanceChip';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Lock, Library } from '@/design/components/icons';
import { listSectionQuestions, deleteSectionQuestion } from '@/api/exams';
import { saveExamQuestionToBank, type SaveToBankInput } from '@/api/questionBanks';
import { examEditorMutationKey } from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Stagger, StaggerItem } from '@/design/components/Reveal';
import { BankQuestionPicker } from '@/components/bank/BankQuestionPicker';
import { SaveToBankButton } from '@/components/bank/SaveToBankButton';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { plural } from '@/components/hr/exam/examEditorUi';
import { McqComposer } from '@/components/hr/exam/McqComposer';

// ── MCQ section question list ─────────────────────────────────────────────────

interface McqSectionProps {
  examId: string;
  sectionId: string;
  locked: boolean;
}
export function McqSection({ examId, sectionId, locked }: McqSectionProps) {
  const qc = useQueryClient();
  const qKey = ['hr', 'exam', examId, 'section', sectionId, 'questions'];
  const editorKey = examEditorMutationKey(examId);

  const questionsQuery = useQuery({
    queryKey: qKey,
    queryFn: () => listSectionQuestions(examId, sectionId),
  });
  const questions = questionsQuery.data ?? [];

  // PH4-D1: the bank picker, and per-row "Save to bank".
  const [showBankPicker, setShowBankPicker] = useState(false);
  const [savingToBankId, setSavingToBankId] = useState<string | null>(null);

  // Prefix invalidation: the question list, and the structure's question
  // counts that gate Publish, and the exam list's "N Qs".
  const refresh = () => void invalidateAfterHrMutation(qc);

  const saveToBankMut = useMutation({
    mutationFn: ({ qid, body }: { qid: string; body: SaveToBankInput }) =>
      saveExamQuestionToBank(examId, qid, body),
    onSuccess: () => {
      toast.success('Saved to bank as a new draft');
      setSavingToBankId(null);
    },
    onError: (e: unknown) => {
      toast.error(errorMessage(e, 'Could not save this question to a bank.'));
      setSavingToBankId(null);
    },
  });

  const delMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (qid: string) => deleteSectionQuestion(examId, sectionId, qid),
    onSuccess: () => {
      toast.success('Question deleted');
      refresh();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the question.')),
  });

  return (
    <div className="mt-3 space-y-3">
      {questionsQuery.isLoading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <span className="sr-only">Loading questions</span>
          <Skeleton className="h-16 rounded-[14px]" />
        </div>
      ) : null}
      {questionsQuery.isError && !questionsQuery.data ? (
        <QueryErrorPanel
          title="Could not load this section's questions."
          error={questionsQuery.error}
          onRetry={() => void questionsQuery.refetch()}
          retrying={questionsQuery.isFetching}
        />
      ) : null}

      {/* Existing questions */}
      {questions.length > 0 && (
        <Stagger className="flex flex-col gap-2">
          {questions.map((q, i) => (
            <StaggerItem key={q.id}>
              <div
                className={cn(
                  'rounded-[14px] border border-border p-3.5',
                  locked ? 'bg-card' : 'bg-[var(--ui-inset-soft)]',
                )}
              >
                <div className="flex items-start gap-3">
                  <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-[var(--ui-inset)] font-mono text-[12px] text-[var(--ui-soft)]">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="text-[13px] font-medium leading-snug text-foreground">
                        {q.prompt}{' '}
                        <span className="font-normal text-muted-foreground">({q.points} pt)</span>
                      </p>
                      <BankProvenanceChip
                        rootId={q.source_bank_root_id}
                        version={q.source_bank_version}
                      />
                    </div>
                    {/* Outline row facts: type, marks, and whether the row can
                        still change. Locked is a word and an icon, not just a
                        muted look. */}
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[12px] text-muted-foreground">
                      <span>MCQ</span>
                      <span aria-hidden="true">·</span>
                      <span className="tabular-nums">{plural(q.points, 'mark')}</span>
                      {locked ? (
                        <span className="inline-flex items-center gap-1 font-medium text-foreground">
                          <Lock size={11} aria-hidden="true" /> Locked
                        </span>
                      ) : null}
                    </p>
                    <ul className="mt-2 space-y-0.5 pl-0">
                      {q.options.map((opt, oi) => (
                        <li
                          key={oi}
                          className={cn(
                            'flex items-center gap-1.5 text-[12px]',
                            oi === q.correct_index
                              ? 'font-medium text-[var(--ui-ok)]'
                              : 'text-muted-foreground',
                          )}
                        >
                          {oi === q.correct_index ? (
                            <Check size={12} className="shrink-0" aria-hidden="true" />
                          ) : (
                            <span className="h-3 w-3 shrink-0" aria-hidden="true" />
                          )}
                          {opt}
                        </li>
                      ))}
                    </ul>
                    {/* SaveToBankButton owns its own open/closed state and
                        renders the "Save to bank" link itself, so gating it
                        behind a second one made the user click twice. */}
                    <SaveToBankButton
                      className="mt-2"
                      saving={saveToBankMut.isPending && savingToBankId === q.id}
                      onSave={(body) => {
                        setSavingToBankId(q.id);
                        saveToBankMut.mutate({ qid: q.id, body });
                      }}
                    />
                  </div>
                  {!locked && (
                    <ConfirmDeleteButton
                      title={`Delete question ${i + 1}`}
                      confirmText="Delete question"
                      pending={delMut.isPending && delMut.variables === q.id}
                      onConfirm={() => delMut.mutate(q.id)}
                    />
                  )}
                </div>
              </div>
            </StaggerItem>
          ))}
        </Stagger>
      )}

      {/* PH4-D1: add an approved MCQ question straight from a bank */}
      {!locked && (
        <Button type="button" variant="outline" onClick={() => setShowBankPicker(true)}>
          <Library aria-hidden="true" /> Add from bank
        </Button>
      )}
      {showBankPicker && (
        <BankQuestionPicker
          examId={examId}
          sectionId={sectionId}
          sectionKind="mcq"
          onClose={() => setShowBankPicker(false)}
          onAdded={refresh}
        />
      )}

      {/* Composer: three ways to add questions; hidden when locked */}
      {!locked && <McqComposer examId={examId} sectionId={sectionId} onChanged={refresh} />}

      {locked && (
        <div className="flex items-center gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12px] text-muted-foreground">
          <Lock size={13} aria-hidden="true" /> Questions are locked. See the banner above.
        </div>
      )}
    </div>
  );
}
