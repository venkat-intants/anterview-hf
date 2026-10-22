// SectionPanel: one section of a round (rename, time limit, delete) with its
// MCQ or coding question authoring. Moved out of pages/hr/ExamEditor.tsx.
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { Suspense, lazy, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Loader2,
  ListChecks,
  Clock,
  X,
  Pencil,
  ChevronDown,
  ChevronRight,
  Code2,
} from '@/design/components/icons';
import { updateSection, type Section } from '@/api/exams';
import { examEditorMutationKey, minutesToSeconds } from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { StatusTag } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { smallInputCls } from '@/components/hr/exam/examEditorUi';
import { McqSection } from '@/components/hr/exam/McqSection';

// Lazy-load CodingAuthoringSection to avoid importing the code-editor eagerly
const CodingAuthoringSection = lazy(() => import('@/pages/hr/CodingAuthoringSection'));

// ── Section panel ─────────────────────────────────────────────────────────────

interface SectionPanelProps {
  examId: string;
  section: Section;
  locked: boolean;
  onDelete: (sectionId: string) => void;
  deleting: boolean;
}

export function SectionPanel({ examId, section, locked, onDelete, deleting }: SectionPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState(section.title);
  const sectionMinutes = () =>
    section.time_limit_seconds ? String(Math.round(section.time_limit_seconds / 60)) : '';
  const [minutesDraft, setMinutesDraft] = useState(sectionMinutes);
  const qc = useQueryClient();

  const renameMut = useMutation({
    mutationKey: examEditorMutationKey(examId),
    mutationFn: (patch: { title: string; time_limit_seconds: number | null }) =>
      updateSection(examId, section.round_id, section.id, patch),
    onSuccess: () => {
      setEditing(false);
      toast.success('Section saved');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not save the section.')),
  });

  const kindLabel = section.kind === 'coding' ? 'Coding' : 'MCQ';

  return (
    <div className="rounded-[16px] border border-border bg-[var(--ui-inset)]">
      {/* Section header */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} section ${section.title}`}
          className="text-muted-foreground"
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        </Button>

        {section.kind === 'coding' ? (
          <Code2 size={14} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
        ) : (
          <ListChecks size={14} className="shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
        )}

        {editing ? (
          <form
            className="flex flex-1 flex-wrap items-center gap-2"
            aria-label={`Edit section ${section.title}`}
            onSubmit={(e) => {
              e.preventDefault();
              if (!titleDraft.trim()) return toast.error('Give the section a title.');
              renameMut.mutate({
                title: titleDraft.trim(),
                time_limit_seconds: minutesToSeconds(minutesDraft),
              });
            }}
          >
            <input
              autoFocus
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              maxLength={200}
              className={cn(smallInputCls, 'min-w-[160px] flex-1')}
              aria-label="Section title"
            />
            <input
              type="number"
              min={1}
              placeholder="No limit"
              value={minutesDraft}
              onChange={(e) => setMinutesDraft(e.target.value)}
              className={cn(smallInputCls, 'w-28')}
              aria-label="Section time limit in minutes (blank for no limit)"
            />
            <Button type="submit" disabled={renameMut.isPending} aria-busy={renameMut.isPending}>
              {renameMut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Save
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Cancel"
              onClick={() => {
                setEditing(false);
                setTitleDraft(section.title);
                setMinutesDraft(sectionMinutes());
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </form>
        ) : (
          <>
            <span className="flex-1 text-[13px] font-medium text-foreground">{section.title}</span>
            <StatusTag tone="neutral" className="text-[12px]">
              {kindLabel}
            </StatusTag>
            {section.time_limit_seconds && (
              <span className="flex items-center gap-1 text-[12px] text-[var(--ui-faint)]">
                <Clock size={11} aria-hidden="true" />
                {Math.round(section.time_limit_seconds / 60)} min
              </span>
            )}
            {!locked && (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Edit section ${section.title}`}
                  className="text-muted-foreground"
                  onClick={() => {
                    setTitleDraft(section.title);
                    setMinutesDraft(sectionMinutes());
                    setEditing(true);
                  }}
                >
                  <Pencil aria-hidden="true" />
                </Button>
                <ConfirmDeleteButton
                  title={`Delete section ${section.title}`}
                  confirmText="Delete section"
                  pending={deleting}
                  onConfirm={() => onDelete(section.id)}
                />
              </>
            )}
          </>
        )}
      </div>

      {/* Section body */}
      {expanded && (
        <div className="px-4 pb-3">
          {section.kind === 'coding' ? (
            <Suspense
              fallback={
                <div className="flex flex-col gap-2" aria-busy="true">
                  <span className="sr-only">Loading editor</span>
                  <Skeleton className="h-16 rounded-[14px]" />
                </div>
              }
            >
              <CodingAuthoringSection examId={examId} sectionId={section.id} locked={locked} />
            </Suspense>
          ) : (
            <McqSection examId={examId} sectionId={section.id} locked={locked} />
          )}
        </div>
      )}
    </div>
  );
}
