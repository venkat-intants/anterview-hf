// SaveToBankButton — PH4-D1. The exam editor's row action that copies a
// live exam or coding question into a bank as a new draft
// (`origin: 'from_exam'`) — the exam's own copy is untouched either way.

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listQuestionBanks } from '@/api/questionBanks';
import type { BankDifficulty, SaveToBankInput } from '@/api/questionBanks';
import type { ExamLanguage } from '@/api/exams';
import { Loader2 } from '@/design/components/icons';
import { cn } from '@/lib/utils';

const selectCls =
  'rounded-[8px] border border-border bg-secondary px-2 py-1 text-[11.5px] text-foreground focus:border-[var(--accent)] focus:outline-none';

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
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn('text-[11.5px] text-[var(--ui-info)] hover:underline', className)}
      >
        Save to bank
      </button>
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
      <select
        value={bankId}
        onChange={(e) => setBankId(e.target.value)}
        aria-label="Bank"
        className={selectCls}
      >
        <option value="">
          {banks.isLoading ? 'Loading banks…' : 'Choose a bank'}
        </option>
        {(banks.data ?? []).map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
      <select
        value={difficulty}
        onChange={(e) => setDifficulty(e.target.value as BankDifficulty)}
        aria-label="Difficulty"
        className={selectCls}
      >
        <option value="easy">Easy</option>
        <option value="medium">Medium</option>
        <option value="hard">Hard</option>
      </select>
      <select
        value={language}
        onChange={(e) => setLanguage(e.target.value as ExamLanguage)}
        aria-label="Language"
        className={selectCls}
      >
        <option value="en">EN</option>
        <option value="hi">HI</option>
        <option value="te">TE</option>
      </select>
      <button
        type="button"
        disabled={!ready || saving}
        onClick={() => onSave({ bank_id: bankId, difficulty, language })}
        className="inline-flex items-center gap-1 rounded-[8px] bg-primary px-2.5 py-1 text-[11.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {saving ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> : null}
        Save
      </button>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="text-[11.5px] text-muted-foreground hover:text-foreground"
      >
        Cancel
      </button>
    </div>
  );
}
