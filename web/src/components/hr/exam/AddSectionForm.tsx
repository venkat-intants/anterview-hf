// AddSectionForm: the inline form that adds an MCQ or coding section to one
// round. Split out of RoundPanel.tsx so each file stays readable.
// English-only by design (CLAUDE.md: staff consoles are not translated).

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Plus, Loader2, X } from '@/design/components/icons';
import { createSection } from '@/api/exams';
import { examEditorMutationKey, minutesToSeconds } from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface AddSectionFormProps {
  examId: string;
  roundId: string;
  /** Called once the section is saved, so the structure refreshes. */
  onAdded: () => void;
  /** Closes the form (after a save, or on Cancel). */
  onClose: () => void;
}

export function AddSectionForm({ examId, roundId, onAdded, onClose }: AddSectionFormProps) {
  const [sectionTitle, setSectionTitle] = useState('');
  const [sectionKind, setSectionKind] = useState<'mcq' | 'coding'>('mcq');
  const [sectionTimeMin, setSectionTimeMin] = useState('');

  const addSectionMut = useMutation({
    mutationKey: examEditorMutationKey(examId),
    mutationFn: () =>
      createSection(examId, roundId, {
        title: sectionTitle.trim() || (sectionKind === 'coding' ? 'Coding section' : 'MCQ section'),
        kind: sectionKind,
        time_limit_seconds: minutesToSeconds(sectionTimeMin),
      }),
    onSuccess: () => {
      setSectionTitle('');
      setSectionKind('mcq');
      setSectionTimeMin('');
      onClose();
      toast.success('Section added');
      onAdded();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the section.')),
  });

  return (
    <form
      className="mb-3 flex flex-wrap items-end gap-2 rounded-[14px] border border-dashed border-border bg-[var(--ui-inset-soft)] p-3"
      onSubmit={(e) => {
        e.preventDefault();
        addSectionMut.mutate();
      }}
      aria-label="New section"
    >
      <div className="flex gap-1.5">
        {(['mcq', 'coding'] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setSectionKind(k)}
            className={cn(
              'min-h-10 rounded-[9px] border px-3 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              sectionKind === k
                ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]'
                : 'border-border text-muted-foreground hover:text-foreground',
            )}
            aria-pressed={sectionKind === k}
          >
            {k === 'mcq' ? 'MCQ' : 'Coding'}
          </button>
        ))}
      </div>
      <Input
        className="min-w-[160px] flex-1"
        placeholder="Section title (optional)"
        value={sectionTitle}
        maxLength={200}
        onChange={(e) => setSectionTitle(e.target.value)}
        aria-label="Section title"
      />
      <Input
        type="number"
        min={1}
        placeholder="Min limit"
        value={sectionTimeMin}
        onChange={(e) => setSectionTimeMin(e.target.value)}
        className="w-24"
        aria-label="Section time limit in minutes"
      />
      <Button
        type="submit"
        variant="outline"
        disabled={addSectionMut.isPending}
        aria-busy={addSectionMut.isPending}
      >
        {addSectionMut.isPending ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <Plus aria-hidden="true" />
        )}
        Add section
      </Button>
      <Button type="button" variant="ghost" size="icon" aria-label="Cancel" onClick={onClose}>
        <X aria-hidden="true" />
      </Button>
    </form>
  );
}
