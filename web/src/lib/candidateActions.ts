// candidateActions — the one ordered answer to "what is waiting on me?".
//
// The dashboard's next-step strip, each application card's one action row and
// the "n more waiting" count all read this queue, so they cannot disagree.
//
// Pure on purpose: it takes what the three candidate endpoints returned and
// returns a list. It fetches nothing and mints nothing — the action it carries
// is DESCRIBED here (`target`), and only a deliberate click on the button the UI
// builds from it asks the server for a link. Minting rotates the token, so doing
// it on render would silently kill the emailed link of everyone who merely
// looked at their dashboard.
//
// Order (candidate-journey §3 P1):
//   1. an assessment already under way — a clock is running;
//   2. everything else waiting on the candidate, soonest date first;
//   3. undated items, in the fixed kind order below;
//   4. nothing waiting but applications still open: "with the hiring team";
//   5. no open application at all: the practice next step.
//
// Nothing here guesses. An offer or interview is placed on an application only
// when the server sent `application_id`; otherwise it keeps its own role and
// company and renders on its own. Never matched on job title: two openings can
// share one.

import type { MyApplication } from '@/api/applications';
import type { CandidateLoop } from '@/api/scheduling';
import type { CandidateOffer } from '@/api/offers';

export type CandidateActionKind =
  | 'exam_resume'
  | 'exam'
  | 'interview'
  | 'task'
  | 'choose_time'
  | 'offer'
  | 'documents'
  | 'with_team'
  | 'practice_focus'
  | 'practice_first'
  | 'practice_target'
  | 'practice_next';

/** What pressing the action does. */
export type CandidateActionTarget =
  | { type: 'mint_exam'; assignmentId: string; inProgress: boolean }
  | { type: 'mint_interview'; inviteId: string }
  | { type: 'mint_task'; submissionId: string }
  | { type: 'mint_offer'; offerId: string }
  /** `anchor` names an element on /applications to scroll to when already there. */
  | { type: 'route'; to: string; anchor?: string }
  | { type: 'none' };

/** Which sentence the date belongs to; the UI turns it into words. */
export type CandidateActionDateKind =
  | 'exam_opens'
  | 'exam_before'
  | 'scheduled'
  | 'due'
  | 'respond_by';

export interface CandidateAction {
  /** Stable across renders; safe as a React key. */
  id: string;
  kind: CandidateActionKind;
  /** i18n key for the headline, under `nextStep.*`. */
  titleKey: string;
  /** Values for `titleKey` (the practice-focus competency). */
  titleValues?: Record<string, string>;
  /** i18n key for the button label. Absent when there is no action. */
  ctaKey?: string;
  role: string | null;
  company: string | null;
  /** The application this belongs to, when known. */
  applicationId: string | null;
  /** ISO date the item is due or starts; null when there is none. */
  date: string | null;
  dateKind: CandidateActionDateKind | null;
  target: CandidateActionTarget;
}

/** What the practice fallback needs to know. Optional: callers without a plan omit it. */
export interface PracticeContext {
  planReady: boolean;
  focusCompetency: string | null;
  interviewsTaken: number;
}

/**
 * Kinds that mean "it is the candidate's move". `with_team` and the practice
 * kinds are fallbacks and are never counted as waiting.
 */
const WAITING_ON_YOU: ReadonlySet<CandidateActionKind> = new Set<CandidateActionKind>([
  'exam_resume',
  'exam',
  'interview',
  'task',
  'choose_time',
  'offer',
  'documents',
]);

export function isWaitingOnYou(action: CandidateAction): boolean {
  return WAITING_ON_YOU.has(action.kind);
}

/** The fixed order undated items (and date ties) fall back to. */
const KIND_ORDER: readonly CandidateActionKind[] = [
  'exam',
  'interview',
  'task',
  'choose_time',
  'offer',
  'documents',
];

function kindRank(kind: CandidateActionKind): number {
  const i = KIND_ORDER.indexOf(kind);
  return i === -1 ? KIND_ORDER.length : i;
}

function timeOf(iso: string | null): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * The queue items that belong to one application, in queue order. Only items
 * the server tied to that application (its own ids, or an `application_id` on a
 * loop or offer) appear; nothing is matched by job title.
 */
export function actionsForApplication(
  actions: readonly CandidateAction[],
  applicationId: string,
): CandidateAction[] {
  return actions.filter((a) => a.applicationId === applicationId && isWaitingOnYou(a));
}

/**
 * Whose move it is on an application: the candidate's exactly when something is
 * waiting on them for it; otherwise closed or with the hiring team.
 */
export function turnOf(
  application: Pick<MyApplication, 'closed'>,
  mine: readonly CandidateAction[],
): 'you' | 'them' | 'closed' {
  if (mine.length > 0) return 'you';
  return application.closed ? 'closed' : 'them';
}

interface Draft {
  action: CandidateAction;
  /** Position in the input, so equal items keep the order the server gave them. */
  seq: number;
}

/**
 * The ordered queue for one candidate.
 *
 * `loops` and `offers` are `[]` when not fetched (no applications) or when the
 * fetch failed; a missing source never blocks the others.
 */
export function deriveCandidateActions(
  applications: readonly MyApplication[],
  loops: readonly CandidateLoop[],
  offers: readonly CandidateOffer[],
  practice?: PracticeContext,
): CandidateAction[] {
  const byId = new Map(applications.map((a) => [a.id, a] as const));
  const drafts: Draft[] = [];
  let seq = 0;
  const push = (action: CandidateAction): void => {
    drafts.push({ action, seq: seq++ });
  };

  for (const app of applications) {
    const base = {
      role: app.job_title,
      company: app.company_name,
      applicationId: app.id,
    };

    if (app.exam_assignment_id) {
      const inProgress = app.exam_in_progress;
      // A scheduled round's own window is the deadline that binds; the link's
      // expiry is the fallback. Neither is shown for an attempt already under
      // way — it keeps its original deadline and the page says "pick up where
      // you left off" instead.
      const opens = app.exam_scheduled_at;
      const date = inProgress ? null : (opens ?? app.exam_expires_at);
      push({
        ...base,
        id: `exam:${app.id}`,
        kind: inProgress ? 'exam_resume' : 'exam',
        titleKey: inProgress ? 'nextStep.resumeExam' : 'nextStep.startExam',
        ctaKey: inProgress ? 'applications.resumeAssessment' : 'applications.startAssessment',
        date,
        dateKind: inProgress
          ? null
          : opens
            ? 'exam_opens'
            : app.exam_expires_at
              ? 'exam_before'
              : null,
        target: { type: 'mint_exam', assignmentId: app.exam_assignment_id, inProgress },
      });
    }

    if (app.interview_invite_id) {
      push({
        ...base,
        id: `interview:${app.id}`,
        kind: 'interview',
        titleKey: 'nextStep.startInterview',
        ctaKey: 'applications.startInterview',
        date: app.interview_scheduled_at,
        dateKind: app.interview_scheduled_at ? 'scheduled' : null,
        target: { type: 'mint_interview', inviteId: app.interview_invite_id },
      });
    }

    if (app.task_submission_id) {
      push({
        ...base,
        id: `task:${app.id}`,
        kind: 'task',
        titleKey: 'nextStep.openTask',
        ctaKey: 'applications.openTask',
        date: app.task_due_at,
        dateKind: app.task_due_at ? 'due' : null,
        target: { type: 'mint_task', submissionId: app.task_submission_id },
      });
    }
  }

  for (const loop of loops) {
    if (loop.status === 'cancelled' || !loop.self_schedule) continue;
    if (!loop.sessions.some((s) => s.status === 'awaiting_slot')) continue;
    const app = loop.application_id ? byId.get(loop.application_id) : undefined;
    push({
      id: `choose_time:${loop.id}`,
      kind: 'choose_time',
      titleKey: 'nextStep.chooseTime',
      ctaKey: 'nextStep.pickTime',
      role: app?.job_title ?? loop.job_title,
      company: app?.company_name ?? null,
      applicationId: app?.id ?? null,
      date: null,
      dateKind: null,
      target: { type: 'route', to: '/applications', anchor: `loop-${loop.id}` },
    });
  }

  for (const offer of offers) {
    const app = offer.application_id ? byId.get(offer.application_id) : undefined;
    const where = {
      role: app?.job_title ?? offer.job_title,
      company: app?.company_name ?? offer.company,
      applicationId: app?.id ?? null,
    };
    if (offer.status === 'sent') {
      push({
        ...where,
        id: `offer:${offer.id}`,
        kind: 'offer',
        titleKey: 'nextStep.answerOffer',
        ctaKey: 'myOffers.openOffer',
        date: offer.expires_at,
        dateKind: offer.expires_at ? 'respond_by' : null,
        target: { type: 'mint_offer', offerId: offer.id },
      });
    } else if (offer.status === 'accepted' && !offer.preboarding_completed_at) {
      // The signed-in payload carries no document count, so this cannot say
      // "upload"; the page it opens knows what is still needed.
      push({
        ...where,
        id: `documents:${offer.id}`,
        kind: 'documents',
        titleKey: 'nextStep.checkDocuments',
        ctaKey: 'nextStep.openDocuments',
        date: null,
        dateKind: null,
        target: { type: 'mint_offer', offerId: offer.id },
      });
    }
  }

  const resuming = drafts.filter((d) => d.action.kind === 'exam_resume');
  const rest = drafts.filter((d) => d.action.kind !== 'exam_resume');
  const dated = rest
    .filter((d) => timeOf(d.action.date) !== null)
    .sort(
      (a, b) =>
        (timeOf(a.action.date) as number) - (timeOf(b.action.date) as number) ||
        kindRank(a.action.kind) - kindRank(b.action.kind) ||
        a.seq - b.seq,
    );
  const undated = rest
    .filter((d) => timeOf(d.action.date) === null)
    .sort((a, b) => kindRank(a.action.kind) - kindRank(b.action.kind) || a.seq - b.seq);

  const queue = [...resuming, ...dated, ...undated].map((d) => d.action);
  if (queue.length > 0) return queue;

  if (applications.some((a) => !a.closed)) {
    return [
      {
        id: 'with_team',
        kind: 'with_team',
        titleKey: 'nextStep.nothing',
        role: null,
        company: null,
        applicationId: null,
        date: null,
        dateKind: null,
        target: { type: 'none' },
      },
    ];
  }

  return [practiceAction(practice)];
}

function practiceAction(practice?: PracticeContext): CandidateAction {
  const none = {
    role: null,
    company: null,
    applicationId: null,
    date: null,
    dateKind: null,
  } as const;
  if (practice?.planReady && practice.focusCompetency) {
    return {
      ...none,
      id: 'practice',
      kind: 'practice_focus',
      titleKey: 'nextStep.practiceFocus',
      titleValues: { competency: practice.focusCompetency },
      ctaKey: 'nextStep.startPractice',
      target: { type: 'route', to: '/start' },
    };
  }
  if (!practice || practice.interviewsTaken === 0) {
    return {
      ...none,
      id: 'practice',
      kind: 'practice_first',
      titleKey: 'nextStep.practiceFirst',
      ctaKey: 'nextStep.startPractice',
      target: { type: 'route', to: '/start' },
    };
  }
  if (!practice.planReady) {
    return {
      ...none,
      id: 'practice',
      kind: 'practice_target',
      titleKey: 'nextStep.practiceTarget',
      ctaKey: 'nextStep.setTarget',
      target: { type: 'route', to: '/onboarding' },
    };
  }
  return {
    ...none,
    id: 'practice',
    kind: 'practice_next',
    titleKey: 'nextStep.practiceNext',
    ctaKey: 'nextStep.startPractice',
    target: { type: 'route', to: '/start' },
  };
}
