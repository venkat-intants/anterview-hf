// ExamAttemptRedirect (/hr/exams/attempts/:attemptId) — PH5-E1. Lands an exam
// ATTEMPT citation on the attempt.
//
// WHY A RESOLVER AND NOT A PAGE. The screen that shows one attempt already
// exists — ExamAttemptDetail, at `/hr/exams/:examId/attempts/:attemptId` — and
// every API behind it is scoped by the exam. But `CITATION_ROUTES.exam_attempt`
// is `/hr/exams/attempts/{id}`: one id, no exam, and the citation table cannot
// be repointed from this side (it is pinned to `shared/agents/schema.py` by a
// parity test). So the exam is looked up here and the browser is sent on.
//
// ONE REQUEST (audit E25). This used to ask every exam in the company for its
// attempts (1 + N requests), and any one of those failing read as "not found".
// `GET /hr/exam-attempts/{id}` now answers with the attempt's exam id, scoped to
// the caller's company; a 404 means not found, anything else means we could
// not look.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { Link, Navigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import { locateAttempt } from '@/api/exams';
import { GlassCard } from '@/design/components/primitives';
import { ArrowLeft, Loader2 } from '@/design/components/icons';
import { Page } from '@/design/components/page';
import { Button } from '@/components/ui/button';

const NOT_FOUND_TEXT =
  'That exam attempt is not in this company’s exams. It may have been deleted, or it ' +
  'belongs to an exam you cannot see.';
const LOAD_FAILED_TEXT = 'The attempt could not be looked up just now, so it could not be opened.';

export default function ExamAttemptRedirect(): JSX.Element {
  const { attemptId = '' } = useParams<{ attemptId: string }>();

  const lookup = useQuery({
    queryKey: ['hr', 'exam-attempt-location', attemptId],
    queryFn: () => locateAttempt(attemptId),
    enabled: Boolean(attemptId),
    retry: false,
  });

  if (lookup.data) {
    // `replace`: Back should return to whatever linked here, not to this
    // resolver, which would immediately redirect again.
    return (
      <Navigate to={`/hr/exams/${lookup.data.exam_id}/attempts/${attemptId}`} replace />
    );
  }

  const notFound = lookup.error instanceof ApiError && lookup.error.status === 404;

  return (
    <Page width="form">
      <Link
        to="/hr/exams"
        className="inline-flex min-h-10 self-start items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Back to exams
      </Link>

      <GlassCard className="p-6">
        {lookup.isPending && attemptId ? (
          <p
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 text-[13px] text-muted-foreground"
          >
            <Loader2 size={15} className="animate-spin" aria-hidden="true" />
            Finding that exam attempt…
          </p>
        ) : (
          <>
            <h1 className="text-[18px] font-semibold text-foreground">
              We could not open that attempt
            </h1>
            <p className="mt-1.5 text-[13px] text-muted-foreground">
              {notFound || !attemptId ? NOT_FOUND_TEXT : LOAD_FAILED_TEXT}
            </p>
            {!notFound && attemptId ? (
              <Button
                type="button"
                variant="outline"
                className="mt-3"
                disabled={lookup.isFetching}
                onClick={() => void lookup.refetch()}
              >
                Try again
              </Button>
            ) : null}
          </>
        )}
      </GlassCard>
    </Page>
  );
}
