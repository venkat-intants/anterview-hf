// ExamAttemptRedirect (/hr/exams/attempts/:attemptId) — PH5-E1. Lands an exam
// ATTEMPT citation on the attempt.
//
// WHY A RESOLVER AND NOT A PAGE. The screen that shows one attempt already
// exists — ExamAttemptDetail, at `/hr/exams/:examId/attempts/:attemptId` — and
// every API behind it is scoped by the exam: `GET /hr/exams/{exam_id}/attempts`
// and `…/attempts/{aid}/breakdown`. But `CITATION_ROUTES.exam_attempt` is
// `/hr/exams/attempts/{id}`: one id, no exam. Nothing in `data_gateway` resolves
// a bare attempt id (there is no `GET /hr/exams/attempts/{id}`), and the
// citation table cannot be repointed from this side — it is pinned to
// `shared/agents/schema.py` by a parity test. So the exam is found here, from
// the attempt lists the console can already read, and the browser is sent on to
// the real screen.
//
// THE COST, STATED. This asks every exam in the company for its attempts —
// 1 + N requests, all cached under the same keys ExamResults and
// ExamAttemptDetail use, so the page it redirects to is already warm and a
// second citation for the same exam costs nothing. It is a rescue path for a
// followed link, not something a console renders in normal use. The honest fix
// is a backend lookup (`GET /hr/exams/attempts/{attempt_id}` → the attempt's
// exam id, one request); until that exists this is the whole of it, and it is
// bounded by the company's exam count rather than by its applicants.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { Link, Navigate, useParams } from 'react-router-dom';
import { useQueries, useQuery } from '@tanstack/react-query';
import { listAttempts, listExams } from '@/api/exams';
import { GlassCard } from '@/design/components/primitives';
import { ArrowLeft, Loader2 } from '@/design/components/icons';

const NOT_FOUND_TEXT =
  'That exam attempt is not in this company’s exams. It may have been deleted, or it ' +
  'belongs to an exam you cannot see.';
const LOAD_FAILED_TEXT = 'Your exams could not be loaded, so that attempt could not be opened.';

export default function ExamAttemptRedirect(): JSX.Element {
  const { attemptId = '' } = useParams<{ attemptId: string }>();

  // Same key as Exams.tsx, so an HR manager who has just been on that screen
  // pays nothing for this one.
  const exams = useQuery({
    queryKey: ['hr', 'exams'],
    queryFn: () => listExams(),
    retry: false,
  });

  const examList = exams.data ?? [];
  // Same keys as ExamResults/ExamAttemptDetail — this fills their cache rather
  // than a private copy of it.
  const attemptLists = useQueries({
    queries: examList.map((exam) => ({
      queryKey: ['hr', 'exam', exam.id, 'attempts'],
      queryFn: () => listAttempts(exam.id),
      retry: false,
    })),
  });

  const matchIndex = attemptLists.findIndex((q) => q.data?.some((a) => a.attempt_id === attemptId));
  const examId = matchIndex >= 0 ? examList[matchIndex]?.id : undefined;

  if (examId) {
    // `replace`: Back should return to whatever linked here, not to this
    // resolver, which would immediately redirect again.
    return <Navigate to={`/hr/exams/${examId}/attempts/${attemptId}`} replace />;
  }

  // Still looking. One exam list that has not answered yet is enough — the
  // attempt may be in it.
  const searching = exams.isLoading || attemptLists.some((q) => q.isLoading || q.isFetching);

  return (
    <div className="mx-auto max-w-[720px] px-6 py-8 lg:px-8">
      <Link
        to="/hr/exams"
        className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Back to exams
      </Link>

      <GlassCard className="mt-4 p-6">
        {searching ? (
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
              {exams.isError ? LOAD_FAILED_TEXT : NOT_FOUND_TEXT}
            </p>
          </>
        )}
      </GlassCard>
    </div>
  );
}
