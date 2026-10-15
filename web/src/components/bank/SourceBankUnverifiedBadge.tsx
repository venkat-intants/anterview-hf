// SourceBankUnverifiedBadge - shows "Unverified" on an EXAM coding question that
// was added from the question bank, when that bank question still carries the
// server's unverified marker.
//
// Exam coding questions have no tags of their own; each carries only the id of
// the bank question it came from. The tag lives on that bank question, so it is
// looked up - once per id (long staleTime, never polled), and only when the row
// is on screen. If the lookup fails the badge is simply absent: this is
// information, and must never block the editor.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useQuery } from '@tanstack/react-query';
import { UnverifiedBadge } from '@/components/bank/QuestionBadges';
import { useInView } from '@/hooks/useInView';
import { getBankQuestion, isReferenceUnverified } from '@/api/questionBanks';

/** A bank question's tags change rarely, and only through an edit that
 *  invalidates this key. */
const STALE_MS = 60 * 60 * 1000;

export function SourceBankUnverifiedBadge({
  bankQuestionId,
}: {
  bankQuestionId: string | null | undefined;
}): JSX.Element | null {
  const [ref, seen] = useInView<HTMLSpanElement>();
  const lookup = useQuery({
    // The same key the bank's own detail pane uses, so they share one entry.
    queryKey: ['hr', 'bank-questions', bankQuestionId],
    queryFn: () => getBankQuestion(bankQuestionId ?? ''),
    enabled: Boolean(bankQuestionId) && seen,
    staleTime: STALE_MS,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (!bankQuestionId) return null;
  const unverified = lookup.data ? isReferenceUnverified(lookup.data.question) : false;
  return (
    <span ref={ref} className="inline-flex">
      {unverified ? <UnverifiedBadge testId="exam-unverified-badge" /> : null}
    </span>
  );
}

export default SourceBankUnverifiedBadge;
