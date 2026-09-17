// Ids are validated where they enter a URL (security audit W1 and its INFO).
//
// React Router decodes %2F in a route parameter, and encodeURIComponent leaves
// '..' alone, so an id reaching these helpers could steer an authenticated
// request at another API path. Every id they take is a server-issued UUID;
// anything else is refused before a request is made.

import { describe, it, expect, vi, beforeEach } from 'vitest';

const calls: string[] = [];
vi.mock('../api/client', () => ({
  apiGet: vi.fn((path: string) => {
    calls.push(path);
    return Promise.resolve({});
  }),
  apiPut: vi.fn((path: string) => {
    calls.push(path);
    return Promise.resolve({});
  }),
  apiPost: vi.fn((path: string) => {
    calls.push(path);
    return Promise.resolve({});
  }),
}));

import { getScorecard, getScorecardKit, getPrivateNotes } from '../api/interviewer';
import { getEnrolmentScorecards, getRoundKit, withdrawScorecard } from '../api/scorecards';

const ID = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41';
const HELPERS = [
  getScorecard,
  getScorecardKit,
  getPrivateNotes,
  getEnrolmentScorecards,
  getRoundKit,
  (id: string) => withdrawScorecard(id),
];

describe('API path ids', () => {
  beforeEach(() => {
    calls.length = 0;
  });

  it.each(['../../hr/rounds/abc/kit', '..', '.', '', 'sc-1', `${ID}/../x`])(
    'refuses %j before making any request',
    (bad) => {
      for (const helper of HELPERS) {
        expect(() => helper(bad)).toThrow(/valid record/);
      }
      expect(calls).toHaveLength(0);
    },
  );

  it('builds the path from a real id', async () => {
    for (const helper of HELPERS) await helper(ID);
    expect(calls).toHaveLength(HELPERS.length);
    for (const path of calls) expect(path).toContain(`/${ID}`);
  });
});
