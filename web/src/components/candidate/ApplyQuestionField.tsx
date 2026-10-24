// One recruiter-defined screening question, shared by the apply form and the
// saved-application page so both ask it the same way.
//
// Every kind is a plain form control rather than anything clever: these are
// pages a stranger fills in on a phone, and a custom widget is a thing that
// can fail to work there. The server validates all of it again regardless.

import { useTranslation } from 'react-i18next';
import type { AnswerValue, PostingQuestion } from '@/api/publicApply';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

export function ApplyQuestionField({
  question,
  value,
  onChange,
}: {
  question: PostingQuestion;
  value: AnswerValue | undefined;
  onChange: (next: AnswerValue) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const id = `q-${question.id}`;

  const optional = (
    <span className="ml-1 font-normal text-[var(--ui-soft)]">{t('apply.optional')}</span>
  );
  const label = (
    <span className="mb-1.5 block text-[13px] font-medium text-foreground">
      {question.prompt}
      {question.required ? (
        <span className="ml-1 text-[var(--ui-danger)]" aria-hidden="true">
          *
        </span>
      ) : (
        optional
      )}
    </span>
  );

  const help = question.help_text ? (
    <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">{question.help_text}</p>
  ) : null;

  if (question.kind === 'yes_no') {
    return (
      <fieldset>
        <legend className="mb-1.5 text-[13px] font-medium text-foreground">
          {question.prompt}
          {question.required ? null : optional}
        </legend>
        <div className="flex gap-2">
          {[
            { label: t('apply.yes'), v: true },
            { label: t('apply.no'), v: false },
          ].map((opt) => (
            <label
              key={String(opt.v)}
              className={cn(
                'cursor-pointer rounded-full border px-5 py-2.5 text-[14px] focus-within:ring-2 focus-within:ring-[var(--accent)]',
                value === opt.v
                  ? 'border-foreground bg-[var(--ui-inset)] font-medium text-foreground'
                  : 'border-border text-[var(--ui-soft)]',
              )}
            >
              <input
                type="radio"
                name={id}
                className="sr-only"
                checked={value === opt.v}
                onChange={() => onChange(opt.v)}
              />
              {opt.label}
            </label>
          ))}
        </div>
        {help}
      </fieldset>
    );
  }

  if (question.kind === 'single_choice') {
    return (
      <div>
        <label htmlFor={id}>{label}</label>
        <SelectControl
          id={id}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{t('apply.chooseOne')}</option>
          {question.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </SelectControl>
        {help}
      </div>
    );
  }

  if (question.kind === 'multi_choice') {
    const chosen = Array.isArray(value) ? value : [];
    return (
      <fieldset>
        <legend className="mb-1.5 text-[13px] font-medium text-foreground">
          {question.prompt}
          {question.required ? null : optional}
        </legend>
        <div className="flex flex-wrap gap-2">
          {question.options.map((o) => (
            <label
              key={o}
              className={cn(
                'cursor-pointer rounded-full border px-4 py-2 text-[13.5px] focus-within:ring-2 focus-within:ring-[var(--accent)]',
                chosen.includes(o)
                  ? 'border-foreground bg-[var(--ui-inset)] font-medium text-foreground'
                  : 'border-border text-[var(--ui-soft)]',
              )}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={chosen.includes(o)}
                onChange={() =>
                  onChange(chosen.includes(o) ? chosen.filter((c) => c !== o) : [...chosen, o])
                }
              />
              {o}
            </label>
          ))}
        </div>
        {help}
      </fieldset>
    );
  }

  if (question.kind === 'long_text') {
    return (
      <div>
        <label htmlFor={id}>{label}</label>
        <Textarea
          id={id}
          rows={4}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
        />
        {help}
      </div>
    );
  }

  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <Input
        id={id}
        type={question.kind === 'number' ? 'number' : 'text'}
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => onChange(e.target.value)}
      />
      {help}
    </div>
  );
}
