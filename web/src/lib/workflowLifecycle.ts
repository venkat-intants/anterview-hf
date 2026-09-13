// The workflow lifecycle rules the builder shows (D5), kept pure so they can be
// tested without rendering the builder.

export type StepState = 'done' | 'current' | 'blocked' | 'todo';

export interface LifecycleInput {
  status: 'draft' | 'published' | 'archived';
  previewed: boolean;
  issues: number;
  publishable: boolean;
}

/** Draft → Preview → Validate → Publish, as states for the stepper. */
export function stepStates(i: LifecycleInput): Record<'draft' | 'preview' | 'validate' | 'publish', StepState> {
  if (i.status !== 'draft') {
    return { draft: 'done', preview: 'done', validate: 'done', publish: 'done' };
  }
  return {
    draft: 'done',
    preview: i.previewed ? 'done' : 'current',
    validate: i.publishable ? 'done' : i.previewed ? 'blocked' : 'todo',
    publish: i.publishable ? (i.previewed ? 'current' : 'todo') : 'todo',
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
