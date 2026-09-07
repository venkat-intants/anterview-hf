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
import { GlassCard, Pill, StatusTag } from '@/design/components/primitives';
import { AlertCircle, Lock, Plus, Trash2 } from '@/design/components/icons';
import { cn } from '@/lib/utils';

const INPUT =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13.5px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50';

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
    mutationFn: (body: Parameters<typeof updateQuestion>[1]) =>
      updateQuestion(question.id, body),
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
          <input
            aria-label={`Question ${question.position + 1}`}
            value={prompt}
            disabled={frozen}
            onChange={(e) => setPrompt(e.target.value)}
            onBlur={() => {
              if (!frozen && prompt.trim() && prompt !== question.prompt) {
                save.mutate({ prompt: prompt.trim() });
              }
            }}
            className={INPUT}
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusTag tone="neutral">{QUESTION_KIND_LABELS[question.kind]}</StatusTag>
            <label className="flex cursor-pointer items-center gap-1.5 text-[12.5px] text-[#b8babf]">
              <input
                type="checkbox"
                checked={question.required}
                onChange={(e) => save.mutate({ required: e.target.checked })}
                className="h-3.5 w-3.5 accent-[var(--accent)]"
              />
              Required
            </label>
            {question.options.length > 0 ? (
              <span className="text-[12px] text-[#70757c]">
                {question.options.join(' · ')}
              </span>
            ) : null}
          </div>
        </div>

        <button
          type="button"
          onClick={() => retire.mutate()}
          aria-label={`Retire question: ${question.prompt}`}
          className="shrink-0 rounded-[8px] p-1.5 text-[#888b91] hover:text-[#e6714f] focus:outline-none focus-visible:text-[#e6714f]"
        >
          <Trash2 size={15} aria-hidden="true" />
        </button>
      </div>

      {/* Said before anyone types, not after they press save. */}
      {frozen ? (
        <p className="mt-3 flex items-start gap-2 rounded-[10px] border border-white/[0.07] bg-white/[0.02] p-2.5 text-[12px] leading-relaxed text-[#888b91]">
          <Lock size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            {question.answer_count}{' '}
            {question.answer_count === 1 ? 'applicant has' : 'applicants have'} answered
            this, so the wording is fixed — changing it would rewrite what they were
            asked. Retire it and add a new one instead; their answers stay on file.
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
        className="flex w-full items-center justify-center gap-1.5 rounded-[14px] border border-dashed border-white/15 py-3 text-[13px] text-[#b8babf] hover:text-white focus:outline-none focus-visible:border-[var(--accent)]"
      >
        <Plus size={14} aria-hidden="true" />
        Add a question
      </button>
    );
  }

  return (
    <GlassCard className="p-4">
      <div className="flex flex-col gap-3">
        <div>
          <label
            htmlFor="new-question-prompt"
            className="mb-1.5 block text-[12.5px] font-medium text-[#b8babf]"
          >
            What do you want to ask?
          </label>
          <input
            id="new-question-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Why are you interested in this role?"
            className={INPUT}
          />
        </div>

        <div>
          <label
            htmlFor="new-question-kind"
            className="mb-1.5 block text-[12.5px] font-medium text-[#b8babf]"
          >
            Answer type
          </label>
          <select
            id="new-question-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as QuestionKind)}
            className={INPUT}
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
          <p className="mt-1 text-[11.5px] text-[#70757c]">
            This cannot be changed later, so pick the one that fits.
          </p>
        </div>

        {needsOptions ? (
          <div>
            <label
              htmlFor="new-question-options"
              className="mb-1.5 block text-[12.5px] font-medium text-[#b8babf]"
            >
              Options, one per line
            </label>
            <textarea
              id="new-question-options"
              rows={3}
              value={optionText}
              onChange={(e) => setOptionText(e.target.value)}
              placeholder={'Immediate\n30 days\n60 days'}
              className={INPUT}
            />
            {!optionsOk ? (
              <p className="mt-1 flex items-center gap-1.5 text-[11.5px] text-[#e6714f]">
                <AlertCircle size={12} aria-hidden="true" />
                A choice needs at least two options.
              </p>
            ) : null}
          </div>
        ) : null}

        <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-[#b8babf]">
          <input
            type="checkbox"
            checked={required}
            onChange={(e) => setRequired(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
          Applicants must answer this
        </label>

        <div className="flex items-center gap-2">
          <Pill
            onClick={() => add.mutate()}
            disabled={!canAdd || add.isPending}
            className="px-4 py-2"
          >
            {add.isPending ? 'Adding…' : 'Add question'}
          </Pill>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-[12.5px] text-[#888b91] hover:text-white"
          >
            Cancel
          </button>
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
        <h2 id="questions-heading" className="text-[16px] font-semibold text-white">
          Application questions
        </h2>
        <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-[#888b91]">
          Asked on the application form, after the CV. Leave this empty and the form
          simply does not have a questions step.
        </p>
      </div>

      {questions.isError ? (
        <p className="text-[13px] text-[#888b91]">
          Could not load the questions. Refresh to try again.
        </p>
      ) : null}

      {items.map((q) => (
        <QuestionRow key={q.id} question={q} requisitionId={requisitionId} />
      ))}

      <NewQuestion requisitionId={requisitionId} />

      {items.length > 0 ? (
        <p className={cn('text-[12px] text-[#70757c]')}>
          {items.filter((q) => q.required).length} of {items.length} required.
        </p>
      ) : null}
    </section>
  );
}
