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
  }

  function addFromDraft() {
    const name = draft.trim();
    if (!name) return;
    const id = slugifyCompetencyId(name);
    if (!id) return;
    addCompetency({ id, name });
  }

  function remove(id: string) {
    onChange(value.filter((c) => c.id !== id));
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="competency-tagger-input" className="text-[12px] font-medium text-[var(--ui-soft)]">
        Competencies ({value.length}/{MAX_COMPETENCIES})
      </label>
      <div className="flex flex-wrap gap-1.5">
        {value.map((c) => (
          <span
            key={c.id}
            className="inline-flex items-center gap-1 rounded-pill bg-[rgba(var(--accent-rgb),0.14)] px-2.5 py-1 text-[11.5px] font-medium text-[var(--ui-info)]"
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
            <input
              id="competency-tagger-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addFromDraft();
                }
              }}
              placeholder="Type to search or add a competency…"
              className="w-full rounded-[10px] border border-border bg-secondary px-3 py-1.5 text-[12.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
            />
            <button
              type="button"
              onClick={addFromDraft}
              disabled={!draft.trim()}
              className="rounded-[10px] border border-border px-3 py-1.5 text-[12px] text-foreground disabled:opacity-40"
            >
              Add
            </button>
          </div>
          {draft.trim() && suggestions.length > 0 ? (
            <ul
              role="listbox"
              aria-label="Competency suggestions"
              className="absolute z-10 mt-1 max-h-40 w-full overflow-y-auto rounded-[10px] border border-border bg-card shadow-lg"
            >
              {suggestions.slice(0, 8).map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => addCompetency(s)}
                    className="block w-full px-3 py-1.5 text-left text-[12.5px] text-foreground hover:bg-[var(--ui-inset)]"
                  >
                    {s.name}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
