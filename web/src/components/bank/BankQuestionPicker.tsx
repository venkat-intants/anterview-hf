// BankQuestionPicker — PH4-D1. "Add from bank" per exam section: approved
// questions of the section's own kind, across every bank in the company, with
// anything already copied into THIS exam disabled and its reason shown —
// `already_in_exam` and the skip reasons in the add response both come
// straight from `app/question_banks.py` (`picker_skip_reason`), so this panel
// can never show a row as pickable that the server would then skip anyway.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addBankQuestionsToSection,
  listBankCompetencies,
  searchBankQuestions,
  type AddToSectionResult,
  type BankDifficulty,
} from '@/api/questionBanks';
import type { ExamLanguage } from '@/api/exams';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Loader2 } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { QuestionBadges } from '@/components/bank/QuestionBadges';

// A native <select> keeps the filter row keyboard- and test-driven the way it was;
// the look matches the shared Input.
const selectCls =
  'h-10 rounded-[10px] border border-input bg-card px-3.5 text-[14px] text-foreground ' +
  'hover:border-[var(--ui-line-strong)] focus-visible:border-[var(--accent)] focus-visible:outline-none ' +
  'focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

/** A section add is one request carrying every id — an unbounded selection
 *  would be a payload of arbitrary size with no server-side cap to catch it. */
const SELECTION_CAP = 100;

export interface BankQuestionPickerProps {
  examId: string;
  sectionId: string;
  sectionKind: 'mcq' | 'coding';
  onClose: () => void;
  /** Fired once the section's questions should be refetched. */
  onAdded: () => void;
}

export function BankQuestionPicker({
  examId,
  sectionId,
  sectionKind,
  onClose,
  onAdded,
}: BankQuestionPickerProps): JSX.Element {
  const [q, setQ] = useState('');
  const [difficulty, setDifficulty] = useState<BankDifficulty | ''>('');
  const [language, setLanguage] = useState<ExamLanguage | ''>('');
  const [competency, setCompetency] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<AddToSectionResult | null>(null);
  const qc = useQueryClient();

  const catalog = useQuery({
    queryKey: ['hr', 'bank-competencies'],
    queryFn: listBankCompetencies,
  });

  const search = useQuery({
    queryKey: [
      'hr',
      'bank-questions',
      { kind: sectionKind, q, difficulty, language, competency, excludeExamId: examId },
    ],
    queryFn: () =>
      searchBankQuestions({
        kind: sectionKind,
        status: 'approved',
        q: q.trim() || undefined,
        difficulty: difficulty || undefined,
        language: language || undefined,
        competency: competency || undefined,
        excludeExamId: examId,
      }),
  });

  const rows = search.data ?? [];
  const pickableCount = rows.filter((r) => !r.already_in_exam).length;

  const addMut = useMutation({
    mutationFn: () => addBankQuestionsToSection(examId, sectionId, [...selected]),
    onSuccess: (res) => {
      setResult(res);
      setSelected(new Set());
      if (res.added > 0) {
        toast.success(`${res.added} question${res.added === 1 ? '' : 's'} added`);
        onAdded();
        void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
      } else {
        toast.error('Nothing was added - see the reasons below.');
      }
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add these questions.')),
  });

  function toggle(id: string, on: boolean) {
    setSelected((prev) => {
      if (on && !prev.has(id) && prev.size >= SELECTION_CAP) {
        toast.error(`You can select up to ${SELECTION_CAP} questions at once.`);
        return prev;
      }
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const reasonById = new Map((result?.skipped ?? []).map((s) => [s.id, s.reason]));

  return (
    // Radix supplies the dialog role, the focus trap, Escape and the overlay click.
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        className="flex max-h-[85dvh] w-[calc(100%-2rem)] max-w-[720px] flex-col gap-0 overflow-hidden p-0"
      >
        <div className="border-b border-border px-5 py-4 pr-14">
          <DialogTitle className="text-[17px] font-bold leading-snug tracking-[-0.2px]">
            Add from bank - {sectionKind === 'mcq' ? 'MCQ' : 'Coding'}
          </DialogTitle>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Approved questions from your banks. Each one is copied into this section.
          </p>
        </div>

        <div className="flex flex-wrap gap-2 border-b border-border px-5 py-3">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search prompt…"
            aria-label="Search bank questions"
            className="h-10 min-w-[160px] flex-1 basis-full rounded-[10px] sm:basis-40"
          />
          <select
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as BankDifficulty | '')}
            aria-label="Filter by difficulty"
            className={cn(selectCls, 'min-w-[10.5rem] flex-1')}
          >
            <option value="">Any difficulty</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value as ExamLanguage | '')}
            aria-label="Filter by language"
            className={cn(selectCls, 'min-w-[10.5rem] flex-1')}
          >
            <option value="">Any language</option>
            <option value="en">English</option>
            <option value="hi">हिन्दी</option>
            <option value="te">తెలుగు</option>
          </select>
          <select
            value={competency}
            onChange={(e) => setCompetency(e.target.value)}
            aria-label="Filter by competency"
            className={cn(selectCls, 'min-w-[10.5rem] flex-1')}
          >
            <option value="">Any competency</option>
            {(catalog.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {search.isLoading ? (
            <div className="flex flex-col gap-2 py-2" aria-busy="true">
              <span className="sr-only">Loading…</span>
              <Skeleton className="h-14 rounded-[12px]" />
              <Skeleton className="h-14 rounded-[12px]" />
              <Skeleton className="h-14 rounded-[12px]" />
            </div>
          ) : search.isError ? (
            <div role="alert" className="flex flex-wrap items-center gap-3 py-4">
              <p className="text-[14px] text-[var(--ui-danger)]">
                Could not load the bank&apos;s approved questions.
              </p>
              <Button type="button" variant="outline" onClick={() => void search.refetch()}>
                Try again
              </Button>
            </div>
          ) : rows.length === 0 ? (
            <p className="py-6 text-center text-[14px] text-muted-foreground">
              No approved {sectionKind === 'mcq' ? 'MCQ' : 'coding'} questions match these filters.
            </p>
          ) : (
            <ul className="flex flex-col gap-2" aria-label="Bank questions">
              {rows.map((row) => {
                const isSelected = selected.has(row.id);
                const capReached = !isSelected && selected.size >= SELECTION_CAP;
                const disabled = row.already_in_exam || capReached;
                const reason = reasonById.get(row.id);
                return (
                  <li
                    key={row.id}
                    className={cn(
                      'flex items-start gap-1 rounded-[12px] border border-border pr-3',
                      disabled && 'opacity-60',
                    )}
                  >
                    {/* The whole 40px square is the checkbox's hit area. */}
                    <label className="flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={disabled}
                        onChange={(e) => toggle(row.id, e.target.checked)}
                        aria-label={`Select ${row.prompt}`}
                        className="h-4 w-4 accent-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                      />
                    </label>
                    <div className="min-w-0 flex-1 py-2.5">
                      <p className="truncate text-[14px] font-medium text-foreground">
                        {row.prompt}
                      </p>
                      <p className="text-[12.5px] text-muted-foreground">
                        {row.bank_name} · v{row.version} · {row.difficulty} · {row.language}
                      </p>
                      <QuestionBadges question={row} className="mt-1" />
                      {row.already_in_exam ? (
                        <p className="mt-0.5 text-[12.5px] text-[var(--ui-warn)]">
                          {reason ?? 'Already in this exam'}
                        </p>
                      ) : capReached ? (
                        <p className="mt-0.5 text-[12.5px] text-[var(--ui-warn)]">
                          Selection limit reached ({SELECTION_CAP})
                        </p>
                      ) : reason ? (
                        <p className="mt-0.5 text-[12.5px] text-[var(--ui-danger)]">
                          Skipped - {reason}
                        </p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {result ? (
          <div className="border-t border-border px-5 py-3 text-[13px]" role="status">
            <p className="text-[var(--ui-ok)]">
              {result.added} added
              {result.skipped.length > 0 ? `, ${result.skipped.length} skipped` : ''}.
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3">
          <span className="text-[13px] text-muted-foreground">
            {selected.size}/{SELECTION_CAP} selected · {pickableCount} available
          </span>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Done
            </Button>
            <Button
              type="button"
              disabled={selected.size === 0 || addMut.isPending}
              onClick={() => addMut.mutate()}
            >
              {addMut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Add {selected.size || ''} question{selected.size === 1 ? '' : 's'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
