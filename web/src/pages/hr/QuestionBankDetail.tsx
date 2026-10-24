// QuestionBankDetail (/hr/question-banks/:bankId, and /new) - one bank, as a
// guided flow instead of a table with three panels above it.
//
//   1 Set up the bank      name, what it assesses
//   2 Add questions        write / AI MCQs / AI coding / import
//   3 Check your drafts    master-detail over the drafts
//   4 Send for review      submit them; see what is waiting
//   5 Approved and in use  what came back, what was sent back, where it is used
//
// Every stage's state is computed by lib/bankFlow.ts from what the server holds
// (the bank's status counts and its question list). The page's one next step is
// the NextStep strip under the header; the stage the person has OPEN is theirs:
// it is carried in `?stage=` and a refetch never swaps the panel under them.
//
// `/hr/question-banks/new` is the same rail with only stage 1 open. Creating the
// bank lands on stage 2 of the same page - one flow, not a wizard and a page.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Breadcrumbs, EmptyState, Page, PageHeader } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { JourneyRail } from '@/design/components/journey-rail';
import { NextStep } from '@/design/components/next-step';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { QueryErrorPanel } from '@/components/QueryState';
import { useUnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { BankAddPanel } from '@/components/hr/bank/BankAddPanel';
import { BankSetupPanel } from '@/components/hr/bank/BankSetupPanel';
import { ApprovedPanel, CheckPanel, SendPanel } from '@/components/hr/bank/BankReviewPanels';
import { StageFrame } from '@/components/hr/bank/StageFrame';
import {
  BANK_STATE_LABELS,
  bankFacts,
  bankStages,
  canOpenStage,
  defaultStageKey,
  isSentBack,
  newBankStages,
  nextStatement,
  parseStage,
  type BankStageKey,
} from '@/lib/bankFlow';
import { listBankQuestions, listQuestionBanks, type BankQuestion } from '@/api/questionBanks';

const NEW = 'new';

const STAGE_BLURB: Record<BankStageKey, string> = {
  setup: 'Name the bank and say what it assesses, so a colleague can tell it from the others.',
  add: 'Choose how to add questions. Every method saves drafts; none of them approves anything.',
  check:
    'Read each draft and fix or delete what is wrong. Questions written by AI need a human read before they go for review.',
  send: 'Send the drafts to someone else to approve, and see what is already waiting.',
  approved:
    'Approved questions can be picked into an exam. Anything sent back is listed with the reviewer’s note.',
};

export default function QuestionBankDetail(): JSX.Element {
  const { bankId = '' } = useParams();
  // Keyed by bank, so the stage, selection and pending edits of one bank never
  // carry over to the next - including from /new to the bank it just created.
  return <BankFlowPage key={bankId} bankId={bankId} />;
}

function BankFlowPage({ bankId }: { bankId: string }): JSX.Element {
  const isNew = bankId === NEW;
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const guard = useUnsavedGuard();

  const banksQuery = useQuery({
    queryKey: ['hr', 'question-banks'],
    queryFn: listQuestionBanks,
    enabled: !isNew,
  });
  const bank = isNew ? undefined : (banksQuery.data ?? []).find((b) => b.id === bankId);

  const listQuery = useQuery({
    queryKey: ['hr', 'question-banks', bankId, 'questions'],
    queryFn: () => listBankQuestions(bankId),
    enabled: !isNew && Boolean(bankId) && Boolean(bank),
  });
  const questions: readonly BankQuestion[] = useMemo(() => listQuery.data ?? [], [listQuery.data]);
  const listReady = listQuery.isSuccess;

  const facts = useMemo(
    () => (bank ? bankFacts(bank, listQuery.data) : null),
    [bank, listQuery.data],
  );
  const stages = useMemo(
    () => (isNew ? newBankStages() : facts ? bankStages(facts) : null),
    [isNew, facts],
  );

  // ── Which stage is open ────────────────────────────────────────────────
  // `?stage=` wins. Without it the bank opens on its real next stage, decided
  // ONCE, when its counts first arrive - so a refetch (or a submit that changes
  // the counts) can never swap the panel the person is working in.
  const urlStage = parseStage(params.get('stage'));
  const [firstStage, setFirstStage] = useState<BankStageKey | null>(null);
  useEffect(() => {
    if (firstStage === null && stages && !isNew) setFirstStage(defaultStageKey(stages));
  }, [stages, firstStage, isNew]);

  const wanted = urlStage ?? firstStage;
  const active: BankStageKey | null = isNew
    ? 'setup'
    : wanted && canOpenStage(Boolean(bank), wanted)
      ? wanted
      : null;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const moved = useRef(false);

  function goTo(key: BankStageKey, questionId: string | null = null): void {
    if (isNew || !canOpenStage(Boolean(bank), key)) return;
    if (key === active && questionId === null) return;
    if (!guard.confirmLeave()) return;
    moved.current = true;
    setSelectedId(questionId);
    // REPLACE, not push: a stage change is a view inside this page, not a
    // place. Pushing one entry per stage made the browser Back button step
    // through stages - and out of a dirty editor - without ever asking.
    setParams({ stage: key }, { replace: true });
  }

  // ── Loading and missing states ─────────────────────────────────────────
  if (!isNew && banksQuery.isLoading) {
    return (
      <Page width="default">
        <div className="flex flex-col gap-4" role="status" aria-label="Loading this question bank">
          <Skeleton className="h-10 w-72" />
          <Skeleton className="h-20 w-full rounded-card" />
          <Skeleton className="h-72 w-full rounded-card" />
        </div>
      </Page>
    );
  }
  if (!isNew && banksQuery.isError) {
    return (
      <Page width="default">
        <QueryErrorPanel
          title="Could not load this question bank."
          error={banksQuery.error}
          onRetry={() => void banksQuery.refetch()}
          retrying={banksQuery.isFetching}
        />
      </Page>
    );
  }
  if (!isNew && !bank) {
    return (
      <Page width="default">
        <EmptyState
          size="page"
          title="This bank was not found"
          description="It may have been archived, or the link is wrong."
          action={
            <Button asChild variant="outline">
              <Link to="/hr/question-banks">Back to question banks</Link>
            </Button>
          }
        />
      </Page>
    );
  }

  const title = bank?.name ?? 'New question bank';
  const statement = facts ? nextStatement(facts) : null;
  const drafts = questions.filter((q) => q.status === 'draft');
  const inReview = questions.filter((q) => q.status === 'in_review');
  const decided = questions.filter((q) => q.status === 'approved' || q.status === 'retired');
  const sentBack = drafts.filter(isSentBack);

  const browser = {
    allQuestions: questions,
    guard,
    selectedId,
    onSelect: setSelectedId,
  };

  const listState = listQuery.isError ? (
    <QueryErrorPanel
      title="Could not load this bank's questions."
      error={listQuery.error}
      onRetry={() => void listQuery.refetch()}
      retrying={listQuery.isFetching}
    />
  ) : (
    <div className="flex flex-col gap-3" role="status" aria-label="Loading questions">
      <Skeleton className="h-16 w-full rounded-card" />
      <Skeleton className="h-40 w-full rounded-card" />
    </div>
  );

  let panel: JSX.Element | null = null;
  if (active === 'setup') {
    panel = (
      <StageFrame
        key="setup"
        title="Set up the bank"
        description={STAGE_BLURB.setup}
        focusOnMount={moved.current}
      >
        <Card>
          <BankSetupPanel
            bank={bank}
            guard={guard}
            onCreated={(created) => {
              void navigate(`/hr/question-banks/${created.id}?stage=add`, { replace: true });
            }}
          />
        </Card>
      </StageFrame>
    );
  } else if (active === 'add' && facts) {
    panel = (
      <StageFrame
        key="add"
        title="Add questions"
        description={STAGE_BLURB.add}
        focusOnMount={moved.current}
      >
        <BankAddPanel
          bankId={bankId}
          facts={facts}
          factsReady={listReady}
          guard={guard}
          onCheckDrafts={() => goTo('check')}
        />
      </StageFrame>
    );
  } else if (active === 'check' && facts) {
    panel = (
      <StageFrame
        key="check"
        title="Check your drafts"
        description={STAGE_BLURB.check}
        focusOnMount={moved.current}
      >
        {listReady ? (
          <CheckPanel drafts={drafts} facts={facts} onContinue={() => goTo('send')} {...browser} />
        ) : (
          listState
        )}
      </StageFrame>
    );
  } else if (active === 'send' && facts) {
    panel = (
      <StageFrame
        key="send"
        title="Send for review"
        description={STAGE_BLURB.send}
        focusOnMount={moved.current}
      >
        {listReady ? (
          <SendPanel
            bankId={bankId}
            drafts={drafts}
            inReview={inReview}
            facts={facts}
            {...browser}
          />
        ) : (
          listState
        )}
      </StageFrame>
    );
  } else if (active === 'approved' && facts) {
    panel = (
      <StageFrame
        key="approved"
        title="Approved and in use"
        description={STAGE_BLURB.approved}
        focusOnMount={moved.current}
      >
        {listReady ? (
          <ApprovedPanel
            approved={decided}
            sentBack={sentBack}
            facts={facts}
            onOpenSentBack={(id) => goTo('check', id)}
            {...browser}
          />
        ) : (
          listState
        )}
      </StageFrame>
    );
  }

  const showAction = statement?.stage && statement.stage !== active;

  return (
    <Page width="default">
      <PageHeader
        compact
        breadcrumbs={
          <Breadcrumbs
            items={[{ label: 'Question banks', to: '/hr/question-banks' }, { label: title }]}
          />
        }
        title={title}
        description={
          isNew ? 'Create a bank, then fill it and send it for review.' : bank?.description
        }
        meta={
          bank ? (
            <span className="text-[13px] text-muted-foreground">
              Used in {bank.used_in_exams} exam{bank.used_in_exams === 1 ? '' : 's'}
            </span>
          ) : undefined
        }
        actions={
          <Button asChild variant="outline">
            <Link to="/hr/question-banks/reviews">Question reviews</Link>
          </Button>
        }
      />

      {statement && showAction ? (
        // Only when the person is somewhere else: on the next stage itself the
        // rail and the panel already say it, and a second strip is just height.
        <NextStep
          tone={statement.tone}
          title={statement.title}
          context={statement.context}
          data-testid="bank-next-step"
          action={
            statement.stage ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => goTo(statement.stage as BankStageKey)}
              >
                {statement.actionLabel}
              </Button>
            ) : undefined
          }
        />
      ) : null}

      {stages && active ? (
        <JourneyRail
          label="Bank flow"
          data-testid="bank-rail"
          stages={stages}
          activeKey={active}
          onSelect={(key) => goTo(key as BankStageKey)}
          stateLabels={BANK_STATE_LABELS}
        >
          {panel}
        </JourneyRail>
      ) : (
        <Skeleton className="h-72 w-full rounded-card" />
      )}
    </Page>
  );
}
