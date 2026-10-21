// statusLabel: every HR status in words, by domain (audit X10, X12). Screens
// showed raw lowercase values ("held", "open", "awaiting slot") beside
// title-case pills, and one returned-approval state had two names.

import { describe, expect, it } from 'vitest';
import { humaniseStatus, knownStatuses, statusLabel, type StatusDomain } from '../lib/statusLabel';
import type { ApprovalStatus, EnrolmentStatus, RequisitionStatus } from '../api/requisitions';
import type { OfferStatus } from '../api/offers';
import type { LoopStatus, SessionStatus } from '../api/scheduling';
import type { InviteStatus } from '../api/publicInterview';
import type { ExamStatus, RoundStatus } from '../api/exams';
import type { TaskSubmissionStatus } from '../api/jobTasks';
import type { ScorecardStatus } from '../api/interviewer';
import type { AccommodationStatus } from '../api/accommodations';
import type { ReviewStatus, WorkflowStatus } from '../api/workflows';
import type { BankQuestionStatus } from '../api/questionBanks';
import type { CorpusVersionStatus } from '../api/corpus';

/**
 * Every member of a status union, checked by the COMPILER: a Record over the
 * union must name each member, so a status added to an API type without being
 * listed here fails typecheck, and then fails the label test below until
 * statusLabel knows it.
 */
const every = <T extends string>(members: Record<T, true>): T[] => Object.keys(members) as T[];

const DOMAIN_VALUES: Record<StatusDomain, readonly string[]> = {
  candidate: every<EnrolmentStatus>({
    new: true,
    shortlisted: true,
    interviewed: true,
    held: true,
    hired: true,
    rejected: true,
  }),
  opening: every<RequisitionStatus>({ open: true, paused: true, closed: true }),
  approval: every<ApprovalStatus>({
    draft: true,
    pending_approval: true,
    approved: true,
    rejected: true,
  }),
  offer: every<OfferStatus>({
    draft: true,
    pending_approval: true,
    approved: true,
    rejected: true,
    sent: true,
    accepted: true,
    declined: true,
    expired: true,
    withdrawn: true,
  }),
  session: every<SessionStatus>({
    awaiting_slot: true,
    scheduled: true,
    cancelled: true,
    completed: true,
    no_show: true,
  }),
  loop: every<LoopStatus>({ draft: true, scheduled: true, completed: true, cancelled: true }),
  invite: every<InviteStatus>({
    invited: true,
    consumed: true,
    completed: true,
    expired: true,
    revoked: true,
  }),
  exam: every<ExamStatus>({ draft: true, published: true, closed: true }),
  round: every<RoundStatus>({ draft: true, published: true }),
  task: every<TaskSubmissionStatus>({
    assigned: true,
    in_progress: true,
    submitted: true,
    expired: true,
    withdrawn: true,
  }),
  scorecard: every<ScorecardStatus>({ assigned: true, in_progress: true, submitted: true }),
  accommodation: every<AccommodationStatus>({ active: true, revoked: true }),
  workflow: every<WorkflowStatus>({ draft: true, published: true, archived: true }),
  review: every<ReviewStatus>({
    draft: true,
    in_review: true,
    changes_requested: true,
    approved: true,
  }),
  bankQuestion: every<BankQuestionStatus>({
    draft: true,
    in_review: true,
    approved: true,
    retired: true,
  }),
  corpus: every<CorpusVersionStatus>({ parsed: true, indexing: true, indexed: true, failed: true }),
};

describe('statusLabel', () => {
  it.each(Object.entries(DOMAIN_VALUES))(
    'has a sentence-case label for every %s status',
    (domain, values) => {
      const known = knownStatuses(domain as StatusDomain);
      for (const value of values) {
        expect(known, `${domain}.${value}`).toContain(value);
        const label = statusLabel(value, domain as StatusDomain);
        expect(label).toMatch(/^[A-Z][a-z]*( [a-z]+)*$/);
      }
    },
  );

  it('reads the value by domain', () => {
    expect(statusLabel('rejected', 'candidate')).toBe('Rejected');
    expect(statusLabel('rejected', 'offer')).toBe('Sent back');
    expect(statusLabel('published', 'exam')).toBe('Live');
    expect(statusLabel('held', 'candidate')).toBe('Held');
    expect(statusLabel('open', 'opening')).toBe('Open');
    expect(statusLabel('awaiting_slot', 'session')).toBe('Awaiting slot');
    expect(statusLabel('no_show', 'session')).toBe('No show');
  });

  it('gives a returned approval one name everywhere', () => {
    expect(statusLabel('rejected', 'approval')).toBe('Sent back');
    expect(statusLabel('rejected', 'offer')).toBe('Sent back');
    expect(statusLabel('changes_requested', 'review')).toBe('Sent back');
  });

  it('humanises an unknown value instead of showing it raw, and dashes a missing one', () => {
    expect(statusLabel('on_hold', 'candidate')).toBe('On hold');
    expect(statusLabel(null, 'candidate')).toBe('-');
    expect(statusLabel(undefined, 'offer')).toBe('-');
    expect(humaniseStatus('READY-TO_go')).toBe('Ready to go');
  });
});
