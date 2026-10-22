// Shared styles and small helpers for the HR exam editor and its panels
// (moved out of pages/hr/ExamEditor.tsx so each panel lives in its own file).

// ── Shared styles ─────────────────────────────────────────────────────────────

export const inputCls =
  'w-full rounded-[12px] border border-border bg-secondary px-3 py-2 ' +
  'text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none ' +
  'focus:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors';

export const smallInputCls =
  'h-10 rounded-[10px] border border-border bg-secondary px-2.5 text-[13px] text-foreground ' +
  'placeholder:text-[var(--ui-faint)] focus:outline-none focus:border-[var(--accent)] ' +
  'focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-60';

/** "1 question" / "3 questions". */
export function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

// ── Status helpers ────────────────────────────────────────────────────────────

export type TagToneKey = 'neutral' | 'forest' | 'amber';

export function roundStatusTone(s: string): TagToneKey {
  return s === 'published' ? 'forest' : 'amber';
}
