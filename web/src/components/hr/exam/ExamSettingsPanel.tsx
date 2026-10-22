// ExamSettingsPanel: exam-level settings and closing the exam.
// Moved out of pages/hr/ExamEditor.tsx.
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Lock, ChevronDown, ChevronRight, Settings } from '@/design/components/icons';
import { updateExam, type ExamDetail, type ExamUpdateInput } from '@/api/exams';
import { examEditorMutationKey } from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { GlassCard, ToggleSwitch } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { Button } from '@/components/ui/button';
import { smallInputCls } from '@/components/hr/exam/examEditorUi';

// ── Exam settings ─────────────────────────────────────────────────────────────

/** Exam-level settings, and closing the exam (audit E11: after creation
 *  nothing in the UI could change these, or close an exam at all). */
export function ExamSettingsPanel({
  exam,
  hasLiveRound,
}: {
  exam: ExamDetail;
  hasLiveRound: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [titleDraft, setTitleDraft] = useState(exam.title);
  useEffect(() => setTitleDraft(exam.title), [exam.title]);

  const saveMut = useMutation({
    mutationKey: examEditorMutationKey(exam.id),
    mutationFn: ({ patch }: { patch: ExamUpdateInput; done: string }) => updateExam(exam.id, patch),
    onSuccess: (_e, { done }) => {
      toast.success(done);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the exam settings.')),
  });

  const closed = exam.status === 'closed';

  return (
    <GlassCard className="p-0">
      <Button
        type="button"
        variant="ghost"
        className="w-full justify-start rounded-card px-5"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Settings aria-hidden="true" />
        Exam settings
        {open ? (
          <ChevronDown className="ml-auto" aria-hidden="true" />
        ) : (
          <ChevronRight className="ml-auto" aria-hidden="true" />
        )}
      </Button>
      {open ? (
        <div className="space-y-4 border-t border-border px-5 py-4">
          <form
            className="flex flex-wrap items-end gap-2"
            aria-label="Exam title"
            onSubmit={(e) => {
              e.preventDefault();
              const title = titleDraft.trim();
              if (!title) return toast.error('Give the exam a title.');
              saveMut.mutate({ patch: { title }, done: 'Exam renamed' });
            }}
          >
            <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-[12px] text-muted-foreground">
              <span className="uppercase tracking-[0.5px]">Title</span>
              <input
                value={titleDraft}
                maxLength={300}
                onChange={(e) => setTitleDraft(e.target.value)}
                className={cn(smallInputCls, 'w-full text-[14px]')}
              />
            </label>
            <Button
              type="submit"
              variant="outline"
              disabled={saveMut.isPending || titleDraft.trim() === exam.title}
            >
              Save title
            </Button>
          </form>

          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <ToggleSwitch
                checked={exam.allow_retake}
                disabled={saveMut.isPending}
                onChange={(v) =>
                  saveMut.mutate({
                    patch: { allow_retake: v },
                    done: v ? 'Retakes allowed' : 'Retakes turned off',
                  })
                }
                label="Allow retake"
              />
              <span className="text-[13px] text-[var(--ui-soft)]">Allow retake</span>
            </div>
            <div className="flex items-center gap-3">
              <ToggleSwitch
                checked={exam.auto_advance_on_pass}
                disabled={saveMut.isPending}
                onChange={(v) =>
                  saveMut.mutate({
                    patch: { auto_advance_on_pass: v },
                    done: v ? 'Auto-advance turned on' : 'Auto-advance turned off',
                  })
                }
                label="Auto-advance on pass"
              />
              <span className="text-[13px] text-[var(--ui-soft)]">
                Auto-advance on pass (invite to the interview when the last round is passed)
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
            <p className="min-w-[220px] flex-1 text-[12.5px] text-muted-foreground">
              {closed
                ? 'This exam is closed. Nobody can start or continue it, including people who already have a link.'
                : 'Closing the exam stops anyone starting or continuing it, including people who already have a link. You can reopen it.'}
            </p>
            {closed ? (
              <Button
                type="button"
                variant="outline"
                disabled={saveMut.isPending}
                onClick={() =>
                  saveMut.mutate({
                    patch: { status: hasLiveRound ? 'published' : 'draft' },
                    done: 'Exam reopened',
                  })
                }
              >
                Reopen exam
              </Button>
            ) : (
              <ConfirmDeleteButton
                label="Close exam"
                icon={Lock}
                pending={saveMut.isPending}
                onConfirm={() =>
                  saveMut.mutate({ patch: { status: 'closed' }, done: 'Exam closed' })
                }
              />
            )}
          </div>
        </div>
      ) : null}
    </GlassCard>
  );
}
