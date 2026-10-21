// The exam editor's failure toasts go through errorMessage (audit X16, C2).
//
// The editor had 18 fallbacks of the form "Delete failed" / "Add section
// failed", and every one of them printed the raw error first, so a dropped
// connection read "Failed to fetch" and a server error with no detail read
// "HTTP 500". The behaviour of errorMessage itself is pinned in
// errorMessage.test.ts; what this pins is that the editor uses it, in the house
// style "Could not <verb> <object>.".
//
// Source assertions, on the precedent in ExamEditorCameraToggle.test.ts: the
// editor is too large to render cheaply for each of its 19 mutations, and the
// property is "no toast bypasses the helper", which the source states exactly.

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const editor = readFileSync(resolve(__dirname, '../pages/hr/ExamEditor.tsx'), 'utf-8');

describe('exam editor error toasts', () => {
  it('never shows a raw error message with an "X failed" fallback', () => {
    expect(editor).not.toMatch(/e instanceof Error \? e\.message :/);
    expect(editor).not.toMatch(/'[A-Z][a-z ]* failed'/);
  });

  it('routes every mutation failure through errorMessage with a full sentence', () => {
    const calls = [...editor.matchAll(/errorMessage\(e, '([^']+)'\)/g)].map((m) => m[1]);
    expect(calls.length).toBeGreaterThanOrEqual(19);
    for (const copy of calls) {
      expect(copy).toMatch(/^Could not [a-z].*\.$/);
    }
  });
});
