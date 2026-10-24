// AiDraftPreview: the AI-generated MCQ drafts, shown for review before any
// of them is added to the section. Split out of McqComposer.tsx.
// English-only by design (CLAUDE.md: staff consoles are not translated).

import { Plus, Check, Loader2, X } from '@/design/components/icons';
import type { GeneratedQuestion } from '@/api/exams';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { plural } from '@/lib/formatters';

interface AiDraftPreviewProps {
  drafts: GeneratedQuestion[];
  /** True while the drafts are being saved; every control is disabled. */
  adding: boolean;
  onDiscard: (index: number) => void;
  onDiscardAll: () => void;
  onAdd: () => void;
}

export function AiDraftPreview({
  drafts,
  adding,
  onDiscard,
  onDiscardAll,
  onAdd,
}: AiDraftPreviewProps) {
  return (
    <div className="space-y-2 rounded-[12px] border border-[rgba(var(--accent-rgb),0.25)] bg-[rgba(var(--accent-rgb),0.05)] p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] font-semibold text-[var(--ui-info)]">
          {plural(drafts.length, 'draft question')}
        </p>
        <Button type="button" variant="ghost" size="sm" disabled={adding} onClick={onDiscardAll}>
          Discard all
        </Button>
      </div>
      <ul className="space-y-1.5">
        {drafts.map((q, qi) => (
          <li
            key={qi}
            className="rounded-[9px] border border-border bg-[var(--ui-inset-soft)] p-2.5"
          >
            <div className="flex items-start justify-between gap-2">
              <p className="text-[12px] font-medium text-foreground">
                {qi + 1}. {q.prompt}
              </p>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Discard draft ${qi + 1}`}
                className="shrink-0 text-muted-foreground hover:text-[var(--ui-danger)]"
                disabled={adding}
                onClick={() => onDiscard(qi)}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
            <ul className="mt-1 space-y-0.5">
              {q.options.map((opt, oi) => (
                <li
                  key={oi}
                  className={cn(
                    'flex items-center gap-1 text-[12px]',
                    oi === q.correct_index
                      ? 'font-medium text-[var(--ui-ok)]'
                      : 'text-muted-foreground',
                  )}
                >
                  {oi === q.correct_index ? (
                    <Check size={11} className="shrink-0" aria-hidden="true" />
                  ) : (
                    <span className="h-2.5 w-2.5 shrink-0" aria-hidden="true" />
                  )}
                  {opt}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <Button type="button" variant="outline" disabled={adding} aria-busy={adding} onClick={onAdd}>
        {adding ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <Plus aria-hidden="true" />
        )}
        Add {plural(drafts.length, 'question')}
      </Button>
    </div>
  );
}
