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
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Loader2, X } from '@/design/components/icons';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const inputCls =
  'rounded-[9px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground ' +
  'placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';

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
        toast.error('Nothing was added — see the reasons below.');
      }
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not add these questions')),
  });

  function toggle(id: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const reasonById = new Map((result?.skipped ?? []).map((s) => [s.id, s.reason]));

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="bank-picker-heading"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-full max-w-[720px] flex-col overflow-hidden rounded-[18px] border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 id="bank-picker-heading" className="text-[15px] font-semibold text-foreground">
            Add from bank — {sectionKind === 'mcq' ? 'MCQ' : 'Coding'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-[8px] p-1 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex flex-wrap gap-2 border-b border-border px-5 py-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search prompt…"
            aria-label="Search bank questions"
            className={cn(inputCls, 'min-w-[160px] flex-1')}
          />
          <select
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as BankDifficulty | '')}
            aria-label="Filter by difficulty"
            className={inputCls}
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
            className={inputCls}
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
            className={inputCls}
          >
            <option value="">Any competency</option>
            {(catalog.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3">
          {search.isLoading ? (
            <p className="flex items-center gap-2 py-6 text-[13px] text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading…
            </p>
          ) : search.isError ? (
            <p className="py-6 text-[13px] text-[var(--ui-danger)]">
              Could not load the bank&apos;s approved questions.
            </p>
          ) : rows.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">
              No approved {sectionKind === 'mcq' ? 'MCQ' : 'coding'} questions match these filters.
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5" aria-label="Bank questions">
              {rows.map((row) => {
                const disabled = row.already_in_exam;
                const reason = reasonById.get(row.id);
                return (
                  <li
                    key={row.id}
                    className={cn(
                      'flex items-start gap-2.5 rounded-[10px] border border-border px-3 py-2.5',
                      disabled && 'opacity-50',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(row.id)}
                      disabled={disabled}
                      onChange={(e) => toggle(row.id, e.target.checked)}
                      aria-label={`Select ${row.prompt}`}
                      className="mt-1 h-3.5 w-3.5 accent-[var(--accent)]"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-foreground">
                        {row.prompt}
                      </p>
                      <p className="text-[11.5px] text-muted-foreground">
                        {row.bank_name} · v{row.version} · {row.difficulty} · {row.language}
                      </p>
                      {disabled ? (
                        <p className="mt-0.5 text-[11px] text-[var(--ui-warn)]">
                          {reason ?? 'Already in this exam'}
                        </p>
                      ) : reason ? (
                        <p className="mt-0.5 text-[11px] text-[var(--ui-danger)]">Skipped — {reason}</p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {result ? (
          <div className="border-t border-border px-5 py-3 text-[12.5px]">
            <p className="text-[var(--ui-ok)]">
              {result.added} added
              {result.skipped.length > 0 ? `, ${result.skipped.length} skipped` : ''}.
            </p>
          </div>
        ) : null}

        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <span className="text-[12px] text-muted-foreground">
            {selected.size} selected · {pickableCount} available
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground"
            >
              Done
            </button>
            <button
              type="button"
              disabled={selected.size === 0 || addMut.isPending}
              onClick={() => addMut.mutate()}
              className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
            >
              {addMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
              Add {selected.size || ''} question{selected.size === 1 ? '' : 's'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
