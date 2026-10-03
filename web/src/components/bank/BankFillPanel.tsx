// BankFillPanel — the three ways to put questions into a bank, plus the two
// bulk review actions that make a large import usable.
//
// Until 2026-10-03 a bank could only be filled one question at a time, while an
// EXAM's section had both AI generation and spreadsheet import. That was
// backwards: the bank is the thing worth filling once and drawing exams from.
//
// AI IS A PREVIEW, NEVER A SAVE. Generation returns candidates and HR reads
// them before anything is stored — the same two-step the exam generator uses,
// for the same reason: a model's output is a suggestion, and these questions go
// on to decide whether people get interviews.
//
// THE REVIEW RULE IS NOT RELAXED BY EITHER BULK BUTTON. The server loops the
// same per-question review, so the author, the submitter and anyone who edited
// a question's content are each refused individually. A one-person bank
// approves nothing here, and this panel says so in words rather than reporting
// a success — see the skipped list below.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Download, Loader2, Sparkles, Upload } from '@/design/components/icons';
import { GlassCard } from '@/design/components/primitives';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  approveAllBankQuestions,
  createBankQuestionsBulk,
  downloadBankQuestionTemplate,
  generateBankQuestions,
  importBankQuestions,
  submitAllBankQuestions,
  type BankDifficulty,
  type BankImportRowError,
  type BankQuestionInput,
  type BulkReviewResult,
} from '@/api/questionBanks';
import type { ExamLanguage } from '@/api/exams';

const field =
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground ' +
  'placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';

const DIFFICULTIES: BankDifficulty[] = ['easy', 'medium', 'hard'];
const LANGUAGES: ExamLanguage[] = ['en', 'hi', 'te'];

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export default function BankFillPanel({ bankId }: { bankId: string }): JSX.Element {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const [topic, setTopic] = useState('');
  const [count, setCount] = useState('5');
  const [difficulty, setDifficulty] = useState<BankDifficulty>('medium');
  const [language, setLanguage] = useState<ExamLanguage>('en');
  const [jobTitle, setJobTitle] = useState('');
  const [preview, setPreview] = useState<BankQuestionInput[] | null>(null);
  const [rowErrors, setRowErrors] = useState<BankImportRowError[]>([]);
  const [bulk, setBulk] = useState<{ what: string; result: BulkReviewResult } | null>(null);

  // The page's list query is keyed ['hr','question-banks', bankId, 'questions',
  // filters] and its bank header is ['hr','question-banks']. React Query
  // prefix-matches, so this one key refreshes both — the first version of this
  // used 'question-bank' (singular) and matched nothing, which meant importing
  // 40 questions left the table showing none of them.
  const invalidate = (): void => {
    void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
  };

  const genMut = useMutation({
    mutationFn: () =>
      generateBankQuestions(bankId, {
        topic: topic.trim(),
        num_questions: Math.max(1, Math.min(20, Number(count) || 5)),
        difficulty,
        language,
        job_title: jobTitle.trim(),
      }),
    onSuccess: (out) => {
      setPreview(out.questions);
      setRowErrors([]);
      setBulk(null);
    },
    onError: (e) => toast.error(errText(e, 'Could not generate questions')),
  });

  const keepMut = useMutation({
    mutationFn: (questions: BankQuestionInput[]) => createBankQuestionsBulk(bankId, questions),
    onSuccess: (saved) => {
      toast.success(
        `${saved.length} question${saved.length === 1 ? '' : 's'} added as drafts`,
      );
      setPreview(null);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'Could not save those questions')),
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importBankQuestions(bankId, file, { difficulty, language }),
    onSuccess: (out) => {
      setRowErrors(out.errors);
      setPreview(null);
      setBulk(null);
      if (out.added > 0) {
        toast.success(`${out.added} question${out.added === 1 ? '' : 's'} added as drafts`);
        invalidate();
      } else {
        // Not a success, and not a crash either — the file was read and every
        // row was unusable. Saying "0 added" with the reasons below is the
        // only honest report.
        toast.warning('No rows could be imported — see the list below');
      }
    },
    onError: (e) => toast.error(errText(e, 'Could not import that file')),
  });

  const templateMut = useMutation({
    mutationFn: downloadBankQuestionTemplate,
    onError: (e) => toast.error(errText(e, 'Could not download the template')),
  });

  const submitAllMut = useMutation({
    mutationFn: () => submitAllBankQuestions(bankId),
    onSuccess: (result) => {
      setBulk({ what: 'submitted', result });
      if (result.acted > 0) {
        toast.success(`${result.acted} sent for review`);
        invalidate();
      } else {
        toast.warning('Nothing was sent for review');
      }
    },
    onError: (e) => toast.error(errText(e, 'Could not submit these for review')),
  });

  const approveAllMut = useMutation({
    mutationFn: () => approveAllBankQuestions(bankId),
    onSuccess: (result) => {
      setBulk({ what: 'approved', result });
      if (result.acted > 0) {
        toast.success(`${result.acted} approved`);
        invalidate();
      } else {
        // The expected answer in a one-person company, so it is worded as the
        // rule rather than as a failure.
        toast.warning('Nothing could be approved by you — another reviewer must');
      }
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'Could not approve these')),
  });

  const busy =
    genMut.isPending || keepMut.isPending || importMut.isPending ||
    submitAllMut.isPending || approveAllMut.isPending;

  return (
    <GlassCard className="mt-5 p-5" data-testid="bank-fill-panel">
      <h2 className="text-[15px] font-semibold text-foreground">Add many questions</h2>
      <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">
        Everything added here arrives as a <span className="font-medium">draft</span> and goes
        through the same review as a question you type by hand. Importing is not a way past it.
      </p>

      {/* ── 1. AI ──────────────────────────────────────────────────────── */}
      <section className="mt-4 rounded-[12px] border border-border p-4">
        <div className="flex items-center gap-1.5">
          <Sparkles size={14} className="text-[var(--ui-faint)]" aria-hidden="true" />
          <h3 className="text-[13px] font-medium text-foreground">Draft with AI</h3>
        </div>
        <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
          <label className="block">
            <span className="text-[12px] text-[var(--ui-soft)]">Topic</span>
            <input
              className={cn(field, 'mt-1')}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Electrical safety, three-phase wiring…"
              aria-label="Topic"
            />
          </label>
          <label className="block">
            <span className="text-[12px] text-[var(--ui-soft)]">Role (optional)</span>
            <input
              className={cn(field, 'mt-1')}
              value={jobTitle}
              onChange={(e) => setJobTitle(e.target.value)}
              placeholder="Bench Fitter"
              aria-label="Role"
            />
          </label>
        </div>
        <div className="mt-2.5 grid gap-2.5 sm:grid-cols-3">
          <label className="block">
            <span className="text-[12px] text-[var(--ui-soft)]">How many</span>
            <input
              className={cn(field, 'mt-1')}
              type="number"
              min={1}
              max={20}
              value={count}
              onChange={(e) => setCount(e.target.value)}
              aria-label="How many"
            />
          </label>
          <label className="block">
            <span className="text-[12px] text-[var(--ui-soft)]">Difficulty</span>
            <select
              className={cn(field, 'mt-1')}
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value as BankDifficulty)}
              aria-label="Difficulty"
            >
              {DIFFICULTIES.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-[12px] text-[var(--ui-soft)]">Language</span>
            <select
              className={cn(field, 'mt-1')}
              value={language}
              onChange={(e) => setLanguage(e.target.value as ExamLanguage)}
              aria-label="Language"
            >
              {LANGUAGES.map((l) => (
                <option key={l} value={l}>{l.toUpperCase()}</option>
              ))}
            </select>
          </label>
        </div>
        <button
          type="button"
          disabled={busy || topic.trim().length < 2}
          onClick={() => genMut.mutate()}
          className="mt-3 inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground disabled:opacity-40"
        >
          {genMut.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles size={15} aria-hidden="true" />
          )}
          Generate a preview
        </button>

        {preview ? (
          <div className="mt-3" data-testid="bank-ai-preview">
            <p className="text-[12.5px] text-muted-foreground">
              {preview.length} suggested — nothing is saved until you add them.
            </p>
            <ul className="mt-2 flex flex-col gap-2">
              {preview.map((q, i) => (
                <li key={`${i}-${q.prompt.slice(0, 24)}`} className="rounded-[10px] border border-border p-2.5">
                  <p className="text-[12.5px] text-foreground">{q.prompt}</p>
                  <ul className="mt-1 flex flex-col gap-0.5">
                    {(q.options ?? []).map((o, oi) => (
                      <li
                        key={`${i}-opt-${oi}`}
                        className={cn(
                          'text-[11.5px]',
                          oi === q.correct_index
                            ? 'font-medium text-[var(--ui-ok)]'
                            : 'text-muted-foreground',
                        )}
                      >
                        {String.fromCharCode(65 + oi)}. {o}
                        {oi === q.correct_index ? ' ✓' : ''}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => keepMut.mutate(preview)}
                className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground disabled:opacity-40"
              >
                {keepMut.isPending ? 'Adding…' : `Add all ${preview.length} as drafts`}
              </button>
              <button
                type="button"
                onClick={() => setPreview(null)}
                className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground"
              >
                Discard
              </button>
            </div>
          </div>
        ) : null}
      </section>

      {/* ── 2. Spreadsheet ─────────────────────────────────────────────── */}
      <section className="mt-4 rounded-[12px] border border-border p-4">
        <div className="flex items-center gap-1.5">
          <Upload size={14} className="text-[var(--ui-faint)]" aria-hidden="true" />
          <h3 className="text-[13px] font-medium text-foreground">Import from Excel or CSV</h3>
        </div>
        <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">
          One question per row. The <span className="font-medium">Correct</span> column takes a
          letter (A–D), a number (1–4) or the option&apos;s own text. Difficulty, Language and
          Competency are optional — rows that leave them blank use the choices above, so an exam
          sheet you already have will import as it is.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={templateMut.isPending}
            onClick={() => templateMut.mutate()}
            className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] px-3.5 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
          >
            <Download size={15} aria-hidden="true" />
            {templateMut.isPending ? 'Preparing…' : 'Download the template'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.csv"
            className="hidden"
            aria-label="Choose a spreadsheet to import"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) importMut.mutate(file);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground disabled:opacity-40"
          >
            {importMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Upload size={15} aria-hidden="true" />
            )}
            Choose a file
          </button>
        </div>

        {rowErrors.length > 0 ? (
          <div className="mt-3 rounded-[10px] border border-[var(--ui-warn)]/30 p-3" data-testid="bank-import-errors">
            <p className="text-[12.5px] font-medium text-foreground">
              {rowErrors.length} row{rowErrors.length === 1 ? '' : 's'} could not be imported
            </p>
            {/* The row number is the whole point — "import failed" sends
                someone back to a 40-row file with nowhere to look. */}
            <ul className="mt-1.5 flex flex-col gap-0.5">
              {rowErrors.map((e) => (
                <li key={`${e.row}-${e.message}`} className="font-mono text-[11.5px] text-muted-foreground">
                  Row {e.row}: {e.message}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-[11.5px] text-[var(--ui-faint)]">
              Everything else was imported. Fix these rows and import the file again — the
              questions already added are not duplicated by a second import of the same rows
              unless the rows themselves are duplicates.
            </p>
          </div>
        ) : null}
      </section>

      {/* ── 3. Getting a batch through review ──────────────────────────── */}
      <section className="mt-4 rounded-[12px] border border-border p-4">
        <h3 className="text-[13px] font-medium text-foreground">Send a batch through review</h3>
        <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">
          A question can only go into an exam once it is approved, and{' '}
          <span className="font-medium">someone other than its author must approve it</span>.
          These two buttons do that in one request instead of one per question — they do not skip
          it. If you wrote or imported these questions, the approve button will tell you it
          approved nothing.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => submitAllMut.mutate()}
            className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
          >
            {submitAllMut.isPending ? 'Sending…' : 'Send all drafts for review'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => approveAllMut.mutate()}
            className="rounded-[10px] border border-[var(--ui-ok)]/35 px-4 py-2 text-[13px] text-[var(--ui-ok)] hover:bg-[var(--ui-ok)]/10 disabled:opacity-40"
          >
            {approveAllMut.isPending ? 'Approving…' : 'Approve all I am allowed to'}
          </button>
        </div>

        {bulk ? (
          <div className="mt-3 rounded-[10px] border border-border p-3" data-testid="bank-bulk-result">
            <p className="text-[12.5px] text-foreground">
              {bulk.result.acted} {bulk.what}.
              {bulk.result.skipped.length > 0
                ? ` ${bulk.result.skipped.length} left for someone else.`
                : ''}
            </p>
            {bulk.result.skipped.length > 0 ? (
              <>
                {/* Reasons, not an error list: this is the review rule
                    answering per question, and HR needs to know which. */}
                <ul className="mt-1.5 flex flex-col gap-0.5">
                  {Array.from(new Set(bulk.result.skipped.map((s) => s.reason))).map((reason) => (
                    <li key={reason} className="text-[11.5px] text-muted-foreground">
                      {reason} (
                      {bulk.result.skipped.filter((s) => s.reason === reason).length})
                    </li>
                  ))}
                </ul>
                {bulk.result.acted === 0 && bulk.what === 'approved' ? (
                  <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--ui-faint)]">
                    Nothing was approved. Ask another HR manager, or your company&apos;s super
                    admin, to review these — that second pair of eyes is the point of the rule,
                    not an obstacle to it.
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
      </section>
    </GlassCard>
  );
}
