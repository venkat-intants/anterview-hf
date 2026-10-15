// The question bank's creation flow, as data.
//
// A bank goes from "just created" to "usable in an exam" through five stages.
// This file turns what the server holds into the stages `JourneyRail` draws and
// nothing else: no component, no fetching, so every rule is table-testable.
//
// THE RULE A TICK FOLLOWS
// `done` means the server holds the artefact, never that a form was filled:
//
//   1 Set up the bank       the bank exists (it has an id)
//   2 Add questions         the bank holds at least one question (any status)
//   3 Check your drafts     there is at least one question and NO draft is left
//                           unsubmitted (`counts.draft === 0`)
//   4 Send for review       drafts === 0 and nothing is still in review
//                           (`awaiting` while `counts.in_review > 0`)
//   5 Approved and in use   at least one question is approved
//
// Where a count is not on `BankOut` (kinds, sent-back, AI origin) it comes from
// the bank's question list, and the stage says nothing about it until that list
// has loaded - a missing list is `undefined`, never zero.
//
// A draft that carries a `review_note` was sent back by a reviewer (a request
// for changes returns the question to `draft` with the note). That is the only
// signal the API gives, so that is the definition used here.

import type { JourneyStage, StageState } from '@/design/components/journey-rail';
import {
  isReferenceUnverified,
  type BankOut,
  type BankQuestion,
  type BankQuestionStatus,
} from '@/api/questionBanks';

export type BankStageKey = 'setup' | 'add' | 'check' | 'send' | 'approved';

export const BANK_STAGE_ORDER: readonly BankStageKey[] = [
  'setup',
  'add',
  'check',
  'send',
  'approved',
];

export const BANK_STAGE_TITLE: Record<BankStageKey, string> = {
  setup: 'Set up the bank',
  add: 'Add questions',
  check: 'Check your drafts',
  send: 'Send for review',
  approved: 'Approved and in use',
};

export const BANK_STATE_LABELS: Record<StageState, string> = {
  todo: 'To do',
  active: 'In progress',
  blocked: 'Not yet',
  awaiting: 'Waiting',
  done: 'Done',
  optional: 'Optional',
};

export interface BankStage extends JourneyStage {
  key: BankStageKey;
}

/** Everything the stages read. Build it with `bankFacts()`. */
export interface BankFacts {
  draft: number;
  inReview: number;
  approved: number;
  retired: number;
  total: number;
  usedInExams: number;
  /** From the question list; undefined until it has loaded. */
  kinds?: { mcq: number; coding: number };
  sentBack?: number;
  /** Drafts written by the AI generator. */
  aiDrafts?: number;
  /** Coding drafts the server marked unverified: nobody has run their tests. */
  unverifiedDrafts?: number;
}

const n = (v: number | undefined): number => (typeof v === 'number' && v > 0 ? v : 0);

/** A draft that a reviewer returned with a note. */
export function isSentBack(q: Pick<BankQuestion, 'status' | 'review_note'>): boolean {
  return q.status === 'draft' && Boolean(q.review_note && q.review_note.trim());
}

export function bankFacts(bank: BankOut, questions?: readonly BankQuestion[]): BankFacts {
  const c = bank.counts;
  const draft = n(c.draft);
  const inReview = n(c.in_review);
  const approved = n(c.approved);
  const retired = n(c.retired);
  const facts: BankFacts = {
    draft,
    inReview,
    approved,
    retired,
    total: draft + inReview + approved + retired,
    usedInExams: bank.used_in_exams,
  };
  if (questions) {
    const live = questions.filter((q) => q.status !== 'retired');
    facts.kinds = {
      mcq: live.filter((q) => q.kind === 'mcq').length,
      coding: live.filter((q) => q.kind === 'coding').length,
    };
    const drafts = questions.filter((q) => q.status === 'draft');
    facts.sentBack = drafts.filter(isSentBack).length;
    facts.aiDrafts = drafts.filter((q) => q.origin === 'ai_draft').length;
    facts.unverifiedDrafts = drafts.filter(
      (q) => q.kind === 'coding' && isReferenceUnverified(q),
    ).length;
  }
  return facts;
}

const plural = (count: number, one: string, many = `${one}s`): string =>
  `${count} ${count === 1 ? one : many}`;

/** "3 MCQ · 2 coding", leaving out a kind that has none; null when both are zero. */
export function kindTally(kinds: { mcq: number; coding: number } | undefined): string | null {
  if (!kinds) return null;
  const parts: string[] = [];
  if (kinds.mcq > 0) parts.push(`${kinds.mcq} MCQ`);
  if (kinds.coding > 0) parts.push(`${kinds.coding} coding`);
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** The five stages of a bank that exists. */
export function bankStages(f: BankFacts): BankStage[] {
  const empty = f.total === 0;
  // Questions exist but every one is retired: nothing is live, so the honest
  // next step is the same as for an empty bank - add questions - and no later
  // stage can read as done.
  const allRetired = f.total > 0 && f.draft + f.inReview + f.approved === 0;
  const noLive = empty || allRetired;

  const setup: BankStage = {
    key: 'setup',
    title: BANK_STAGE_TITLE.setup,
    state: 'done',
    fact: 'Bank created',
  };

  const tally = kindTally(f.kinds);
  const add: BankStage = noLive
    ? {
        key: 'add',
        title: BANK_STAGE_TITLE.add,
        state: 'active',
        fact: allRetired ? 'Every question is retired' : 'No questions yet',
      }
    : {
        key: 'add',
        title: BANK_STAGE_TITLE.add,
        state: 'done',
        fact: tally ?? plural(f.total, 'question'),
      };

  let check: BankStage;
  if (noLive) {
    check = {
      key: 'check',
      title: BANK_STAGE_TITLE.check,
      state: 'blocked',
      reason: 'Add questions first.',
    };
  } else if (f.draft > 0) {
    const bits = [`${plural(f.draft, 'draft')} not yet submitted`];
    if (n(f.sentBack) > 0) bits.push(`${n(f.sentBack)} sent back`);
    check = {
      key: 'check',
      title: BANK_STAGE_TITLE.check,
      state: 'active',
      fact: bits.join(' · '),
    };
  } else {
    check = {
      key: 'check',
      title: BANK_STAGE_TITLE.check,
      state: 'done',
      fact: 'No drafts left unsubmitted',
    };
  }

  let send: BankStage;
  if (f.draft > 0) {
    send = {
      key: 'send',
      title: BANK_STAGE_TITLE.send,
      state: 'todo',
      fact:
        f.inReview > 0
          ? `${plural(f.draft, 'draft')} to send · ${f.inReview} already in review`
          : `${plural(f.draft, 'draft')} to send`,
    };
  } else if (f.inReview > 0) {
    send = {
      key: 'send',
      title: BANK_STAGE_TITLE.send,
      state: 'awaiting',
      fact: `${f.inReview} in review`,
      actor: 'Another reviewer must approve',
    };
  } else if (f.approved > 0) {
    send = {
      key: 'send',
      title: BANK_STAGE_TITLE.send,
      state: 'done',
      fact: 'Nothing waiting for review',
    };
  } else {
    send = {
      key: 'send',
      title: BANK_STAGE_TITLE.send,
      state: 'blocked',
      reason: 'No questions have been submitted.',
    };
  }

  let approved: BankStage;
  if (f.approved > 0) {
    const bits = [`${f.approved} approved`];
    if (f.retired > 0) bits.push(`${f.retired} retired`);
    if (n(f.sentBack) > 0) bits.push(`${n(f.sentBack)} sent back`);
    approved = {
      key: 'approved',
      title: BANK_STAGE_TITLE.approved,
      state: 'done',
      fact: bits.join(' · '),
    };
  } else {
    approved = {
      key: 'approved',
      title: BANK_STAGE_TITLE.approved,
      state: 'blocked',
      reason: allRetired ? 'Every question is retired.' : 'Nothing is approved yet.',
    };
  }

  return [setup, add, check, send, approved];
}

/** The rail before the bank exists: stage 1 is the only thing that can be done. */
export function newBankStages(): BankStage[] {
  return BANK_STAGE_ORDER.map(
    (key): BankStage =>
      key === 'setup'
        ? { key, title: BANK_STAGE_TITLE[key], state: 'active', fact: 'Name it to begin' }
        : { key, title: BANK_STAGE_TITLE[key], state: 'blocked', reason: 'Create the bank first.' },
  );
}

/** The first stage still waiting on the user. Null when none is. */
export function nextStageKey(stages: readonly BankStage[]): BankStageKey | null {
  return stages.find((s) => s.state === 'todo' || s.state === 'active')?.key ?? null;
}

/** Which stage a bank opens on: the next one, else one that is waiting, else the last. */
export function defaultStageKey(stages: readonly BankStage[]): BankStageKey {
  const next = nextStageKey(stages);
  if (next) return next;
  const waiting = stages.find((s) => s.state === 'awaiting');
  if (waiting) return waiting.key;
  return 'approved';
}

/** A `?stage=` value, or null for anything that is not a stage of this flow. */
export function parseStage(raw: string | null | undefined): BankStageKey | null {
  return BANK_STAGE_ORDER.find((k) => k === raw) ?? null;
}

/** Whether the user can open a stage (a bank that does not exist yet has only stage 1). */
export function canOpenStage(exists: boolean, key: BankStageKey): boolean {
  return exists || key === 'setup';
}

/* ── Statements for the NextStep strip ──────────────────────────────────── */

export interface NextStatement {
  tone: 'info' | 'warn' | 'ok' | 'default';
  title: string;
  context?: string;
  /** The stage the strip's action leads to; null for a plain statement. */
  stage: BankStageKey | null;
  actionLabel?: string;
}

/** What the bank needs next, in a sentence a person can act on. */
export function nextStatement(f: BankFacts): NextStatement {
  if (f.total === 0) {
    return {
      tone: 'info',
      title: 'Add the first questions',
      context: 'Write them, draft them with AI, or import a file. Everything starts as a draft.',
      stage: 'add',
      actionLabel: 'Add questions',
    };
  }
  if (f.draft > 0) {
    const extra: string[] = [];
    if (n(f.aiDrafts) > 0) extra.push(`${n(f.aiDrafts)} written by AI`);
    if (n(f.unverifiedDrafts) > 0)
      extra.push(`${n(f.unverifiedDrafts)} with tests nobody has run`);
    if (n(f.sentBack) > 0) extra.push(`${n(f.sentBack)} sent back by a reviewer`);
    return {
      tone: 'info',
      title: `${plural(f.draft, 'draft')} not yet submitted`,
      context:
        extra.length > 0
          ? `${extra.join(', ')}. Read them, then send them for review.`
          : 'Read them, then send them for review.',
      stage: 'check',
      actionLabel: `Check ${plural(f.draft, 'draft')}`,
    };
  }
  if (f.inReview > 0) {
    return {
      tone: 'warn',
      title: `${f.inReview} in review`,
      context: 'Another reviewer has to approve or send them back. You cannot approve your own.',
      stage: 'send',
      actionLabel: 'See what is waiting',
    };
  }
  if (f.approved > 0) {
    return {
      tone: 'ok',
      title: `${f.approved} approved and ready to use`,
      context: 'Add them to an exam from its question section: Add from bank.',
      stage: 'approved',
      actionLabel: 'See approved questions',
    };
  }
  return {
    tone: 'default',
    title: 'Every question is retired',
    context: 'Add new questions to use this bank again.',
    stage: 'add',
    actionLabel: 'Add questions',
  };
}

/* ── The list page ──────────────────────────────────────────────────────── */

export interface BankNextAction {
  /** "3 drafts to submit". The arrow is drawn by the component. */
  label: string;
  /** The stage of the bank page the link opens. */
  stage: BankStageKey;
  /** `act` is something to do; `wait` is someone else's move; `ready` is finished. */
  kind: 'act' | 'wait' | 'ready';
}

/** The single next action for a bank, from its status counts alone. */
export function bankNextAction(bank: BankOut): BankNextAction {
  const f = bankFacts(bank);
  if (f.total === 0) return { label: 'Add the first questions', stage: 'add', kind: 'act' };
  if (f.draft > 0) {
    return { label: `${plural(f.draft, 'draft')} to submit`, stage: 'check', kind: 'act' };
  }
  if (f.inReview > 0) return { label: 'Waiting on review', stage: 'send', kind: 'wait' };
  if (f.approved > 0) return { label: 'Ready to use in exams', stage: 'approved', kind: 'ready' };
  return { label: 'Add new questions', stage: 'add', kind: 'act' };
}

export type BankListFilter = 'all' | 'drafts' | 'review' | 'ready' | 'empty';

export const BANK_LIST_FILTERS: { value: BankListFilter; label: string }[] = [
  { value: 'all', label: 'All banks' },
  { value: 'drafts', label: 'Has drafts' },
  { value: 'review', label: 'Waiting on review' },
  { value: 'ready', label: 'Ready to use' },
  { value: 'empty', label: 'Empty' },
];

export function matchesBankFilter(bank: BankOut, filter: BankListFilter): boolean {
  const f = bankFacts(bank);
  switch (filter) {
    case 'all':
      return true;
    case 'drafts':
      return f.draft > 0;
    case 'review':
      return f.inReview > 0;
    case 'ready':
      return f.approved > 0;
    case 'empty':
      return f.total === 0;
  }
}

/** Question-kind filter for the bank list. Only offered when a bank carries `kind_counts`. */
export type BankKindFilter = 'all' | 'mcq' | 'coding';

export const BANK_KIND_FILTERS: { value: BankKindFilter; label: string }[] = [
  { value: 'all', label: 'All question types' },
  { value: 'mcq', label: 'Has MCQ' },
  { value: 'coding', label: 'Has coding' },
];

/** True when at least one bank reports its MCQ / coding split. */
export function anyKindCounts(banks: readonly BankOut[]): boolean {
  return banks.some((b) => b.kind_counts !== undefined);
}

/** A bank that did not report its split cannot be said to have either kind, so it does not match. */
export function matchesKindFilter(bank: BankOut, filter: BankKindFilter): boolean {
  if (filter === 'all') return true;
  if (!bank.kind_counts) return false;
  return filter === 'mcq' ? bank.kind_counts.mcq > 0 : bank.kind_counts.coding > 0;
}

/** "12 MCQ · 3 coding", or null when the server did not send the split. */
export function kindSummary(bank: BankOut): string | null {
  const k = bank.kind_counts;
  return k ? `${k.mcq} MCQ · ${k.coding} coding` : null;
}

/** Status counts in the order a bank moves through them, zero counts left out. */
export function statusCounts(
  bank: BankOut,
): { status: BankQuestionStatus; count: number; label: string }[] {
  const order: BankQuestionStatus[] = ['draft', 'in_review', 'approved', 'retired'];
  return order
    .map((status) => ({
      status,
      count: n(bank.counts[status]),
      label: `${n(bank.counts[status])} ${status.replace('_', ' ')}`,
    }))
    .filter((s) => s.count > 0);
}

/** The first non-empty line of a prompt, shortened for a list row. */
export function questionBrief(prompt: string, max = 110): string {
  const first = prompt.split(/\r?\n/).find((l) => l.trim().length > 0) ?? '';
  const t = first.trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}
