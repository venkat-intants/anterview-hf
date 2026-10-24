// ExamEditor: Rounds → Sections → Questions tree (HR Phase 2+).
// Each exam has ordered Rounds; each Round has Sections (mcq|coding).
// HR can add/rename/reorder/delete rounds; set pass_threshold, time_limit,
// advances_to_interview; publish/unpublish per round; add/rename/delete sections;
// author questions per section (MCQ inline, coding via CodingAuthoringSection).
// Per-round: an Assign/schedule panel that posts to the assignment endpoint
// with round_id + optional scheduled_at.
// Exam-level settings (title, retake, auto-advance, close/reopen) sit above the
// rounds.
// Legacy single-round/single-section exams render gracefully from GET /structure.
// The panels live in components/hr/exam/ (McqSection, SectionPanel, RoundPanel,
// RoundAssignPanel, ExamSettingsPanel); this file is the page that composes them.
//
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, BarChart3, Loader2, Sparkles } from '@/design/components/icons';
import { getExam, getStructure, createRound, deleteRound, reorderRounds } from '@/api/exams';
import { examEditorMutationKey, minutesToSeconds, parseThreshold } from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { plural } from '@/lib/formatters';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { statusLabel } from '@/lib/statusLabel';
import { toast } from '@/lib/toast';
import { GlassCard } from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { EmptyState, Page } from '@/design/components/page';
import { EditorHeader, DocStatusPill } from '@/components/hr/EditorHeader';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { RoundPanel } from '@/components/hr/exam/RoundPanel';
import { ExamSettingsPanel } from '@/components/hr/exam/ExamSettingsPanel';

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExamEditor() {
  const { examId = '' } = useParams<{ examId: string }>();
  const qc = useQueryClient();
  const editorKey = examEditorMutationKey(examId);

  const examQuery = useQuery({
    queryKey: ['hr', 'exam', examId],
    queryFn: () => getExam(examId),
  });
  const exam = examQuery.data;

  const structureQuery = useQuery({
    queryKey: ['hr', 'exam', examId, 'structure'],
    queryFn: () => getStructure(examId),
  });
  const structure = structureQuery.data;

  // Every edit on this screen saves on its own; the header reports whether
  // any of THIS exam's edits is in flight (audit E22).
  const saving = useIsMutating({ mutationKey: editorKey });

  // Add round state
  const [addingRound, setAddingRound] = useState(false);
  const [roundTitle, setRoundTitle] = useState('');
  const [roundThreshold, setRoundThreshold] = useState('60');
  const [roundTimeMin, setRoundTimeMin] = useState('');
  const [roundAdvances, setRoundAdvances] = useState(false);

  const invalidateAll = () => void invalidateAfterHrMutation(qc);

  const addRoundMut = useMutation({
    mutationKey: editorKey,
    mutationFn: () =>
      createRound(examId, {
        title: roundTitle.trim() || `Round ${(structure?.rounds.length ?? 0) + 1}`,
        pass_threshold: parseThreshold(roundThreshold, 60),
        time_limit_seconds: minutesToSeconds(roundTimeMin),
        advances_to_interview: roundAdvances,
      }),
    onSuccess: () => {
      setRoundTitle('');
      setRoundThreshold('60');
      setRoundTimeMin('');
      setRoundAdvances(false);
      setAddingRound(false);
      toast.success('Round added');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not add the round.')),
  });

  const deleteRoundMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (roundId: string) => deleteRound(examId, roundId),
    onSuccess: () => {
      toast.success('Round deleted');
      invalidateAll();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the round.')),
  });

  // A mutation, not a bare promise (audit E6): the failure is reported, and
  // the arrows are disabled while a move is in flight, so a double click
  // cannot send two racing reorders.
  const reorderMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (ids: string[]) => reorderRounds(examId, ids),
    onSuccess: invalidateAll,
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not reorder the rounds.')),
  });

  const rounds = useMemo(
    () => [...(structure?.rounds ?? [])].sort((a, b) => a.position - b.position),
    [structure],
  );

  function handleMove(roundId: string, direction: -1 | 1) {
    // By index in the sorted list, not by stored position: positions are not
    // compacted after a delete, so "position <= 1" did not mean "first".
    const idx = rounds.findIndex((r) => r.id === roundId);
    const target = idx + direction;
    if (idx < 0 || target < 0 || target >= rounds.length) return;
    const ids = rounds.map((r) => r.id);
    [ids[idx], ids[target]] = [ids[target], ids[idx]];
    reorderMut.mutate(ids);
  }

  // ── Failed / loading ─────────────────────────────────────────────────────────
  // A failed load used to leave the editor on skeletons for ever (audit E14).
  const failed = (examQuery.isError && !exam) || (structureQuery.isError && !structure);
  if (failed) {
    const err = examQuery.isError ? examQuery.error : structureQuery.error;
    return (
      <Page width="default">
        <Link
          to="/hr/exams"
          className="inline-flex min-h-10 self-start items-center text-[13px] font-semibold text-[var(--ui-info)] hover:underline"
        >
          Back to exams
        </Link>
        <QueryErrorPanel
          title="Could not load this exam."
          error={err}
          onRetry={() => {
            void examQuery.refetch();
            void structureQuery.refetch();
          }}
          retrying={examQuery.isFetching || structureQuery.isFetching}
        />
      </Page>
    );
  }

  if (!exam || !structure) {
    return (
      <Page width="default">
        <div aria-busy="true" className="flex flex-col gap-4">
          <span className="sr-only">Loading exam</span>
          <Skeleton className="h-5 w-28" />
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-40 w-full rounded-card" />
          <Skeleton className="h-48 w-full rounded-card" />
        </div>
      </Page>
    );
  }

  const hasLiveRound = rounds.some((r) => r.status === 'published');

  return (
    <Page width="default">
      {/* Sticky document header: title, status and the screen's one action
          stay in view while a long exam is being authored. */}
      <EditorHeader
        breadcrumbs={[{ label: 'Exams', to: '/hr/exams' }, { label: exam.title }]}
        title={exam.title}
        status={
          <DocStatusPill
            status={
              exam.status === 'closed' ? 'locked' : exam.status === 'published' ? 'live' : 'draft'
            }
          >
            {statusLabel(exam.status, 'exam')}
          </DocStatusPill>
        }
        meta={
          <>
            <span>{plural(rounds.length, 'round')}</span>
            {exam.auto_advance_on_pass && <span>Auto-advance on pass</span>}
          </>
        }
        saveState={saving > 0 ? 'saving' : 'saved'}
        secondary={
          <Button asChild variant="outline">
            <Link to={`/hr/exams/${examId}/results`}>
              <BarChart3 aria-hidden="true" /> Results
            </Link>
          </Button>
        }
        primary={
          <Button
            type="button"
            onClick={() => {
              setAddingRound(true);
              window.setTimeout(
                () =>
                  document.getElementById('new-round-form')?.scrollIntoView?.({ block: 'nearest' }),
                0,
              );
            }}
            disabled={addingRound}
          >
            <Plus aria-hidden="true" /> Add round
          </Button>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[200px_minmax(0,1fr)]">
        {/* Outline: the exam's rounds as in-page anchors, so a long exam can
            be jumped through. */}
        <nav aria-label="Exam outline" className="hidden lg:sticky lg:top-[200px] lg:block">
          <p className="mb-2 text-[12.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Outline
          </p>
          <ol className="flex flex-col gap-0.5">
            {rounds.map((r) => (
              <li key={r.id}>
                <a
                  href={`#round-${r.id}`}
                  className="flex min-h-[36px] items-start gap-2 rounded-[8px] px-2 py-1.5 text-[13px] text-muted-foreground hover:bg-[var(--ui-inset-soft)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  <span className="font-mono tabular-nums">{r.round_number}.</span>
                  <span className="min-w-0 flex-1 break-words">
                    {r.title}
                    <span className="block text-[12px]">
                      {r.status === 'published' ? 'Published · locked' : 'Draft'}
                    </span>
                  </span>
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="min-w-0 space-y-4">
          <ExamSettingsPanel exam={exam} hasLiveRound={hasLiveRound} />

          {/* Rounds */}
          <div className="space-y-4">
            {rounds.length === 0 ? (
              <Reveal delay={0.04}>
                <EmptyState
                  icon={Sparkles}
                  title="No rounds yet"
                  description="Add the first round with the Add round button."
                />
              </Reveal>
            ) : (
              <Stagger>
                {rounds.map((round, idx) => (
                  <StaggerItem key={round.id}>
                    <div id={`round-${round.id}`} className="scroll-mt-52">
                      <RoundPanel
                        examId={examId}
                        round={round}
                        totalRounds={rounds.length}
                        isFirst={idx === 0}
                        isLast={idx === rounds.length - 1}
                        onDelete={(rid) => deleteRoundMut.mutate(rid)}
                        deleting={deleteRoundMut.isPending && deleteRoundMut.variables === round.id}
                        onMove={handleMove}
                        reordering={reorderMut.isPending}
                      />
                    </div>
                  </StaggerItem>
                ))}
              </Stagger>
            )}

            {/* Add round */}
            {addingRound ? (
              <Reveal delay={0.02}>
                <div id="new-round-form" className="scroll-mt-52">
                  <GlassCard className="p-5" role="group" aria-label="New round">
                    <h3 className="mb-4 text-[15px] font-semibold">New round</h3>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        addRoundMut.mutate();
                      }}
                      className="space-y-3"
                      aria-label="New round form"
                    >
                      <Input
                        placeholder="Round title (e.g. Technical screening)"
                        value={roundTitle}
                        maxLength={200}
                        onChange={(e) => setRoundTitle(e.target.value)}
                        aria-label="Round title"
                      />
                      <div className="grid gap-3 sm:grid-cols-3">
                        <FormField label="Pass threshold %">
                          {(p) => (
                            <Input
                              {...p}
                              type="number"
                              min={0}
                              max={100}
                              value={roundThreshold}
                              onChange={(e) => setRoundThreshold(e.target.value)}
                              aria-label="Pass threshold"
                            />
                          )}
                        </FormField>
                        <FormField label="Time limit (min)">
                          {(p) => (
                            <Input
                              {...p}
                              type="number"
                              min={1}
                              placeholder="No limit"
                              value={roundTimeMin}
                              onChange={(e) => setRoundTimeMin(e.target.value)}
                              aria-label="Time limit in minutes"
                            />
                          )}
                        </FormField>
                        <div className="flex items-end pb-1">
                          <label className="flex min-h-10 items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
                            <input
                              type="checkbox"
                              checked={roundAdvances}
                              onChange={(e) => setRoundAdvances(e.target.checked)}
                              className="h-4 w-4 accent-[var(--accent)]"
                            />
                            Advances to interview
                          </label>
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          type="submit"
                          disabled={addRoundMut.isPending}
                          aria-busy={addRoundMut.isPending}
                        >
                          {addRoundMut.isPending ? (
                            <Loader2 className="animate-spin" aria-hidden="true" />
                          ) : (
                            <Plus aria-hidden="true" />
                          )}
                          {addRoundMut.isPending ? 'Adding…' : 'Add round'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => setAddingRound(false)}
                          disabled={addRoundMut.isPending}
                        >
                          Cancel
                        </Button>
                      </div>
                    </form>
                  </GlassCard>
                </div>
              </Reveal>
            ) : null}
          </div>
        </div>
      </div>
    </Page>
  );
}
