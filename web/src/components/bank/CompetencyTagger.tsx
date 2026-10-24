// CompetencyTagger — PH4-D1. Tags a bank question with up to 8 competencies,
// drawn from the same catalogue the role engine and the company's own round
// rubrics use (GET /hr/bank-questions/competencies), so a tag on a question
// uses the same words a round's rubric does. A typed name that matches no
// suggestion is still accepted — the id is slugified client-side the same way
// the server validates it (`^[a-z0-9_]{1,80}$`).

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listBankCompetencies, type Competency } from '@/api/questionBanks';
import { slugifyCompetencyId } from '@/lib/competencyId';
import { X } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const MAX_COMPETENCIES = 8;

interface CompetencyTaggerProps {
  value: Competency[];
  onChange: (next: Competency[]) => void;
  disabled?: boolean;
}

export function CompetencyTagger({
  value,
  onChange,
  disabled = false,
}: CompetencyTaggerProps): JSX.Element {
  const [draft, setDraft] = useState('');
  const [hint, setHint] = useState<string | null>(null);
  // No error branch on purpose: the catalogue only feeds suggestions, and a
  // typed name is accepted without it, so a failed load costs nothing to show.
  const catalog = useQuery({
    queryKey: ['hr', 'bank-competencies'],
    queryFn: listBankCompetencies,
  });

  const selectedIds = useMemo(() => new Set(value.map((c) => c.id)), [value]);
  const suggestions = (catalog.data ?? []).filter(
    (c) =>
      !selectedIds.has(c.id) &&
      (draft.trim() === '' || c.name.toLowerCase().includes(draft.trim().toLowerCase())),
  );

  function addCompetency(comp: Competency) {
    if (value.length >= MAX_COMPETENCIES || selectedIds.has(comp.id)) return;
    onChange([...value, comp]);
    setDraft('');
    setHint(null);
  }

  function addFromDraft() {
    const name = draft.trim();
    if (!name) return;
    const id = slugifyCompetencyId(name);
    if (!id) {
      // A competency's id is [a-z0-9_], so a name written only in Devanagari
      // or Telugu slugifies to nothing. Day-1 languages are EN, HI and TE, so
      // that is a name people will reasonably type — say why it did not stick
      // rather than swallowing the click.
      setHint('A competency name needs Latin letters or digits, because it becomes the id.');
      return;
    }
    addCompetency({ id, name });
  }

  function remove(id: string) {
    onChange(value.filter((c) => c.id !== id));
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor="competency-tagger-input"
        className="text-[12px] font-medium text-[var(--ui-soft)]"
      >
        Competencies ({value.length}/{MAX_COMPETENCIES})
      </label>
      <div className="flex flex-wrap gap-1.5">
        {value.map((c) => (
          <span
            key={c.id}
            className="inline-flex items-center gap-1 rounded-pill bg-[rgba(var(--accent-rgb),0.14)] px-2.5 py-1 text-[12px] font-medium text-[var(--ui-info)]"
          >
            {c.name}
            {!disabled ? (
              <button
                type="button"
                aria-label={`Remove competency ${c.name}`}
                onClick={() => remove(c.id)}
                className="text-[var(--ui-info)] hover:text-foreground"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            ) : null}
          </span>
        ))}
      </div>
      {!disabled && value.length < MAX_COMPETENCIES ? (
        <div className="relative">
          <div className="flex gap-2">
            <Input
              id="competency-tagger-input"
              size="sm"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addFromDraft();
                }
              }}
              placeholder="Type to search or add a competency…"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={addFromDraft}
              disabled={!draft.trim()}
            >
              Add
            </Button>
          </div>
          {draft.trim() && suggestions.length > 0 ? (
            <ul
              role="listbox"
              aria-label="Competency suggestions"
              className="absolute z-10 mt-1.5 max-h-52 w-full overflow-y-auto rounded-[12px] border border-[var(--ui-line-strong)] bg-popover p-1 shadow-elevated"
            >
              {suggestions.slice(0, 8).map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => addCompetency(s)}
                    className="flex min-h-9 w-full items-center rounded-[8px] px-2.5 py-1.5 text-left text-[14px] text-popover-foreground outline-none transition-colors hover:bg-[var(--ui-inset-strong)] focus-visible:bg-[var(--ui-inset-strong)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  >
                    {s.name}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {hint ? (
            <p role="status" className="mt-1 text-[12px] text-[var(--ui-danger)]">
              {hint}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
