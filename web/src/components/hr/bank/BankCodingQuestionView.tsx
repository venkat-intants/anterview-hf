// BankCodingQuestionView — a read-only preview of one coding question.
//
// One component for both places a coding question is read before a decision:
// an AI draft HR has not saved yet, and a saved bank question on its detail
// page or in the review queue. It takes the structural subset the two share, so
// neither caller has to convert.
//
// WHAT A REVIEWER MUST BE ABLE TO SEE AT A GLANCE
//   - which test cases the candidate is shown and which are hidden (a word and
//     an icon each, never colour alone);
//   - whether anybody has run the reference solution. For an AI draft nobody
//     has, and the badge and the note under the solution both say so.
//
// It draws no box of its own: put it inside a Card, a Section or a dialog.
//
// English-only by design (CLAUDE.md — staff consoles are not translated). The
// STATEMENT is candidate-facing and is shown in whatever language it was
// written in (EN / HI / TE), untouched.

import { AlertTriangle, Eye, Lock } from '@/design/components/icons';
import { Well } from '@/design/components/rows';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import { codingLanguageLabel } from '@/lib/codingLanguages';
import { isReferenceUnverified, UNVERIFIED_EXPLANATION } from '@/api/questionBanks';
import type { CodingTestCase } from '@/api/exams';

/** What this view reads. A `BankQuestion` and a `GeneratedBankCodingQuestion`
 *  both satisfy it as they are. */
export interface CodingQuestionPreview {
  prompt: string;
  allowed_languages: string[] | null;
  starter_code: string | null;
  reference_solution: string | null;
  test_cases: CodingTestCase[] | null;
  time_limit_ms: number | null;
  points?: number;
  tags?: string[] | null;
  /** Only an unsaved AI draft knows this; a saved question does not store it. */
  reference_solution_language?: string | null;
}

export interface BankCodingQuestionViewProps {
  question: CodingQuestionPreview;
  /** Heading level for the view's own group titles. Defaults to `h4`. */
  headingAs?: 'h3' | 'h4' | 'h5';
  className?: string;
  'data-testid'?: string;
}

// Past this a test input is noise in a preview; the full text is in the editor.
const MAX_SHOWN_CHARS = 1200;

const GROUP_TITLE = 'text-[13px] font-semibold text-foreground';
const CODE =
  'whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed text-foreground';

function clip(text: string): { shown: string; hidden: number } {
  if (text.length <= MAX_SHOWN_CHARS) return { shown: text, hidden: 0 };
  return { shown: text.slice(0, MAX_SHOWN_CHARS), hidden: text.length - MAX_SHOWN_CHARS };
}

function CodeBlock({ label, text }: { label: string; text: string }): JSX.Element {
  const { shown, hidden } = clip(text);
  return (
    <div className="min-w-0">
      <p className="mb-1 text-[12px] font-medium text-muted-foreground">{label}</p>
      <Well className="p-2.5">
        {text === '' ? (
          <p className="text-[12.5px] italic text-muted-foreground">(empty)</p>
        ) : (
          <pre className={CODE}>{shown}</pre>
        )}
        {hidden > 0 ? (
          <p className="mt-1 text-[12px] text-muted-foreground">
            … {hidden.toLocaleString('en-IN')} more characters not shown
          </p>
        ) : null}
      </Well>
    </div>
  );
}

export function BankCodingQuestionView({
  question,
  headingAs: Heading = 'h4',
  className,
  'data-testid': testId = 'bank-coding-question-view',
}: BankCodingQuestionViewProps): JSX.Element {
  const tests = question.test_cases ?? [];
  const samples = tests.filter((t) => t.is_sample);
  const hiddenCount = tests.length - samples.length;
  const languages = question.allowed_languages ?? [];
  const unverified = isReferenceUnverified(question);
  const referenceLabel = question.reference_solution_language
    ? `Reference solution (${codingLanguageLabel(question.reference_solution_language)})`
    : 'Reference solution';

  return (
    <div className={cn('flex flex-col gap-5', className)} data-testid={testId}>
      {/* ── Facts ─────────────────────────────────────────────────────── */}
      <ul className="flex flex-wrap items-center gap-2" aria-label="Question facts">
        {languages.map((slug) => (
          <li key={slug}>
            <Badge variant="outline">{codingLanguageLabel(slug)}</Badge>
          </li>
        ))}
        {question.time_limit_ms ? (
          <li>
            <Badge variant="secondary">
              {(question.time_limit_ms / 1000).toLocaleString('en-IN')} s time limit
            </Badge>
          </li>
        ) : null}
        {question.points !== undefined ? (
          <li>
            <Badge variant="secondary">{question.points} points</Badge>
          </li>
        ) : null}
        {unverified ? (
          <li>
            <Badge
              variant="warning"
              className="gap-1"
              data-testid="unverified-badge"
              title={UNVERIFIED_EXPLANATION}
            >
              <AlertTriangle size={12} aria-hidden="true" />
              Unverified
            </Badge>
          </li>
        ) : null}
      </ul>

      {/* ── Statement (candidate-facing; shown as written) ─────────────── */}
      <section>
        <Heading className={GROUP_TITLE}>Problem statement</Heading>
        <p className="mt-1.5 whitespace-pre-wrap break-words text-[14px] leading-relaxed text-foreground">
          {question.prompt}
        </p>
      </section>

      {/* ── Examples: exactly what a candidate is shown ────────────────── */}
      <section>
        <Heading className={GROUP_TITLE}>Examples shown to the candidate</Heading>
        {samples.length === 0 ? (
          <p className="mt-1.5 text-[13px] text-muted-foreground">
            No sample test case. A question needs at least one before it can be saved.
          </p>
        ) : (
          <ol className="mt-2 flex flex-col gap-3">
            {samples.map((t, i) => (
              <li key={`sample-${i}`} className="grid gap-2.5 sm:grid-cols-2">
                <CodeBlock label={`Example ${i + 1} input`} text={t.stdin} />
                <CodeBlock label={`Example ${i + 1} output`} text={t.expected_output} />
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* ── Code ───────────────────────────────────────────────────────── */}
      <section>
        <Heading className={GROUP_TITLE}>Code</Heading>
        <Tabs defaultValue="reference" className="mt-2">
          <TabsList aria-label="Code for this question">
            <TabsTrigger value="reference">{referenceLabel}</TabsTrigger>
            <TabsTrigger value="starter">Starter code</TabsTrigger>
          </TabsList>
          <TabsContent value="reference">
            {question.reference_solution ? (
              <Well>
                <pre className={CODE}>{question.reference_solution}</pre>
              </Well>
            ) : (
              <p className="text-[13px] text-muted-foreground">
                No reference solution. Reviewers have nothing to check the expected outputs against
                until one is written.
              </p>
            )}
            {unverified ? (
              <p className="mt-2 flex items-start gap-1.5 text-[12.5px] leading-relaxed text-[var(--ui-warn)]">
                <AlertTriangle size={14} className="mt-0.5 flex-none" aria-hidden="true" />
                <span>
                  {UNVERIFIED_EXPLANATION} Check the expected outputs before approving.
                </span>
              </p>
            ) : null}
          </TabsContent>
          <TabsContent value="starter">
            {question.starter_code ? (
              <>
                <Well>
                  <pre className={CODE}>{question.starter_code}</pre>
                </Well>
                {languages.length > 1 ? (
                  <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">
                    The same starter code is pre-filled whichever language the candidate picks.
                  </p>
                ) : null}
              </>
            ) : (
              <p className="text-[13px] text-muted-foreground">
                No starter code. Candidates begin with an empty editor.
              </p>
            )}
          </TabsContent>
        </Tabs>
      </section>

      {/* ── Every test case, hidden ones marked ────────────────────────── */}
      <section>
        <Heading className={GROUP_TITLE}>
          Test cases ({samples.length} sample, {hiddenCount} hidden)
        </Heading>
        {tests.length === 0 ? (
          <p className="mt-1.5 text-[13px] text-muted-foreground">No test cases.</p>
        ) : (
          <ol className="mt-2 flex flex-col divide-y divide-border">
            {tests.map((t, i) => (
              <li
                key={`test-${i}`}
                className="py-3 first:pt-0 last:pb-0"
                data-testid={t.is_sample ? 'test-sample' : 'test-hidden'}
              >
                <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px] font-medium text-foreground">
                  <span>Test {i + 1}</span>
                  {t.is_sample ? (
                    <Badge variant="outline" className="gap-1">
                      <Eye size={12} aria-hidden="true" />
                      Sample
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="gap-1">
                      <Lock size={12} aria-hidden="true" />
                      Hidden
                    </Badge>
                  )}
                  <span className="font-normal text-muted-foreground">Weight {t.weight}</span>
                </div>
                <div className="grid gap-2.5 sm:grid-cols-2">
                  <CodeBlock label="Input" text={t.stdin} />
                  <CodeBlock label="Expected output" text={t.expected_output} />
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

export default BankCodingQuestionView;
