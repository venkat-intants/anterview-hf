// EvidenceTrail (/hr/enrolments/:enrolmentId/evidence) — PH5-E5. A LIST, not
// a node-link diagram: what existed when one hire/reject decision was
// recorded, and what came after. hr_manager only.
//
// WORDING, non-negotiable: the section below is "Available when this was
// decided", never "what the decision was based on" — the system can prove
// what existed by timestamp; it cannot prove what the decider actually read.
// English-only by design (CLAUDE.md — the HR console is not translated).

import { useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ApiError, isTransientApiError } from '@/api/client';
import {
  getDecisionTrace,
  getEvidenceGraph,
  PRODUCED_BY_LABELS,
  STAGE_LABELS,
  STAGE_ORDER,
  type EvidenceNode,
  type EvidenceNodeContent,
  type EvidenceStage,
  type ProducedBy,
  type TraceEvidenceItem,
} from '@/api/evidence';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Loader2 } from '@/design/components/icons';

const KIND_LABELS: Record<EvidenceNode['kind'], string> = {
  application: 'Application',
  screening_ats: 'Resume screening',
  screening_answers: 'Screening answers',
  stage_move: 'Stage move',
  exam_attempt: 'Exam attempt',
  round_result: 'Round result',
  ai_interview: 'AI interview',
  interview_session: 'Interview session',
  human_scorecard: 'Interviewer scorecard',
  task_submission: 'Task submission',
  offer: 'Offer',
  decision: 'Decision',
};

const PRODUCED_BY_TONE: Record<ProducedBy, TagTone> = {
  human: 'electric',
  ai: 'lavender',
  candidate: 'forest',
  system: 'neutral',
};

function asStr(v: unknown): string | null {
  return typeof v === 'string' && v ? v : null;
}
function asNum(v: unknown): number | null {
  return typeof v === 'number' ? v : null;
}
function asBool(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null;
}

/** A short, defensive one-line summary of a node's content — varies by
 *  `kind` (see evidence_graph.py's node builders). Never renders raw JSON;
 *  an unrecognised or absent field is simply left out of the line. */
function contentSummary(kind: EvidenceNode['kind'], content: EvidenceNodeContent): string | null {
  const parts: (string | null)[] = (() => {
    switch (kind) {
      case 'application':
        return [
          asStr(content.source),
          asStr(content.status_now) ? `now ${asStr(content.status_now)}` : null,
        ];
      case 'screening_ats': {
        const overall = asNum(content.ats_overall);
        return [
          overall !== null ? `Resume match ${overall}/100` : null,
          asStr(content.ats_recommendation),
        ];
      }
      case 'screening_answers': {
        const count = asNum(content.count);
        return [count !== null ? `${count} answer${count === 1 ? '' : 's'}` : null];
      }
      case 'stage_move': {
        const from = asStr(content.from_status);
        const to = asStr(content.to_status);
        return [from && to ? `${from} → ${to}` : to];
      }
      case 'exam_attempt': {
        const pct = asNum(content.score_percent);
        const passed = asBool(content.passed);
        return [
          asStr(content.exam),
          pct !== null ? `${pct}%` : null,
          passed === null ? null : passed ? 'passed' : 'held',
        ];
      }
      case 'round_result': {
        const pct = asNum(content.percent);
        const passed = asBool(content.passed);
        return [
          pct !== null ? `${pct}%` : null,
          passed === null ? null : passed ? 'advanced' : 'held',
        ];
      }
      case 'ai_interview': {
        const composite = asNum(content.composite_0_10);
        return [asStr(content.status), composite !== null ? `${composite}/10` : null];
      }
      case 'interview_session':
        return [asStr(content.title), asStr(content.status)];
      case 'human_scorecard': {
        const interviewer = asStr(content.interviewer);
        return [interviewer ? `By ${interviewer}` : null, asStr(content.summary)];
      }
      case 'task_submission':
        return [asStr(content.status)];
      case 'offer':
        return [asStr(content.status)];
      case 'decision':
        return [asStr(content.outcome), asStr(content.reason_label)];
      default:
        return [];
    }
  })();
  const text = parts.filter((p): p is string => Boolean(p)).join(' · ');
  return text || null;
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function EvidenceRow({
  item,
  showChangeNote,
}: {
  item: TraceEvidenceItem;
  showChangeNote: boolean;
}) {
  const node = item.node;
  const summary = node.content ? contentSummary(node.kind, node.content) : null;
  return (
    <li className="rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <StatusTag tone={PRODUCED_BY_TONE[node.provenance.produced_by]}>
            {PRODUCED_BY_LABELS[node.provenance.produced_by]}
          </StatusTag>
          <span className="text-[13px] font-medium text-foreground">{KIND_LABELS[node.kind]}</span>
        </div>
        <Link
          to={node.href}
          className="text-[12px] text-[var(--ui-info)] underline decoration-dotted underline-offset-2 hover:opacity-80"
        >
          Open
        </Link>
      </div>
      <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
        {formatDateTime(node.occurred_at)}
        {node.provenance.actor?.name ? ` · ${node.provenance.actor.name}` : ''}
        {node.lifecycle !== 'live' ? ` · ${node.lifecycle}` : ''}
      </p>
      {node.content_hidden_reason ? (
        <p className="mt-1.5 text-[12px] italic text-[var(--ui-faint)]">
          {node.content_hidden_reason}
        </p>
      ) : summary ? (
        <p className="mt-1.5 text-[12.5px] text-[var(--ui-soft)]">{summary}</p>
      ) : null}
      {showChangeNote && item.changed_after_decision ? (
        <p className="mt-1.5 text-[11.5px] text-[var(--ui-warn)]">{item.changed_after_decision}</p>
      ) : null}
    </li>
  );
}

export default function EvidenceTrail(): JSX.Element {
  const { enrolmentId } = useParams<{ enrolmentId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const graph = useQuery({
    queryKey: ['hr', 'evidence-graph', enrolmentId],
    queryFn: () => getEvidenceGraph(enrolmentId as string),
    enabled: Boolean(enrolmentId),
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  const decisions = useMemo(
    () =>
      [...(graph.data?.decisions ?? [])].sort(
        (a, b) => new Date(b.decided_at).getTime() - new Date(a.decided_at).getTime(),
      ),
    [graph.data],
  );

  const requestedDecision = searchParams.get('decision');
  const selectedDecisionId = useMemo(() => {
    const requested = requestedDecision ? Number(requestedDecision) : null;
    if (requested !== null && decisions.some((d) => d.id === requested)) return requested;
    return decisions[0]?.id ?? null;
  }, [requestedDecision, decisions]);

  const trace = useQuery({
    queryKey: ['hr', 'decision-trace', selectedDecisionId],
    queryFn: () => {
      // `enabled` below guarantees this never runs while null; the guard is
      // for the type checker, not a real runtime path.
      if (selectedDecisionId === null) throw new Error('No decision selected.');
      return getDecisionTrace(selectedDecisionId);
    },
    enabled: selectedDecisionId !== null,
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  const notFound = graph.error instanceof ApiError && graph.error.status === 404;

  const groupedEvidence = useMemo(() => {
    const byStage = new Map<EvidenceStage, TraceEvidenceItem[]>();
    for (const item of trace.data?.evidence ?? []) {
      const list = byStage.get(item.node.stage.name) ?? [];
      list.push(item);
      byStage.set(item.node.stage.name, list);
    }
    return STAGE_ORDER.map((stage) => ({ stage, items: byStage.get(stage) ?? [] })).filter(
      (g) => g.items.length > 0,
    );
  }, [trace.data]);

  return (
    <div className="mx-auto max-w-[880px] px-6 py-8 lg:px-8">
      <h1 className="text-[26px] font-semibold tracking-[-1px] text-foreground">Evidence trail</h1>
      <p className="mt-1 text-[13px] text-muted-foreground">
        This shows what existed when this was decided. It cannot show what the decider actually
        read.
      </p>

      {graph.isLoading ? (
        <div className="mt-6 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </div>
      ) : notFound ? (
        <p className="mt-6 text-[13px] text-muted-foreground">
          This application could not be found, or you do not have access to it.
        </p>
      ) : graph.isError ? (
        <p className="mt-6 text-[13px] text-[var(--ui-danger)]">Could not load this application.</p>
      ) : graph.data ? (
        <div className="mt-5 flex flex-col gap-5">
          <GlassCard className="p-4">
            <p className="text-[15px] font-semibold text-foreground">
              {graph.data.candidate.name}
              {graph.data.candidate.erased ? (
                <span className="ml-2 text-[12px] font-normal text-[var(--ui-faint)]">
                  (erased)
                </span>
              ) : null}
            </p>
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">
              {graph.data.requisition.title ?? 'No opening on file'}
              {graph.data.workflow.version ? ` · workflow v${graph.data.workflow.version}` : ''}
            </p>
          </GlassCard>

          {decisions.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              No hire or reject decision has been recorded for this application yet.
            </p>
          ) : (
            <>
              {decisions.length > 1 ? (
                <div className="flex flex-wrap gap-2" role="tablist" aria-label="Decision">
                  {decisions.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      role="tab"
                      aria-selected={d.id === selectedDecisionId}
                      onClick={() => setSearchParams({ decision: String(d.id) })}
                      className={`rounded-[10px] border px-3 py-1.5 text-[12.5px] ${
                        d.id === selectedDecisionId
                          ? 'border-[var(--accent)] text-foreground'
                          : 'border-border text-muted-foreground hover:border-[var(--ui-line-strong)]'
                      }`}
                    >
                      {d.outcome === 'hired' ? 'Hired' : 'Rejected'} ·{' '}
                      {formatDateTime(d.decided_at)}
                    </button>
                  ))}
                </div>
              ) : null}

              {trace.isLoading ? (
                <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Loading…
                </div>
              ) : trace.isError ? (
                <p className="text-[13px] text-[var(--ui-danger)]">Could not load this decision.</p>
              ) : trace.data ? (
                <>
                  <GlassCard className="p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusTag
                        tone={trace.data.decision.outcome === 'hired' ? 'forest' : 'ember'}
                      >
                        {trace.data.decision.outcome === 'hired' ? 'Hired' : 'Rejected'}
                      </StatusTag>
                      {trace.data.decision.reversal ? (
                        <StatusTag tone="amber">reverses a hire</StatusTag>
                      ) : null}
                    </div>
                    <p className="mt-2 text-[13px] text-foreground">
                      {trace.data.decision.decided_by.name ?? 'A reviewer'}
                      <span className="text-muted-foreground">
                        {' '}
                        · {formatDateTime(trace.data.decision.decided_at)}
                      </span>
                    </p>
                    {trace.data.decision.reason_label || trace.data.decision.reason ? (
                      <p className="mt-1.5 text-[12.5px] text-[var(--ui-soft)]">
                        {trace.data.decision.reason_label}
                        {trace.data.decision.reason_label && trace.data.decision.reason ? ': ' : ''}
                        {trace.data.decision.reason}
                      </p>
                    ) : null}
                    <p className="mt-2.5 text-[12px] text-muted-foreground">
                      {trace.data.ai_involvement.ai_produced_evidence === 0
                        ? 'No evidence available when this was decided was produced by AI.'
                        : trace.data.ai_involvement.ai_produced_evidence === 1
                          ? '1 piece of evidence was produced by AI.'
                          : `${trace.data.ai_involvement.ai_produced_evidence} pieces of evidence were produced by AI.`}{' '}
                      A named person made the decision.
                    </p>
                  </GlassCard>

                  <div>
                    <h2 className="text-[15px] font-semibold text-foreground">
                      Available when this was decided
                    </h2>
                    {groupedEvidence.length === 0 ? (
                      <p className="mt-2 text-[13px] text-muted-foreground">
                        Nothing was recorded before this decision.
                      </p>
                    ) : (
                      <div className="mt-3 flex flex-col gap-4">
                        {groupedEvidence.map((g) => (
                          <div key={g.stage}>
                            <h3 className="text-[12px] font-medium uppercase tracking-wide text-[var(--ui-faint)]">
                              {STAGE_LABELS[g.stage]}
                            </h3>
                            <ul className="mt-1.5 flex flex-col gap-2">
                              {g.items.map((item) => (
                                <EvidenceRow key={item.node.id} item={item} showChangeNote />
                              ))}
                            </ul>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {trace.data.after_decision.length > 0 ? (
                    <details className="rounded-[12px] border border-border p-3">
                      <summary className="cursor-pointer text-[13px] font-medium text-foreground">
                        Recorded after the decision ({trace.data.after_decision.length})
                      </summary>
                      <ul className="mt-2 flex flex-col gap-2">
                        {trace.data.after_decision.map((a) => (
                          <EvidenceRow
                            key={a.node.id}
                            item={{ node: a.node, path: [], changed_after_decision: a.reason }}
                            showChangeNote={false}
                          />
                        ))}
                      </ul>
                    </details>
                  ) : null}

                  {trace.data.omitted.length > 0 ? (
                    <p className="text-[11.5px] leading-relaxed text-[var(--ui-faint)]">
                      Not shown here:{' '}
                      {trace.data.omitted.map((o, i) => (
                        <span key={o.kind}>
                          {i > 0 ? '; ' : ''}
                          {o.kind.replace(/_/g, ' ')} — {o.reason}
                        </span>
                      ))}
                    </p>
                  ) : null}
                </>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
