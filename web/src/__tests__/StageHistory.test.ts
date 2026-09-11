// B2 — the transition ledger, read out loud in the candidate drawer.

import { describe, it, expect } from 'vitest';
import type { StageHistoryEntry } from '../api/requisitions';
import { describeMove } from '../lib/stageHistory';

const base: StageHistoryEntry = {
  occurred_at: '2026-09-12T10:00:00Z',
  from_status: 'new',
  to_status: 'shortlisted',
  from_round: null,
  to_round: null,
  automated: false,
  actor: 'Priya HR',
  reason: null,
};

describe('describeMove', () => {
  it('reads a status change and names the person who made it', () => {
    expect(describeMove(base)).toBe('new → shortlisted · by Priya HR');
  });

  it('says a system move was automatic, never naming anyone', () => {
    expect(describeMove({ ...base, automated: true, actor: null })).toBe(
      'new → shortlisted · automatic',
    );
  });

  it('reads a round move even though the status did not change', () => {
    // The whole reason round moves needed recording: status stays put.
    expect(
      describeMove({
        ...base,
        from_status: 'shortlisted',
        to_status: 'shortlisted',
        from_round: 'Aptitude',
        to_round: 'AI interview',
        automated: true,
        actor: null,
      }),
    ).toBe('Aptitude → AI interview · automatic');
  });

  it('reads the first round and the end of the workflow', () => {
    const same = { ...base, from_status: 'shortlisted', to_status: 'shortlisted', automated: true, actor: null };
    expect(describeMove({ ...same, to_round: 'Aptitude' })).toBe('Started Aptitude · automatic');
    expect(describeMove({ ...same, from_round: 'Panel' })).toBe('Finished Panel · automatic');
  });

  it('reads the application itself', () => {
    expect(describeMove({ ...base, from_status: null, to_status: 'new', automated: true, actor: null })).toBe(
      'Applied — new · automatic',
    );
  });
});
