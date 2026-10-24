// Small helpers for the HR exam editor and its panels (moved out of
// pages/hr/ExamEditor.tsx so each panel lives in its own file). The form
// controls come from components/ui (Input, Textarea, SelectControl) and counts
// from lib/formatters `plural`, so no local class constants live here.

// ── Status helpers ────────────────────────────────────────────────────────────

export type TagToneKey = 'neutral' | 'forest' | 'amber';

export function roundStatusTone(s: string): TagToneKey {
  return s === 'published' ? 'forest' : 'amber';
}
