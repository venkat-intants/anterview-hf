// WorkflowBuilder — D1–D5. Where an opening gets its own hiring process.
//
// The product idea in one line: every job can have its own workflow, configured
// by HR, and candidates walk it without anyone chasing them. This page is where
// that workflow is authored — a canvas of rounds, a panel for whichever one is
// selected, and a live read of whether the process actually measures the job.
//
// THE DRAFT / PUBLISHED SPLIT (D5) is the spine of this screen and is worth
// stating plainly, because every other decision here follows from it:
//
//   A published workflow is immutable. Editing it clones it to version n+1 and
//   leaves the original running. That is not caution for its own sake — people
//   are mid-process inside it. Someone who passed round 2 under a 60% threshold
//   must not find themselves retroactively below a 70% one, and someone
//   interviewed against four competencies must not be scored against six. The
//   old version stays live until its last candidate finishes.
//
// So the canvas is editable when `workflow.editable` is true and read-only
// otherwise, and there is exactly one way from read-only back to editable:
// clone. No inline "unlock", no override.
//
// Every mutation returns the WHOLE workflow and is written straight into the
// query cache. Positions, the next-round chain and `needs_questions` all shift
// when a round moves; reassembling that client-side is how a canvas drifts out
// of step with what the server will actually run.

import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Copy,
  Eye,
  Loader2,
  Sparkles,
  Trash2,
  Users,
} from '@/design/components/icons';
import { GlassCard, StatusTag, ToggleSwitch } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { getRequisition } from '@/api/requisitions';
import {
  addRound,
  cloneWorkflow,
  discardDraft,
  getRoleModel,
  getWorkflow,
  listWorkflows,
  publishWorkflow,
  removeRound,
  reorderRounds,
  setRoundCriteria,
  startDraft,
  updateRound,
  updateSettings,
  validateWorkflow,
  MAX_ROUNDS,
  type CriterionInput,
  type Round,
  type RoundKind,
  type Workflow,
  type WorkflowSettings,
} from '@/api/workflows';
import WorkflowCanvas from '@/components/workflow/WorkflowCanvas';
import WorkflowCopilot from '@/components/workflow/WorkflowCopilot';
import ProposalPreview from '@/components/workflow/ProposalPreview';
import { getAgentStatus, type Proposal } from '@/api/agent';
import RoundInspector from '@/components/workflow/RoundInspector';
import CoveragePanel from '@/components/workflow/CoveragePanel';
import PostingEditor from '@/components/workflow/PostingEditor';
import QuestionEditor from '@/components/workflow/QuestionEditor';
import { ROUND_KIND_META, TEMPLATES } from '@/components/workflow/roundKinds';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/* ── Settings panel — C9 ─────────────────────────────────────────────────────
 *
 * Every switch here removes a chase-up. None of them removes a decision: there
 * is no auto-reject toggle because the field does not exist on the server, and
 * the settings allow-list lives in the service layer, so nothing this form
 * could send would introduce one (D-05).
 */
const AUTOMATIONS: {
  key: keyof WorkflowSettings;
  label: string;
  help: string;
}[] = [
  {
    key: 'auto_score_on_apply',
    label: 'Score resumes on arrival',
    help: 'An applicant is ATS-scored the moment they apply, not when someone opens the list.',
  },
  {
    key: 'auto_assign_first_round',
    label: 'Send the first round automatically',
    help: 'A shortlisted candidate is invited to round one without anyone pressing send.',
  },
  {
    key: 'auto_advance_rounds',
    label: 'Advance between rounds automatically',
    help: 'Clearing a round sends the next one. Anyone below a threshold still waits for you.',
  },
  {
    key: 'reminders_enabled',
    label: 'Remind candidates',
    help: 'Nudge before a deadline and tell them when a link has expired.',
  },
];

function SettingsPanel({
  workflow,
  editable,
  onPatch,
}: {
  workflow: Workflow;
  editable: boolean;
  onPatch: (fields: Partial<WorkflowSettings>) => void;
}) {
  const s = workflow.settings;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        {AUTOMATIONS.map((a) => (
          <div key={a.key} className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] text-foreground">{a.label}</div>
              <div className="mt-0.5 text-[11.5px] leading-snug text-muted-foreground">{a.help}</div>
            </div>
            <div className={cn(!editable && 'pointer-events-none opacity-50')}>
              <ToggleSwitch
                checked={Boolean(s[a.key])}
                onChange={(next) => onPatch({ [a.key]: next } as Partial<WorkflowSettings>)}
                label={a.label}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-border pt-4">
        <label htmlFor="hold-band" className="block text-[13px] text-foreground">
          Send to you rather than past you
        </label>
        <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted-foreground">
          A candidate scoring within this many points below a round&rsquo;s threshold is held
          for your decision instead of quietly stalling. Nobody is ever rejected
          automatically — this only decides who lands in front of you.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <input
            id="hold-band"
            type="number"
            min={0}
            max={100}
            disabled={!editable}
            value={s.hold_band ?? ''}
            onChange={(e) => {
              const v = e.target.value.trim();
              onPatch({ hold_band: v === '' ? null : Number(v) });
            }}
            className="w-24 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
          />
          <span className="text-[13px] text-muted-foreground">points</span>
        </div>
      </div>

      <div className="border-t border-border pt-4">
        <label htmlFor="ats-threshold" className="block text-[13px] text-foreground">
          Auto-shortlist at ATS score
        </label>
        <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted-foreground">
          Applicants at or above this are shortlisted and enter the workflow. Leave empty
          to shortlist everyone by hand.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <input
            id="ats-threshold"
            type="number"
            min={0}
            max={10}
            disabled={!editable}
            value={s.shortlist_ats_threshold ?? ''}
            placeholder="off"
            onChange={(e) => {
              const v = e.target.value.trim();
              onPatch({ shortlist_ats_threshold: v === '' ? null : Number(v) });
            }}
            className="w-24 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
          />
          <span className="text-[13px] text-muted-foreground">/ 10</span>
        </div>
      </div>
    </div>
  );
}

/* ── The three ways in — D4 ───────────────────────────────────────────────── */

function EmptyState({
  onBlank,
  onTemplate,
  busy,
}: {
  onBlank: () => void;
  onTemplate: (key: string) => void;
  busy: boolean;
}) {
  return (
    <GlassCard className="p-8">
      <h2 className="text-[18px] font-semibold text-foreground">Give this opening a process</h2>
      <p className="mt-1.5 max-w-[62ch] text-[13.5px] leading-relaxed text-muted-foreground">
        Start from a shape that fits and change anything you like, or build it from
        nothing. Either way it stays a draft until you publish it, and no candidate sees
        it before then.
      </p>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        {TEMPLATES.map((t) => (
          <button
            key={t.key}
            type="button"
            disabled={busy}
            onClick={() => onTemplate(t.key)}
            className="flex flex-col rounded-[16px] border border-border p-4 text-left transition-colors hover:border-[var(--accent)]/50 hover:bg-[var(--ui-inset-soft)] disabled:opacity-50"
          >
            <span className="text-[14px] font-medium text-foreground">{t.name}</span>
            <span className="mt-1 text-[12px] leading-snug text-muted-foreground">
              {t.description}
            </span>
            <span className="mt-3 flex flex-wrap gap-1">
              {t.rounds.map((r, i) => (
                <span
                  key={`${r.kind}-${i}`}
                  className="rounded-pill border border-border px-2 py-0.5 text-[10.5px] text-[var(--ui-soft)]"
                >
                  {ROUND_KIND_META[r.kind].label}
                </span>
              ))}
            </span>
          </button>
        ))}
      </div>

      <button
        type="button"
        disabled={busy}
        onClick={onBlank}
        className="mt-4 inline-flex items-center gap-1.5 text-[13px] text-[var(--accent)] hover:underline disabled:opacity-50"
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
        Start from an empty canvas instead
      </button>
    </GlassCard>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function WorkflowBuilder(): JSX.Element {
  const { requisitionId = '' } = useParams();
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedRound, setSelectedRound] = useState<string | null>(null);
  const [tab, setTab] = useState<'round' | 'settings'>('round');
  const [confirmPublish, setConfirmPublish] = useState(false);
  // What the copilot has drafted and the user has not yet accepted or
  // discarded. Held here rather than inside the chat panel because the preview
  // is drawn on the canvas, and only one of the two surfaces can own it.
  const [pending, setPending] = useState<Proposal[]>([]);

  const req = useQuery({
    queryKey: ['hr', 'requisition', requisitionId],
    queryFn: () => getRequisition(requisitionId),
    enabled: Boolean(requisitionId),
  });

  const versions = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId),
    enabled: Boolean(requisitionId),
  });

  // Default to the draft when one exists — that is what someone came here to
  // work on. Otherwise the live version, so opening the page shows what
  // candidates are actually walking rather than an empty canvas.
  const defaultId = useMemo(() => {
    const list = versions.data ?? [];
    return (
      list.find((w) => w.status === 'draft')?.id ??
      list.find((w) => w.status === 'published')?.id ??
      null
    );
  }, [versions.data]);

  const workflowId = activeId ?? defaultId;

  const wf = useQuery({
    queryKey: ['hr', 'workflow', workflowId],
    queryFn: () => getWorkflow(workflowId as string),
    enabled: Boolean(workflowId),
  });

  const validation = useQuery({
    queryKey: ['hr', 'workflow', workflowId, 'validate'],
    queryFn: () => validateWorkflow(workflowId as string),
    enabled: Boolean(workflowId) && (wf.data?.rounds.length ?? 0) > 0,
  });

  const roleModel = useQuery({
    queryKey: ['hr', 'role-model', requisitionId],
    queryFn: () => getRoleModel(requisitionId),
    enabled: Boolean(requisitionId),
  });

  // The copilot is offered only when the deployment actually has one. Without
  // this the panel renders, the user types, and every message fails — which
  // reads as the product being broken rather than the feature being off.
  const agent = useQuery({ queryKey: ['agent', 'status'], queryFn: getAgentStatus });
  const copilotAvailable =
    Boolean(agent.data?.enabled) && (agent.data?.surfaces ?? []).includes('workflow_builder');

  const workflow = wf.data;
  const editable = workflow?.editable ?? false;

  /** Every round mutation lands here: cache the returned workflow, recheck. */
  const applyWorkflow = (next: Workflow) => {
    qc.setQueryData(['hr', 'workflow', next.id], next);
    void qc.invalidateQueries({ queryKey: ['hr', 'workflow', next.id, 'validate'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
  };

  const mutating = useMutation({
    mutationFn: async (op: () => Promise<Workflow>) => op(),
    onSuccess: applyWorkflow,
    onError: (e) => toast.error(errText(e, 'That change did not save')),
  });

  const run = (op: () => Promise<Workflow>) => mutating.mutate(op);

  // ── Creating ──────────────────────────────────────────────────────────────
  const createMut = useMutation({
    mutationFn: async (templateKey: string | null) => {
      const tpl = templateKey ? TEMPLATES.find((t) => t.key === templateKey) : null;
      let draft = await startDraft(requisitionId, tpl?.name);
      // Sequential, not parallel: rounds are positioned in insert order, and
      // firing four concurrent POSTs would race for those positions.
      for (const r of tpl?.rounds ?? []) {
        draft = await addRound(draft.id, {
          title: r.title,
          kind: r.kind,
          pass_threshold: r.pass_threshold,
        });
      }
      return draft;
    },
    onSuccess: (draft) => {
      setActiveId(draft.id);
      setSelectedRound(draft.rounds[0]?.id ?? null);
      applyWorkflow(draft);
      toast.success('Draft created');
    },
    onError: (e) => toast.error(errText(e, 'Could not start a draft')),
  });

  const publishMut = useMutation({
    mutationFn: () => publishWorkflow(workflowId as string),
    onSuccess: (res) => {
      setConfirmPublish(false);
      applyWorkflow(res);
      toast.success(`Version ${res.version} is live`);
    },
    onError: (e) => {
      setConfirmPublish(false);
      // The 422 carries the reasons. Refetching validate puts them in the panel
      // where they can be acted on, rather than in a toast that scrolls away.
      void qc.invalidateQueries({ queryKey: ['hr', 'workflow', workflowId, 'validate'] });
      toast.error(errText(e, 'This workflow is not ready to publish'));
    },
  });

  const cloneMut = useMutation({
    mutationFn: () => cloneWorkflow(workflowId as string),
    onSuccess: (draft) => {
      setActiveId(draft.id);
      applyWorkflow(draft);
      toast.success(`Editing as version ${draft.version} — version ${draft.version - 1} stays live`);
    },
    onError: (e) => toast.error(errText(e, 'Could not open a new version')),
  });

  const discardMut = useMutation({
    mutationFn: () => discardDraft(workflowId as string),
    onSuccess: () => {
      setActiveId(null);
      setSelectedRound(null);
      void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
      toast.success('Draft discarded');
    },
    onError: (e) => toast.error(errText(e, 'Could not discard this draft')),
  });

  const selected: Round | undefined = workflow?.rounds.find((r) => r.id === selectedRound);
  const busy = mutating.isPending || createMut.isPending;

  // ── Render ────────────────────────────────────────────────────────────────
  if (!requisitionId) {
    return <div className="p-8 text-[13px] text-muted-foreground">No opening selected.</div>;
  }

  return (
    <div className="mx-auto w-full max-w-[1280px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <Link
            to="/hr/requisitions"
            className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            All openings
          </Link>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-[24px] font-semibold tracking-[-0.8px] text-foreground">
                {req.data?.title ?? 'Hiring workflow'}
              </h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground">
                {workflow ? (
                  <>
                    <StatusTag
                      tone={
                        workflow.status === 'published'
                          ? 'forest'
                          : workflow.status === 'draft'
                            ? 'amber'
                            : 'neutral'
                      }
                      dot
                    >
                      {workflow.status === 'published'
                        ? `v${workflow.version} · live`
                        : workflow.status === 'draft'
                          ? `v${workflow.version} · draft`
                          : `v${workflow.version} · archived`}
                    </StatusTag>
                    <span>
                      {workflow.rounds.length} round{workflow.rounds.length === 1 ? '' : 's'}
                    </span>
                  </>
                ) : null}
                {req.data ? (
                  <span>
                    {req.data.total_enrolments} candidate
                    {req.data.total_enrolments === 1 ? '' : 's'}
                  </span>
                ) : null}
              </div>
            </div>

            {workflow ? (
              <div className="flex flex-wrap items-center gap-2">
                {(versions.data ?? []).length > 1 ? (
                  <select
                    aria-label="Workflow version"
                    value={workflow.id}
                    onChange={(e) => {
                      setActiveId(e.target.value);
                      setSelectedRound(null);
                    }}
                    className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                  >
                    {(versions.data ?? []).map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version} · {v.status}
                        {v.enrolled_candidates > 0 ? ` · ${v.enrolled_candidates} inside` : ''}
                      </option>
                    ))}
                  </select>
                ) : null}

                <Link
                  to={`/hr/requisitions/${requisitionId}/decisions`}
                  className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)] hover:text-foreground"
                >
                  <Users className="h-3.5 w-3.5" aria-hidden="true" />
                  Decision queue
                </Link>

                {editable ? (
                  <>
                    <button
                      type="button"
                      onClick={() => discardMut.mutate()}
                      disabled={discardMut.isPending}
                      className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] px-3 py-2 text-[12.5px] text-muted-foreground hover:border-[var(--ui-danger)]/40 hover:text-[var(--ui-danger)] disabled:opacity-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      Discard
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmPublish(true)}
                      disabled={publishMut.isPending || workflow.rounds.length === 0}
                      className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                    >
                      {publishMut.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      Publish
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => cloneMut.mutate()}
                    disabled={cloneMut.isPending}
                    className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                  >
                    {cloneMut.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    Edit as new version
                  </button>
                )}
              </div>
            ) : null}
          </div>
        </header>
      </Reveal>

      {/* Publish confirmation. Worth an extra step: publishing starts sending
          real invitations to real people. */}
      {confirmPublish && workflow ? (
        <GlassCard className="mb-5 border-[var(--accent)]/30 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Sparkles className="h-4 w-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-[13px] leading-relaxed text-[var(--ui-soft)]">
              Publishing makes this version live for {req.data?.title ?? 'this opening'}.
              New candidates start here; anyone already inside an older version finishes
              it on the rounds they began with.
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => publishMut.mutate()}
                className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground hover:opacity-90"
              >
                Publish version {workflow.version}
              </button>
              <button
                type="button"
                onClick={() => setConfirmPublish(false)}
                className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
              >
                Not yet
              </button>
            </div>
          </div>
        </GlassCard>
      ) : null}

      {versions.isLoading || (workflowId && wf.isLoading) ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading workflow…
        </div>
      ) : !workflow ? (
        // Three ways in, side by side (D4): a template, an empty canvas, or a
        // conversation. The copilot belongs most here — "design a process for
        // this role" is the question someone opening a blank builder has.
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
          <EmptyState
            busy={createMut.isPending}
            onBlank={() => createMut.mutate(null)}
            onTemplate={(key) => createMut.mutate(key)}
          />
          {copilotAvailable ? (
            <GlassCard className="flex h-[440px] flex-col p-5">
              <WorkflowCopilot
                requisitionId={requisitionId}
                jobTitle={req.data?.title ?? 'this role'}
                editable
                hasRounds={false}
                onProposals={(ps) => setPending(ps)}
              />
            </GlassCard>
          ) : null}
          {pending.map((p) => (
            <ProposalPreview
              key={p.id}
              proposal={p}
              existingRounds={0}
              onApplied={() => {
                setPending((prev) => prev.filter((x) => x.id !== p.id));
                void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
              }}
              onDismiss={() => setPending((prev) => prev.filter((x) => x.id !== p.id))}
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
          {/* Canvas */}
          <GlassCard className="p-5">
            {!editable ? (
              <div className="mb-4 flex items-start gap-2 rounded-[12px] border border-border bg-black/25 p-3 text-[12.5px] leading-relaxed text-muted-foreground">
                <Eye className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                {workflow.status === 'published'
                  ? 'This version is live, so it is read-only. Editing creates version ' +
                    (workflow.version + 1) +
                    ' and leaves this one running for everyone already inside it.'
                  : 'An archived version, kept because candidates finished under it.'}
              </div>
            ) : null}

            {/* D7 — drafts are previewed where they would land, above the
                rounds they would join, rather than as JSON in a side panel. */}
            {pending.map((p) => (
              <ProposalPreview
                key={p.id}
                proposal={p}
                existingRounds={workflow.rounds.length}
                onApplied={() => {
                  setPending((prev) => prev.filter((x) => x.id !== p.id));
                  void qc.invalidateQueries({ queryKey: ['hr', 'workflow', workflow.id] });
                  void qc.invalidateQueries({
                    queryKey: ['hr', 'workflow', workflow.id, 'validate'],
                  });
                  void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
                }}
                onDismiss={() => setPending((prev) => prev.filter((x) => x.id !== p.id))}
              />
            ))}

            <WorkflowCanvas
              rounds={workflow.rounds}
              selectedId={selectedRound}
              editable={editable}
              busy={busy}
              onSelect={(id) => {
                setSelectedRound(id);
                setTab('round');
              }}
              onAdd={(kind: RoundKind) => {
                const meta = ROUND_KIND_META[kind];
                run(() =>
                  addRound(workflow.id, {
                    title: meta.label,
                    kind,
                    // Prefilled so a new round is not born invalid; the
                    // inspector is where it gets a real number.
                    pass_threshold: meta.needsThreshold ? 60 : null,
                  }),
                );
              }}
              onRemove={(roundId) => {
                if (roundId === selectedRound) setSelectedRound(null);
                run(() => removeRound(workflow.id, roundId));
              }}
              onReorder={(ids) => run(() => reorderRounds(workflow.id, ids))}
            />

            {workflow.rounds.length >= MAX_ROUNDS ? (
              <p className="mt-3 flex items-center gap-1.5 text-[12px] text-[var(--ui-warn)]">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                {MAX_ROUNDS} rounds is the maximum.
              </p>
            ) : null}
          </GlassCard>

          {/* Inspector + coverage */}
          <div className="flex flex-col gap-5">
            <GlassCard className="p-5">
              <div className="mb-4 flex gap-1 rounded-pill border border-border bg-secondary p-1">
                {(
                  [
                    ['round', selected ? 'Round' : 'Select a round'],
                    ['settings', 'Automation'],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTab(key)}
                    aria-selected={tab === key}
                    role="tab"
                    className={cn(
                      'flex-1 rounded-pill px-3 py-1.5 text-[12.5px] font-medium transition-colors',
                      tab === key ? 'bg-primary text-primary-foreground' : 'text-[var(--ui-soft)] hover:text-foreground',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {tab === 'settings' ? (
                <SettingsPanel
                  workflow={workflow}
                  editable={editable}
                  onPatch={(fields) => run(() => updateSettings(workflow.id, fields))}
                />
              ) : selected ? (
                <RoundInspector
                  key={selected.id}
                  round={selected}
                  roleModel={roleModel.data}
                  editable={editable}
                  saving={busy}
                  onPatch={(fields) => run(() => updateRound(workflow.id, selected.id, fields))}
                  onCriteria={(criteria: CriterionInput[]) =>
                    run(() => setRoundCriteria(workflow.id, selected.id, criteria))
                  }
                />
              ) : (
                <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                  Pick a round on the left to set what it asks and what score moves a
                  candidate on.
                </p>
              )}
            </GlassCard>

            <GlassCard className="p-5">
              <CoveragePanel report={validation.data} loading={validation.isFetching} />
            </GlassCard>

            {copilotAvailable ? (
              <GlassCard className="flex h-[440px] flex-col p-5">
                <WorkflowCopilot
                  requisitionId={requisitionId}
                  jobTitle={req.data?.title ?? 'this role'}
                  editable={editable}
                  hasRounds={workflow.rounds.length > 0}
                  onProposals={(ps) => setPending(ps)}
                />
              </GlassCard>
            ) : null}
          </div>
        </div>
      )}

      {/* ── The posting and its questions ──
          Not gated on `editable`. A published workflow is frozen because
          candidates are mid-process on it; the advert and the application
          questions are not part of anybody's assessment, and refusing to fix a
          typo in the location would be the immutability rule applied where it
          buys nothing. The question editor has its own, narrower freeze: a
          question somebody has answered cannot be reworded. */}
      {req.data ? (
        <div className="mt-8 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <PostingEditor requisition={req.data} />
          <QuestionEditor requisitionId={requisitionId} />
        </div>
      ) : null}
    </div>
  );
}
