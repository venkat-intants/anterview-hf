// Camera proctoring must be reachable from the product, not only from the API.
//
// THE DEFECT THIS PINS. The camera-proctoring feature shipped with a working
// backend setting (`camera_proctoring_required` on a round), a complete
// candidate flow (consent gate, detection, events) and an HR review panel —
// and NO control anywhere to switch it on. `grep camera_proctoring_required
// web/src` returned nothing at all. Every test passed, because every test
// exercised a layer that was genuinely finished. The feature was simply
// unreachable: the only way to enable it was a hand-written PATCH.
//
// A round-trip test would not have caught it either, since the API works. What
// was missing was the question "can a human turn this on?", so that is what
// this asserts.
//
// Source assertions, on the precedent already set in
// test_exam_integrity_event_rate_limit.py: the property under test is "this
// control exists and is wired to the round update", which no rendered object
// exposes cheaply for a component this size.

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, it, expect } from 'vitest';

const editor = readFileSync(
  resolve(__dirname, '../pages/hr/ExamEditor.tsx'),
  'utf-8',
);
const api = readFileSync(resolve(__dirname, '../api/exams.ts'), 'utf-8');

describe('camera proctoring is reachable from the HR exam editor', () => {
  it('sends camera_proctoring_required through updateRound', () => {
    expect(editor).toContain('camera_proctoring_required');
    // Wired to the round update, not merely mentioned in a comment.
    const idx = editor.indexOf('camera_proctoring_required');
    expect(editor.slice(idx, idx + 400)).toContain('updateRound');
  });

  it('renders a checkbox bound to the round’s current value', () => {
    expect(editor).toContain('checked={round.camera_proctoring_required}');
  });

  it('tells HR what enabling it actually does to the candidate', () => {
    // Not decoration. Someone ticking this is turning a written test into one
    // that watches a person, and the two facts that matter — the exam cannot
    // start without consent, and nothing is recorded — belong next to the
    // control, not in documentation nobody opens.
    expect(editor).toMatch(/camera access/i);
    expect(editor).toMatch(/no video or\s+image is recorded/i);
  });

  it('models the field in both the Round type and the update input', () => {
    // Without these the control cannot compile, and the API's own capability
    // stays invisible to every screen — which is how this was missed.
    expect(api).toMatch(/camera_proctoring_required:\s*boolean;/);
    const updateInput = api.slice(
      api.indexOf('RoundUpdateInput'),
      api.indexOf('RoundUpdateInput') + 400,
    );
    expect(updateInput).toContain('camera_proctoring_required');
  });
});
