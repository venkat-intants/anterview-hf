// SaveToBankButton — PH4-D1. The exam editor's row action that copies a
// live exam or coding question into a bank as a new draft
// (`origin: 'from_exam'`) — the exam's own copy is untouched either way.

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listQuestionBanks } from '@/api/questionBanks';
import type { BankDifficulty, SaveToBankInput } from '@/api/questionBanks';
import type { ExamLanguage } from '@/api/exams';
import { Loader2 } from '@/design/components/icons';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { SelectControl } from '@/components/ui/select-control';
import { cn } from '@/lib/utils';

// SelectControl size="sm" gives the 36px compact control; only the width is
// set here so the longest bank name is not clipped.
const SELECT_WIDTH = 'w-auto min-w-[8.5rem] max-w-full';

export interface SaveToBankButtonProps {
  onSave: (input: SaveToBankInput) => void;
  saving?: boolean;
  className?: string;
}

export function SaveToBankButton({
  onSave,
  saving = false,
  className,
}: SaveToBankButtonProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [bankId, setBankId] = useState('');
  const [difficulty, setDifficulty] = useState<BankDifficulty>('medium');
  const [language, setLanguage] = useState<ExamLanguage>('en');

  const banks = useQuery({
    queryKey: ['hr', 'question-banks'],
    queryFn: listQuestionBanks,
    enabled: open,
  });

  if (!open) {
    return (
      <Button
        type="button"
        variant="link"
        onClick={() => setOpen(true)}
        className={cn('h-auto p-0 text-[12px] font-normal', className)}
      >
        Save to bank
      </Button>
    );
  }

  const ready = Boolean(bankId);

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1.5 rounded-[9px] border border-dashed border-border bg-[var(--ui-inset-soft)] p-2',
        className,
      )}
    >
      <SelectControl
        value={bankId}
        onChange={(e) => setBankId(e.target.value)}
        aria-label="Bank"
        size="sm"
        className={SELECT_WIDTH}
      >
        <option value="">{banks.isLoading ? 'Loading banks…' : 'Choose a bank'}</option>
        {(banks.data ?? []).map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </SelectControl>
      <SelectControl
        value={difficulty}
        onChange={(e) => setDifficulty(e.target.value as BankDifficulty)}
        aria-label="Difficulty"
        size="sm"
        className={SELECT_WIDTH}
      >
        <option value="easy">Easy</option>
        <option value="medium">Medium</option>
        <option value="hard">Hard</option>
      </SelectControl>
      <SelectControl
        value={language}
        onChange={(e) => setLanguage(e.target.value as ExamLanguage)}
        aria-label="Language"
        size="sm"
        className={SELECT_WIDTH}
      >
        <option value="en">EN</option>
        <option value="hi">HI</option>
        <option value="te">TE</option>
      </SelectControl>
      {/* A row action inside the exam editor, whose one primary is its own:
          Save is outline here, Cancel ghost. */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!ready || saving}
        onClick={() => onSave({ bank_id: bankId, difficulty, language })}
      >
        {saving ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
        Save
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {banks.isError ? (
        <QueryErrorPanel
          className="basis-full"
          title="Could not load the question banks."
          error={banks.error}
          onRetry={() => void banks.refetch()}
          retrying={banks.isFetching}
        />
      ) : null}
    </div>
  );
}
