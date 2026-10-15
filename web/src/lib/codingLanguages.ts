// Display names for the coding round's programming-language slugs
// (`CODING_LANGUAGES` in api/exams.ts; `SUPPORTED_LANGUAGES` on the server).
//
// BankQuestionEditor, CodingAuthoringSection and CodingTaking each carry a
// private copy of this table. New code imports this one.

const LABELS: Record<string, string> = {
  python: 'Python',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  java: 'Java',
  cpp: 'C++',
  c: 'C',
  go: 'Go',
  csharp: 'C#',
  ruby: 'Ruby',
  rust: 'Rust',
};

/** `cpp` -> `C++`. An unknown slug is shown as it is rather than hidden. */
export function codingLanguageLabel(slug: string): string {
  return LABELS[slug] ?? slug;
}
