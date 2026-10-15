// bankFlow - the question bank's five stages, as pure functions of what the
// server holds. Table-driven: one row per bank state, every stage asserted.

import { describe, it, expect } from 'vitest';
import {
  bankFacts,
  bankNextAction,
  bankStages,
  canOpenStage,
  defaultStageKey,
  isSentBack,
  kindTally,
  matchesBankFilter,
  newBankStages,
  nextStageKey,
  nextStatement,
  parseStage,
  questionBrief,
  statusCounts,
  type BankStageKey,
} from '../lib/bankFlow';
import { REFERENCE_UNVERIFIED_TAG, type BankOut, type BankQuestion } from '../api/questionBanks';
import type { StageState } from '../design/components/journey-rail';

function bank(counts: BankOut['counts'], over: Partial<BankOut> = {}): BankOut {
  return {
    id: 'b-1',
    name: 'Backend Core',
    description: null,
    counts,
    used_in_exams: 0,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-01T00:00:00.000Z',
    ...over,
  };
}

function q(over: Partial<BankQuestion> = {}): BankQuestion {
  return {
    id: 'q',
    bank_id: 'b-1',
    root_id: 'q',
    version: 1,
    kind: 'mcq',
    prompt: 'What is a closure?',
    points: 1,
    options: ['a', 'b'],
    correct_index: 0,
    starter_code: null,
    reference_solution: null,
    allowed_languages: null,
    test_cases: null,
    time_limit_ms: null,
    difficulty: 'medium',
    language: 'en',
    competencies: [],
    tags: [],
    status: 'draft',
    origin: 'authored',
    content_hash: 'h',
    created_by_user_id: 'u-1',
    submitted_by_user_id: null,
    submitted_at: null,
    reviewed_by_user_id: null,
    reviewed_at: null,
    review_note: null,
    retired_by_user_id: null,
    retired_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-01T00:00:00.000Z',
    ...over,
  };
}

type Row = [
  name: string,
  counts: BankOut['counts'],
  states: [StageState, StageState, StageState, StageState, StageState],
  open: BankStageKey,
  next: BankStageKey | null,
];

const ROWS: Row[] = [
  ['empty', {}, ['done', 'active', 'blocked', 'blocked', 'blocked'], 'add', 'add'],
  [
    'empty with zero counts',
    { draft: 0, approved: 0 },
    ['done', 'active', 'blocked', 'blocked', 'blocked'],
    'add',
    'add',
  ],
  ['drafts only', { draft: 4 }, ['done', 'done', 'active', 'todo', 'blocked'], 'check', 'check'],
  [
    'drafts and approved',
    { draft: 2, approved: 5 },
    ['done', 'done', 'active', 'todo', 'done'],
    'check',
    'check',
  ],
  [
    'drafts and in review',
    { draft: 1, in_review: 3 },
    ['done', 'done', 'active', 'todo', 'blocked'],
    'check',
    'check',
  ],
  [
    'in review only',
    { in_review: 3 },
    ['done', 'done', 'done', 'awaiting', 'blocked'],
    'send',
    null,
  ],
  [
    'in review and approved',
    { in_review: 1, approved: 4 },
    ['done', 'done', 'done', 'awaiting', 'done'],
    'send',
    null,
  ],
  ['fully approved', { approved: 6 }, ['done', 'done', 'done', 'done', 'done'], 'approved', null],
  [
    'approved and retired',
    { approved: 2, retired: 1 },
    ['done', 'done', 'done', 'done', 'done'],
    'approved',
    null,
  ],
  // Every question retired: nothing is live, so this reads like an empty bank -
  // Add questions is the next stage, and no later stage claims to be done.
  [
    'only retired',
    { retired: 2 },
    ['done', 'active', 'blocked', 'blocked', 'blocked'],
    'add',
    'add',
  ],
  [
    'drafts, in review and approved together',
    { draft: 1, in_review: 2, approved: 3 },
    ['done', 'done', 'active', 'todo', 'done'],
    'check',
    'check',
  ],
  [
    'drafts, in review, approved and retired',
    { draft: 1, in_review: 1, approved: 1, retired: 4 },
    ['done', 'done', 'active', 'todo', 'done'],
    'check',
    'check',
  ],
  [
    'in review and retired',
    { in_review: 2, retired: 1 },
    ['done', 'done', 'done', 'awaiting', 'blocked'],
    'send',
    null,
  ],
];

describe('bankStages - every state comes from the bank counts', () => {
  it.each(ROWS)('%s', (_name, counts, states, open, next) => {
    const stages = bankStages(bankFacts(bank(counts)));
    expect(stages.map((s) => s.key)).toEqual(['setup', 'add', 'check', 'send', 'approved']);
    expect(stages.map((s) => s.state)).toEqual(states);
    expect(defaultStageKey(stages)).toBe(open);
    expect(nextStageKey(stages)).toBe(next);
  });

  it.each(ROWS)('%s - the opened stage agrees with the next-step strip', (_name, counts) => {
    const f = bankFacts(bank(counts));
    expect(defaultStageKey(bankStages(f))).toBe(nextStatement(f).stage);
  });

  it('an all-retired bank says so on the add stage and blocks the later ones', () => {
    const stages = bankStages(bankFacts(bank({ retired: 3 })));
    expect(stages[1].fact).toBe('Every question is retired');
    expect(stages.slice(2).every((s) => s.state === 'blocked')).toBe(true);
    expect(stages[4].reason).toBe('Every question is retired.');
  });

  it('only sent-back drafts: the check stage is active and names them', () => {
    const list = [
      q({ id: '1', review_note: 'Option B is also right.' }),
      q({ id: '2', review_note: 'Too easy.' }),
    ];
    const f = bankFacts(bank({ draft: 2 }), list);
    const stages = bankStages(f);
    expect(stages.map((s) => s.state)).toEqual(['done', 'done', 'active', 'todo', 'blocked']);
    expect(stages[2].fact).toBe('2 drafts not yet submitted · 2 sent back');
    expect(nextStatement(f).context).toMatch(/2 sent back by a reviewer/);
    expect(defaultStageKey(stages)).toBe('check');
  });

  it('only in-review: nothing to do but wait, and the strip says who must act', () => {
    const f = bankFacts(bank({ in_review: 2 }), [
      q({ id: '1', status: 'in_review' }),
      q({ id: '2', status: 'in_review' }),
    ]);
    const stages = bankStages(f);
    expect(nextStageKey(stages)).toBeNull();
    expect(defaultStageKey(stages)).toBe('send');
    expect(nextStatement(f).stage).toBe('send');
    expect(nextStatement(f).tone).toBe('warn');
  });

  it('states the draft count as a fact, never a percentage', () => {
    const stages = bankStages(bankFacts(bank({ draft: 4 })));
    expect(stages[2].fact).toBe('4 drafts not yet submitted');
    expect(JSON.stringify(stages)).not.toMatch(/%/);
  });

  it('says there is nothing unsubmitted once the drafts are gone', () => {
    const stages = bankStages(bankFacts(bank({ in_review: 2 })));
    expect(stages[2].fact).toBe('No drafts left unsubmitted');
    expect(stages[3].fact).toBe('2 in review');
    expect(stages[3].actor).toMatch(/another reviewer/i);
  });

  it('singularises one draft', () => {
    expect(bankStages(bankFacts(bank({ draft: 1 })))[2].fact).toBe('1 draft not yet submitted');
  });

  it('gives a blocked stage the reason it is blocked', () => {
    const stages = bankStages(bankFacts(bank({})));
    expect(stages[2].reason).toBe('Add questions first.');
    expect(stages[3].reason).toBeTruthy();
    expect(stages[4].reason).toBeTruthy();
  });

  it('counts the stage-2 fact by kind once the question list is known', () => {
    const list = [q({ id: '1' }), q({ id: '2', kind: 'coding' }), q({ id: '3', kind: 'coding' })];
    const stages = bankStages(bankFacts(bank({ draft: 3 }), list));
    expect(stages[1].fact).toBe('1 MCQ · 2 coding');
  });

  it('falls back to a plain count while the list has not loaded', () => {
    expect(bankStages(bankFacts(bank({ draft: 3 })))[1].fact).toBe('3 questions');
  });

  it('adds the sent-back count to the check stage and the approved stage', () => {
    const list = [
      q({ id: '1', review_note: 'Option B is also right.' }),
      q({ id: '2' }),
      q({ id: '3', status: 'approved' }),
    ];
    const stages = bankStages(bankFacts(bank({ draft: 2, approved: 1 }), list));
    expect(stages[2].fact).toBe('2 drafts not yet submitted · 1 sent back');
    expect(stages[4].fact).toBe('1 approved · 1 sent back');
  });
});

describe('newBankStages - before the bank exists', () => {
  it('opens only stage 1', () => {
    const stages = newBankStages();
    expect(stages.map((s) => s.state)).toEqual([
      'active',
      'blocked',
      'blocked',
      'blocked',
      'blocked',
    ]);
    expect(nextStageKey(stages)).toBe('setup');
    expect(canOpenStage(false, 'setup')).toBe(true);
    expect(canOpenStage(false, 'add')).toBe(false);
    expect(canOpenStage(true, 'approved')).toBe(true);
  });
});

describe('bankFacts', () => {
  it('reads kinds, sent-back, AI drafts and unverified solutions from the list', () => {
    const list = [
      q({ id: '1', origin: 'ai_draft' }),
      q({
        id: '2',
        kind: 'coding',
        origin: 'ai_draft',
        reference_solution: 'print(1)',
        tags: [REFERENCE_UNVERIFIED_TAG],
      }),
      // A coding draft whose solution is not flagged is not "unverified".
      q({ id: '3', kind: 'coding', reference_solution: 'print(2)' }),
      q({ id: '4', status: 'retired' }),
    ];
    const f = bankFacts(bank({ draft: 3, retired: 1 }), list);
    expect(f.kinds).toEqual({ mcq: 1, coding: 2 }); // retired is not "in the bank"
    expect(f.aiDrafts).toBe(2);
    expect(f.unverifiedDrafts).toBe(1);
    expect(f.sentBack).toBe(0);
    expect(f.total).toBe(4);
  });

  it('leaves list-derived facts undefined until the list exists, never zero', () => {
    const f = bankFacts(bank({ draft: 1 }));
    expect(f.kinds).toBeUndefined();
    expect(f.sentBack).toBeUndefined();
  });

  it('only a DRAFT with a note is sent back', () => {
    expect(isSentBack({ status: 'draft', review_note: 'Fix it' })).toBe(true);
    expect(isSentBack({ status: 'draft', review_note: '  ' })).toBe(false);
    expect(isSentBack({ status: 'draft', review_note: null })).toBe(false);
    expect(isSentBack({ status: 'approved', review_note: 'Looks good' })).toBe(false);
  });
});

describe('nextStatement', () => {
  it.each([
    ['empty', {}, 'Add the first questions', 'add'],
    ['drafts', { draft: 3 }, '3 drafts not yet submitted', 'check'],
    ['in review', { in_review: 2 }, '2 in review', 'send'],
    ['approved', { approved: 5 }, '5 approved and ready to use', 'approved'],
    ['retired', { retired: 1 }, 'Every question is retired', 'add'],
  ] as const)('%s', (_n, counts, title, stage) => {
    const s = nextStatement(bankFacts(bank(counts)));
    expect(s.title).toBe(title);
    expect(s.stage).toBe(stage);
  });

  it('says plainly that the author cannot approve their own questions', () => {
    expect(nextStatement(bankFacts(bank({ in_review: 1 }))).context).toMatch(
      /cannot approve your own/,
    );
  });

  it('flags AI drafts and unverified solutions in the draft statement', () => {
    const list = [
      q({
        id: '1',
        origin: 'ai_draft',
        kind: 'coding',
        reference_solution: 'x',
        tags: [REFERENCE_UNVERIFIED_TAG],
      }),
    ];
    const s = nextStatement(bankFacts(bank({ draft: 1 }), list));
    expect(s.context).toMatch(/1 written by AI/);
    expect(s.context).toMatch(/tests nobody has run/);
  });
});

describe('the list page', () => {
  it.each([
    ['empty', {}, 'Add the first questions', 'add', 'act'],
    ['drafts', { draft: 3 }, '3 drafts to submit', 'check', 'act'],
    ['one draft', { draft: 1, approved: 4 }, '1 draft to submit', 'check', 'act'],
    ['waiting', { in_review: 2 }, 'Waiting on review', 'send', 'wait'],
    ['ready', { approved: 5 }, 'Ready to use in exams', 'approved', 'ready'],
    ['retired', { retired: 1 }, 'Add new questions', 'add', 'act'],
  ] as const)('next action - %s', (_n, counts, label, stage, kind) => {
    expect(bankNextAction(bank(counts))).toEqual({ label, stage, kind });
  });

  it('filters by what the bank needs', () => {
    const empty = bank({});
    const drafts = bank({ draft: 2 });
    const review = bank({ in_review: 1 });
    const ready = bank({ approved: 3 });
    const matrix = {
      all: [1, 1, 1, 1],
      drafts: [0, 1, 0, 0],
      review: [0, 0, 1, 0],
      ready: [0, 0, 0, 1],
      empty: [1, 0, 0, 0],
    } as const;
    for (const [f, expected] of Object.entries(matrix)) {
      expect(
        [empty, drafts, review, ready].map((b) =>
          Number(matchesBankFilter(b, f as keyof typeof matrix)),
        ),
      ).toEqual(expected);
    }
  });

  it('lists status counts in review order and leaves zeros out', () => {
    expect(statusCounts(bank({ approved: 5, draft: 2, in_review: 0 })).map((s) => s.label)).toEqual(
      ['2 draft', '5 approved'],
    );
  });
});

describe('small helpers', () => {
  it('parseStage accepts only the five stages', () => {
    expect(parseStage('check')).toBe('check');
    expect(parseStage('nope')).toBeNull();
    expect(parseStage(null)).toBeNull();
    expect(parseStage('')).toBeNull();
  });

  it('kindTally leaves out a kind with none', () => {
    expect(kindTally({ mcq: 3, coding: 0 })).toBe('3 MCQ');
    expect(kindTally({ mcq: 0, coding: 2 })).toBe('2 coding');
    expect(kindTally({ mcq: 3, coding: 2 })).toBe('3 MCQ · 2 coding');
    expect(kindTally({ mcq: 0, coding: 0 })).toBeNull();
    expect(kindTally(undefined)).toBeNull();
  });

  it('questionBrief takes the first non-empty line and shortens it', () => {
    expect(questionBrief('\n\nFirst line\nSecond')).toBe('First line');
    expect(questionBrief('x'.repeat(200), 10)).toBe('xxxxxxxxx…');
  });
});
