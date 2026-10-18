// hrHome.ts — which openings the HR home lists under "Openings in setup", and
// the next stage of each in words.
//
// Built ONLY from fields the openings list already returns: `status`,
// `approval_status` and `public_apply_enabled`. The list does not carry the
// hiring-process (workflow) state — only the opening's own page does — so this
// never claims it. An opening is "in setup" until the server holds its approval
// AND it is accepting applications; the stage named is the first of those two
// the server does not yet hold.

import type { Requisition } from '@/api/requisitions';

type SetupFields = Pick<Requisition, 'status' | 'approval_status' | 'public_apply_enabled'>;
type SetupText = Pick<Requisition, 'approval_note' | 'submitted_by_name'>;

/** True while an unclosed opening is not yet both approved and accepting applications. */
export function isInSetup(r: SetupFields): boolean {
  if (r.status === 'closed') return false;
  return !(r.approval_status === 'approved' && r.public_apply_enabled);
}

/** The next stage and why, e.g. "Budget and approval — waiting for approval". */
export function nextSetupStage(r: SetupFields & Partial<SetupText>): { stage: string; detail: string } {
  switch (r.approval_status) {
    case 'rejected':
      return {
        stage: 'Budget and approval',
        detail: r.approval_note
          ? `sent back - “${r.approval_note}”. Revise and resubmit.`
          : 'sent back. Revise and resubmit.',
      };
    case 'pending_approval':
      return {
        stage: 'Budget and approval',
        detail: r.submitted_by_name
          ? `waiting for approval (submitted by ${r.submitted_by_name})`
          : 'waiting for approval',
      };
    case 'draft':
      return { stage: 'Budget and approval', detail: 'not submitted for approval yet' };
    default:
      // approved, so the only stage left is going live.
      return { stage: 'Go live', detail: 'approved, but not accepting applications yet' };
  }
}

/** The short word shown at the right of an opening's row. */
export function setupBadge(r: Pick<Requisition, 'approval_status' | 'public_apply_enabled'>): string {
  switch (r.approval_status) {
    case 'rejected':
      return 'Sent back';
    case 'pending_approval':
      return 'Awaiting approval';
    case 'draft':
      return 'Not submitted';
    default:
      return r.public_apply_enabled ? 'Live' : 'Approved';
  }
}
