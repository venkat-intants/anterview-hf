// deriveCandidateActions — the order is the contract.
//
// Table-driven, because the failure mode is a plausible-looking queue in the
// wrong order: the dashboard then leads with a task that can wait while an
// assessment clock is running. Each case names what a candidate would see first.

import { describe, it, expect } from 'vitest';
import type { MyApplication } from '../api/applications';
import type { CandidateLoop } from '../api/scheduling';
import type { CandidateOffer } from '../api/offers';
import {
  actionsForApplication,
  deriveCandidateActions,
  isWaitingOnYou,
  turnOf,
  type CandidateActionKind,
} from '../lib/candidateActions';

function app(over: Partial<MyApplication> = {}): MyApplication {
  return {
    id: 'app-1',
    job_title: 'Backend Engineer',
    company_name: 'Acme',
    applied_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    stage: 'Application received',
    next_step: 'x',
    closed: false,
    current_round_title: null,
    current_round_kind: null,
    round_number: null,
    total_rounds: null,
    interview_invite_id: null,
    interview_scheduled_at: null,
    task_submission_id: null,
    task_due_at: null,
    task_status: null,
    exam_assignment_id: null,
    exam_in_progress: false,
    exam_expires_at: null,
    exam_scheduled_at: null,
    ...over,
  };
}

function loop(over: Partial<CandidateLoop> = {}): CandidateLoop {
  return {
    id: 'loop-1',
    title: 'Panel',
    job_title: 'Backend Engineer',
    status: 'draft',
    timezone: 'Asia/Kolkata',
    self_schedule: true,
    sessions: [
      {
        id: 's-1',
        title: 'Round 1',
        duration_minutes: 30,
        starts_at: null,
        ends_at: null,
        location: null,
        status: 'awaiting_slot',
        interviewers: [],
      },
    ],
    ...over,
  };
}

function offer(over: Partial<CandidateOffer> = {}): CandidateOffer {
  return {
    id: 'off-1',
    status: 'sent',
    company: 'Acme',
    job_title: 'Backend Engineer',
    employment_type: null,
    start_date: null,
    location: null,
    base_salary: null,
    currency: null,
    pay_period: null,
    bonus: null,
    equity: null,
    benefits: null,
    terms: null,
    probation_months: null,
    notice_period_days: null,
    expires_at: null,
    responded_at: null,
    preboarding_completed_at: null,
    ...over,
  };
}

const kinds = (xs: ReturnType<typeof deriveCandidateActions>): CandidateActionKind[] =>
  xs.map((a) => a.kind);

describe('deriveCandidateActions — every kind', () => {
  const cases: Array<{
    name: string;
    apps: MyApplication[];
    loops?: CandidateLoop[];
    offers?: CandidateOffer[];
    kind: CandidateActionKind;
    target: string;
    titleKey: string;
  }> = [
    {
      name: 'an assessment waiting',
      apps: [app({ exam_assignment_id: 'asn-1', exam_expires_at: '2026-10-20T00:00:00Z' })],
      kind: 'exam',
      target: 'mint_exam',
      titleKey: 'nextStep.startExam',
    },
    {
      name: 'an assessment under way',
      apps: [app({ exam_assignment_id: 'asn-1', exam_in_progress: true })],
      kind: 'exam_resume',
      target: 'mint_exam',
      titleKey: 'nextStep.resumeExam',
    },
    {
      name: 'an AI interview waiting',
      apps: [app({ interview_invite_id: 'inv-1' })],
      kind: 'interview',
      target: 'mint_interview',
      titleKey: 'nextStep.startInterview',
    },
    {
      name: 'a task waiting',
      apps: [app({ task_submission_id: 'sub-1' })],
      kind: 'task',
      target: 'mint_task',
      titleKey: 'nextStep.openTask',
    },
    {
      name: 'a time to choose',
      apps: [app()],
      loops: [loop({ application_id: 'app-1' })],
      kind: 'choose_time',
      target: 'route',
      titleKey: 'nextStep.chooseTime',
    },
    {
      name: 'an offer to answer',
      apps: [app()],
      offers: [offer({ status: 'sent' })],
      kind: 'offer',
      target: 'mint_offer',
      titleKey: 'nextStep.answerOffer',
    },
    {
      name: 'joining documents',
      apps: [app({ closed: true })],
      offers: [offer({ status: 'accepted', preboarding_completed_at: null })],
      kind: 'documents',
      target: 'mint_offer',
      titleKey: 'nextStep.checkDocuments',
    },
  ];

  it.each(cases)('$name', ({ apps, loops, offers, kind, target, titleKey }) => {
    const [first, ...rest] = deriveCandidateActions(apps, loops ?? [], offers ?? []);
    expect(first.kind).toBe(kind);
    expect(first.target.type).toBe(target);
    expect(first.titleKey).toBe(titleKey);
    expect(isWaitingOnYou(first)).toBe(true);
    expect(rest).toEqual([]);
  });

  it('words the documents action as a check, never an upload', () => {
    // The signed-in offer payload carries no document count, so "upload" could
    // point at an empty checklist.
    const [a] = deriveCandidateActions(
      [app({ closed: true })],
      [],
      [offer({ status: 'accepted' })],
    );
    expect(a.titleKey).toBe('nextStep.checkDocuments');
    expect(a.ctaKey).toBe('nextStep.openDocuments');
  });

  it('carries role, company and the application it belongs to', () => {
    const [a] = deriveCandidateActions(
      [
        app({
          id: 'app-9',
          job_title: 'Data Analyst',
          company_name: 'Northwind',
          interview_invite_id: 'i',
        }),
      ],
      [],
      [],
    );
    expect(a).toMatchObject({ role: 'Data Analyst', company: 'Northwind', applicationId: 'app-9' });
  });
});

describe('deriveCandidateActions — order', () => {
  it('puts an assessment under way ahead of an earlier deadline', () => {
    const q = deriveCandidateActions(
      [
        app({ id: 'a', interview_invite_id: 'i', interview_scheduled_at: '2026-10-13T00:00:00Z' }),
        app({ id: 'b', exam_assignment_id: 'e', exam_in_progress: true }),
      ],
      [],
      [],
    );
    expect(kinds(q)).toEqual(['exam_resume', 'interview']);
  });

  it('orders what else is waiting by soonest date', () => {
    const q = deriveCandidateActions(
      [
        app({ id: 'a', task_submission_id: 't', task_due_at: '2026-10-30T00:00:00Z' }),
        app({ id: 'b', exam_assignment_id: 'e', exam_expires_at: '2026-10-18T00:00:00Z' }),
        app({ id: 'c', interview_invite_id: 'i', interview_scheduled_at: '2026-10-14T00:00:00Z' }),
      ],
      [],
      [offer({ expires_at: '2026-10-16T00:00:00Z' })],
    );
    expect(kinds(q)).toEqual(['interview', 'offer', 'exam', 'task']);
  });

  it('uses a scheduled round’s own date, not the link expiry', () => {
    const [a] = deriveCandidateActions(
      [
        app({
          exam_assignment_id: 'e',
          exam_scheduled_at: '2026-10-14T09:00:00Z',
          exam_expires_at: '2026-11-30T00:00:00Z',
        }),
      ],
      [],
      [],
    );
    expect(a.date).toBe('2026-10-14T09:00:00Z');
    expect(a.dateKind).toBe('exam_opens');
  });

  it('falls back to the link expiry for an unscheduled assessment', () => {
    const [a] = deriveCandidateActions(
      [app({ exam_assignment_id: 'e', exam_expires_at: '2026-10-20T00:00:00Z' })],
      [],
      [],
    );
    expect(a.dateKind).toBe('exam_before');
  });

  it('puts dated items before undated ones, and undated in the fixed order', () => {
    const q = deriveCandidateActions(
      [
        app({ id: 'a', task_submission_id: 't' }),
        app({ id: 'b', exam_assignment_id: 'e' }),
        app({ id: 'c', interview_invite_id: 'i' }),
        app({ id: 'd', task_submission_id: 't2', task_due_at: '2026-12-01T00:00:00Z' }),
      ],
      [loop({ application_id: 'a' })],
      [offer({ id: 'o1', status: 'sent' }), offer({ id: 'o2', status: 'accepted' })],
    );
    expect(kinds(q)).toEqual([
      'task', // the only dated item
      'exam',
      'interview',
      'task',
      'choose_time',
      'offer',
      'documents',
    ]);
    expect(q[0].applicationId).toBe('d');
  });

  it('breaks a date tie by the fixed kind order', () => {
    const when = '2026-10-15T00:00:00Z';
    const q = deriveCandidateActions(
      [
        app({ id: 'a', task_submission_id: 't', task_due_at: when }),
        app({ id: 'b', interview_invite_id: 'i', interview_scheduled_at: when }),
      ],
      [],
      [],
    );
    expect(kinds(q)).toEqual(['interview', 'task']);
  });

  it('keeps the server’s order for equal items', () => {
    const q = deriveCandidateActions(
      [
        app({ id: 'first', interview_invite_id: 'i1' }),
        app({ id: 'second', interview_invite_id: 'i2' }),
      ],
      [],
      [],
    );
    expect(q.map((a) => a.applicationId)).toEqual(['first', 'second']);
  });

  it('treats an unparseable date as undated rather than sorting on NaN', () => {
    const q = deriveCandidateActions(
      [
        app({ id: 'a', task_submission_id: 't', task_due_at: 'not-a-date' }),
        app({ id: 'b', interview_invite_id: 'i', interview_scheduled_at: '2026-10-14T00:00:00Z' }),
      ],
      [],
      [],
    );
    expect(kinds(q)).toEqual(['interview', 'task']);
  });
});

describe('deriveCandidateActions — what is NOT waiting on the candidate', () => {
  it('says "with the hiring team" when applications are open and nothing is waiting', () => {
    const q = deriveCandidateActions([app()], [], []);
    expect(kinds(q)).toEqual(['with_team']);
    expect(q[0].target).toEqual({ type: 'none' });
    expect(q[0].ctaKey).toBeUndefined();
    expect(isWaitingOnYou(q[0])).toBe(false);
  });

  it('ignores a decided offer, a declined offer and a finished loop', () => {
    const q = deriveCandidateActions(
      [app()],
      [loop({ status: 'completed', sessions: [] }), loop({ id: 'l2', self_schedule: false })],
      [
        offer({ id: 'a', status: 'declined' }),
        offer({ id: 'b', status: 'expired' }),
        offer({ id: 'c', status: 'withdrawn' }),
        offer({ id: 'd', status: 'accepted', preboarding_completed_at: '2026-10-01T00:00:00Z' }),
      ],
    );
    expect(kinds(q)).toEqual(['with_team']);
  });

  it('ignores a cancelled loop and a loop with no session awaiting a time', () => {
    const q = deriveCandidateActions(
      [app()],
      [
        loop({ status: 'cancelled' }),
        loop({
          id: 'l2',
          sessions: [{ ...loop().sessions[0], status: 'scheduled' }],
        }),
      ],
      [],
    );
    expect(kinds(q)).toEqual(['with_team']);
  });

  it('falls to practice when there are no applications at all', () => {
    const q = deriveCandidateActions([], [], []);
    expect(kinds(q)).toEqual(['practice_first']);
    expect(q[0].target).toEqual({ type: 'route', to: '/start' });
  });

  it('falls to practice when every application is closed and nothing is waiting', () => {
    const q = deriveCandidateActions([app({ closed: true })], [], []);
    expect(q[0].kind.startsWith('practice')).toBe(true);
  });

  it.each<[string, Parameters<typeof deriveCandidateActions>[3], CandidateActionKind, string]>([
    [
      'the plan focus first',
      { planReady: true, focusCompetency: 'Machine Setup', interviewsTaken: 4 },
      'practice_focus',
      '/start',
    ],
    [
      'a first interview next',
      { planReady: false, focusCompetency: null, interviewsTaken: 0 },
      'practice_first',
      '/start',
    ],
    [
      'a target role after that',
      { planReady: false, focusCompetency: null, interviewsTaken: 3 },
      'practice_target',
      '/onboarding',
    ],
    [
      'otherwise just the next practice',
      { planReady: true, focusCompetency: null, interviewsTaken: 3 },
      'practice_next',
      '/start',
    ],
  ])('offers %s', (_name, practice, kind, to) => {
    const [a] = deriveCandidateActions([], [], [], practice);
    expect(a.kind).toBe(kind);
    expect(a.target).toEqual({ type: 'route', to });
    if (kind === 'practice_focus') expect(a.titleValues).toEqual({ competency: 'Machine Setup' });
  });

  it('never mints: nothing in the result is a link, only the means to ask for one', () => {
    const q = deriveCandidateActions(
      [app({ exam_assignment_id: 'e', interview_invite_id: 'i', task_submission_id: 't' })],
      [],
      [offer()],
    );
    expect(JSON.stringify(q)).not.toMatch(/https?:|#tok|_url/);
  });
});

describe('deriveCandidateActions — placing loops and offers', () => {
  it('places a loop or offer on its application only by application_id', () => {
    const q = deriveCandidateActions(
      [
        app({ id: 'one', job_title: 'Backend Engineer', company_name: 'Acme' }),
        app({ id: 'two', job_title: 'Backend Engineer', company_name: 'Acme' }),
      ],
      [loop({ application_id: 'two' })],
      [offer({ application_id: 'one' })],
    );
    const byKind = Object.fromEntries(q.map((a) => [a.kind, a.applicationId]));
    expect(byKind).toEqual({ choose_time: 'two', offer: 'one' });
  });

  it('never matches on job title when the id is absent', () => {
    // Same title, same company, two openings: guessing would put the offer on
    // the wrong card. Without an id it stays unplaced.
    const q = deriveCandidateActions([app({ id: 'one' }), app({ id: 'two' })], [loop()], [offer()]);
    expect(q.every((a) => a.applicationId === null)).toBe(true);
    // ...but it is still in the queue, with its own role and company.
    expect(q.find((a) => a.kind === 'offer')).toMatchObject({
      role: 'Backend Engineer',
      company: 'Acme',
    });
  });

  it('leaves an offer unplaced when its application_id is not in the list', () => {
    const [a] = deriveCandidateActions([app()], [], [offer({ application_id: 'gone' })]);
    expect(a.applicationId).toBeNull();
  });
});

describe('actionsForApplication / turnOf', () => {
  const queue = deriveCandidateActions(
    [
      app({ id: 'mine', interview_invite_id: 'i' }),
      app({ id: 'theirs' }),
      app({ id: 'done', closed: true }),
    ],
    [],
    [],
  );

  it('selects only an application’s own waiting items', () => {
    expect(actionsForApplication(queue, 'mine')).toHaveLength(1);
    expect(actionsForApplication(queue, 'theirs')).toEqual([]);
  });

  it('is the candidate’s move exactly when an action is present', () => {
    expect(turnOf(app({ id: 'mine' }), actionsForApplication(queue, 'mine'))).toBe('you');
    expect(turnOf(app({ id: 'theirs' }), [])).toBe('them');
    expect(turnOf(app({ closed: true }), [])).toBe('closed');
  });

  it('keeps a closed application in the candidate’s move while documents are due', () => {
    const q = deriveCandidateActions(
      [app({ id: 'hired', closed: true })],
      [],
      [offer({ status: 'accepted', application_id: 'hired' })],
    );
    expect(turnOf(app({ closed: true }), actionsForApplication(q, 'hired'))).toBe('you');
  });
});
