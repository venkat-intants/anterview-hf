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
  return `${i.title} is accepting applications, but no workflow is live - nothing happens for applicants until one is.${who}${next}`;
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
  return `Thin coverage: the rounds assess only ${pct}% of what this role weighs - check the gaps below.`;
}

/* ── The approval track and the one next action (UX round 3, stream B) ───────
 *
 * `stepStates()` above is the older five-step read and stays exported as it was
 * (the opening hub imports it). The builder's track has the five steps the
 * owner named — Rounds valid, Dry run, Submitted for review, Approved,
 * Published — and the header's single primary follows from the same inputs, so
 * the two can never disagree about what comes next.
 */

export type TrackStep = 'rounds' | 'dryRun' | 'submitted' | 'approved' | 'published';
export type TrackState = 'done' | 'current' | 'todo' | 'returned';

export interface TrackInput extends LifecycleInput {
  /** How many rounds the version has. An empty version has nothing to validate. */
  roundCount: number;
  /**
   * Another version of this opening is already a draft. The server allows ONE
   * draft per opening (workflows.create_draft refuses a second with "This
   * opening already has a draft workflow"), so cloning cannot succeed then.
   */
  draftExists?: boolean;
}

/**
 * Where a version stands on the path to live. `returned` (the reviewer asked
 * for changes) sits on the submission step: that is the step that was sent
 * back. At most one step is `current`.
 */
export function trackStates(i: TrackInput): Record<TrackStep, TrackState> {
  if (i.status !== 'draft') {
    return { rounds: 'done', dryRun: 'done', submitted: 'done', approved: 'done', published: 'done' };
  }
  if (i.reviewStatus === 'approved') {
    return { rounds: 'done', dryRun: 'done', submitted: 'done', approved: 'done', published: 'current' };
  }
  if (i.reviewStatus === 'in_review') {
    return { rounds: 'done', dryRun: 'done', submitted: 'done', approved: 'current', published: 'todo' };
  }
  // 'draft' or 'changes_requested' - still being authored.
  const roundsValid = i.roundCount > 0 && i.publishable;
  const returned = i.reviewStatus === 'changes_requested';
  return {
    rounds: roundsValid ? 'done' : 'current',
    dryRun: i.simulated ? 'done' : roundsValid ? 'current' : 'todo',
    submitted: returned ? 'returned' : roundsValid && i.simulated ? 'current' : 'todo',
    approved: 'todo',
    published: 'todo',
  };
}

export type NextAction = 'dry_run' | 'submit' | 'publish' | 'edit_new_version';

/**
 * The one legal next action, or null when there is none to offer (an empty
 * draft; a version waiting on its reviewer, whose only moves — withdraw — are
 * quiet ones). Dry run is the next action until one exists for the CURRENT
 * content; the server runs its own on submit, but the track says dry run comes
 * first and so does the button.
 *
 * "Edit as new version" is offered only where it can succeed: for the live
 * version while no draft exists. An archived version has been superseded, and a
 * second draft is refused by the server, so neither gets a primary.
 */
export function nextAction(i: TrackInput): NextAction | null {
  if (i.status === 'archived') return null;
  if (i.status === 'published') return i.draftExists ? null : 'edit_new_version';
  if (i.reviewStatus === 'approved') return 'publish';
  if (i.reviewStatus === 'in_review') return null;
  if (i.roundCount === 0) return null;
  return i.simulated ? 'submit' : 'dry_run';
}
