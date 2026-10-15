// openingStages() — the setup rail's states.
//
// The rule these pin: a stage is `done` ONLY where the server holds the named
// artefact. A tick that lies is worse than no tick, so each table is built to
// show the neighbouring cases that must NOT tick.

import { describe, it, expect } from 'vitest';
import type { ApprovalStatus } from '../api/requisitions';
import {
  defaultStageKey,
  examProgress,
  jdFacts,
  nextStageKey,
  openingStages,
  primaryAction,
  type OpeningSetupInput,
  type OpeningStage,
  type StageKey,
  type WorkflowFacts,
} from '../lib/openingSetup';

const LIVE_WF: WorkflowFacts = {
  version: 2,
  status: 'published',
  reviewStatus: 'approved',
  rounds: [{ kind: 'mcq', needsQuestions: false, examRoundId: 'x1' }],
  simulated: true,
  publishable: true,
};

function draftWf(over: Partial<WorkflowFacts> = {}): WorkflowFacts {
  return {
    version: 1,
    status: 'draft',
    reviewStatus: 'draft',
    rounds: [
      { kind: 'mcq', needsQuestions: false, examRoundId: 'x1' },
      { kind: 'ai_interview', needsQuestions: false, examRoundId: null },
    ],
    simulated: false,
    publishable: true,
    ...over,
  };
}

function input(over: Partial<OpeningSetupInput> = {}): OpeningSetupInput {
  return {
    requisitionId: 'req-1',
    status: 'open',
    publicApplyEnabled: false,
    approvalStatus: 'draft',
    publishedWorkflowVersion: null,
    draftWorkflowVersion: null,
    hasPublishedWorkflow: false,
    workflow: null,
    jd: { publishedVersionId: null, publishedVersion: null, draftVersion: null },
    publishAt: null,
    documentCount: 0,
    ...over,
  };
}

const stage = (stages: OpeningStage[], key: StageKey): OpeningStage => {
  const s = stages.find((x) => x.key === key);
  if (!s) throw new Error(`no stage ${key}`);
  return s;
};

describe('the five stages', () => {
  it('are in the ruled order, with preboarding last', () => {
    expect(openingStages(input()).map((s) => s.key)).toEqual([
      'role',
      'process',
      'approval',
      'golive',
      'documents',
    ]);
  });

  it('a brand-new opening ticks nothing', () => {
    const stages = openingStages(input());
    expect(stages.filter((s) => s.state === 'done')).toEqual([]);
  });
});

describe('stage 1, role and posting: done only with a published JD version', () => {
  const cases: [string, OpeningSetupInput['jd'], string, RegExp][] = [
    ['nothing written', { publishedVersionId: null, publishedVersion: null, draftVersion: null }, 'todo', /No JD published/],
    ['a draft only', { publishedVersionId: null, publishedVersion: null, draftVersion: 1 }, 'active', /draft v1 unpublished/],
    ['published', { publishedVersionId: 'j1', publishedVersion: 2, draftVersion: null }, 'done', /JD v2 published/],
    ['published and a newer draft', { publishedVersionId: 'j1', publishedVersion: 2, draftVersion: 3 }, 'done', /draft v3 unpublished/],
  ];
  it.each(cases)('%s', (_n, jd, state, fact) => {
    const s = stage(openingStages(input({ jd })), 'role');
    expect(s.state).toBe(state);
    expect(String(s.fact)).toMatch(fact);
  });

  it('is loading, and not a tick, until JD history answers', () => {
    const s = stage(openingStages(input({ jd: undefined })), 'role');
    expect(s.state).toBe('todo');
    expect(s.loading).toBe(true);
  });

  it('builds its facts from JD history', () => {
    expect(
      jdFacts({
        published_version_id: 'b',
        versions: [
          { id: 'a', version: 1, status: 'archived' },
          { id: 'b', version: 2, status: 'published' },
          { id: 'c', version: 3, status: 'draft' },
        ],
      }),
    ).toEqual({ publishedVersionId: 'b', publishedVersion: 2, draftVersion: 3 });
    expect(jdFacts({ published_version_id: null, versions: [] }).publishedVersion).toBeNull();
  });
});

describe('stage 2, hiring process: done only when a workflow is published', () => {
  const cases: [string, Partial<OpeningSetupInput>, string][] = [
    ['no workflow', {}, 'todo'],
    ['a draft being built', { draftWorkflowVersion: 1, workflow: draftWf() }, 'active'],
    ['a draft changes requested', { draftWorkflowVersion: 1, workflow: draftWf({ reviewStatus: 'changes_requested' }) }, 'active'],
    ['a draft in review', { draftWorkflowVersion: 1, workflow: draftWf({ reviewStatus: 'in_review' }) }, 'awaiting'],
    ['a draft approved but not published', { draftWorkflowVersion: 1, workflow: draftWf({ reviewStatus: 'approved' }) }, 'active'],
    ['published', { publishedWorkflowVersion: 2, hasPublishedWorkflow: true, workflow: LIVE_WF }, 'done'],
    ['published with a draft in review', { publishedWorkflowVersion: 2, hasPublishedWorkflow: true, draftWorkflowVersion: 3, workflow: draftWf({ version: 3, reviewStatus: 'in_review' }) }, 'done'],
  ];
  it.each(cases)('%s', (_n, over, state) => {
    expect(stage(openingStages(input(over)), 'process').state).toBe(state);
  });

  it('is not ticked by an approved OPENING: the two approvals are parallel', () => {
    const s = stage(openingStages(input({ approvalStatus: 'approved' })), 'process');
    expect(s.state).toBe('todo');
  });

  it('does not read the opening approval to decide, in either direction', () => {
    const published = { publishedWorkflowVersion: 2, hasPublishedWorkflow: true, workflow: LIVE_WF };
    for (const a of ['draft', 'pending_approval', 'approved', 'rejected'] as ApprovalStatus[]) {
      expect(stage(openingStages(input({ ...published, approvalStatus: a })), 'process').state).toBe('done');
    }
  });
});

describe('stage 2 sub-steps', () => {
  const states = (over: Partial<OpeningSetupInput>) =>
    Object.fromEntries(
      (stage(openingStages(input(over)), 'process').substeps ?? []).map((x) => [x.key, x.state]),
    );

  it('names the six sub-steps in order', () => {
    const keys = (stage(openingStages(input()), 'process').substeps ?? []).map((x) => x.key);
    expect(keys).toEqual(['rounds', 'exams', 'dryRun', 'submitted', 'approved', 'published']);
  });

  it('a workflow that does not exist starts at Rounds', () => {
    expect(states({})).toEqual({
      rounds: 'active', exams: 'todo', dryRun: 'todo', submitted: 'todo', approved: 'todo', published: 'todo',
    });
  });

  it('a draft with no rounds has nothing to dry-run, submit or approve', () => {
    expect(states({ draftWorkflowVersion: 1, workflow: draftWf({ rounds: [] }) })).toEqual({
      rounds: 'active', exams: 'todo', dryRun: 'todo', submitted: 'todo', approved: 'todo', published: 'todo',
    });
  });

  it('exams are ticked only when every exam-backed round has one attached', () => {
    const missing = draftWf({
      rounds: [
        { kind: 'mcq', needsQuestions: false, examRoundId: 'x1' },
        { kind: 'coding', needsQuestions: true, examRoundId: null },
      ],
    });
    expect(states({ draftWorkflowVersion: 1, workflow: missing }).exams).toBe('active');
    const labelled = stage(openingStages(input({ draftWorkflowVersion: 1, workflow: missing })), 'process');
    expect(labelled.substeps?.find((x) => x.key === 'exams')?.label).toBe('Exams attached (1 of 2)');

    const flagged = draftWf({ rounds: [{ kind: 'mcq', needsQuestions: true, examRoundId: 'x1' }] });
    expect(states({ draftWorkflowVersion: 1, workflow: flagged }).exams).toBe('active');
    expect(states({ draftWorkflowVersion: 1, workflow: draftWf() }).exams).toBe('done');
  });

  it('reuses stepStates() for dry run, submitted, approved and published', () => {
    const base = { draftWorkflowVersion: 1 };
    expect(states({ ...base, workflow: draftWf({ simulated: false }) })).toMatchObject({
      rounds: 'done', dryRun: 'active', submitted: 'todo', approved: 'todo', published: 'todo',
    });
    expect(states({ ...base, workflow: draftWf({ simulated: true, publishable: true }) })).toMatchObject({
      dryRun: 'done', submitted: 'active',
    });
    expect(states({ ...base, workflow: draftWf({ simulated: true, publishable: false }) })).toMatchObject({
      submitted: 'blocked',
    });
    expect(states({ ...base, workflow: draftWf({ reviewStatus: 'in_review' }) })).toMatchObject({
      dryRun: 'done', submitted: 'active', approved: 'todo',
    });
    expect(states({ ...base, workflow: draftWf({ reviewStatus: 'approved' }) })).toMatchObject({
      submitted: 'done', approved: 'done', published: 'active',
    });
  });

  it('a live version with no draft has every sub-step done', () => {
    const st = states({ publishedWorkflowVersion: 2, hasPublishedWorkflow: true, workflow: LIVE_WF });
    expect(Object.values(st).every((x) => x === 'done')).toBe(true);
  });

  it('are left out, not guessed, while the workflow detail is loading or for the read-only viewer', () => {
    expect(stage(openingStages(input({ draftWorkflowVersion: 1, workflow: undefined })), 'process').substeps).toBeUndefined();
    expect(
      stage(openingStages(input({ viewer: 'readonly', publishedWorkflowVersion: 2, hasPublishedWorkflow: true })), 'process')
        .substeps,
    ).toBeUndefined();
  });

  it('counts exam-backed rounds from the round fields', () => {
    expect(
      examProgress([
        { kind: 'mcq', needsQuestions: false, examRoundId: 'a' },
        { kind: 'coding', needsQuestions: true, examRoundId: null },
        { kind: 'human_review', needsQuestions: false, examRoundId: null },
      ]),
    ).toEqual({ total: 2, attached: 1 });
  });
});

describe('stage 3, budget and approval: done only when approval_status is approved', () => {
  const cases: [ApprovalStatus, string][] = [
    ['draft', 'todo'],
    ['pending_approval', 'awaiting'],
    ['approved', 'done'],
    ['rejected', 'active'],
  ];
  it.each(cases)('%s', (approvalStatus, state) => {
    expect(stage(openingStages(input({ approvalStatus })), 'approval').state).toBe(state);
  });

  it('names who submitted and when while it waits', () => {
    const s = stage(
      openingStages(input({ approvalStatus: 'pending_approval', submittedByName: 'Asha R', submittedAt: '2026-10-04T09:00:00Z' })),
      'approval',
    );
    expect(s.actor).toBe('Submitted by Asha R');
    expect(s.at).toMatch(/Oct/);
  });

  it('carries the decider and the note on a send-back', () => {
    const s = stage(
      openingStages(input({ approvalStatus: 'rejected', decidedByName: 'Meera S', approvalNote: 'Backfill needs a band' })),
      'approval',
    );
    expect(String(s.fact)).toMatch(/Backfill needs a band/);
    expect(s.actor).toBe('Sent back by Meera S');
  });

  it('states the budget as a fact, or says there is none', () => {
    const withBudget = stage(
      openingStages(input({ budget: { amount: 1800000, currency: 'INR', basis: 'per_hire', period: 'annual' } })),
      'approval',
    );
    expect(String(withBudget.fact)).toMatch(/INR 18,00,000 per hire, annual/);
    expect(String(stage(openingStages(input()), 'approval').fact)).toMatch(/no budget set/);
  });

  it('is independent of the workflow', () => {
    const s = stage(
      openingStages(input({ approvalStatus: 'approved', draftWorkflowVersion: 1, workflow: draftWf() })),
      'approval',
    );
    expect(s.state).toBe('done');
  });
});

describe('stage 4, go live', () => {
  const live = { publicApplyEnabled: true, status: 'open' as const, hasPublishedWorkflow: true, publishedWorkflowVersion: 2, approvalStatus: 'approved' as const };

  it('is done only when applications are on, the opening is open and a workflow is live', () => {
    expect(stage(openingStages(input({ ...live, workflow: LIVE_WF })), 'golive').state).toBe('done');
    expect(stage(openingStages(input({ ...live, publicApplyEnabled: false })), 'golive').state).not.toBe('done');
    expect(stage(openingStages(input({ ...live, status: 'paused' })), 'golive').state).not.toBe('done');
    expect(stage(openingStages(input({ ...live, hasPublishedWorkflow: false, publishedWorkflowVersion: null })), 'golive').state).not.toBe('done');
  });

  const blocked: [string, Partial<OpeningSetupInput>, RegExp][] = [
    ['nothing done', {}, /live hiring process \(stage 2\) and budget approval \(stage 3\)/],
    ['approved, no workflow', { approvalStatus: 'approved' }, /^Needs a live hiring process \(stage 2\)\.$/],
    ['workflow live, not approved', { hasPublishedWorkflow: true, publishedWorkflowVersion: 2 }, /^Needs budget approval \(stage 3\)\.$/],
    ['workflow live, approval pending', { hasPublishedWorkflow: true, publishedWorkflowVersion: 2, approvalStatus: 'pending_approval' }, /^Needs budget approval \(stage 3\)\.$/],
  ];
  it.each(blocked)('is blocked, with the reason named: %s', (_n, over, reason) => {
    const s = stage(openingStages(input(over)), 'golive');
    expect(s.state).toBe('blocked');
    expect(String(s.reason)).toMatch(reason);
    expect(s.action).toBeUndefined();
  });

  it('stages 2 and 3 never block each other', () => {
    // Approval done does not make the process stage anything but its own state,
    // and vice versa.
    const a = openingStages(input({ approvalStatus: 'approved' }));
    expect(stage(a, 'process').state).toBe('todo');
    const b = openingStages(input({ hasPublishedWorkflow: true, publishedWorkflowVersion: 2, workflow: LIVE_WF }));
    expect(stage(b, 'approval').state).toBe('todo');
    expect(stage(a, 'process').state).not.toBe('blocked');
    expect(stage(b, 'approval').state).not.toBe('blocked');
  });

  it('opens up once both are done', () => {
    const ready = openingStages(input({ approvalStatus: 'approved', hasPublishedWorkflow: true, publishedWorkflowVersion: 2, workflow: LIVE_WF }));
    expect(stage(ready, 'golive').state).toBe('todo');
    expect(stage(ready, 'golive').action?.label).toBe('Open to applications');
  });

  it('shows a scheduled go-live as waiting', () => {
    const s = stage(
      openingStages(input({ approvalStatus: 'approved', hasPublishedWorkflow: true, publishedWorkflowVersion: 2, workflow: LIVE_WF, publishAt: '2026-10-20T04:30:00Z' })),
      'golive',
    );
    expect(s.state).toBe('awaiting');
  });

  it('says so when it is switched on but the opening is not open', () => {
    const s = stage(
      openingStages(input({ approvalStatus: 'approved', hasPublishedWorkflow: true, publishedWorkflowVersion: 2, workflow: LIVE_WF, publicApplyEnabled: true, status: 'paused' })),
      'golive',
    );
    expect(s.state).toBe('active');
    expect(String(s.fact)).toMatch(/paused/);
  });
});

describe('stage 5, preboarding documents: never ticked', () => {
  it.each([0, 1, 4, undefined])('is optional whatever the count (%s)', (documentCount) => {
    const s = stage(openingStages(input({ documentCount })), 'documents');
    expect(s.state).toBe('optional');
  });

  it('states the count as a fact', () => {
    expect(String(stage(openingStages(input({ documentCount: 3 })), 'documents').fact)).toBe('3 documents listed');
    expect(String(stage(openingStages(input({ documentCount: 0 })), 'documents').fact)).toBe('None required');
    expect(stage(openingStages(input({ documentCount: undefined })), 'documents').fact).toBeUndefined();
  });
});

describe('every approval status x workflow state: done only where the named field is set', () => {
  const approvals: ApprovalStatus[] = ['draft', 'pending_approval', 'approved', 'rejected'];
  const workflows: [string, Partial<OpeningSetupInput>][] = [
    ['none', {}],
    ['draft', { draftWorkflowVersion: 1, workflow: draftWf() }],
    ['in review', { draftWorkflowVersion: 1, workflow: draftWf({ reviewStatus: 'in_review' }) }],
    ['published', { publishedWorkflowVersion: 2, hasPublishedWorkflow: true, workflow: LIVE_WF }],
  ];
  const matrix = approvals.flatMap((a) => workflows.map(([w, over]) => [a, w, over] as const));

  it.each(matrix)('%s x %s', (approvalStatus, _w, over) => {
    const stages = openingStages(input({ approvalStatus, ...over }));
    expect(stage(stages, 'approval').state === 'done').toBe(approvalStatus === 'approved');
    expect(stage(stages, 'process').state === 'done').toBe(Boolean(over.hasPublishedWorkflow));
    // go live is never done here: applications are off in every row
    expect(stage(stages, 'golive').state).not.toBe('done');
    expect(stage(stages, 'documents').state).toBe('optional');
    // blocked exactly when stage 2 or 3 is not done
    const bothDone = stage(stages, 'approval').state === 'done' && stage(stages, 'process').state === 'done';
    expect(stage(stages, 'golive').state === 'blocked').toBe(!bothDone);
  });
});

describe('the read-only viewer', () => {
  it('sees stages 2 to 4, which the dashboard alone can state', () => {
    const stages = openingStages(input({ viewer: 'readonly' }));
    expect(stages.map((s) => s.key)).toEqual(['process', 'approval', 'golive']);
  });
});

describe('next stage and the primary action', () => {
  it('is the first stage that is neither done, waiting nor blocked', () => {
    expect(nextStageKey(openingStages(input()))).toBe('role');
    const jdDone = { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null } };
    expect(nextStageKey(openingStages(input(jdDone)))).toBe('process');
    expect(
      nextStageKey(openingStages(input({ ...jdDone, hasPublishedWorkflow: true, publishedWorkflowVersion: 1, workflow: LIVE_WF }))),
    ).toBe('approval');
  });

  it('skips a stage that is waiting on someone else', () => {
    const jdDone = { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null } };
    const stages = openingStages(
      input({ ...jdDone, draftWorkflowVersion: 1, workflow: draftWf({ reviewStatus: 'in_review' }) }),
    );
    expect(nextStageKey(stages)).toBe('approval');
  });

  it('is null while the first stage is still loading, so the answer does not flip', () => {
    expect(nextStageKey(openingStages(input({ jd: undefined })))).toBeNull();
    expect(primaryAction(openingStages(input({ jd: undefined })))).toBeNull();
    expect(defaultStageKey(openingStages(input({ jd: undefined })))).toBe('role');
  });

  it('never picks preboarding documents', () => {
    const done = openingStages(input({
      jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null },
      approvalStatus: 'approved', hasPublishedWorkflow: true, publishedWorkflowVersion: 1, workflow: LIVE_WF,
      publicApplyEnabled: true,
    }));
    expect(nextStageKey(done)).toBeNull();
    expect(primaryAction(done)).toBeNull();
  });

  const actions: [string, Partial<OpeningSetupInput>, string, string | null][] = [
    ['no JD', {}, 'Write the job description', null],
    ['JD draft', { jd: { publishedVersionId: null, publishedVersion: null, draftVersion: 1 } }, 'Publish the job description', null],
    ['no workflow', { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null } }, 'Build the hiring process', '/hr/requisitions/req-1/workflow'],
    ['workflow draft', { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null }, draftWorkflowVersion: 1, workflow: draftWf() }, 'Continue the hiring process', '/hr/requisitions/req-1/workflow'],
    ['workflow approved', { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null }, draftWorkflowVersion: 1, workflow: draftWf({ reviewStatus: 'approved' }) }, 'Publish the workflow', '/hr/requisitions/req-1/workflow'],
    ['not submitted', { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null }, hasPublishedWorkflow: true, publishedWorkflowVersion: 1, workflow: LIVE_WF }, 'Review budget and submit', null],
    ['sent back', { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null }, hasPublishedWorkflow: true, publishedWorkflowVersion: 1, workflow: LIVE_WF, approvalStatus: 'rejected' }, 'Revise and resubmit', null],
    ['ready to go live', { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null }, hasPublishedWorkflow: true, publishedWorkflowVersion: 1, workflow: LIVE_WF, approvalStatus: 'approved' }, 'Open to applications', null],
  ];
  it.each(actions)('the primary for %s', (_n, over, label, to) => {
    const a = primaryAction(openingStages(input(over)));
    expect(a?.label).toBe(label);
    expect(a?.to).toBe(to);
  });

  it('defaults the open stage to the next one, else one that is waiting', () => {
    const jdDone = { jd: { publishedVersionId: 'j', publishedVersion: 1, draftVersion: null } };
    expect(defaultStageKey(openingStages(input(jdDone)))).toBe('process');
    const waiting = openingStages(input({
      ...jdDone, hasPublishedWorkflow: true, publishedWorkflowVersion: 1, workflow: LIVE_WF,
      approvalStatus: 'pending_approval',
    }));
    expect(nextStageKey(waiting)).toBeNull();
    expect(defaultStageKey(waiting)).toBe('approval');
  });
});
