// The last three stages of the bank flow:
//
//   CheckPanel     read the drafts (master-detail), fix them, delete the bad ones
//   SendPanel      send the drafts for review; see what is already waiting
//   ApprovedPanel  what came back approved, what was sent back and why, and where
//                  approved questions are used
//
// None of these decides anything about a question's quality. Approval belongs to
// a person other than the author, in Question reviews; this page only sends the
// work there and shows what happened.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, CheckCircle2, Loader2, Send } from '@/design/components/icons';
import { Card } from '@/design/components/card';
import { Collapsible } from '@/design/components/collapsible';
import { EmptyState } from '@/design/components/page';
import { RowList, Row, Section, Well } from '@/design/components/rows';
import { Button } from '@/components/ui/button';
import type { UnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { BankQuestionBrowser } from '@/components/hr/bank/BankQuestionBrowser';
import { toast } from '@/lib/toast';
import { kindTally, questionBrief, type BankFacts } from '@/lib/bankFlow';
import {
  approveAllBankQuestions,
  submitAllBankQuestions,
  type BankQuestion,
  type BulkReviewResult,
} from '@/api/questionBanks';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function plural(count: number, one: string): string {
  return `${count} ${one}${count === 1 ? '' : 's'}`;
}

interface BrowserProps {
  allQuestions: readonly BankQuestion[];
  guard: UnsavedGuard;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

/* ── 3. Check your drafts ────────────────────────────────────────────────── */

export function CheckPanel({
  drafts,
  facts,
  onContinue,
  ...browser
}: BrowserProps & {
  drafts: readonly BankQuestion[];
  facts: BankFacts;
  onContinue: () => void;
}): JSX.Element {
  if (drafts.length === 0) {
    return (
      <Card>
        <EmptyState
          bordered={false}
          icon={CheckCircle2}
          title="No drafts left unsubmitted"
          description={
            facts.total === 0
              ? 'Add questions first; they arrive here as drafts.'
              : 'Everything in this bank has been sent for review or decided.'
          }
        />
      </Card>
    );
  }
  return (
    <Card>
      <div className="flex flex-col gap-4">
        <BankQuestionBrowser
          questions={drafts}
          allQuestions={browser.allQuestions}
          guard={browser.guard}
          selectedId={browser.selectedId}
          onSelect={browser.onSelect}
          listLabel="Draft questions"
          emptyTitle="No drafts"
        />
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="max-w-[560px] text-[13px] leading-relaxed text-muted-foreground">
            {plural(drafts.length, 'draft')} not yet submitted.
            {facts.aiDrafts
              ? ` ${facts.aiDrafts} written by AI need a human read before they go.`
              : ''}
            {facts.unverifiedDrafts
              ? ` ${facts.unverifiedDrafts} coding ${facts.unverifiedDrafts === 1 ? 'question has' : 'questions have'} tests nobody has run.`
              : ''}
          </p>
          <Button type="button" onClick={onContinue}>
            Continue to send for review
            <ArrowRight aria-hidden="true" />
          </Button>
        </div>
      </div>
    </Card>
  );
}

/* ── 4. Send for review ──────────────────────────────────────────────────── */

export function SendPanel({
  bankId,
  drafts,
  inReview,
  facts,
  ...browser
}: BrowserProps & {
  bankId: string;
  drafts: readonly BankQuestion[];
  inReview: readonly BankQuestion[];
  facts: BankFacts;
}): JSX.Element {
  const qc = useQueryClient();
  const [sent, setSent] = useState<BulkReviewResult | null>(null);
  const [approved, setApproved] = useState<BulkReviewResult | null>(null);

  const invalidate = (): void => {
    void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-questions'] });
    // The reviewer's queue is the one screen this action exists to populate.
    void qc.invalidateQueries({ queryKey: ['hr', 'bank-review-queue'] });
  };

  const submitMut = useMutation({
    mutationFn: () => submitAllBankQuestions(bankId),
    onMutate: () => setApproved(null),
    onSuccess: (result) => {
      setSent(result);
      if (result.acted > 0) {
        toast.success(`${result.acted} sent for review`);
        invalidate();
      } else {
        toast.warning('Nothing was sent for review');
      }
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not submit these for review')),
  });

  const approveMut = useMutation({
    mutationFn: () => approveAllBankQuestions(bankId),
    onMutate: () => setSent(null),
    onSuccess: (result) => {
      setApproved(result);
      if (result.acted > 0) {
        toast.success(`${result.acted} approved`);
        invalidate();
      } else {
        // The expected answer in a one-person company, so it is worded as the
        // rule rather than as a failure.
        toast.warning('Nothing could be approved by you — another reviewer must');
      }
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not approve these')),
  });

  const mcq = drafts.filter((d) => d.kind === 'mcq').length;
  const coding = drafts.length - mcq;
  const kinds = kindTally({ mcq, coding });
  const ai = drafts.filter((d) => d.origin === 'ai_draft').length;
  const unverified = facts.unverifiedDrafts ?? 0;
  const busy = submitMut.isPending || approveMut.isPending;

  return (
    <Card>
      <div className="flex flex-col gap-5">
        <Section
          title={
            drafts.length > 0
              ? `Send ${plural(drafts.length, 'draft')} for review`
              : 'Nothing to send'
          }
          titleAs="h3"
        >
          {drafts.length > 0 ? (
            <div className="flex flex-col gap-3">
              <Well data-testid="send-summary">
                <p className="text-[14px] font-semibold text-foreground">{kinds}</p>
                {ai > 0 ? (
                  <p className="mt-1 text-[13px] text-muted-foreground">
                    {ai} written by AI. A person should have read each one.
                  </p>
                ) : null}
                {unverified > 0 ? (
                  <p className="mt-1 text-[13px] text-muted-foreground">
                    {plural(unverified, 'coding question')} {unverified === 1 ? 'has' : 'have'}{' '}
                    tests nobody has run.
                  </p>
                ) : null}
              </Well>
              <p className="max-w-[640px] text-[13px] leading-relaxed text-muted-foreground">
                <span className="font-semibold text-foreground">Who can approve them.</span> Only a
                person who did not write or submit a question: another HR manager, or your
                company&apos;s super admin. You cannot approve your own, and importing or AI
                drafting is not a way round that. An approved question can then be picked into an
                exam.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button type="button" disabled={busy} onClick={() => submitMut.mutate()}>
                  {submitMut.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Send aria-hidden="true" />
                  )}
                  {submitMut.isPending
                    ? 'Sending…'
                    : `Send ${plural(drafts.length, 'draft')} for review`}
                </Button>
                <Link
                  to="/hr/question-banks/reviews"
                  className="inline-flex min-h-10 items-center rounded-[10px] text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  Open question reviews
                </Link>
              </div>
            </div>
          ) : (
            <p className="text-[13.5px] text-muted-foreground">
              {facts.total === 0
                ? 'This bank has no questions yet.'
                : 'No drafts are waiting. Everything has been sent or decided.'}
            </p>
          )}

          {sent ? (
            <Well role="status" data-testid="bank-bulk-result" className="mt-3">
              <p className="text-[13.5px] text-foreground">
                {sent.acted} submitted.
                {sent.skipped.length > 0 ? ` ${sent.skipped.length} not sent.` : ''}
              </p>
              {sent.skipped.length > 0 ? (
                <ul className="mt-1.5 flex flex-col gap-0.5">
                  {Array.from(new Set(sent.skipped.map((s) => s.reason))).map((reason) => (
                    <li key={reason} className="text-[12.5px] text-muted-foreground">
                      {reason} ({sent.skipped.filter((s) => s.reason === reason).length})
                    </li>
                  ))}
                </ul>
              ) : null}
            </Well>
          ) : null}
        </Section>

        <Section title="Waiting for review" titleAs="h3">
          {inReview.length === 0 ? (
            <p className="text-[13.5px] text-muted-foreground">
              Nothing in this bank is waiting on a reviewer.
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-[13px] text-muted-foreground">
                {plural(inReview.length, 'question')} waiting for someone else to approve or send
                back. Only the person who submitted one can withdraw it.
              </p>
              {drafts.length === 0 ? (
                // Nothing else competes for the page: show the questions outright.
                <BankQuestionBrowser
                  questions={inReview}
                  allQuestions={browser.allQuestions}
                  guard={browser.guard}
                  selectedId={browser.selectedId}
                  onSelect={browser.onSelect}
                  listLabel="Questions in review"
                  emptyTitle="Nothing in review"
                />
              ) : (
                <Collapsible title="See the questions" count={inReview.length} noun="question">
                  <BankQuestionBrowser
                    questions={inReview}
                    allQuestions={browser.allQuestions}
                    guard={browser.guard}
                    selectedId={browser.selectedId}
                    onSelect={browser.onSelect}
                    listLabel="Questions in review"
                    emptyTitle="Nothing in review"
                  />
                </Collapsible>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => approveMut.mutate()}
                >
                  {approveMut.isPending ? 'Approving…' : 'Approve all I am allowed to'}
                </Button>
                <span className="text-[12.5px] text-muted-foreground">
                  Skips anything you wrote or submitted.
                </span>
              </div>
              {approved ? (
                <Well role="status" data-testid="bank-approve-result">
                  <p className="text-[13.5px] text-foreground">
                    {approved.acted} approved.
                    {approved.skipped.length > 0
                      ? ` ${approved.skipped.length} left for someone else.`
                      : ''}
                  </p>
                  {approved.skipped.length > 0 ? (
                    <ul className="mt-1.5 flex flex-col gap-0.5">
                      {Array.from(new Set(approved.skipped.map((s) => s.reason))).map((reason) => (
                        <li key={reason} className="text-[12.5px] text-muted-foreground">
                          {reason} ({approved.skipped.filter((s) => s.reason === reason).length})
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {approved.acted === 0 ? (
                    <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">
                      Nothing was approved. Ask another HR manager, or your company&apos;s super
                      admin, to review these. That second pair of eyes is the point of the rule.
                    </p>
                  ) : null}
                </Well>
              ) : null}
            </div>
          )}
        </Section>
      </div>
    </Card>
  );
}

/* ── 5. Approved and in use ──────────────────────────────────────────────── */

export function ApprovedPanel({
  approved,
  sentBack,
  facts,
  onOpenSentBack,
  ...browser
}: BrowserProps & {
  approved: readonly BankQuestion[];
  /** Drafts a reviewer returned, with their note. */
  sentBack: readonly BankQuestion[];
  facts: BankFacts;
  onOpenSentBack: (id: string) => void;
}): JSX.Element {
  const live = approved.filter((q) => q.status === 'approved');
  const tally = kindTally({
    mcq: live.filter((q) => q.kind === 'mcq').length,
    coding: live.filter((q) => q.kind === 'coding').length,
  });
  return (
    <Card>
      <div className="flex flex-col gap-5">
        {sentBack.length > 0 ? (
          <Section
            title={`${sentBack.length} sent back for changes`}
            description="A reviewer returned these to drafts with a note. Fix them, then send them again."
            titleAs="h3"
          >
            <RowList aria-label="Questions sent back">
              {sentBack.map((q) => (
                <Row
                  key={q.id}
                  title={questionBrief(q.prompt, 90)}
                  meta={<>Reviewer: “{q.review_note}”</>}
                  trailing={
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => onOpenSentBack(q.id)}
                    >
                      Open question
                    </Button>
                  }
                />
              ))}
            </RowList>
          </Section>
        ) : null}

        <Section
          title={
            live.length > 0
              ? `${live.length} approved${tally ? ` · ${tally}` : ''}`
              : 'Nothing approved yet'
          }
          titleAs="h3"
          description={
            facts.retired > 0
              ? `${facts.retired} more retired. Used in ${plural(facts.usedInExams, 'exam')}.`
              : `Used in ${plural(facts.usedInExams, 'exam')}.`
          }
        >
          {approved.length > 0 ? (
            <BankQuestionBrowser
              questions={approved}
              allQuestions={browser.allQuestions}
              guard={browser.guard}
              selectedId={browser.selectedId}
              onSelect={browser.onSelect}
              listLabel="Approved questions"
              statuses={['approved', 'retired']}
              emptyTitle="Nothing approved"
            />
          ) : (
            <EmptyState
              bordered={false}
              title="Nothing is approved yet"
              description="Once another person approves what you sent, it appears here and can be used in an exam."
            />
          )}
        </Section>

        {live.length > 0 ? (
          <Well
            className="flex flex-wrap items-center justify-between gap-3"
            data-testid="use-in-exams"
          >
            <p className="max-w-[560px] text-[13.5px] leading-relaxed text-foreground">
              Approved questions can now be picked into an exam: open an exam, go to a question
              section and choose <span className="font-semibold">Add from bank</span>. A published
              round keeps its own copy.
            </p>
            <Button asChild variant="outline">
              <Link to="/hr/exams">
                Go to exams
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
          </Well>
        ) : null}
      </div>
    </Card>
  );
}
