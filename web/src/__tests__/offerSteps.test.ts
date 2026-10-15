import { describe, it, expect } from 'vitest';
import type { PublicChecklistItem } from '../api/publicOffer';
import { documentsRemaining, offerStep } from '../lib/offerSteps';

describe('offerStep', () => {
  it.each([
    ['sent', null, null, { current: 'review', complete: false }],
    ['sent', null, 'decline', { current: 'review', complete: false }],
    ['sent', null, 'accept', { current: 'accept', complete: false }],
    ['accepted', null, null, { current: 'documents', complete: false }],
    ['accepted', '2026-09-18T00:00:00Z', null, { current: 'documents', complete: true }],
    ['declined', null, null, null],
    ['expired', null, null, null],
    ['withdrawn', null, null, null],
  ] as const)('%s / preboarding %s / answering %s', (status, done, answering, expected) => {
    expect(offerStep(status, done, answering)).toEqual(expected);
  });
});

function item(over: Partial<PublicChecklistItem>): PublicChecklistItem {
  return {
    requirement_id: 'r',
    name: 'Doc',
    doc_type: 'other',
    description: null,
    mandatory: true,
    requires_expiry: false,
    state: 'outstanding',
    document: null,
    ...over,
  };
}

describe('documentsRemaining', () => {
  it('counts the server outstanding list against the required documents', () => {
    const items = [
      item({ requirement_id: 'a', name: 'PAN' }),
      item({ requirement_id: 'b', name: 'ID', state: 'submitted' }),
      item({ requirement_id: 'c', name: 'Bank', state: 'verified' }),
      item({ requirement_id: 'd', name: 'Photo', mandatory: false }),
    ];
    expect(documentsRemaining({ items, outstanding: ['PAN', 'ID'] })).toEqual({
      n: 2,
      total: 3,
      inReview: 1,
    });
  });

  it('is none still needed when everything required is verified', () => {
    const items = [item({ state: 'verified' })];
    expect(documentsRemaining({ items, outstanding: [] })).toEqual({ n: 0, total: 1, inReview: 0 });
  });

  it('never reports more needed than there are documents', () => {
    expect(documentsRemaining({ items: [], outstanding: ['PAN'] })).toEqual({
      n: 1,
      total: 1,
      inReview: 0,
    });
  });
});
