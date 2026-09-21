// competencyId — slugifies a typed competency name into the id shape the
// server validates (`^[a-z0-9_]{1,80}$` in app/question_banks.py). Kept out
// of CompetencyTagger.tsx so that component file exports only the component
// (react-refresh/only-export-components).

export function slugifyCompetencyId(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80);
}
