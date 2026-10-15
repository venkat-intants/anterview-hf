// QuestionReviews (/hr/question-banks/reviews) — PH4-D1. The HR review queue
// for bank questions submitted for review. See
// components/bank/QuestionReviewQueue.tsx for the shared body — the super
// admin's mirror is pages/superadmin/QuestionReviews.tsx.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { PageHeader, Breadcrumbs, Page } from '@/design/components/page';
import { QuestionReviewQueue } from '@/components/bank/QuestionReviewQueue';
import {
  approveBankQuestion,
  listBankReviewQueue,
  requestBankQuestionChanges,
} from '@/api/questionBanks';

export default function QuestionReviews(): JSX.Element {
  return (
    <Page width="form">
      <PageHeader
        breadcrumbs={
          <Breadcrumbs
            items={[{ label: 'Question banks', to: '/hr/question-banks' }, { label: 'Reviews' }]}
          />
        }
        title="Question reviews"
        description="Bank questions submitted for review — approved by anyone other than whoever wrote or submitted them. Oldest submission first."
      />

      <QuestionReviewQueue
        queryKey={['hr', 'bank-review-queue']}
        fetchQueue={listBankReviewQueue}
        approve={approveBankQuestion}
        requestChanges={requestBankQuestionChanges}
        emptyHint="A question your colleagues submit appears here."
      />
    </Page>
  );
}
