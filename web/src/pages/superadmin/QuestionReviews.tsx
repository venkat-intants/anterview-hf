// QuestionReviews (/superadmin/question-reviews) — PH4-D1. The company super
// admin's mirror of HR's bank-question review queue, so a single-HR company
// is never blocked on a second HR approver. Same rules, `/admin` endpoints —
// see components/bank/QuestionReviewQueue.tsx for the shared body, and
// pages/hr/QuestionReviews.tsx for the HR side. Mirrors WorkflowReviews.tsx.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { Reveal } from '@/design/components/Reveal';
import { QuestionReviewQueue } from '@/components/bank/QuestionReviewQueue';
import {
  approveBankQuestionAdmin,
  listAdminQuestionReviews,
  requestBankQuestionChangesAdmin,
} from '@/api/questionBanks';

export default function QuestionReviews(): JSX.Element {
  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8">
      <Reveal>
        <h1 className="text-[22px] font-semibold text-foreground">Question reviews</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Bank questions your HR managers have submitted for review. Oldest submission first.
        </p>
      </Reveal>

      <QuestionReviewQueue
        queryKey={['admin', 'bank-review-queue']}
        fetchQueue={listAdminQuestionReviews}
        approve={approveBankQuestionAdmin}
        requestChanges={requestBankQuestionChangesAdmin}
        emptyHint="A question your HR managers submit for review appears here."
      />
    </div>
  );
}
