// Exams — HR MCQ exam list + create (HR workflow Phase 2).
// Layout: faithfully reproduces anterview-pages/src/screens/hr/Exams.tsx
//   (header + create-new dashed tile + card grid with status/Qs/actions).
// Behavior: all live mutations/queries from the previous version preserved.
//   Status taxonomy: published → forest/live; draft → neutral/not-published.
//   Inline create form from the previous version retained inside the new layout.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listExams, createExam, type ExamSummary } from '@/api/exams';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Reveal } from '@/design/components/Reveal';
import {
  GlassCard,
  Pill,
  StatusTag,
  Field,
  ToggleSwitch,
} from '@/design/components/primitives';
import { Plus, Pencil, BarChart3, Sparkles } from '@/design/components/icons';

// ── Status → design tone mapping ─────────────────────────────────────────────
type ToneKey = 'forest' | 'neutral' | 'amber';

function statusTone(s: string): ToneKey {
  if (s === 'published') return 'forest';
  if (s === 'closed') return 'neutral';
  return 'amber';
}

function statusLabel(s: string): string {
  if (s === 'published') return 'Live';
  if (s === 'closed') return 'Closed';
  return 'Draft';
}

// ── Exam card (design layout) ─────────────────────────────────────────────────
function ExamCard({ e }: { e: ExamSummary }) {
  const navigate = useNavigate();
  const tone = statusTone(e.status);
  const label = statusLabel(e.status);
  const isPublished = e.status === 'published';

  return (
    <GlassCard hover className="flex h-full flex-col p-5">
      {/* Status + question count */}
      <div className="mb-3.5 flex items-center justify-between">
        <StatusTag tone={tone} dot={isPublished}>
          {label}
        </StatusTag>
        <span className="font-mono text-[12px] text-[#70757c]">{e.question_count} Qs</span>
      </div>

      {/* Title */}
      <h3 className="text-[16px] font-semibold line-clamp-2">{e.title}</h3>

      {/* Meta */}
      <div className="mt-1 text-[12.5px] text-[#70757c]">pass ≥ {e.pass_threshold}%</div>

      {/* Attempts / not-published */}
      {isPublished ? (
        <div className="mt-4 flex items-center gap-4 text-[12.5px] text-[#888b91]">
          <span>{e.attempt_count} attempts</span>
        </div>
      ) : (
        <div className="mt-4 text-[12.5px] text-[#70757c]">Not published yet</div>
      )}

      {/* Actions */}
      <div className="mt-auto flex gap-2 pt-4">
        <button
          type="button"
          className="flex-1"
          onClick={() => navigate(`/hr/exams/${e.id}`)}
          aria-label={`Edit exam ${e.title}`}
        >
          <Pill variant="ghost" className="w-full py-2 text-[12.5px]">
            <Pencil size={14} aria-hidden="true" /> Edit
          </Pill>
        </button>
        {isPublished && (
          <button
            type="button"
            className="flex-1"
            onClick={() => navigate(`/hr/exams/${e.id}/results`)}
            aria-label={`View results for exam ${e.title}`}
          >
            <Pill variant="accent" className="w-full py-2 text-[12.5px]">
              <BarChart3 size={14} aria-hidden="true" /> Results
            </Pill>
          </button>
        )}
      </div>
    </GlassCard>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function Exams() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  // Create-form state
  const [title, setTitle] = useState('');
  const [threshold, setThreshold] = useState('60');
  const [minutes, setMinutes] = useState('');
  const [allowRetake, setAllowRetake] = useState(false);
  const [autoAdvance, setAutoAdvance] = useState(false);
  const [kind, setKind] = useState<'mcq' | 'coding'>('mcq');
  const [showForm, setShowForm] = useState(false);

  const { data: exams, isLoading } = useQuery({
    queryKey: ['hr', 'exams'],
    queryFn: () => listExams(),
  });

  const createMut = useMutation({
    mutationFn: () =>
      createExam({
        title: title.trim(),
        pass_threshold: Number(threshold) || 60,
        time_limit_seconds: minutes.trim() ? Math.max(1, Number(minutes)) * 60 : null,
        allow_retake: allowRetake,
        auto_advance_on_pass: autoAdvance,
        kind,
      }),
    onSuccess: (e) => {
      toast.success('Exam created — add questions next');
      setTitle('');
      setMinutes('');
      setThreshold('60');
      setAllowRetake(false);
      setAutoAdvance(false);
      setKind('mcq');
      setShowForm(false);
      void qc.invalidateQueries({ queryKey: ['hr', 'exams'] });
      navigate(`/hr/exams/${e.id}`);
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Create failed'),
  });

  function onSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!title.trim()) {
      toast.error('Give the exam a title.');
      return;
    }
    createMut.mutate();
  }

  const list = exams ?? [];

  return (
    <div className="mx-auto max-w-[1120px] px-6 py-8 lg:px-8">
      {/* ── Header ── */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-semibold tracking-[-1px]">Exams</h1>
          <p className="mt-1 text-[14px] text-[#888b91]">
            Build and publish AI interview exams.
          </p>
        </div>
      </div>

      {/* ── Inline create form ── */}
      {showForm && (
        <Reveal delay={0.04}>
          <GlassCard feature className="mt-6 p-5">
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
                      'flex-1 rounded-[10px] border px-3 py-2 text-[13px] font-medium transition-colors ' +
                      (kind === k
                        ? 'border-[rgba(var(--accent-rgb),0.5)] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]'
                        : 'border-white/[0.1] text-[#888b91] hover:text-white')
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
                    <span className="text-[13px] text-[#b8babf]">Allow retake</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <ToggleSwitch
                      checked={autoAdvance}
                      onChange={setAutoAdvance}
                      label="Auto-advance on pass"
                    />
                    <span className="text-[13px] text-[#b8babf]">Auto-advance on pass</span>
                  </div>
                </div>
              </div>
              <div className="flex gap-2">
                <Pill
                  type="submit"
                  disabled={createMut.isPending}
                  aria-busy={createMut.isPending}
                  className="gap-1.5"
                >
                  <Plus size={16} aria-hidden="true" />
                  {createMut.isPending ? 'Creating…' : 'Create exam'}
                </Pill>
                <Pill
                  type="button"
                  variant="ghost"
                  onClick={() => setShowForm(false)}
                  disabled={createMut.isPending}
                >
                  Cancel
                </Pill>
              </div>
            </form>
          </GlassCard>
        </Reveal>
      )}

      {/* ── Exam grid ── */}
      <div
        className={cn(
          'mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3',
        )}
        aria-busy={isLoading ? 'true' : undefined}
        aria-label={isLoading ? 'Loading exams' : undefined}
      >
        {/* "Create new" dashed tile — always first */}
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className={cn(
            'flex min-h-[212px] flex-col items-center justify-center gap-2.5',
            'rounded-[24px] border-[1.5px] border-dashed border-[rgba(var(--accent-rgb),0.4)]',
            'bg-[rgba(var(--accent-rgb),0.06)] text-[#60a5fa]',
            'transition-colors hover:bg-[rgba(var(--accent-rgb),0.1)]',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
          )}
          aria-label="Create new exam"
        >
          <Plus size={28} aria-hidden="true" />
          <span className="text-[14px] font-semibold">
            {list.length === 0 ? 'Create your first exam' : 'Create new exam'}
          </span>
        </button>

        {/* Loading skeletons */}
        {isLoading &&
          [0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-[212px] w-full animate-pulse rounded-[24px] bg-white/[0.04]"
            />
          ))}

        {/* Empty hint when no exams */}
        {!isLoading && list.length === 0 && (
          <div
            className={cn(
              'flex flex-col items-center justify-center gap-3 rounded-[24px]',
              'border border-dashed border-white/[0.08] bg-white/[0.02] p-8 text-center',
              'sm:col-span-1 lg:col-span-2',
            )}
          >
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.06] text-[#888b91]">
              <Sparkles size={24} aria-hidden="true" />
            </span>
            <p className="text-[13px] text-[#888b91]">
              No exams yet. Create one, add questions, then publish to start receiving
              attempts.
            </p>
          </div>
        )}

        {/* Exam cards */}
        {!isLoading &&
          list.map((e) => (
            <Reveal key={e.id} dir="zoom">
              <ExamCard e={e} />
            </Reveal>
          ))}
      </div>
    </div>
  );
}
