// QuestionEditor — the questions an opening asks its applicants.
//
// Builder step 4. HR writes them here; the public form renders whatever is in
// this list; the answers arrive with each application.
//
// THE THING THIS UI EXISTS TO EXPLAIN
// Once anybody has answered a question, its wording and its type are frozen —
// the server refuses an edit with a 409. That rule is right (rewording "Do you
// have a work visa?" into "Do you need visa sponsorship?" would invert every
// stored yes with nothing recording it) but it is surprising, and a form that
// only surprises you at save time is a form that wasted your work.
//
// So the freeze is visible before anyone types: an answered question shows its
// answer count, its prompt field is disabled, and the retire button explains
// what retiring does to the answers. `answer_count` is on the API for exactly
// this.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CHOICE_KINDS,
  QUESTION_KIND_LABELS,
  addQuestion,
  isFrozen,
  listQuestions,
  retireQuestion,
  updateQuestion,
  type ApplicationQuestion,
  type QuestionKind,
} from '@/api/questions';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { EmptyState } from '@/design/components/page';
import { AlertCircle, Lock, Plus, Trash2 } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

// Native <select>/<textarea> in the same look as the shared Input.
const FIELD =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30 disabled:opacity-50';
const SELECT = `${FIELD} h-11 py-0`;
const LABEL = 'mb-1.5 block text-[13px] font-semibold text-foreground';

const KIND_ORDER: QuestionKind[] = [
  'short_text',
  'long_text',
  'number',
  'single_choice',
  'multi_choice',
  'yes_no',
];

function QuestionRow({
  question,
  requisitionId,
}: {
  question: ApplicationQuestion;
  requisitionId: string;
}) {
  const client = useQueryClient();
  const frozen = isFrozen(question);
  const [prompt, setPrompt] = useState(question.prompt);

  const save = useMutation({
    mutationFn: (body: Parameters<typeof updateQuestion>[1]) => updateQuestion(question.id, body),
    onSuccess: (list) => client.setQueryData(['questions', requisitionId], list),
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not save that change.'),
  });

  const retire = useMutation({
    mutationFn: () => retireQuestion(question.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['questions', requisitionId] });
      toast.success('Question retired. Existing answers are still on file.');
    },
  });

  return (
    <GlassCard className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Input
            aria-label={`Question ${question.position + 1}`}
            value={prompt}
            disabled={frozen}
            onChange={(e) => setPrompt(e.target.value)}
            onBlur={() => {
              if (!frozen && prompt.trim() && prompt !== question.prompt) {
                save.mutate({ prompt: prompt.trim() });
              }
            }}
          />
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusTag tone="neutral">{QUESTION_KIND_LABELS[question.kind]}</StatusTag>
            <label className="flex min-h-10 cursor-pointer items-center gap-2 text-[13px] text-[var(--ui-soft)]">
              <input
                type="checkbox"
                checked={question.required}
                onChange={(e) => save.mutate({ required: e.target.checked })}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Required
            </label>
            {question.options.length > 0 ? (
              <span className="min-w-0 break-words text-[12.5px] text-muted-foreground">
                {question.options.join(' · ')}
              </span>
            ) : null}
          </div>
        </div>

        <button
          type="button"
          onClick={() => retire.mutate()}
          aria-label={`Retire question: ${question.prompt}`}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          <Trash2 size={16} aria-hidden="true" />
        </button>
      </div>

      {/* Said before anyone types, not after they press save. */}
      {frozen ? (
        <p className="mt-3 flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3 text-[13px] leading-relaxed text-muted-foreground">
          <Lock size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            {question.answer_count}{' '}
            {question.answer_count === 1 ? 'applicant has' : 'applicants have'} answered this, so
            the wording is fixed - changing it would rewrite what they were asked. Retire it and add
            a new one instead; their answers stay on file.
          </span>
        </p>
      ) : null}
    </GlassCard>
  );
}

function NewQuestion({ requisitionId }: { requisitionId: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [kind, setKind] = useState<QuestionKind>('short_text');
  const [required, setRequired] = useState(false);
  const [optionText, setOptionText] = useState('');

  const needsOptions = CHOICE_KINDS.includes(kind);
  const options = optionText
    .split('\n')
    .map((o) => o.trim())
    .filter(Boolean);
  // Mirrors the server's rule so the reason is visible next to the field
  // rather than arriving as a 422 after pressing Add.
  const optionsOk = !needsOptions || options.length >= 2;
  const canAdd = prompt.trim().length >= 3 && optionsOk;

  const add = useMutation({
    mutationFn: () =>
      addQuestion(requisitionId, {
        prompt: prompt.trim(),
        kind,
        required,
        options: needsOptions ? options : [],
      }),
    onSuccess: (list) => {
      client.setQueryData(['questions', requisitionId], list);
      setPrompt('');
      setOptionText('');
      setRequired(false);
      setOpen(false);
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not add that question.'),
  });

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex min-h-12 w-full items-center justify-center gap-1.5 rounded-[16px] border border-dashed border-[var(--ui-line-strong)] py-3 text-[14px] font-semibold text-[var(--ui-soft)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <Plus size={16} aria-hidden="true" />
        Add a question
      </button>
    );
  }

  return (
    <GlassCard className="p-4">
      <div className="flex flex-col gap-4">
        <div>
          <label htmlFor="new-question-prompt" className={LABEL}>
            What do you want to ask?
          </label>
          <Input
            id="new-question-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Why are you interested in this role?"
          />
        </div>

        <div>
          <label htmlFor="new-question-kind" className={LABEL}>
            Answer type
          </label>
          <select
            id="new-question-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as QuestionKind)}
            className={SELECT}
          >
            {KIND_ORDER.map((k) => (
              <option key={k} value={k}>
                {QUESTION_KIND_LABELS[k]}
              </option>
            ))}
          </select>
          {/* The type cannot be changed later, answered or not — changing it
              would reinterpret every answer given. Said here, where it is
              still a free choice. */}
          <p className="mt-1.5 text-[12.5px] text-muted-foreground">
            This cannot be changed later, so pick the one that fits.
          </p>
        </div>

        {needsOptions ? (
          <div>
            <label htmlFor="new-question-options" className={LABEL}>
              Options, one per line
            </label>
            <textarea
              id="new-question-options"
              rows={3}
              value={optionText}
              onChange={(e) => setOptionText(e.target.value)}
              placeholder={'Immediate\n30 days\n60 days'}
              className={FIELD}
            />
            {!optionsOk ? (
              <p
                role="alert"
                className="mt-1.5 flex items-center gap-1.5 text-[12.5px] text-[var(--ui-danger)]"
              >
                <AlertCircle size={13} aria-hidden="true" />A choice needs at least two options.
              </p>
            ) : null}
          </div>
        ) : null}

        <label className="flex min-h-10 cursor-pointer items-center gap-2 text-[14px] text-[var(--ui-soft)]">
          <input
            type="checkbox"
            checked={required}
            onChange={(e) => setRequired(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Applicants must answer this
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={() => add.mutate()} disabled={!canAdd || add.isPending}>
            {add.isPending ? 'Adding…' : 'Add question'}
          </Button>
          <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      </div>
    </GlassCard>
  );
}

export default function QuestionEditor({ requisitionId }: { requisitionId: string }) {
  const questions = useQuery({
    queryKey: ['questions', requisitionId],
    queryFn: () => listQuestions(requisitionId),
    retry: false,
    throwOnError: false,
  });

  const items = questions.data ?? [];

  return (
    <section aria-labelledby="questions-heading" className="flex flex-col gap-3">
      <div>
        <h2
          id="questions-heading"
          className="text-[17px] font-bold tracking-[-0.2px] text-foreground"
        >
          Application questions
        </h2>
        <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-muted-foreground">
          Asked on the application form, after the CV. Leave this empty and the form simply does not
          have a questions step.
        </p>
      </div>

      {questions.isLoading ? (
        <div className="flex flex-col gap-3" aria-busy="true">
          <span className="sr-only">Loading…</span>
          <Skeleton className="h-24 rounded-card" />
        </div>
      ) : null}

      {questions.isError ? (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-[13.5px] text-muted-foreground">
            Could not load the questions. Refresh to try again.
          </p>
          <Button type="button" variant="outline" onClick={() => void questions.refetch()}>
            Try again
          </Button>
        </div>
      ) : null}

      {!questions.isLoading && !questions.isError && items.length === 0 ? (
        <EmptyState title="No questions yet" description="Add the first one below." />
      ) : null}

      {items.map((q) => (
        <QuestionRow key={q.id} question={q} requisitionId={requisitionId} />
      ))}

      <NewQuestion requisitionId={requisitionId} />

      {items.length > 0 ? (
        <p className="text-[12.5px] text-muted-foreground">
          {items.filter((q) => q.required).length} of {items.length} required.
        </p>
      ) : null}
    </section>
  );
}
