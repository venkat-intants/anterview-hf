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
  CheckCircle2,
  Copy,
  Eye,
  Loader2,
  Play,
  Send,
  Sparkles,
  Users,
} from '@/design/components/icons';
import { GlassCard, StatusTag, ToggleSwitch, type TagTone } from '@/design/components/primitives';
import { Page } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { Section, Well } from '@/design/components/rows';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { EditorHeader, DocStatusPill } from '@/components/hr/EditorHeader';
import { toast } from '@/lib/toast';
import { queryKeys } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { getRequisition } from '@/api/requisitions';
import {
  addRound,
  cloneWorkflow,
  createFromTemplate,
  listWorkflowTemplates,
  type WorkflowTemplateSummary,
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
  type ReviewStatus,
  type Round,
  type RoundKind,
  type Workflow,
  type WorkflowSettings,
} from '@/api/workflows';
import { getReview, getSimulation, runSimulation } from '@/api/workflowReview';
import WorkflowCanvas from '@/components/workflow/WorkflowCanvas';
import WorkflowCopilot from '@/components/workflow/WorkflowCopilot';
import ProposalPreview from '@/components/workflow/ProposalPreview';
import { getAgentStatus, type Proposal } from '@/api/agent';
import RoundInspector from '@/components/workflow/RoundInspector';
import CoveragePanel from '@/components/workflow/CoveragePanel';
import CandidatePreview from '@/components/workflow/CandidatePreview';
import WorkflowTrack from '@/components/workflow/WorkflowTrack';
import SubmitBlockers from '@/components/workflow/SubmitBlockers';
import ReviewPanel from '@/components/workflow/ReviewPanel';
import DryRunPanel from '@/components/workflow/DryRunPanel';
import StageSettings from '@/components/workflow/StageSettings';
import { NumberField } from '@/components/workflow/NumberField';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { errorMessage } from '@/lib/errorMessage';
import { applicationsWarning, nextAction, publishImpact } from '@/lib/workflowLifecycle';
import { ROUND_KIND_META } from '@/components/workflow/roundKinds';

/** PH4-O6 — the badge next to a version's status, everywhere this page shows one. */
const REVIEW_META: Record<ReviewStatus, { label: string; tone: TagTone }> = {
  draft: { label: 'Draft', tone: 'neutral' },
  in_review: { label: 'In review', tone: 'electric' },
  changes_requested: { label: 'Changes requested', tone: 'amber' },
  approved: { label: 'Approved', tone: 'forest' },
};

type InspectorTab = 'round' | 'settings' | 'assistant';

/** What the right-hand sheet is showing: the submit step, or the review and dry-run record. */
type SheetKind = 'submit' | 'details' | null;

const PRIMARY_CLASS =
  'inline-flex min-h-[40px] items-center gap-1.5 rounded-full bg-primary px-5 py-2 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-40';

/** The shared error copy (errorMessage), with this page's period-less fallbacks. */
function errText(e: unknown, fallback: string): string {
  return errorMessage(e, `${fallback}.`);
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

/* ── Auto-shortlist — removing the human shortlist gate, on purpose ─────────── */

/**
 * THE ONE GATE HR CAN NOW SWITCH OFF, AND THE ONE IT CANNOT. Turning this on
 * means an applicant whose ATS clears the bar starts round one with no one
 * confirming. It only ever advances people: below the bar nothing happens, and
 * the final hire/reject is still a person's (D-05, CLAUDE.md #9).
 *
 * Disabled without a bar rather than allowed and refused: "shortlist
 * automatically" with no threshold would mean everyone, and the server refuses
 * that combination anyway. Saying so here is kinder than a failed save.
 */
function AutoShortlistToggle({
  on,
  bar,
  autoAssign,
  editable,
  onChange,
}: {
  on: boolean;
  bar: number | null;
  /** "Send the first round automatically". Required on: shortlisting someone
   *  automatically and then starting nothing would strand them. */
  autoAssign: boolean;
  editable: boolean;
  onChange: (next: boolean) => void;
}) {
  // A bar of 0 is no bar: `score >= 0` admits everyone, including an applicant
  // whose CV the scorer failed on and recorded as 0. Mirrors update_settings.
  const hasBar = bar !== null && bar >= 1;
  const ready = hasBar && autoAssign;
  // Turning it OFF is always allowed, whatever else is true — never trap a
  // setting in a state the server would refuse.
  const canToggle = editable && (ready || on);
  return (
    <div className="mt-4 flex items-start gap-3" data-testid="auto-shortlist-setting">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] text-foreground">Shortlist without my confirmation</div>
        <div className="mt-0.5 text-[12px] leading-snug text-muted-foreground">
          {!hasBar
            ? 'Set an ATS score of at least 10 above first - without one, every applicant would be shortlisted.'
            : !autoAssign
              ? 'Turn on “Send the first round automatically” first - otherwise applicants would be shortlisted and then left with nothing started.'
              : on
              ? `Applicants who applied through your careers page, scored ${bar * 10}/100 or higher and confirmed their email start round one with no one confirming them. Everyone else - below the bar, uploaded by HR, or not yet confirmed - stays in your queue. The final decision is still yours.`
              : 'Off: you confirm every shortlist. Turn on to let applicants above the bar start on their own.'}
        </div>
        {on && (
          <p className="mt-1.5 text-[11.5px] leading-snug text-[var(--ui-warn)]">
            This removes a human step, so a super admin sees it when approving this version.
          </p>
        )}
      </div>
      <ToggleSwitch
        checked={on}
        onChange={onChange}
        label="Shortlist without my confirmation"
        disabled={!canToggle}
      />
    </div>
  );
}

function SettingsPanel({
  workflow,
  editable,
  onPatch,
  resetKey,
}: {
  workflow: Workflow;
  editable: boolean;
  onPatch: (fields: Partial<WorkflowSettings>) => void;
  /** Bumped when a save fails, so number fields drop a refused value. */
  resetKey: number;
}) {
  const s = workflow.settings;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        {AUTOMATIONS.map((a) => (
          <div key={a.key} className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] text-foreground">{a.label}</div>
              <div className="mt-0.5 text-[12px] leading-snug text-muted-foreground">{a.help}</div>
            </div>
            <div className={cn(!editable && 'pointer-events-none opacity-50')}>
              <ToggleSwitch
                checked={Boolean(s[a.key])}
                onChange={(next) => onPatch({ [a.key]: next } as Partial<WorkflowSettings>)}
                label={a.label}
                disabled={!editable}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-border pt-4">
        <label htmlFor="hold-band" className="block text-[13px] text-foreground">
          Mark near misses within
        </label>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
          Everyone scoring below a round&rsquo;s threshold is held for your decision. Those within
          this many points are marked as a near miss, so you can see who was close. Nobody is ever
          rejected automatically.
        </p>
        <div className="mt-2 flex items-center gap-2">
          {/* HR-H2: saved on blur or Enter, after a range check, not per keystroke. */}
          <NumberField
            id="hold-band"
            min={0}
            max={100}
            disabled={!editable}
            value={s.hold_band}
            onCommit={(n) => {
              if (n !== null) onPatch({ hold_band: n });
            }}
            resetKey={resetKey}
            className="w-24"
          />
          <span className="text-[13px] text-muted-foreground">points</span>
        </div>
      </div>

      <div className="border-t border-border pt-4">
        <label htmlFor="ats-threshold" className="block text-[13px] text-foreground">
          {s.auto_shortlist ? 'Shortlist automatically at ATS score' : 'Suggest shortlisting at ATS score'}
        </label>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
          {s.auto_shortlist
            ? 'Applicants at or above this start their first round on their own. Below it, nobody is moved.'
            : 'Applicants at or above this are flagged to you as ready to shortlist. You confirm, and that starts their first round.'}
        </p>
        <div className="mt-2 flex items-center gap-2">
          {/* HR-L7: entered out of 100 like every other ATS figure in the
              console; the workflow stores tenths (0-10), so it moves in tens.
              With automatic shortlisting on, the bar cannot be cleared: the
              server refuses that combination, so say so before saving. */}
          <NumberField
            id="ats-threshold"
            min={0}
            max={100}
            step={10}
            allowEmpty={!s.auto_shortlist}
            disabled={!editable}
            value={s.shortlist_ats_threshold === null ? null : s.shortlist_ats_threshold * 10}
            placeholder="off"
            rangeMessage={
              s.auto_shortlist
                ? 'Enter 10, 20 and so on up to 100. Turn off automatic shortlisting to clear it.'
                : 'Enter 10, 20 and so on up to 100, or leave it empty to turn suggestions off.'
            }
            onCommit={(n) => onPatch({ shortlist_ats_threshold: n === null ? null : n / 10 })}
            resetKey={resetKey}
            className="w-24"
          />
          <span className="text-[13px] text-muted-foreground">/ 100</span>
        </div>

        <AutoShortlistToggle
          on={Boolean(s.auto_shortlist)}
          bar={s.shortlist_ats_threshold}
          autoAssign={Boolean(s.auto_assign_first_round)}
          editable={editable}
          onChange={(next) => onPatch({ auto_shortlist: next })}
        />
      </div>

      {/* PH4-O1. The final decision has no round of its own, so its owner and
          SLA live here rather than in a round's inspector. Editable on a live
          version too — see StageSettings. */}
      <div className="border-t border-border pt-4">
        <h3 className="mb-2 text-[13px] text-foreground">Final decision - owner &amp; SLA</h3>
        <StageSettings workflowId={workflow.id} roundId={null} label="the final decision" />
      </div>
    </div>
  );
}

/* ── The three ways in — D4 ───────────────────────────────────────────────── */

function EmptyState({
  onBlank,
  onTemplate,
  busy,
  templates,
}: {
  onBlank: () => void;
  onTemplate: (key: string) => void;
  busy: boolean;
  /** Built on the server against this role's competencies (D4). */
  templates: WorkflowTemplateSummary[] | undefined;
}) {
  return (
    <GlassCard className="p-8">
      <h2 className="text-[18px] font-semibold text-foreground">Give this opening a process</h2>
      <p className="mt-1.5 max-w-[62ch] text-[13.5px] leading-relaxed text-muted-foreground">
        Start from a shape that fits and change anything you like, or build it from nothing. Either
        way it stays a draft until you publish it, and no candidate sees it before then.
      </p>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        {templates === undefined ? (
          <p className="text-[12.5px] text-muted-foreground">Loading templates for this role…</p>
        ) : null}
        {(templates ?? []).map((t) => (
          <button
            key={t.key}
            type="button"
            disabled={busy}
            onClick={() => onTemplate(t.key)}
            aria-label={`Start from the ${t.name} template`}
            data-testid={`template-${t.key}`}
            className={cn(
              'flex flex-col rounded-[16px] border p-4 text-left transition-colors hover:border-[var(--accent)]/50 hover:bg-[var(--ui-inset-soft)] disabled:opacity-50',
              t.recommended ? 'border-[var(--accent)]/40' : 'border-border',
            )}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="text-[14px] font-medium text-foreground">{t.name}</span>
              {t.recommended ? (
                <span className="rounded-pill bg-[var(--accent)]/15 px-2 py-0.5 text-[12px] text-[var(--accent)]">
                  Suits this role
                </span>
              ) : null}
            </span>
            <span className="mt-1 text-[12px] leading-snug text-muted-foreground">
              {t.description}
            </span>
            {/* What it would actually create, for this role: each round and
                the competencies it would assess. */}
            <ol className="mt-3 flex flex-col gap-1.5">
              {t.rounds.map((r, i) => (
                <li key={`${r.kind}-${i}`} className="text-[12px] leading-snug">
                  <span className="font-medium text-[var(--ui-soft)]">
                    {i + 1}. {ROUND_KIND_META[r.kind].label}
                  </span>
                  {r.competencies.length > 0 ? (
                    <span className="text-[var(--ui-faint)]"> - {r.competencies.join(', ')}</span>
                  ) : null}
                </li>
              ))}
            </ol>
          </button>
        ))}
      </div>
      <p className="mt-3 text-[12px] leading-relaxed text-[var(--ui-faint)]">
        A template fills in the rounds, what each assesses, timings and settings. Test rounds still
        need their questions attached before you can publish.
      </p>

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
  const [tab, setTab] = useState<InspectorTab>('round');
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [sheet, setSheet] = useState<SheetKind>(null);
  // D5: the candidate's-eye preview.
  const [previewOpen, setPreviewOpen] = useState(false);
  // What the copilot has drafted and the user has not yet accepted or
  // discarded. Held here rather than inside the chat panel because the preview
  // is drawn on the canvas, and only one of the two surfaces can own it.
  const [pending, setPending] = useState<Proposal[]>([]);

  const req = useQuery({
    queryKey: queryKeys.requisition(requisitionId),
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

  // PH4-O2 — shares its cache key with DryRunPanel's own query, so "Run dry
  // run" from either the lifecycle strip or the panel updates both.
  const simulation = useQuery({
    queryKey: ['hr', 'workflow', workflowId, 'simulation'],
    queryFn: () => getSimulation(workflowId as string),
    enabled: Boolean(workflowId),
  });

  // Who submitted, who decided and why it was sent back — read only while the
  // version is a draft (a live one's review is history), and only once.
  const review = useQuery({
    queryKey: ['hr', 'workflow', workflowId, 'review'],
    queryFn: () => getReview(workflowId as string),
    enabled: Boolean(workflowId) && wf.data?.status === 'draft',
  });

  const templates = useQuery({
    queryKey: ['hr', 'workflow-templates', requisitionId],
    queryFn: () => listWorkflowTemplates(requisitionId),
    staleTime: 5 * 60_000,
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
  // Why the header Publish button is disabled, or null when it is not. Mirrors
  // the track (publishable from the validate report), plus the states in
  // which there is no report yet to trust.
  const publishIssues = validation.data?.errors.length ?? 0;
  const publishBlockedReason: string | null =
    (workflow?.rounds.length ?? 0) === 0
      ? 'Add at least one round before publishing.'
      : !validation.data
        ? 'Checking whether this workflow is ready to publish…'
        : !validation.data.publishable
          ? publishIssues > 0
            ? `Fix ${publishIssues} issue${publishIssues === 1 ? '' : 's'} before publishing.`
            : 'This workflow is not ready to publish yet.'
          : null;

  const simulated = Boolean(simulation.data && !simulation.data.stale);
  const trackInput = workflow
    ? {
        status: workflow.status,
        reviewStatus: workflow.review_status,
        simulated,
        publishable: Boolean(validation.data?.publishable),
        roundCount: workflow.rounds.length,
        draftExists: (versions.data ?? []).some((v) => v.status === 'draft'),
      }
    : null;
  const action = trackInput ? nextAction(trackInput) : null;
  // Server validation errors that stop a submit, shown beside the action
  // instead of being found by a refused request. Only while still authoring.
  const authoring =
    workflow?.status === 'draft' &&
    (workflow.review_status === 'draft' || workflow.review_status === 'changes_requested');
  const blockers = authoring ? (validation.data?.errors ?? []) : [];
  const submitBlockedReason: string | null = !authoring
    ? null
    : validation.isLoading
      ? 'Checking whether this version is ready…'
      : blockers.length > 0
        ? `Fix ${blockers.length} issue${blockers.length === 1 ? '' : 's'} before submitting.`
        : null;

  /** Every round mutation lands here: cache the returned workflow, recheck. */
  const applyWorkflow = (next: Workflow) => {
    qc.setQueryData(['hr', 'workflow', next.id], next);
    void qc.invalidateQueries({ queryKey: ['hr', 'workflow', next.id, 'validate'] });
    // An edit makes the last dry run stale. Without this the track and the
    // header primary kept reading "Dry run: Done" and offered "Submit for
    // review" when a new dry run was required.
    void qc.invalidateQueries({ queryKey: ['hr', 'workflow', next.id, 'simulation'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
  };

  // Counts refused saves. A refused PATCH leaves the cached workflow as it
  // was, so the number fields would not otherwise notice and would keep
  // showing the refused value; bumping this puts the saved one back.
  const [failedSaves, setFailedSaves] = useState(0);
  const mutating = useMutation({
    mutationFn: async (op: () => Promise<Workflow>) => op(),
    onSuccess: applyWorkflow,
    onError: (e) => {
      setFailedSaves((n) => n + 1);
      toast.error(errText(e, 'That change did not save'));
    },
  });

  const run = (op: () => Promise<Workflow>) => mutating.mutate(op);

  // PH4-O2 — the lifecycle strip's "2. Dry run" is a shortcut to the same
  // action DryRunPanel offers; both write the same cache key.
  const runDryRunMut = useMutation({
    mutationFn: () => runSimulation(workflowId as string),
    onSuccess: (result) => {
      qc.setQueryData(['hr', 'workflow', workflowId, 'simulation'], result);
      // A clean run needs no reading; anything else is why the user ran it.
      if (result.status !== 'passed') setSheet('details');
      toast.success(
        result.status === 'passed'
          ? 'Dry run passed'
          : result.status === 'warnings'
            ? 'Dry run passed, with warnings to look at'
            : 'Dry run found errors',
      );
    },
    onError: (e) => toast.error(errText(e, 'Could not run the dry run')),
  });

  // ── Creating ──────────────────────────────────────────────────────────────
  const createMut = useMutation({
    mutationFn: async (templateKey: string | null) => {
      // A template is built server-side against the role and created in one
      // transaction; a blank canvas is just a draft.
      return templateKey
        ? createFromTemplate(requisitionId, templateKey)
        : startDraft(requisitionId);
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
      const started = res.started_candidates ?? 0;
      const attached = res.attached_candidates ?? 0;
      const deferred = res.auto_shortlist_deferred ?? 0;
      toast.success(
        attached > 0
          ? `Version ${res.version} is live - ${attached} waiting candidate${attached === 1 ? '' : 's'} joined${started > 0 ? `, ${started} started round 1` : ''}`
          : `Version ${res.version} is live`,
      );
      if (deferred > 0) {
        // Not an error: they are in the shortlist queue, as on a manual version.
        toast.info(
          `${deferred} more waiting candidate${deferred === 1 ? ' was' : 's were'} not checked for automatic shortlisting at publish - review them in the shortlist queue.`,
        );
      }
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
      toast.success(
        `Editing as version ${draft.version} - version ${draft.version - 1} stays live`,
      );
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

  // The inspector shows one panel at a time. Review status lives on the
  // approval track and the review/dry-run record in its sheet, not here.
  const inspectorTabs: [InspectorTab, string][] = [
    ['round', selected ? 'Round' : 'Select a round'],
    ['settings', 'Automation'],
    ...(copilotAvailable ? [['assistant', 'Assistant'] as [InspectorTab, string]] : []),
  ];
  const activeTab: InspectorTab = inspectorTabs.some(([key]) => key === tab) ? tab : 'round';

  // Validation problems, counted for the header and linked to the round each
  // one names (the server prefixes a round's errors with its title).
  const problems = validation.data?.errors ?? [];
  const problemCount = problems.length;

  const goToRound = (id: string) => {
    setSelectedRound(id);
    setTab('round');
    setSheet(null);
  };

  // ── Render ────────────────────────────────────────────────────────────────
  if (!requisitionId) {
    return <div className="p-8 text-[13px] text-muted-foreground">No opening selected.</div>;
  }

  return (
    // EditorHeader hard-codes -mx-6 / lg:-mx-8 against the old page padding. The
    // shell owns the gutters now, so those margins would push it into them (and
    // past the viewport below sm); zero them from here rather than edit it.
    <Page width="workspace">
      <EditorHeader
        breadcrumbs={[
          { label: 'Openings', to: '/hr/requisitions' },
          // HR-L8: name the opening, as the decision queue does.
          { label: req.data?.title ?? 'Opening', to: `/hr/requisitions/${requisitionId}` },
          { label: 'Workflow' },
        ]}
        title={req.data?.title ?? 'Hiring workflow'}
        status={
          workflow ? (
            <span data-testid="workflow-meta" className="inline-flex flex-wrap items-center gap-2">
              <DocStatusPill
                status={
                  workflow.status === 'published'
                    ? 'live'
                    : workflow.status === 'draft'
                      ? workflow.review_status === 'approved'
                        ? 'approved'
                        : 'draft'
                      : 'locked'
                }
              >
                {workflow.status === 'published'
                  ? `v${workflow.version} · live`
                  : workflow.status === 'draft'
                    ? `v${workflow.version} · draft`
                    : `v${workflow.version} · archived`}
              </DocStatusPill>
              {/* PH4-O6: where this version stands in the approval
                  lifecycle. Once published the review is history, not
                  news, so this stops being worth a second badge. */}
              {workflow.status === 'draft' ? (
                <StatusTag tone={REVIEW_META[workflow.review_status].tone} dot>
                  {REVIEW_META[workflow.review_status].label}
                </StatusTag>
              ) : null}
            </span>
          ) : null
        }
        meta={
          <>
            {workflow ? (
              <span>
                {workflow.rounds.length} round{workflow.rounds.length === 1 ? '' : 's'}
              </span>
            ) : null}
            {req.data ? (
              <span>
                {req.data.total_enrolments} candidate
                {req.data.total_enrolments === 1 ? '' : 's'}
              </span>
            ) : null}
            {problemCount > 0 ? (
              <span
                data-testid="problem-count"
                className="inline-flex items-center gap-1 font-medium text-[var(--ui-warn)]"
              >
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                {problemCount} problem{problemCount === 1 ? '' : 's'} to fix
              </span>
            ) : null}
          </>
        }
        saveState={
          !workflow || !editable
            ? undefined
            : busy
              ? 'saving'
              : mutating.isError
                ? 'error'
                : 'saved'
        }
        secondary={
          workflow ? (
            <>
              {/* Discard sits first, away from the primary on the right.
                  HR-M2: it deletes the draft with its rounds and criteria, so
                  it asks first. */}
              {editable ? (
                <ConfirmDeleteButton
                  label="Discard draft"
                  confirmText="Discard draft"
                  onConfirm={() => discardMut.mutate()}
                  pending={discardMut.isPending}
                />
              ) : null}
              {(versions.data ?? []).length > 1 ? (
                <select
                  aria-label="Workflow version"
                  value={workflow.id}
                  onChange={(e) => {
                    setActiveId(e.target.value);
                    setSelectedRound(null);
                  }}
                  className="min-h-[40px] rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus:outline-none"
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
                className="inline-flex min-h-[40px] items-center gap-1.5 rounded-full border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] font-medium text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                Decision queue
              </Link>
            </>
          ) : undefined
        }
        primary={
          // Exactly one primary, and it is the legal next step (nextAction):
          // dry run → submit → publish → edit as a new version. A version
          // waiting on its reviewer has none; its quiet moves are in the sheet.
          workflow && action === 'dry_run' ? (
            <button
              type="button"
              data-primary="true"
              data-testid="run-dry-run"
              onClick={() => runDryRunMut.mutate()}
              disabled={runDryRunMut.isPending}
              className={PRIMARY_CLASS}
            >
              {runDryRunMut.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Run dry run
            </button>
          ) : workflow && action === 'submit' ? (
            <button
              type="button"
              data-primary="true"
              data-testid="submit-review"
              onClick={() => setSheet('submit')}
              className={PRIMARY_CLASS}
            >
              <Send className="h-3.5 w-3.5" aria-hidden="true" />
              Submit for review
            </button>
          ) : workflow && action === 'publish' ? (
            <button
              type="button"
              data-primary="true"
              onClick={() => setConfirmPublish(true)}
              data-testid="publish-workflow"
              // The server refuses anything validate does not call
              // publishable, and 409s unless a super admin approved this exact
              // version, so the button does not offer what cannot work.
              disabled={publishMut.isPending || publishBlockedReason !== null}
              title={publishBlockedReason ?? undefined}
              className={PRIMARY_CLASS}
            >
              {publishMut.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Publish
            </button>
          ) : workflow && action === 'edit_new_version' ? (
            <button
              type="button"
              data-primary="true"
              onClick={() => cloneMut.mutate()}
              data-testid="edit-as-new-version"
              disabled={cloneMut.isPending}
              className={PRIMARY_CLASS}
            >
              {cloneMut.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Edit as new version
            </button>
          ) : undefined
        }
      />

      {workflow && trackInput ? (
        <div className="flex flex-col gap-3">
          <WorkflowTrack
            status={workflow.status}
            reviewStatus={workflow.review_status}
            roundCount={workflow.rounds.length}
            publishedAt={workflow.published_at}
            publishable={trackInput.publishable}
            simulated={simulated}
            simulation={simulation.data}
            review={review.data}
            secondary={
              workflow.status === 'draft' ? (
                <button
                  type="button"
                  onClick={() => setSheet('details')}
                  className="inline-flex items-center justify-center gap-1.5 rounded-full border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] font-medium text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  Review and dry-run details
                </button>
              ) : undefined
            }
          />
          <SubmitBlockers errors={blockers} rounds={workflow.rounds} onGoToRound={goToRound} />
        </div>
      ) : null}

      {/* Publish confirmation. Worth an extra step: publishing starts sending
          real invitations to real people. */}
      {confirmPublish && workflow ? (
        <Card density="compact" className="border-[var(--ui-line-strong)]">
          <div className="flex flex-wrap items-center gap-3">
            <Sparkles className="h-4 w-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-[13px] leading-relaxed text-[var(--ui-soft)]">
              {publishImpact(validation.data?.waiting) ? (
                <strong className="mb-1 block font-medium text-foreground">
                  {publishImpact(validation.data?.waiting)}
                </strong>
              ) : null}
              Publishing makes this version live for {req.data?.title ?? 'this opening'}. New
              candidates start here; anyone already inside an older version finishes it on the
              rounds they began with.
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmPublish(false)}
                className="min-h-[40px] rounded-full border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                Not yet
              </button>
              {/* HR-M3: guarded like the header button, so a double click
                  cannot send two publishes. */}
              <button
                type="button"
                onClick={() => publishMut.mutate()}
                disabled={publishMut.isPending}
                aria-busy={publishMut.isPending}
                data-testid="confirm-publish"
                className="inline-flex min-h-[40px] items-center gap-1.5 rounded-full bg-primary px-5 py-2 text-[13px] font-semibold text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50"
              >
                {publishMut.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : null}
                Publish version {workflow.version}
              </button>
            </div>
          </div>
        </Card>
      ) : null}

      {versions.isLoading || (workflowId && wf.isLoading) ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          <span role="status">Loading workflow…</span>
        </div>
      ) : versions.isError || wf.isError ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 text-[13px] text-[var(--ui-danger)]"
        >
          Could not load this opening&rsquo;s workflow.
          <button
            type="button"
            onClick={() => {
              void versions.refetch();
              void wf.refetch();
            }}
            className="inline-flex min-h-[40px] items-center rounded-full border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] font-medium text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            Try again
          </button>
        </div>
      ) : !workflow ? (
        // Three ways in, side by side (D4): a template, an empty canvas, or a
        // conversation. The copilot belongs most here — "design a process for
        // this role" is the question someone opening a blank builder has.
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <EmptyState
            busy={createMut.isPending}
            onBlank={() => createMut.mutate(null)}
            onTemplate={(key) => createMut.mutate(key)}
            templates={templates.data}
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
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          {/* Canvas — the page's one raised region. */}
          <Card emphasis="raised" density="compact">
            {/* D5: an opening taking applications with nothing live. Nobody is
                lost — publishing attaches them — but nothing moves until then. */}
            {(() => {
              const warning = applicationsWarning({
                title: req.data?.title ?? 'This opening',
                accepting: Boolean(req.data?.public_apply_enabled && req.data?.status === 'open'),
                hasLiveVersion: (versions.data ?? []).some((v) => v.status === 'published'),
                draftVersion:
                  (versions.data ?? []).find((v) => v.status === 'draft')?.version ?? null,
                waiting: validation.data?.waiting,
              });
              return warning ? (
                <div
                  role="alert"
                  className="mb-4 flex items-start gap-2 rounded-[12px] border border-[var(--ui-warn)] bg-[var(--ui-warn-wash)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
                >
                  <AlertTriangle
                    className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]"
                    aria-hidden="true"
                  />
                  {warning}
                </div>
              ) : null;
            })()}

            <button
              type="button"
              onClick={() => setPreviewOpen((open) => !open)}
              className="mb-4 min-h-[40px] rounded-pill border border-border px-4 py-1.5 text-[13px] text-[var(--ui-soft)] hover:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {previewOpen ? 'Hide candidate preview' : 'Preview as a candidate'}
            </button>

            {previewOpen ? (
              <Well className="mb-5">
                <CandidatePreview
                  autoShortlistBar={
                    workflow.settings.auto_shortlist ? workflow.settings.shortlist_ats_threshold : null
                  }
                  title={req.data?.title ?? 'this opening'}
                  rounds={workflow.rounds}
                />
              </Well>
            ) : null}

            {!editable ? (
              <Well className="mb-4 flex items-start gap-2 text-[12.5px] leading-relaxed text-muted-foreground">
                <Eye className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                {workflow.status === 'published'
                  ? 'This version is live, so it is read-only. Editing creates version ' +
                    (workflow.version + 1) +
                    ' and leaves this one running for everyone already inside it.'
                  : workflow.status === 'archived'
                    ? 'An archived version, kept because candidates finished under it.'
                    : workflow.review_status === 'in_review'
                      ? 'This version is waiting for review, so it is locked. Withdraw it from review (Review and dry-run details) to make changes.'
                      : 'This version has been approved, so it is locked. Reopen it (Review and dry-run details) to make changes - it will need approving again.'}
              </Well>
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
                  void qc.invalidateQueries({
                    queryKey: ['hr', 'workflow', workflow.id, 'simulation'],
                  });
                  void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
                }}
                onDismiss={() => setPending((prev) => prev.filter((x) => x.id !== p.id))}
              />
            ))}

            <WorkflowCanvas
              rounds={workflow.rounds}
              autoShortlistBar={
                workflow.settings.auto_shortlist ? workflow.settings.shortlist_ats_threshold : null
              }
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
          </Card>

          {/* Inspector — one panel at a time, chosen by tab, so the page has
              three clear zones: the stages and canvas on the left, whatever
              is selected on the right, and the header above both. */}
          <div className="flex flex-col gap-6">
            <Card density="compact">
              <div
                role="tablist"
                aria-label="Inspector"
                className="mb-4 flex gap-1 rounded-pill bg-[var(--ui-inset-strong)] p-1"
                onKeyDown={(e) => {
                  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
                  const i = inspectorTabs.findIndex(([key]) => key === activeTab);
                  const next =
                    inspectorTabs[
                      (i + (e.key === 'ArrowRight' ? 1 : -1) + inspectorTabs.length) %
                        inspectorTabs.length
                    ][0];
                  setTab(next);
                  document.getElementById(`inspector-tab-${next}`)?.focus();
                }}
              >
                {inspectorTabs.map(([key, label]) => (
                  <button
                    key={key}
                    id={`inspector-tab-${key}`}
                    type="button"
                    onClick={() => setTab(key)}
                    aria-selected={activeTab === key}
                    aria-controls="inspector-panel"
                    tabIndex={activeTab === key ? 0 : -1}
                    role="tab"
                    className={cn(
                      'min-h-[40px] flex-1 rounded-pill px-2 py-1.5 text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                      activeTab === key
                        ? 'bg-card text-foreground shadow-card'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <div
                id="inspector-panel"
                role="tabpanel"
                aria-labelledby={`inspector-tab-${activeTab}`}
              >
                {/* Mounted for the life of the page and only hidden off its tab:
                    the conversation and any run in flight are the copilot's own
                    state, so unmounting on a tab switch would discard them. */}
                {copilotAvailable ? (
                  <div
                    className={activeTab === 'assistant' ? 'flex h-[440px] flex-col' : 'hidden'}
                  >
                    <WorkflowCopilot
                      requisitionId={requisitionId}
                      jobTitle={req.data?.title ?? 'this role'}
                      editable={editable}
                      hasRounds={workflow.rounds.length > 0}
                      onProposals={(ps) => setPending(ps)}
                    />
                  </div>
                ) : null}
                {activeTab === 'settings' ? (
                  <SettingsPanel
                    workflow={workflow}
                    editable={editable}
                    onPatch={(fields) => run(() => updateSettings(workflow.id, fields))}
                    resetKey={failedSaves}
                  />
                ) : activeTab === 'assistant' && copilotAvailable ? null : selected ? (
                  <RoundInspector
                    key={selected.id}
                    round={selected}
                    allRounds={workflow.rounds}
                    workflowId={workflow.id}
                    roleModel={roleModel.data}
                    editable={editable}
                    saving={busy}
                    roundErrors={(validation.data?.errors ?? []).filter((e) =>
                      e.startsWith(`${selected.title}:`),
                    )}
                    returnTo={`/hr/requisitions/${requisitionId}/workflow`}
                    onPatch={(fields) => run(() => updateRound(workflow.id, selected.id, fields))}
                    resetKey={failedSaves}
                    onCriteria={(criteria: CriterionInput[]) =>
                      run(() => setRoundCriteria(workflow.id, selected.id, criteria))
                    }
                  />
                ) : (
                  <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                    Pick a round on the left to set what it asks and what score moves a candidate
                    on.
                  </p>
                )}
              </div>
              <Section title="Coverage" className="mt-5">
                <CoveragePanel
                  report={validation.data}
                  loading={validation.isFetching}
                  blockersShownElsewhere={blockers.length > 0 && Boolean(trackInput)}
                />
              </Section>
            </Card>
          </div>
        </div>
      )}

      {/* The opening's own setup — budget, approval, JD and posting — lives on
          the opening, not here: this page is the workflow and only the workflow. */}
      <p className="text-[13px] leading-relaxed text-muted-foreground" data-testid="setup-link">
        Budget, approval, job description and posting now live on the opening&rsquo;s{' '}
        <Link
          to={`/hr/requisitions/${requisitionId}?tab=setup`}
          className="font-medium text-[var(--accent)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          Setup tab
        </Link>
        .
      </p>

      {/* The review and dry-run record, and the submit step. Mounted only while
          open, so a closed sheet costs no requests. */}
      <Sheet open={sheet !== null} onOpenChange={(open) => (open ? undefined : setSheet(null))}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>{sheet === 'submit' ? 'Submit for review' : 'Review and dry run'}</SheetTitle>
            <SheetDescription>
              {sheet === 'submit'
                ? 'A company super admin reviews this version. Nothing goes live until they approve it and you publish.'
                : 'Where this version stands with the reviewer, and the last dry run of its routing.'}
            </SheetDescription>
          </SheetHeader>
          {workflow && sheet !== null ? (
            <div className="mt-6 flex flex-col gap-6">
              {sheet === 'submit' ? (
                <SubmitBlockers errors={blockers} rounds={workflow.rounds} onGoToRound={goToRound} />
              ) : null}
              {/* PH4-O6 / O2 — where this version stands with the reviewer, and
                  whether a dry run of its routing comes back clean. */}
              <ReviewPanel
                workflowId={workflow.id}
                submitBlockedReason={sheet === 'submit' ? submitBlockedReason : null}
                onChanged={(state) => {
                  void qc.invalidateQueries({ queryKey: ['hr', 'workflow', workflow.id] });
                  void qc.invalidateQueries({ queryKey: ['hr', 'workflows', requisitionId] });
                  if (state.review_status === 'in_review') setSheet(null);
                }}
              />
              {sheet === 'details' ? (
                <div className="border-t border-border pt-5">
                  <DryRunPanel workflowId={workflow.id} />
                </div>
              ) : null}
            </div>
          ) : null}
        </SheetContent>
      </Sheet>
    </Page>
  );
}
