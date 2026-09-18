// The workflow lifecycle rules the builder shows (D5, PH4-O6), kept pure so
// they can be tested without rendering the builder.

export type StepState = 'done' | 'current' | 'blocked' | 'todo';

export type LifecycleStep = 'draft' | 'dryRun' | 'review' | 'approved' | 'publish';

export interface LifecycleInput {
  status: 'draft' | 'published' | 'archived';
  reviewStatus: 'draft' | 'in_review' | 'changes_requested' | 'approved';
  /** A dry run has been run for the CURRENT content of this version — i.e. the
   *  latest simulation exists and is not `stale`. */
  simulated: boolean;
  publishable: boolean;
}

/**
 * Draft → Dry run → Review → Approved → Published, as states for the
 * stepper (PH4-O6 replaced the old Draft → Preview → Validate → Publish: a
 * version cannot be published at all now without a company super admin
 * approving it first — see workflows.py publish()).
 */
export function stepStates(i: LifecycleInput): Record<LifecycleStep, StepState> {
  if (i.status !== 'draft') {
    return { draft: 'done', dryRun: 'done', review: 'done', approved: 'done', publish: 'done' };
  }
  if (i.reviewStatus === 'approved') {
    return { draft: 'done', dryRun: 'done', review: 'done', approved: 'done', publish: 'current' };
  }
  if (i.reviewStatus === 'in_review') {
    return { draft: 'done', dryRun: 'done', review: 'current', approved: 'todo', publish: 'todo' };
  }
  // 'draft' or 'changes_requested' — still being authored.
  return {
    draft: 'done',
    dryRun: i.simulated ? 'done' : 'current',
    review: !i.simulated ? 'todo' : i.publishable ? 'current' : 'blocked',
    approved: 'todo',
    publish: 'todo',
  };
}

export interface ApplicationsInput {
  title: string;
  /** Public applications on and the opening open. */
  accepting: boolean;
  hasLiveVersion: boolean;
  draftVersion: number | null;
  waiting?: { applied: number; shortlisted: number } | null;
}

/**
 * The warning for an opening that is taking applications with nothing live.
 *
 * Nobody is lost — applicants are recorded, and publishing attaches them — but
 * nothing moves for them until then, and the people applying cannot tell.
 * Null when there is nothing to warn about.
 */
export function applicationsWarning(i: ApplicationsInput): string | null {
  if (!i.accepting || i.hasLiveVersion) return null;
  const waiting = (i.waiting?.applied ?? 0) + (i.waiting?.shortlisted ?? 0);
  const who =
    waiting > 0
      ? ` ${waiting} candidate${waiting === 1 ? ' is' : 's are'} already waiting.`
      : '';
  const next =
    i.draftVersion !== null
      ? ` Publish version ${i.draftVersion} to start them.`
      : ' Build and publish a workflow to start them.';
  return `${i.title} is accepting applications, but no workflow is live — nothing happens for applicants until one is.${who}${next}`;
}

/** What publishing will do to the candidates waiting, said before it happens. */
export function publishImpact(waiting?: { applied: number; shortlisted: number } | null): string | null {
  if (!waiting) return null;
  const parts: string[] = [];
  if (waiting.shortlisted > 0) {
    parts.push(
      `${waiting.shortlisted} shortlisted candidate${waiting.shortlisted === 1 ? '' : 's'} will start the first round`,
    );
  }
  if (waiting.applied > 0) {
    parts.push(
      `${waiting.applied} applicant${waiting.applied === 1 ? '' : 's'} will join and wait for your shortlist`,
    );
  }
  return parts.length ? `${parts.join('; ')}.` : null;
}

/**
 * One line on whether the process measures the job overall (D3). The share is
 * computed server-side from the same coverage rows; this only words it.
 * Weighted, because missing a competency worth 0.05 and one worth 0.30 are both
 * "one gap" and only the weight tells them apart.
 */
export function coverageVerdict(share: number | null | undefined): string | null {
  if (share === null || share === undefined) return null;
  const pct = Math.round(share * 100);
  if (pct >= 90) return `Strong coverage: the rounds assess ${pct}% of what this role weighs.`;
  if (pct >= 75) return `Reasonable coverage: the rounds assess ${pct}% of what this role weighs.`;
  return `Thin coverage: the rounds assess only ${pct}% of what this role weighs — check the gaps below.`;
}
