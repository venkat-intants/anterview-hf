// BankQuestionBrowser - a compact master-detail over a set of bank questions.
//
// The left column is a short, filterable list (kind icon, the question's first
// line, difficulty, and the badges that matter: AI draft, Sent back, Unverified
// solution); the right column is the selected question in full
// (BankQuestionDetailPane). The caller decides WHICH questions are in play
// (the drafts, the ones in review, the approved ones) and owns the selection, so
// a stage change never leaves a stale selection behind.
//
// Below `lg` the two columns take turns: the list, then the question with a way
// back. On a wide screen the first question is open already, so the pane is
// never an empty rectangle with a sentence in it.
//
// Filtering is on the loaded list, not a request per keystroke. Lists are capped
// at a handful of rows with "Show all".
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { ClipboardList, Code2, Search } from '@/design/components/icons';
import { EmptyState, Toolbar } from '@/design/components/page';
import { QuestionBadges } from '@/components/bank/QuestionBadges';
import type { UnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { questionBrief } from '@/lib/bankFlow';
import { plural } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import type {
  BankDifficulty,
  BankQuestion,
  BankQuestionKind,
  BankQuestionStatus,
} from '@/api/questionBanks';
import { BankQuestionDetailPane } from './BankQuestionDetailPane';
import { STATUS_WORD } from './bankStatus';

const DESKTOP = '(min-width: 1024px)';

function useIsDesktop(): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window.matchMedia !== 'function') return () => undefined;
      const mq = window.matchMedia(DESKTOP);
      mq.addEventListener('change', notify);
      return () => mq.removeEventListener('change', notify);
    },
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(DESKTOP).matches : true),
    () => true,
  );
}

/** Narrowest width at which a 360px list and a readable pane sit side by side. */
const SIDE_BY_SIDE_MIN = 840;

/**
 * Whether the element is wide enough for the two columns. The viewport is the
 * wrong thing to ask: this browser lives inside a card, behind a sidebar and a
 * stage rail, so a 1100px window can hand it ~770px. Where ResizeObserver does
 * not exist (jsdom, very old browsers) it answers "wide", i.e. the old layout.
 */
function useFitsSideBySide(el: HTMLElement | null): boolean {
  const [fits, setFits] = useState(true);
  useEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = (): void => setFits(el.clientWidth >= SIDE_BY_SIDE_MIN);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return fits;
}

/** A toolbar filter select sizes to its longest option, not the whole row. */
const FILTER_SELECT = 'w-auto';

const INITIAL_ROWS = 8;

export interface BankQuestionBrowserProps {
  questions: readonly BankQuestion[];
  /** Every question in the bank, so a version picked from the history can open. */
  allQuestions?: readonly BankQuestion[];
  guard: UnsavedGuard;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Names the list for assistive tech, e.g. "Draft questions". */
  listLabel: string;
  /** Statuses present in `questions`; more than one adds a status filter. */
  statuses?: BankQuestionStatus[];
  emptyTitle: string;
  emptyDescription?: string;
}

export function BankQuestionBrowser({
  questions,
  allQuestions,
  guard,
  selectedId,
  onSelect,
  listLabel,
  statuses = [],
  emptyTitle,
  emptyDescription,
}: BankQuestionBrowserProps): JSX.Element {
  const desktop = useIsDesktop();
  const [q, setQ] = useState('');
  const [kind, setKind] = useState<BankQuestionKind | ''>('');
  const [status, setStatus] = useState<BankQuestionStatus | ''>('');
  const [difficulty, setDifficulty] = useState<BankDifficulty | ''>('');
  const [moreOpen, setMoreOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const paneHeading = useRef<HTMLHeadingElement>(null);
  const [gridEl, setGridEl] = useState<HTMLDivElement | null>(null);
  const fitsSideBySide = useFitsSideBySide(gridEl);

  const needle = q.trim().toLowerCase();
  const rows = useMemo(
    () =>
      questions.filter(
        (x) =>
          (!kind || x.kind === kind) &&
          (!status || x.status === status) &&
          (!difficulty || x.difficulty === difficulty) &&
          (!needle ||
            x.prompt.toLowerCase().includes(needle) ||
            x.tags.some((t) => t.toLowerCase().includes(needle))),
      ),
    [questions, kind, status, difficulty, needle],
  );

  // The open question: the one chosen, else (wide screens only) the first row.
  // A chosen question that left this view - deleted, or moved on a stage - falls
  // back the same way instead of leaving the pane pointing at nothing.
  const chosen = selectedId
    ? ((allQuestions ?? questions).find((x) => x.id === selectedId) ?? null)
    : null;
  const openQuestion = chosen ?? (desktop ? (rows[0] ?? null) : null);
  const openId = openQuestion?.id ?? null;

  const index = openQuestion ? rows.findIndex((x) => x.id === openQuestion.id) : -1;
  const expanded = showAll || index >= INITIAL_ROWS;
  const shown = expanded ? rows : rows.slice(0, INITIAL_ROWS);
  const filtered = needle || kind || status || difficulty;

  // On a phone the pane replaces the list, so focus has to follow the swap.
  const swapped = useRef(false);
  useEffect(() => {
    if (!desktop && selectedId && swapped.current) paneHeading.current?.focus();
    swapped.current = true;
  }, [desktop, selectedId]);

  function choose(id: string): void {
    if (id === openId) return;
    if (!guard.confirmLeave()) return;
    onSelect(id);
  }

  if (questions.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} bordered={false} />;
  }

  const showingPane = !desktop && Boolean(selectedId);

  return (
    <div className="flex flex-col gap-4" data-testid="bank-question-browser">
      <Toolbar
        className="mb-0 border-0 bg-transparent p-0 shadow-none"
        trailing={
          <span className="text-[13px] tabular-nums text-muted-foreground" role="status">
            {rows.length === questions.length
              ? plural(questions.length, 'question')
              : `${rows.length} of ${questions.length}`}
          </span>
        }
      >
        <div className="relative min-w-[180px] max-w-[320px] flex-1">
          <Search
            size={14}
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search these questions"
            aria-label="Search these questions"
            className="pl-8"
          />
        </div>
        <SelectControl
          value={kind}
          onChange={(e) => setKind(e.target.value as BankQuestionKind | '')}
          aria-label="Filter by kind"
          className={FILTER_SELECT}
        >
          <option value="">Any kind</option>
          <option value="mcq">MCQ</option>
          <option value="coding">Coding</option>
        </SelectControl>
        {statuses.length > 1 ? (
          <SelectControl
            value={status}
            onChange={(e) => setStatus(e.target.value as BankQuestionStatus | '')}
            aria-label="Filter by status"
            className={FILTER_SELECT}
          >
            <option value="">Any status</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {STATUS_WORD[s]}
              </option>
            ))}
          </SelectControl>
        ) : null}
        <button
          type="button"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((v) => !v)}
          className="min-h-10 rounded-full px-3 text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {moreOpen ? 'Fewer filters' : 'More filters'}
        </button>
        {moreOpen ? (
          <SelectControl
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as BankDifficulty | '')}
            aria-label="Filter by difficulty"
            className={FILTER_SELECT}
          >
            <option value="">Any difficulty</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </SelectControl>
        ) : null}
      </Toolbar>

      {rows.length === 0 ? (
        <EmptyState
          title="No questions match these filters."
          description={filtered ? 'Clear a filter to see them again.' : undefined}
          bordered={false}
        />
      ) : (
        <div
          ref={setGridEl}
          className={cn(
            'grid items-start gap-6',
            // Side by side only when the card itself is wide enough; otherwise
            // the list sits above the question (both visible).
            fitsSideBySide && 'lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]',
          )}
        >
          <div className={cn('min-w-0', showingPane && 'hidden')}>
            <ul aria-label={listLabel} className="flex flex-col gap-1">
              {shown.map((row) => {
                const selected = row.id === openId;
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => choose(row.id)}
                      aria-current={selected ? 'true' : undefined}
                      className={cn(
                        'flex min-h-[56px] w-full items-start gap-2.5 rounded-[12px] border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] motion-reduce:transition-none',
                        selected
                          ? 'border-[var(--accent)] bg-[var(--ui-highlight-wash)]'
                          : 'border-transparent hover:bg-[var(--ui-inset-soft)]',
                      )}
                    >
                      {row.kind === 'coding' ? (
                        <Code2
                          className="mt-0.5 h-4 w-4 flex-none text-[var(--ui-info)]"
                          aria-hidden="true"
                        />
                      ) : (
                        <ClipboardList
                          className="mt-0.5 h-4 w-4 flex-none text-[var(--ui-info)]"
                          aria-hidden="true"
                        />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="sr-only">
                          {row.kind === 'coding' ? 'Coding question: ' : 'MCQ: '}
                        </span>
                        <span className="line-clamp-2 break-words text-[13.5px] font-medium text-foreground">
                          {questionBrief(row.prompt)}
                        </span>
                        <span className="mt-0.5 block text-[12.5px] text-muted-foreground">
                          {row.difficulty} · {row.language.toUpperCase()}
                          {row.status !== 'draft' ? ` · ${STATUS_WORD[row.status]}` : ''}
                          {row.version > 1 ? ` · v${row.version}` : ''}
                        </span>
                        <QuestionBadges question={row} showKind={false} className="mt-1" />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {rows.length > INITIAL_ROWS ? (
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => setShowAll((v) => !v)}
                className="mt-1 inline-flex min-h-10 items-center rounded-[10px] px-2 text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                {expanded ? 'Show fewer' : `Show all ${rows.length} questions`}
              </button>
            ) : null}
          </div>

          <div
            className={cn('min-w-0', !desktop && !selectedId && 'hidden')}
            data-testid="bank-question-pane"
          >
            {openQuestion ? (
              <BankQuestionDetailPane
                // A fresh pane per question: edit state never leaks between them.
                key={openQuestion.id}
                question={openQuestion}
                guard={guard}
                headingRef={paneHeading}
                onSelectVersion={(id) => onSelect(id)}
                onGone={() => onSelect(null)}
                onBack={() => {
                  if (guard.confirmLeave()) onSelect(null);
                }}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
