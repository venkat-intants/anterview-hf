// The opening's approval state as a pill, beside its open/paused/closed status.
// The word carries the meaning; the tone only reinforces it.

import { StatusTag, type TagTone } from '@/design/components/primitives';
import type { ApprovalStatus } from '@/api/requisitions';
import { APPROVAL_LABEL } from '@/lib/openingSetup';

const TONE: Record<ApprovalStatus, TagTone> = {
  draft: 'neutral',
  pending_approval: 'amber',
  approved: 'forest',
  rejected: 'ember',
};

export default function ApprovalPill({ status }: { status: ApprovalStatus }): JSX.Element {
  return (
    <span data-testid="approval-state">
      <StatusTag tone={TONE[status]}>{APPROVAL_LABEL[status]}</StatusTag>
    </span>
  );
}
