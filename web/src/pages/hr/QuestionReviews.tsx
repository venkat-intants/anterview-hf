// QuestionReviews (/hr/question-banks/reviews) — PH4-D1. The HR review queue
// for bank questions submitted for review. See
// components/bank/QuestionReviewQueue.tsx for the shared body — the super
// admin's mirror is pages/superadmin/QuestionReviews.tsx.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { Reveal } from '@/design/components/Reveal';
import { QuestionReviewQueue } from '@/components/bank/QuestionReviewQueue';
import {
  approveBankQuestion,
  listBankReviewQueue,
  requestBankQuestionChanges,
} from '@/api/questionBanks';

export default function QuestionReviews(): JSX.Element {
  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8">
      <Reveal>
        <h1 className="text-[22px] font-semibold text-foreground">Question reviews</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Bank questions submitted for review — approved by anyone other than whoever wrote or
          submitted them. Oldest submission first.
        </p>
      </Reveal>

      <QuestionReviewQueue
        queryKey={['hr', 'bank-review-queue']}
        fetchQueue={listBankReviewQueue}
        approve={approveBankQuestion}
        requestChanges={requestBankQuestionChanges}
        emptyHint="A question your colleagues submit appears here."
      />
    </div>
  );
}
