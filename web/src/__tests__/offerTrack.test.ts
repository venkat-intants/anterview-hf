// offerTrack — the offer's approval track is derived from the offer's own record
// only, and the legal next action per status mirrors the server's state machine.

import { describe, expect, it } from 'vitest';
import type { OfferHistoryEntry, OfferStatus } from '../api/offers';
import {
  offerNeeds,
  offerPrimary,
  offerSecondary,
  offerTrack,
  type TrackOffer,
} from '../lib/offerTrack';

const AT = '2026-09-03T10:00:00.000Z';

function offer(status: OfferStatus, over: Partial<TrackOffer> = {}): TrackOffer {
  return {
    status,
    created_at: '2026-09-01T10:00:00.000Z',
    submitted_at: null,
    decided_at: null,
    approval_note: null,
    sent_at: null,
    expires_at: null,
    first_viewed_at: null,
    responded_at: null,
    decline_reason: null,
    withdrawn_at: null,
    withdraw_reason: null,
    preboarding_completed_at: null,
    history: [],
    ...over,
  };
}

function ev(action: string, actor: string): OfferHistoryEntry {
  return { action, actor_type: 'user', actor_name: actor, at: AT, details: {} };
}

type StepStates = string[];
const states = (o: TrackOffer): StepStates => offerTrack(o).steps.map((s) => s.state);

describe('offerTrack — step states per status', () => {
  const cases: [string, TrackOffer, StepStates][] = [
    ['draft', offer('draft'), ['current', 'todo', 'todo', 'todo', 'todo']],
    [
      'pending approval',
      offer('pending_approval', { submitted_at: AT }),
      ['done', 'done', 'current', 'todo', 'todo'],
    ],
    [
      'sent back',
      offer('rejected', { submitted_at: AT, decided_at: AT, approval_note: 'Raise it' }),
      ['done', 'done', 'returned', 'todo', 'todo'],
    ],
    [
      'approved',
      offer('approved', { submitted_at: AT, decided_at: AT }),
      ['done', 'done', 'done', 'current', 'todo'],
    ],
    [
      'sent',
      offer('sent', { submitted_at: AT, decided_at: AT, sent_at: AT }),
      ['done', 'done', 'done', 'done', 'current'],
    ],
    [
      'accepted',
      offer('accepted', { sent_at: AT, responded_at: AT }),
      ['done', 'done', 'done', 'done', 'done'],
    ],
    [
      'declined',
      offer('declined', { sent_at: AT, responded_at: AT, decline_reason: 'Other offer' }),
      ['done', 'done', 'done', 'done', 'returned'],
    ],
    ['expired', offer('expired', { sent_at: AT, expires_at: AT }), ['done', 'done', 'done', 'done', 'returned']],
    // Withdrawn: the step after the furthest recorded one is the one that did not happen.
    ['withdrawn from draft', offer('withdrawn'), ['done', 'returned', 'todo', 'todo', 'todo']],
    [
      'withdrawn while pending',
      offer('withdrawn', { submitted_at: AT }),
      ['done', 'done', 'returned', 'todo', 'todo'],
    ],
    [
      'withdrawn after approval',
      offer('withdrawn', { submitted_at: AT, decided_at: AT, history: [ev('approved', 'Meena')] }),
      ['done', 'done', 'done', 'returned', 'todo'],
    ],
    [
      'withdrawn after sending',
      offer('withdrawn', { submitted_at: AT, decided_at: AT, sent_at: AT, history: [ev('approved', 'Meena')] }),
      ['done', 'done', 'done', 'done', 'returned'],
    ],
  ];

  it.each(cases)('%s', (_name, o, expected) => {
    expect(states(o)).toEqual(expected);
  });

  it('never has more than one current step', () => {
    for (const [, o] of cases) {
      expect(states(o).filter((s) => s === 'current').length).toBeLessThanOrEqual(1);
    }
  });
});

describe('offerTrack — who and when', () => {
  it('puts the actor from the history under each completed step', () => {
    const o = offer('approved', {
      submitted_at: AT,
      decided_at: AT,
      history: [ev('created', 'Asha'), ev('submitted', 'Asha'), ev('approved', 'Meena')],
    });
    const [draft, submitted, approved, sent] = offerTrack(o).steps;
    expect(draft.actor).toBe('Asha');
    expect(submitted.actor).toBe('Asha');
    expect(approved.actor).toBe('Meena');
    expect(approved.at).toBeTruthy();
    // The pending step names who must act, with no date.
    expect(sent.actor).toBe('HR manager sends it');
    expect(sent.at).toBeUndefined();
  });

  it('uses the latest event when an action happened more than once', () => {
    const o = offer('pending_approval', {
      submitted_at: AT,
      history: [ev('submitted', 'Asha'), ev('recalled', 'Asha'), ev('submitted', 'Ravi')],
    });
    expect(offerTrack(o).steps[1].actor).toBe('Ravi');
  });

  it('names the approver as the one who must act on a pending offer', () => {
    const step = offerTrack(offer('pending_approval', { submitted_at: AT })).steps[2];
    expect(step.state).toBe('current');
    expect(step.actor).toBe('Company admin decides');
  });

  it('notes when the candidate has opened a sent offer', () => {
    const step = offerTrack(offer('sent', { sent_at: AT, first_viewed_at: AT })).steps[4];
    expect(step.actor).toBe('Candidate responds');
    expect(step.at).toMatch(/^Opened /);
  });

  it('shows the send-back note inline on the returned step', () => {
    const track = offerTrack(
      offer('rejected', { submitted_at: AT, decided_at: AT, approval_note: 'Raise the base.' }),
    );
    expect(track.steps[2].note).toBe('Raise the base.');
    expect(track.stateLabels.returned).toBe('Sent back');
  });

  it.each([
    ['declined', { decline_reason: 'Took another job' }, 'Declined', 'Decline reason: Took another job'],
    ['declined', { decline_reason: null }, 'Declined', 'No reason was given.'],
    ['withdrawn', { withdraw_reason: 'Position closed' }, 'Withdrawn', 'Withdraw reason: Position closed'],
    ['expired', {}, 'Expired', 'The offer window closed with no answer.'],
  ] as [OfferStatus, Partial<TrackOffer>, string, string][])(
    '%s offer: word and reason',
    (status, over, word, note) => {
      const track = offerTrack(offer(status, { sent_at: AT, ...over }));
      expect(track.stateLabels.returned).toBe(word);
      expect(track.steps.find((s) => s.state === 'returned')?.note).toBe(note);
    },
  );
});

describe('offerPrimary — the one forward step HR can take', () => {
  const rows: [OfferStatus, string | null, string | null][] = [
    ['draft', null, 'submit'],
    ['rejected', null, 'submit'],
    ['pending_approval', null, null],
    ['approved', null, 'send'],
    ['sent', null, null],
    ['accepted', null, 'complete_preboarding'],
    ['accepted', AT, null],
    ['declined', null, null],
    ['expired', null, null],
    ['withdrawn', null, null],
  ];
  it.each(rows)('%s (preboarding done: %s) -> %s', (status, done, expected) => {
    expect(offerPrimary({ status, preboarding_completed_at: done })).toBe(expected);
  });
});

describe('offerSecondary — the quieter actions the state machine allows', () => {
  const rows: [OfferStatus, string | null, string[]][] = [
    ['draft', null, ['withdraw']],
    ['pending_approval', null, ['recall', 'withdraw']],
    ['rejected', null, ['withdraw']],
    ['approved', null, ['reopen', 'withdraw']],
    ['sent', null, ['resend', 'withdraw']],
    ['accepted', null, ['resend']],
    ['accepted', AT, []],
    ['declined', null, []],
    ['expired', null, []],
    ['withdrawn', null, []],
  ];
  it.each(rows)('%s (preboarding done: %s)', (status, done, expected) => {
    const flags = offerSecondary({ status, preboarding_completed_at: done });
    const on = (Object.keys(flags) as (keyof typeof flags)[]).filter((k) => flags[k]).sort();
    expect(on).toEqual([...expected].sort());
  });
});

describe('offerNeeds — what a list row asks of the reader', () => {
  const docs = { mandatory_total: 2, awaiting_review: 0 };
  const rows: [OfferStatus, string | null, number, string, boolean][] = [
    ['draft', null, 0, 'Yours to submit', true],
    ['rejected', null, 0, 'Sent back — yours to revise', true],
    ['approved', null, 0, 'Yours to send', true],
    ['pending_approval', null, 0, 'With the company admin', false],
    ['sent', null, 0, 'Waiting on the candidate', false],
    ['accepted', null, 1, 'Documents to review', true],
    ['accepted', null, 0, 'Waiting on the candidate’s documents', false],
    ['accepted', AT, 0, 'Preboarding complete', false],
    ['declined', null, 0, 'Closed', false],
    ['expired', null, 0, 'Closed', false],
    ['withdrawn', null, 0, 'Closed', false],
  ];
  it.each(rows)('%s (preboarding %s, %i to review)', (status, done, awaiting, text, mine) => {
    expect(
      offerNeeds({
        status,
        preboarding_completed_at: done,
        documents: { ...docs, awaiting_review: awaiting },
      }),
    ).toEqual({ text, mine });
  });
});
