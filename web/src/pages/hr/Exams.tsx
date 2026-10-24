// Exams — HR MCQ exam list + create (HR workflow Phase 2).
// Layout: faithfully reproduces anterview-pages/src/screens/hr/Exams.tsx
//   (header + create-new dashed tile + card grid with status/Qs/actions).
// Behavior: all live mutations/queries from the previous version preserved.
//   Status taxonomy: published → forest/live; draft → neutral/not-published.
//   Inline create form from the previous version retained inside the new layout.

import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listExams, createExam, type ExamSummary } from '@/api/exams';
import { errorMessage } from '@/lib/errorMessage';
import { parseThreshold } from '@/lib/examEditor';
import { plural } from '@/lib/formatters';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { statusLabel } from '@/lib/statusLabel';
import { toast } from '@/lib/toast';
import { appPath } from '@/lib/safeUrl';
import { cn } from '@/lib/utils';
import { Reveal } from '@/design/components/Reveal';
import { PageHeader, EmptyState, Toolbar, Page } from '@/design/components/page';
import { GlassCard, StatusTag, Field, ToggleSwitch } from '@/design/components/primitives';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { ArrowLeft, Plus, Pencil, BarChart3, Sparkles, Search } from '@/design/components/icons';
import { QueryState } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';

// ── Status → design tone mapping ─────────────────────────────────────────────
type ToneKey = 'forest' | 'neutral' | 'amber';

function statusTone(s: string): ToneKey {
  if (s === 'published') return 'forest';
  if (s === 'closed') return 'neutral';
  return 'amber';
}

// ── Exam card (design layout) ─────────────────────────────────────────────────
function ExamCard({ e }: { e: ExamSummary }) {
  const navigate = useNavigate();
  const tone = statusTone(e.status);
  const label = statusLabel(e.status, 'exam');
  const isPublished = e.status === 'published';
  // A closed exam, or one taken back to draft after it was sat, still has
  // results worth opening (audit E17).
  const hasResults = isPublished || e.attempt_count > 0;
  const attempts = plural(e.attempt_count, 'attempt');

  return (
    <GlassCard hover className="flex h-full flex-col p-5">
      {/* Status + question count */}
      <div className="mb-3.5 flex items-center justify-between">
        <StatusTag tone={tone} dot={isPublished}>
          {label}
        </StatusTag>
        <span className="font-mono text-[12px] text-[var(--ui-faint)]">{e.question_count} Qs</span>
      </div>

      {/* Title */}
      <h3 className="text-[16px] font-semibold line-clamp-2">{e.title}</h3>

      {/* Meta */}
      <div className="mt-1 text-[12.5px] text-[var(--ui-faint)]">pass ≥ {e.pass_threshold}%</div>

      {/* Attempts / closed / not-published */}
      {e.status === 'closed' ? (
        <div className="mt-4 text-[12.5px] text-muted-foreground">Closed · {attempts}</div>
      ) : hasResults ? (
        <div className="mt-4 flex items-center gap-4 text-[12.5px] text-muted-foreground">
          <span>{attempts}</span>
        </div>
      ) : (
        <div className="mt-4 text-[12.5px] text-[var(--ui-faint)]">Not published yet</div>
      )}

      {/* Actions. One control each (never a button inside a button). */}
      <div className="mt-auto flex gap-2 pt-4">
        <Button
          variant="outline"
          type="button"
          className="flex-1"
          onClick={() => void navigate(`/hr/exams/${e.id}`)}
          aria-label={`Edit exam ${e.title}`}
        >
          <Pencil aria-hidden="true" /> Edit
        </Button>
        {hasResults && (
          <Button
            variant="highlight"
            type="button"
            className="flex-1"
            onClick={() => void navigate(`/hr/exams/${e.id}/results`)}
            aria-label={`View results for exam ${e.title}`}
          >
            <BarChart3 aria-hidden="true" /> Results
          </Button>
        )}
      </div>
    </GlassCard>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function Exams() {
  const qc = useQueryClient();
  // Where the workflow builder sent the user from, when a round needed an
  // exam. A query string is user-controlled, so only a same-app path is used.
  const [searchParams] = useSearchParams();
  const returnTo = appPath(searchParams.get('returnTo'));
  const navigate = useNavigate();

  // Create-form state
  const [title, setTitle] = useState('');
  const [threshold, setThreshold] = useState('60');
  const [minutes, setMinutes] = useState('');
  const [allowRetake, setAllowRetake] = useState(false);
  const [autoAdvance, setAutoAdvance] = useState(false);
  const [kind, setKind] = useState<'mcq' | 'coding'>('mcq');
  const [showForm, setShowForm] = useState(false);
  // List controls — they only narrow what is already loaded.
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const examsQuery = useQuery({
    queryKey: ['hr', 'exams'],
    queryFn: () => listExams(),
  });
  const exams = examsQuery.data;
  const isLoading = exams === undefined && !examsQuery.isError;

  const createMut = useMutation({
    mutationFn: () =>
      createExam({
        title: title.trim(),
        // 0 is a real pass mark; `Number(x) || 60` turned it into 60 (audit E16).
        pass_threshold: parseThreshold(threshold, 60),
        time_limit_seconds: minutes.trim() ? Math.max(1, Number(minutes)) * 60 : null,
        allow_retake: allowRetake,
        auto_advance_on_pass: autoAdvance,
        kind,
      }),
    onSuccess: (e) => {
      toast.success('Exam created - add questions next');
      setTitle('');
      setMinutes('');
      setThreshold('60');
      setAllowRetake(false);
      setAutoAdvance(false);
      setKind('mcq');
      setShowForm(false);
      void invalidateAfterHrMutation(qc);
      void navigate(`/hr/exams/${e.id}`);
    },
    onError: (err: unknown) => toast.error(errorMessage(err, 'Could not create the exam.')),
  });

  function onSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!title.trim()) {
      toast.error('Give the exam a title.');
      return;
    }
    createMut.mutate();
  }

  const all = exams ?? [];
  const needle = search.trim().toLowerCase();
  const list = all.filter(
    (e) =>
      (!needle || e.title.toLowerCase().includes(needle)) &&
      (!statusFilter || e.status === statusFilter),
  );
  const filtering = needle !== '' || statusFilter !== '';

  return (
    <Page width="default">
      {returnTo ? (
        <Link
          to={returnTo}
          data-testid="exams-return"
          className="inline-flex min-h-10 items-center gap-1.5 self-start rounded text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          <ArrowLeft size={16} aria-hidden="true" />
          Back to the workflow
        </Link>
      ) : null}
      <PageHeader
        title="Exams"
        description="Build an exam, add its questions, then publish it to start receiving attempts."
      />

      {all.length > 0 ? (
        <Toolbar
          trailing={
            <span className="text-[13px] tabular-nums text-muted-foreground" role="status">
              {list.length} of {plural(all.length, 'exam')}
            </span>
          }
        >
          <div className="relative min-w-[200px] flex-1 sm:max-w-[320px]">
            <Search
              size={15}
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search exams"
              aria-label="Search exams"
              className="pl-9"
            />
          </div>
          <SelectControl
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
            className="w-auto"
          >
            <option value="">Any status</option>
            <option value="draft">Only drafts</option>
            <option value="published">Only live</option>
            <option value="closed">Only closed</option>
          </SelectControl>
        </Toolbar>
      ) : null}

      {/* ── Inline create form ── */}
      {showForm && (
        <Reveal delay={0.04}>
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold">New exam</h3>
            <form onSubmit={onSubmit} className="space-y-4">
              {/* Exam type */}
              <div className="flex gap-2">
                {(['mcq', 'coding'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={
                      'min-h-[40px] flex-1 rounded-full border px-4 py-2 text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ' +
                      (kind === k
                        ? 'border-foreground bg-foreground text-background'
                        : 'border-border text-muted-foreground hover:text-foreground')
                    }
                    aria-pressed={kind === k}
                  >
                    {k === 'mcq' ? 'MCQ exam' : 'Coding round'}
                  </button>
                ))}
              </div>
              <Field
                label="Exam title"
                placeholder="e.g. Python Fundamentals Screening"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                aria-label="Exam title"
                required
              />
              <div className="grid gap-3 sm:grid-cols-3">
                <Field
                  label="Pass threshold %"
                  type="number"
                  min={0}
                  max={100}
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  aria-label="Pass threshold percent"
                />
                <Field
                  label="Time limit (min)"
                  type="number"
                  min={1}
                  placeholder="none"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value)}
                  aria-label="Time limit in minutes"
                />
                <div className="flex flex-col gap-2 pb-1">
                  <div className="flex items-center gap-3">
                    <ToggleSwitch
                      checked={allowRetake}
                      onChange={setAllowRetake}
                      label="Allow retake"
                    />
                    <span className="text-[13px] text-[var(--ui-soft)]">Allow retake</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <ToggleSwitch
                      checked={autoAdvance}
                      onChange={setAutoAdvance}
                      label="Auto-advance on pass"
                    />
                    <span className="text-[13px] text-[var(--ui-soft)]">Auto-advance on pass</span>
                  </div>
                </div>
              </div>
              <div className="flex gap-2">
                <Button
                  type="submit"
                  disabled={createMut.isPending}
                  aria-busy={createMut.isPending}
                >
                  <Plus aria-hidden="true" />
                  {createMut.isPending ? 'Creating…' : 'Create exam'}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowForm(false)}
                  disabled={createMut.isPending}
                >
                  Cancel
                </Button>
              </div>
            </form>
          </GlassCard>
        </Reveal>
      )}

      {/* ── Exam grid ── */}
      <div
        className={cn('grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3')}
        aria-busy={isLoading ? 'true' : undefined}
        aria-label={isLoading ? 'Loading exams' : undefined}
      >
        {/* "Create new" dashed tile — always first */}
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className={cn(
            'flex min-h-[212px] flex-col items-center justify-center gap-2.5',
            'rounded-card border-[1.5px] border-dashed border-[var(--ui-line-strong)]',
            'bg-card text-foreground',
            'transition-colors hover:bg-[var(--ui-inset-soft)]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          )}
          aria-label="New exam"
        >
          <Plus size={28} aria-hidden="true" />
          <span className="text-[14px] font-semibold">
            New exam
          </span>
        </button>

        {/* Loading, failed, empty or the cards. A failed load used to fall
            through to "No exams yet" (audit X3). */}
        <QueryState
          query={examsQuery}
          errorTitle="Could not load exams."
          errorClassName="sm:col-span-1 lg:col-span-2"
          loading={[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[212px] w-full rounded-card" />
          ))}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              icon={Sparkles}
              title="No exams yet"
              description="Create one, add questions, then publish to start receiving attempts."
              className="sm:col-span-1 lg:col-span-2"
            />
          }
        >
          {() =>
            list.length === 0 && filtering ? (
              <EmptyState
                title="No exams match"
                description="Clear the search or the status filter to see every exam."
                className="sm:col-span-1 lg:col-span-2"
              />
            ) : (
              list.map((e) => (
                <Reveal key={e.id} dir="zoom">
                  <ExamCard e={e} />
                </Reveal>
              ))
            )
          }
        </QueryState>
      </div>
    </Page>
  );
}
