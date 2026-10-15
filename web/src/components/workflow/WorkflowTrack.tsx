// WorkflowTrack — where this version stands on the path to live.
//
// Rounds valid → Dry run → Submitted for review → Approved → Published, drawn
// with the shared ApprovalTrack. Every mark comes from something the server
// holds: the validate report, the latest (non-stale) dry run, the review state
// and the version's own status. Nothing here is inferred from form fields.

import type { ReactNode } from 'react';
import { ApprovalTrack, type ApprovalStep } from '@/design/components/approval-track';
import type { ReviewState, SimulationResult } from '@/api/workflowReview';
import type { ReviewStatus } from '@/api/workflows';
import { trackStates, type TrackStep } from '@/lib/workflowLifecycle';

const STATE_LABELS = {
  done: 'Done',
  current: 'Current step',
  todo: 'Not yet',
  returned: 'Sent back',
} as const;

const LABELS: Record<TrackStep, string> = {
  rounds: 'Rounds valid',
  dryRun: 'Dry run',
  submitted: 'Submitted for review',
  approved: 'Approved',
  published: 'Published',
};

const ORDER: TrackStep[] = ['rounds', 'dryRun', 'submitted', 'approved', 'published'];

function day(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? undefined
    : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

interface Props {
  status: 'draft' | 'published' | 'archived';
  reviewStatus: ReviewStatus;
  roundCount: number;
  publishedAt: string | null;
  /** The validate report says the version can be published. */
  publishable: boolean;
  /** A dry run exists for the CURRENT content (the latest one is not stale). */
  simulated: boolean;
  simulation: SimulationResult | null | undefined;
  review: ReviewState | undefined;
  /** Quiet controls on the right (open the review and dry-run details). */
  secondary?: ReactNode;
}

export default function WorkflowTrack({
  status,
  reviewStatus,
  roundCount,
  publishedAt,
  publishable,
  simulated,
  simulation,
  review,
  secondary,
}: Props): JSX.Element {
  const states = trackStates({ status, reviewStatus, simulated, publishable, roundCount });

  const steps: ApprovalStep[] = ORDER.map((key) => {
    const step: ApprovalStep = { key, label: LABELS[key], state: states[key] };
    if (key === 'dryRun' && step.state === 'done' && simulation) {
      step.actor = simulation.run_by_name ?? undefined;
      step.at = day(simulation.created_at);
    }
    if (key === 'submitted') {
      if (step.state === 'returned' && review) {
        step.actor = review.reviewed_by_name
          ? `Sent back by ${review.reviewed_by_name}`
          : 'Sent back';
        step.at = day(review.reviewed_at);
        step.note = review.note ?? undefined;
      } else if (step.state === 'done' && review?.submitted_by_name) {
        step.actor = review.submitted_by_name;
        step.at = day(review.submitted_at);
      }
    }
    if (key === 'approved') {
      if (step.state === 'current') {
        step.actor = 'Waiting on a company super admin';
        step.at = day(review?.submitted_at);
      } else if (step.state === 'done' && reviewStatus === 'approved' && review?.reviewed_by_name) {
        step.actor = review.reviewed_by_name;
        step.at = day(review.reviewed_at);
      }
    }
    if (key === 'published' && step.state === 'done') {
      if (status === 'archived') {
        // It WAS published and went through every step above, but it is not live
        // any more: say so, and do not stamp the publish date beside it where it
        // would read as the date it was archived.
        step.actor = 'Archived, no longer live';
      } else {
        step.at = day(publishedAt);
      }
    }
    return step;
  });

  return (
    <ApprovalTrack
      data-testid="workflow-state"
      label="Workflow approval progress"
      stateLabels={STATE_LABELS}
      steps={steps}
      secondary={secondary}
    />
  );
}
