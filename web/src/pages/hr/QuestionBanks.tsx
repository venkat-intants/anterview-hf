// QuestionBanks (/hr/question-banks) - the company's reusable question banks,
// as a list a person can scan: each bank's name and what it assesses, how many
// questions sit at each stage of review, and the ONE thing that bank needs next
// as a link straight to that stage of its flow.
//
// "New bank" is the page's single primary; it opens the creation flow
// (/hr/question-banks/new), which is the same rail every bank page uses.
//
// What the list can and cannot show. The list endpoint returns a bank's name,
// description, status counts and exam usage - not how many of its questions are
// MCQ or coding on older backends, so there the list shows status counts only
// and offers no kind filter; the kind split is on the bank's own page. A newer
// backend sends an optional `kind_counts` per bank: when present the row shows
// "n MCQ · n coding" and the toolbar gains a kind filter; when absent nothing is
// shown for it - never zeros we do not know. Fetching every bank's questions to
// count kinds here would be one request per bank on every visit.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  ArrowRight,
  CheckCircle2,
  Clock,
  Library,
  Pencil,
  Plus,
  Search,
} from '@/design/components/icons';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Page, PageHeader, EmptyState, Toolbar } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/errorMessage';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import {
  BANK_KIND_FILTERS,
  BANK_LIST_FILTERS,
  anyKindCounts,
  bankNextAction,
  kindSummary,
  matchesBankFilter,
  matchesKindFilter,
  statusCounts,
  type BankKindFilter,
  type BankListFilter,
  type BankNextAction,
} from '@/lib/bankFlow';
import {
  archiveQuestionBank,
  listQuestionBanks,
  updateQuestionBank,
  type BankOut,
  type BankQuestionStatus,
} from '@/api/questionBanks';

const STATUS_TONE: Record<BankQuestionStatus, TagTone> = {
  draft: 'neutral',
  in_review: 'amber',
  approved: 'forest',
  retired: 'ember',
};

const field =
  'h-10 rounded-[10px] border border-input bg-card px-3 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

// Rename / Archive are occasional actions: ghost, icon plus label, 40px targets,
// no border or fill until hovered.
const QUIET =
  'inline-flex min-h-10 items-center gap-1.5 rounded-[10px] px-3 text-[13px] font-medium text-[var(--ui-soft)] hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

// Applied to a wrapper: the idle ConfirmDeleteButton is its only <button> child.
const QUIET_ARCHIVE =
  'inline-flex [&>button]:rounded-[10px] [&>button]:border-transparent [&>button]:bg-transparent [&>button]:px-3 [&>button]:font-medium [&>button]:text-[var(--ui-soft)] [&>button:hover]:bg-[var(--ui-inset)] [&>button:hover]:text-[var(--ui-danger)]';

function NextActionLink({ bank, action }: { bank: BankOut; action: BankNextAction }): JSX.Element {
  const to = `/hr/question-banks/${bank.id}?stage=${action.stage}`;
  const Icon = action.kind === 'wait' ? Clock : action.kind === 'ready' ? CheckCircle2 : ArrowRight;
  return (
    <Link
      to={to}
      data-testid="bank-next-action"
      className={cn(
        'inline-flex min-h-10 items-center gap-1.5 rounded-[10px] px-2 text-[13.5px] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
        action.kind === 'act'
          ? 'text-foreground hover:underline'
          : 'text-muted-foreground hover:text-foreground hover:underline',
      )}
    >
      {action.kind !== 'act' ? <Icon size={15} aria-hidden="true" /> : null}
      {action.label}
      {action.kind === 'act' ? <ArrowRight size={15} aria-hidden="true" /> : null}
    </Link>
  );
}

export default function QuestionBanks(): JSX.Element {
  const qc = useQueryClient();
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<BankListFilter>('all');
  const [kindFilter, setKindFilter] = useState<BankKindFilter>('all');

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['hr', 'question-banks'],
    queryFn: listQuestionBanks,
  });

  const invalidate = (): void => void qc.invalidateQueries({ queryKey: ['hr', 'question-banks'] });

  const renameMut = useMutation({
    mutationFn: ({ id, name: next }: { id: string; name: string }) =>
      updateQuestionBank(id, { name: next }),
    onSuccess: () => {
      toast.success('Bank renamed');
      setRenaming(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not rename this bank.')),
  });

  const archiveMut = useMutation({
    mutationFn: (id: string) => archiveQuestionBank(id),
    onSuccess: () => {
      toast.success('Bank archived');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not archive this bank.')),
  });

  const allBanks: BankOut[] = data ?? [];
  const hasKinds = anyKindCounts(allBanks);
  const needle = search.trim().toLowerCase();
  const banks = allBanks.filter(
    (b) =>
      matchesBankFilter(b, filter) &&
      (!hasKinds || matchesKindFilter(b, kindFilter)) &&
      (!needle ||
        b.name.toLowerCase().includes(needle) ||
        (b.description ?? '').toLowerCase().includes(needle)),
  );

  return (
    <Page width="form">
      <PageHeader
        title="Question banks"
        description="Reusable MCQ and coding questions, reviewed once and then copied into any exam. A published round keeps its own copy."
        actions={
          <>
            <Button asChild variant="outline">
              <Link to="/hr/question-banks/reviews">Question reviews</Link>
            </Button>
            <Button asChild>
              <Link to="/hr/question-banks/new">
                <Plus aria-hidden="true" />
                New bank
              </Link>
            </Button>
          </>
        }
      />

      {isLoading ? (
        <div className="flex flex-col gap-2" role="status" aria-label="Loading question banks">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[84px] w-full rounded-card" />
          ))}
        </div>
      ) : isError ? (
        <p role="alert" className="text-[14px] text-[var(--ui-danger)]">
          Could not load question banks.{' '}
          <button type="button" className="font-semibold underline" onClick={() => void refetch()}>
            Try again
          </button>
        </p>
      ) : allBanks.length === 0 ? (
        <EmptyState
          icon={Library}
          size="page"
          title="No banks yet"
          description="A bank holds questions you review once and reuse across exams. Start one and fill it by writing, by AI, or from a file."
          action={
            <Button asChild variant="outline">
              <Link to="/hr/question-banks/new">Create your first bank</Link>
            </Button>
          }
        />
      ) : (
        <div className="flex flex-col gap-4">
          <Toolbar
            className="mb-0"
            trailing={
              <span className="text-[13px] tabular-nums text-muted-foreground" role="status">
                {banks.length} of {allBanks.length} bank{allBanks.length === 1 ? '' : 's'}
              </span>
            }
          >
            <div className="relative min-w-[200px] max-w-[320px] flex-1">
              <Search
                size={14}
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
              />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search banks"
                aria-label="Search banks"
                className={cn(field, 'w-full pl-8')}
              />
            </div>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as BankListFilter)}
              aria-label="Filter banks by status"
              className={field}
            >
              {BANK_LIST_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
            {hasKinds ? (
              <select
                value={kindFilter}
                onChange={(e) => setKindFilter(e.target.value as BankKindFilter)}
                aria-label="Filter banks by question type"
                data-testid="bank-kind-filter"
                className={field}
              >
                {BANK_KIND_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            ) : null}
          </Toolbar>

          {banks.length === 0 ? (
            <EmptyState
              title="No banks match"
              description="Clear the search or the filter to see every bank."
            />
          ) : (
            <Card flush>
              <ul aria-label="Question banks" className="divide-y divide-border">
                {banks.map((b) => {
                  const counts = statusCounts(b);
                  const action = bankNextAction(b);
                  return (
                    <li key={b.id} className="px-4 py-3 sm:px-5" data-testid="bank-row">
                      <div className="grid gap-x-6 gap-y-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
                        <div className="min-w-0">
                          {renaming?.id === b.id ? (
                            <form
                              className="flex flex-wrap items-center gap-2"
                              onSubmit={(e) => {
                                e.preventDefault();
                                const next = renaming.name.trim();
                                if (next.length < 1) {
                                  toast.error('A name is required.');
                                  return;
                                }
                                renameMut.mutate({ id: b.id, name: next });
                              }}
                            >
                              <input
                                aria-label={`New name for ${b.name}`}
                                value={renaming.name}
                                onChange={(e) => setRenaming({ id: b.id, name: e.target.value })}
                                className={cn(field, 'min-w-0 flex-1')}
                              />
                              <Button type="submit" size="sm" disabled={renameMut.isPending}>
                                Save
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => setRenaming(null)}
                              >
                                Cancel
                              </Button>
                            </form>
                          ) : (
                            <Link
                              to={`/hr/question-banks/${b.id}`}
                              className="inline-flex min-h-10 items-center rounded-[6px] text-[15px] font-semibold text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                            >
                              {b.name}
                            </Link>
                          )}
                          {b.description ? (
                            <p className="line-clamp-1 text-[13px] text-muted-foreground">
                              {b.description}
                            </p>
                          ) : null}
                          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                            {counts.length === 0 ? (
                              <span className="text-[12.5px] text-muted-foreground">
                                No questions
                              </span>
                            ) : (
                              counts.map((c) => (
                                <StatusTag
                                  key={c.status}
                                  tone={STATUS_TONE[c.status]}
                                  className="text-[12px]"
                                >
                                  {c.label}
                                </StatusTag>
                              ))
                            )}
                            {kindSummary(b) ? (
                              <span
                                data-testid="bank-kind-counts"
                                className="text-[12.5px] tabular-nums text-muted-foreground"
                              >
                                {kindSummary(b)}
                              </span>
                            ) : null}
                            <span className="text-[12.5px] text-[var(--ui-faint)]">
                              {counts.length > 0 || kindSummary(b) ? (
                                <span aria-hidden="true">· </span>
                              ) : null}
                              Used in {b.used_in_exams} exam{b.used_in_exams === 1 ? '' : 's'}
                            </span>
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-x-1 gap-y-0 md:flex-nowrap md:justify-end">
                          <NextActionLink bank={b} action={action} />
                          {renaming?.id === b.id ? null : (
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => setRenaming({ id: b.id, name: b.name })}
                                className={QUIET}
                              >
                                <Pencil size={14} aria-hidden="true" />
                                Rename
                              </button>
                              {/* ConfirmDeleteButton has no className prop, and its
                                  armed Sure?/Archive/Cancel state must stay as it is, so
                                  the idle button is quieted from outside. The pair it
                                  swaps to when armed is a span and is not touched. */}
                              <span className={QUIET_ARCHIVE}>
                                {/* There is no unarchive in the app, so the
                                    confirm says so (audit E18). */}
                                <ConfirmDeleteButton
                                  label="Archive"
                                  confirmText="Archive bank"
                                  title="Archiving hides this bank. It cannot be restored from the app."
                                  confirmGroupLabel={`Archive ${b.name}? It cannot be restored from the app.`}
                                  icon={Archive}
                                  pending={archiveMut.isPending && archiveMut.variables === b.id}
                                  onConfirm={() => archiveMut.mutate(b.id)}
                                />
                              </span>
                            </div>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </div>
      )}
    </Page>
  );
}
