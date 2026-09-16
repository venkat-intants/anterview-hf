// RoundInspector — D2. Everything one round is, on the right of the canvas.
//
// Three groups, in the order they matter:
//
//   1. WHAT IT IS      title, how long a candidate gets
//   2. WHAT IT ASKS    the exam behind an MCQ/coding round, or the competencies
//                      an AI interview probes
//   3. WHAT IT MEANS   the advance threshold
//
// The criteria picker is the part worth reading carefully. Selecting a
// competency here copies its ANCHORS AND PROBES onto the round, not just its
// name (C8). That is what makes a published workflow reproducible: at interview
// time the worker reads this rubric back instead of deriving a fresh one, so a
// candidate is assessed against exactly what was promised when the workflow was
// published — not against a role model that has since been refined.
//
// Weights are stored as given and renormalised per round on the server, which
// is why the panel shows relative emphasis rather than pretending the numbers
// must add to 100 here.

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronDown,
  ExternalLink,
  Info,
  Loader2,
} from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { cn } from '@/lib/utils';
import { listExams, getStructure } from '@/api/exams';
import type {
  RoundKind,
  Criterion,
  CriterionInput,
  RoleModel,
  Round,
  RoundPatch,
} from '@/api/workflows';
import { MAX_CRITERIA_PER_ROUND } from '@/api/workflows';
import { ROUND_KIND_META, ROUND_KIND_ORDER } from './roundKinds';

interface Props {
  round: Round;
  roleModel: RoleModel | undefined;
  editable: boolean;
  saving: boolean;
  onPatch: (fields: RoundPatch) => void;
  onCriteria: (criteria: CriterionInput[]) => void;
}

/* ── Exam picker ─────────────────────────────────────────────────────────────
 *
 * An MCQ or coding round takes its questions from an exam ROUND, not from the
 * exam as a whole — exams are already structured into rounds and sections, and
 * reusing that structure is what lets one exam serve several openings without
 * anyone re-authoring questions.
 */
function ExamPicker({
  value,
  editable,
  onChange,
}: {
  value: string | null;
  editable: boolean;
  onChange: (examRoundId: string | null) => void;
}) {
  const [examId, setExamId] = useState<string>('');

  const exams = useQuery({
    queryKey: ['hr', 'exams', 'published'],
    // Only published exams: candidates cannot open a draft exam's link, and the
    // server refuses to publish a workflow that points at one.
    queryFn: () => listExams('published'),
  });
  const structure = useQuery({
    queryKey: ['hr', 'exam-structure', examId],
    queryFn: () => getStructure(examId),
    enabled: examId.length > 0,
  });

  // The chosen round may belong to an exam the user has not re-selected in this
  // session (they are editing a saved round), so the label falls back to the id
  // rather than rendering an empty box that reads as "nothing attached".
  const chosen = structure.data?.rounds.find((r) => r.id === value);

  return (
    <div className="flex flex-col gap-2.5">
      {value ? (
        <div className="flex items-center gap-2 rounded-[10px] border border-border bg-[var(--ui-ok-wash)] px-3 py-2 text-[12.5px] text-foreground">
          <span className="truncate">
            Questions attached{chosen ? `: ${chosen.title}` : ''}
          </span>
          {editable ? (
            <button
              type="button"
              onClick={() => onChange(null)}
              className="ml-auto shrink-0 text-[12px] text-muted-foreground hover:text-foreground"
            >
              Change
            </button>
          ) : null}
        </div>
      ) : (
        <>
          <div className="flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This round has no questions yet. It cannot be published until it does.
          </div>
          {editable ? (
            <>
              <label className="text-[12px] font-medium text-[var(--ui-soft)]" htmlFor="exam-select">
                Take questions from
              </label>
              <select
                id="exam-select"
                value={examId}
                onChange={(e) => setExamId(e.target.value)}
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              >
                <option value="">Choose an exam…</option>
                {(exams.data ?? []).map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.title} ({e.question_count} questions)
                  </option>
                ))}
              </select>

              {examId ? (
                structure.isLoading ? (
                  <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    Loading rounds…
                  </span>
                ) : (structure.data?.rounds ?? []).length === 0 ? (
                  <span className="text-[12px] text-muted-foreground">
                    That exam has no rounds yet.{' '}
                    <Link
                      to={`/hr/exams/${examId}`}
                      className="inline-flex items-center gap-1 text-[var(--accent)] hover:underline"
                    >
                      Open the exam editor
                      <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </Link>
                  </span>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {(structure.data?.rounds ?? []).map((r) => {
                      const questions = r.sections.reduce((n, sec) => n + sec.question_count, 0);
                      // A draft round's link does not open for candidates, and
                      // an empty one has nothing to ask — the server refuses to
                      // publish a workflow pointing at either, so do not offer it.
                      const unusable =
                        r.status !== 'published'
                          ? 'Publish this round in the exam editor first.'
                          : questions === 0
                            ? 'Add questions to this round in the exam editor first.'
                            : null;
                      return (
                        <div key={r.id} className="flex flex-col gap-1">
                          <button
                            type="button"
                            onClick={() => onChange(r.id)}
                            disabled={unusable !== null}
                            title={unusable ?? undefined}
                            className="flex items-center justify-between gap-2 rounded-[10px] border border-border px-3 py-2 text-left text-[12.5px] text-foreground hover:border-electric/50 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-border"
                          >
                            <span className="truncate">{r.title}</span>
                            <span className="ml-auto flex shrink-0 items-center gap-2 text-[11px]">
                              <span
                                className={
                                  r.status === 'published'
                                    ? 'text-[var(--ui-ok)]'
                                    : 'text-[var(--ui-warn)]'
                                }
                              >
                                {r.status === 'published' ? 'Published' : 'Draft'}
                              </span>
                              <span
                                className={
                                  questions === 0
                                    ? 'text-[var(--ui-warn)]'
                                    : 'text-[var(--ui-faint)]'
                                }
                              >
                                {questions} question{questions === 1 ? '' : 's'}
                              </span>
                            </span>
                          </button>
                          {unusable ? (
                            <span className="pl-1 text-[11.5px] text-muted-foreground">
                              {unusable}{' '}
                              <Link
                                to={`/hr/exams/${examId}`}
                                className="inline-flex items-center gap-1 text-[var(--accent)] hover:underline"
                              >
                                Open the exam editor
                                <ExternalLink className="h-3 w-3" aria-hidden="true" />
                              </Link>
                            </span>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                )
              ) : null}
            </>
          ) : null}
        </>
      )}
    </div>
  );
}

/* ── Criteria picker ─────────────────────────────────────────────────────── */

function CriteriaPicker({
  round,
  roleModel,
  editable,
  onCriteria,
}: {
  round: Round;
  roleModel: RoleModel | undefined;
  editable: boolean;
  onCriteria: (criteria: CriterionInput[]) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const selected = useMemo(
    () => new Map(round.criteria.map((c) => [c.id, c])),
    [round.criteria],
  );

  const total = round.criteria.reduce((n, c) => n + c.weight, 0) || 1;

  const emit = (next: Criterion[]) =>
    onCriteria(
      next.map((c) => ({
        id: c.id,
        name: c.name,
        kind: c.kind,
        weight: c.weight,
        anchors: c.anchors,
        probes: c.probes,
      })),
    );

  const toggle = (compId: string) => {
    if (!roleModel) return;
    if (selected.has(compId)) {
      emit(round.criteria.filter((c) => c.id !== compId));
      return;
    }
    if (round.criteria.length >= MAX_CRITERIA_PER_ROUND) return;
    const comp = roleModel.competencies.find((c) => c.id === compId);
    if (!comp) return;
    // The whole rubric travels, not the label — this IS the freeze (C8).
    emit([
      ...round.criteria,
      {
        id: comp.id,
        name: comp.name,
        kind: comp.kind,
        weight: comp.weight,
        anchors: comp.anchors,
        probes: comp.probes,
      },
    ]);
  };

  const setWeight = (compId: string, weight: number) =>
    emit(round.criteria.map((c) => (c.id === compId ? { ...c, weight } : c)));

  if (!roleModel) {
    return (
      <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Loading the role model…
      </span>
    );
  }

  const atCap = round.criteria.length >= MAX_CRITERIA_PER_ROUND;

  return (
    <div className="flex flex-col gap-2">
      <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-muted-foreground">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {round.kind === 'ai_interview'
          ? 'The interview probes these and nothing else. Picking one copies its rating scale onto this round, so the same standard applies to everyone who sits it — even if the role model changes later.'
          : 'What this round contributes to the candidate profile. Used for coverage, so you can see what nothing in your process measures.'}
      </p>

      {atCap ? (
        <span className="text-[11.5px] text-[var(--ui-warn)]">
          A round assesses at most {MAX_CRITERIA_PER_ROUND} competencies — deselect one to
          swap.
        </span>
      ) : null}

      <div className="flex flex-col gap-1">
        {roleModel.competencies.map((comp) => {
          const chosen = selected.get(comp.id);
          const open = expanded === comp.id;
          return (
            <div
              key={comp.id}
              className={cn(
                'rounded-[10px] border px-2.5 py-2',
                chosen ? 'border-electric/45 bg-electric/[0.06]' : 'border-border',
              )}
            >
              <div className="flex items-center gap-2.5">
                <input
                  type="checkbox"
                  id={`crit-${round.id}-${comp.id}`}
                  checked={Boolean(chosen)}
                  disabled={!editable || (!chosen && atCap)}
                  onChange={() => toggle(comp.id)}
                  className="h-4 w-4 shrink-0 accent-[var(--accent)]"
                />
                <label
                  htmlFor={`crit-${round.id}-${comp.id}`}
                  className="min-w-0 flex-1 cursor-pointer truncate text-[13px] text-foreground"
                >
                  {comp.name}
                </label>
                {chosen ? (
                  <span className="shrink-0 text-[11px] tabular-nums text-[var(--ui-soft)]">
                    {Math.round((chosen.weight / total) * 100)}%
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : comp.id)}
                  aria-label={`${open ? 'Hide' : 'Show'} rating scale for ${comp.name}`}
                  aria-expanded={open}
                  className="shrink-0 rounded p-1 text-[var(--ui-faint)] hover:text-foreground"
                >
                  <ChevronDown
                    className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')}
                    aria-hidden="true"
                  />
                </button>
              </div>

              {chosen && editable ? (
                <div className="mt-2 flex items-center gap-2 pl-[26px]">
                  <label
                    htmlFor={`w-${round.id}-${comp.id}`}
                    className="text-[11px] text-[var(--ui-faint)]"
                  >
                    Emphasis
                  </label>
                  <input
                    id={`w-${round.id}-${comp.id}`}
                    type="range"
                    min={5}
                    max={100}
                    step={5}
                    value={Math.round(chosen.weight * 100)}
                    onChange={(e) => setWeight(comp.id, Number(e.target.value) / 100)}
                    className="h-1 flex-1 accent-[var(--accent)]"
                  />
                </div>
              ) : null}

              {open ? (
                <dl className="mt-2 flex flex-col gap-1 border-t border-border pt-2 pl-[26px] text-[11.5px]">
                  {(['low', 'mid', 'high'] as const).map((band) => (
                    <div key={band} className="flex gap-2">
                      <dt className="w-10 shrink-0 capitalize text-[var(--ui-faint)]">{band}</dt>
                      <dd className="text-[var(--ui-soft)]">{comp.anchors[band]}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Panel ──────────────────────────────────────────────────────────────── */

export default function RoundInspector({
  round,
  roleModel,
  editable,
  saving,
  onPatch,
  onCriteria,
}: Props): JSX.Element {
  const meta = ROUND_KIND_META[round.kind];
  const Icon = meta.icon;

  // Local text state so typing does not fire a request per keystroke; committed
  // on blur. Keyed by round id so selecting another round resets it.
  const [title, setTitle] = useState(round.title);
  const [titleFor, setTitleFor] = useState(round.id);
  if (titleFor !== round.id) {
    setTitleFor(round.id);
    setTitle(round.title);
  }

  const field =
    'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-60';

  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[var(--ui-inset)]">
          <Icon className="h-4 w-4 text-foreground" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {editable ? (
              <select
                aria-label="Round type"
                value={round.kind}
                onChange={(e) => onPatch({ kind: e.target.value as RoundKind })}
                className="rounded-[8px] border border-border bg-secondary px-2 py-1 text-[12px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              >
                {ROUND_KIND_ORDER.map((k) => (
                  <option key={k} value={k}>
                    {ROUND_KIND_META[k].label}
                  </option>
                ))}
              </select>
            ) : (
              <StatusTag tone={meta.tone}>{meta.label}</StatusTag>
            )}
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--ui-faint)]" aria-hidden="true" />
            ) : null}
          </div>
          <p className="mt-1 text-[11.5px] leading-snug text-muted-foreground">{meta.blurb}</p>
          {editable ? (
            <p className="mt-1 text-[11px] leading-snug text-[var(--ui-faint)]">
              Changing the type clears what no longer applies — questions, threshold or time
              limit — and keeps the rest.
            </p>
          ) : null}
        </div>
      </header>

      {/* 1. What it is */}
      <section className="flex flex-col gap-2.5">
        <label htmlFor="round-title" className="text-[12px] font-medium text-[var(--ui-soft)]">
          Round name
        </label>
        <input
          id="round-title"
          value={title}
          disabled={!editable}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => {
            const next = title.trim();
            if (next && next !== round.title) onPatch({ title: next });
          }}
          className={field}
        />

        <div className="grid grid-cols-2 gap-2.5">
          <div>
            <label htmlFor="deadline" className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]">
              Days to complete
            </label>
            <input
              id="deadline"
              type="number"
              min={1}
              max={365}
              disabled={!editable}
              value={round.deadline_days}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (n >= 1 && n <= 365) onPatch({ deadline_days: n });
              }}
              className={field}
            />
          </div>
          <div>
            <label htmlFor="timelimit" className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]">
              Time limit (min)
            </label>
            <input
              id="timelimit"
              type="number"
              min={1}
              placeholder="none"
              disabled={!editable}
              value={
                round.time_limit_seconds === null ? '' : Math.round(round.time_limit_seconds / 60)
              }
              onChange={(e) => {
                const v = e.target.value.trim();
                onPatch({ time_limit_seconds: v === '' ? null : Number(v) * 60 });
              }}
              className={field}
            />
          </div>
        </div>
      </section>

      {/* 2. What it asks */}
      {meta.needsExam ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-[12px] font-medium text-[var(--ui-soft)]">Questions</h3>
          <ExamPicker
            value={round.exam_round_id}
            editable={editable}
            onChange={(id) => onPatch({ exam_round_id: id })}
          />
        </section>
      ) : null}

      {meta.supportsCriteria ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-[12px] font-medium text-[var(--ui-soft)]">
            {round.kind === 'human_review'
              ? `Reviewer’s checklist (${round.criteria.length})`
              : `What this round assesses (${round.criteria.length})`}
          </h3>
          <CriteriaPicker
            round={round}
            roleModel={roleModel}
            editable={editable}
            onCriteria={onCriteria}
          />
        </section>
      ) : null}

      {/* 3. What it means */}
      {meta.needsThreshold ? (
        <section>
          <label htmlFor="threshold" className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]">
            Advance at or above
          </label>
          <div className="flex items-center gap-2">
            <input
              id="threshold"
              type="number"
              min={0}
              max={100}
              disabled={!editable}
              value={round.pass_threshold ?? ''}
              placeholder="required"
              onChange={(e) => {
                const v = e.target.value.trim();
                onPatch({ pass_threshold: v === '' ? null : Number(v) });
              }}
              className={cn(field, 'w-24')}
            />
            <span className="text-[13px] text-muted-foreground">%</span>
          </div>
          <p className="mt-1.5 text-[11.5px] leading-relaxed text-muted-foreground">
            {/* Said plainly because it is the single most common thing people
                assume a threshold does, and it does not. */}
            Scoring below this does not reject anyone. It puts the candidate in your
            decision queue for a person to look at.
          </p>
        </section>
      ) : (
        <section className="rounded-[10px] border border-border p-3 text-[11.5px] leading-relaxed text-muted-foreground">
          A human review round has no threshold — nothing here is scored. Candidates
          who reach it appear in your decision queue with the checklist above; someone
          passes them on or holds them for a decision. Nobody is rejected automatically.
        </section>
      )}
    </div>
  );
}
