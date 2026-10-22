// hrHome — which openings are "in setup", and the next stage named for each,
// built only from the fields the openings list returns.

import { describe, expect, it } from 'vitest';
import { isInSetup, nextSetupStage, setupBadge } from '../lib/hrHome';
import type { ApprovalStatus, RequisitionStatus } from '../api/requisitions';
import { APPROVAL_LABEL } from '../lib/openingSetup';

const STATUSES: RequisitionStatus[] = ['open', 'paused', 'closed'];
const APPROVALS: ApprovalStatus[] = ['draft', 'pending_approval', 'approved', 'rejected'];

describe('isInSetup', () => {
  it('is false for every closed opening, whatever its approval says', () => {
    for (const approval of APPROVALS) {
      for (const live of [true, false]) {
        expect(isInSetup({ status: 'closed', approval_status: approval, public_apply_enabled: live })).toBe(false);
      }
    }
  });

  it('is false only when approved AND accepting applications', () => {
    for (const status of STATUSES.filter((s) => s !== 'closed')) {
      for (const approval of APPROVALS) {
        for (const live of [true, false]) {
          const expected = !(approval === 'approved' && live);
          expect(isInSetup({ status, approval_status: approval, public_apply_enabled: live })).toBe(expected);
        }
      }
    }
  });
});

describe('nextSetupStage', () => {
  const base = { status: 'open' as const, public_apply_enabled: false };

  it.each([
    ['draft', 'Budget and approval', 'not submitted for approval yet'],
    ['pending_approval', 'Budget and approval', 'waiting for approval'],
    ['rejected', 'Budget and approval', 'sent back. Revise and resubmit.'],
    ['approved', 'Go live', 'approved, but not accepting applications yet'],
  ] as [ApprovalStatus, string, string][])('%s -> %s', (approval_status, stage, detail) => {
    expect(nextSetupStage({ ...base, approval_status })).toEqual({ stage, detail });
  });

  it('names who submitted it and quotes the send-back note, when the server has them', () => {
    expect(
      nextSetupStage({ ...base, approval_status: 'pending_approval', submitted_by_name: 'Asha R' }).detail,
    ).toBe('waiting for approval (submitted by Asha R)');
    expect(
      nextSetupStage({ ...base, approval_status: 'rejected', approval_note: 'Budget too high' }).detail,
    ).toContain('“Budget too high”');
  });

  it('never claims anything about the hiring process, which the list does not carry', () => {
    for (const approval of APPROVALS) {
      const text = JSON.stringify(nextSetupStage({ ...base, approval_status: approval }));
      expect(text).not.toMatch(/workflow|hiring process/i);
    }
  });
});

describe('setupBadge', () => {
  it.each([
    ['draft', false, 'Not submitted'],
    ['pending_approval', false, 'Awaiting approval'],
    ['rejected', false, 'Sent back'],
    ['approved', false, 'Approved'],
    ['approved', true, 'Live'],
  ] as [ApprovalStatus, boolean, string][])('%s, live=%s -> %s', (approval_status, live, word) => {
    expect(setupBadge({ approval_status, public_apply_enabled: live })).toBe(word);
  });
});

describe('one word per approval state (HR-L3)', () => {
  it.each(['draft', 'pending_approval', 'rejected'] as const)(
    'the home badge and the openings list agree on %s',
    (approval) => {
      expect(
        setupBadge({ approval_status: approval, public_apply_enabled: false }),
      ).toBe(APPROVAL_LABEL[approval]);
    },
  );

  it('calls a returned opening "Sent back" everywhere', () => {
    expect(APPROVAL_LABEL.rejected).toBe('Sent back');
  });
});
