// A sent offer past its deadline is expired in fact, whatever the sweep is
// doing (audit OF-F4), and a candidate opening a fresh link from their portal
// is not HR re-sending the offer (audit OF-L3).

import { describe, expect, it } from 'vitest';
import type { OfferHistoryEntry, OfferStatus } from '../api/offers';
import {
  effectiveOfferStatus,
  isLapsed,
  offerNeeds,
  offerPrimary,
  offerSecondary,
  offerTrack,
  type TrackOffer,
} from '../lib/offerTrack';
import { offerEntryWord } from '../lib/offerHistory';

const NOW = Date.parse('2026-10-22T12:00:00.000Z');
const BEFORE = '2026-10-22T11:59:59.000Z';
const AFTER = '2026-10-22T12:00:01.000Z';

function sent(expires: string | null, history: OfferHistoryEntry[] = []): TrackOffer {
  return {
    status: 'sent',
    created_at: '2026-10-01T00:00:00.000Z',
    submitted_at: '2026-10-01T00:00:00.000Z',
    decided_at: '2026-10-02T00:00:00.000Z',
    approval_note: null,
    sent_at: '2026-10-03T00:00:00.000Z',
    expires_at: expires,
    first_viewed_at: null,
    responded_at: null,
    decline_reason: null,
    withdrawn_at: null,
    withdraw_reason: null,
    preboarding_completed_at: null,
    history,
  };
}

describe('isLapsed', () => {
  it.each([
    ['sent', BEFORE, true],
    ['sent', NOW_ISO(), true], // the deadline itself has passed: the server uses <=
    ['sent', AFTER, false],
    ['sent', null, false],
    ['accepted', BEFORE, false],
    ['expired', BEFORE, false],
  ] as [OfferStatus, string | null, boolean][])('%s expiring %s -> %s', (status, at, lapsed) => {
    expect(isLapsed({ status, expires_at: at }, NOW)).toBe(lapsed);
    expect(effectiveOfferStatus({ status, expires_at: at }, NOW)).toBe(
      lapsed ? 'expired' : status,
    );
  });
});

function NOW_ISO(): string {
  return new Date(NOW).toISOString();
}

describe('a lapsed sent offer offers nothing', () => {
  it('no Re-send, no Withdraw, no primary, and "Closed" in the list', () => {
    const o = sent(BEFORE);
    expect(offerPrimary(o, NOW)).toBeNull();
    expect(offerSecondary(o, NOW)).toEqual({
      recall: false,
      reopen: false,
      resend: false,
      withdraw: false,
    });
    expect(
      offerNeeds({ ...o, documents: { mandatory_total: 0, awaiting_review: 0 } }, NOW),
    ).toEqual({ text: 'Closed', mine: false });
  });

  it('its track shows the window closed, not "Candidate responds"', () => {
    const track = offerTrack(sent(BEFORE), NOW);
    expect(track.stateLabels.returned).toBe('Expired');
    expect(track.steps[4].state).toBe('returned');
    expect(track.steps.some((s) => s.actor === 'Candidate responds')).toBe(false);
  });

  it('an offer still in its window keeps its actions', () => {
    const o = sent(AFTER);
    expect(offerSecondary(o, NOW).resend).toBe(true);
    expect(offerSecondary(o, NOW).withdraw).toBe(true);
    expect(offerTrack(o, NOW).steps[4].actor).toBe('Candidate responds');
  });
});

describe('a fresh link opened from the candidate portal', () => {
  const portal: OfferHistoryEntry = {
    action: 'resent',
    actor_type: 'candidate',
    actor_name: 'Kiran Rao',
    at: '2026-10-04T00:00:00.000Z',
    details: { by: 'candidate_portal' },
  };
  const hrSend: OfferHistoryEntry = {
    action: 'sent',
    actor_type: 'user',
    actor_name: 'Meena',
    at: '2026-10-03T00:00:00.000Z',
    details: {},
  };

  it('reads as the candidate opening it, not as a re-send', () => {
    expect(offerEntryWord(portal)).toBe('Candidate opened a fresh link from their portal');
    expect(offerEntryWord({ ...portal, details: {} })).toBe('Re-sent to the candidate');
  });

  it('does not make the candidate the sender on the track', () => {
    const track = offerTrack(
      { ...sent(AFTER, [hrSend, portal]), status: 'accepted', responded_at: AFTER },
      NOW,
    );
    expect(track.steps[3].actor).toBe('Meena');
  });
});
