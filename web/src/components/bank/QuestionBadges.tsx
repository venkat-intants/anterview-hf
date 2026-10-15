// QuestionBadges - the small facts a person needs about a bank question before
// they trust it: what kind it is, whether an AI wrote it, whether a reviewer
// sent it back, and whether the solution to a coding question has ever been run.
//
// One component, used by the drafts list, the review queue and the exam picker,
// so the same fact always has the same words and the same icon. State is an icon
// AND a word, never colour alone.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import {
  AlertTriangle,
  ClipboardList,
  Code2,
  RefreshCw,
  Sparkles,
} from '@/design/components/icons';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { isReferenceUnverified, UNVERIFIED_EXPLANATION, type BankQuestion } from '@/api/questionBanks';

type BadgeSource = Pick<
  BankQuestion,
  'kind' | 'origin' | 'status' | 'review_note' | 'reference_solution' | 'tags'
>;

interface Props {
  question: BadgeSource;
  /** Leave out the kind badge where the row's icon already says it. */
  showKind?: boolean;
  /** Leave out "Unverified" where the coding view beside it already shows it. */
  showUnverified?: boolean;
  className?: string;
}

/** The one "Unverified" badge, with the one explanation, wherever it appears. */
export function UnverifiedBadge({ testId }: { testId: string }): JSX.Element {
  return (
    <Badge variant="warning" className="gap-1" data-testid={testId} title={UNVERIFIED_EXPLANATION}>
      <AlertTriangle size={12} aria-hidden="true" />
      Unverified
    </Badge>
  );
}

export function QuestionBadges({
  question,
  showKind = true,
  showUnverified = true,
  className,
}: Props): JSX.Element {
  const unverified = showUnverified && question.kind === 'coding' && isReferenceUnverified(question);
  const sentBack = question.status === 'draft' && Boolean(question.review_note?.trim());
  return (
    <ul
      className={cn('flex flex-wrap items-center gap-1.5', className)}
      aria-label="Question facts"
    >
      {showKind ? (
        <li>
          <Badge variant="outline" className="gap-1">
            {question.kind === 'coding' ? (
              <Code2 size={12} aria-hidden="true" />
            ) : (
              <ClipboardList size={12} aria-hidden="true" />
            )}
            {question.kind === 'coding' ? 'Coding' : 'MCQ'}
          </Badge>
        </li>
      ) : null}
      {question.origin === 'ai_draft' ? (
        <li>
          <Badge variant="accent" className="gap-1" data-testid="ai-draft-badge">
            <Sparkles size={12} aria-hidden="true" />
            AI draft
          </Badge>
        </li>
      ) : null}
      {sentBack ? (
        <li>
          <Badge variant="warning" className="gap-1" data-testid="sent-back-badge">
            <RefreshCw size={12} aria-hidden="true" />
            Sent back
          </Badge>
        </li>
      ) : null}
      {unverified ? (
        <li>
          <UnverifiedBadge testId="row-unverified-badge" />
        </li>
      ) : null}
    </ul>
  );
}

export default QuestionBadges;
