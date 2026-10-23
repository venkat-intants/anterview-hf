// SetupTab — the opening's setup as an ordered rail, each existing panel sitting
// in the stage it belongs to. Nothing here is new function: it is where the
// panels live now that the workflow page no longer carries them.
//
//   1 Role and posting      OpeningDetails · PostingEditor · JdVersionPanel · QuestionEditor
//   2 Hiring process        a summary and the way into the workflow builder
//   3 Budget and approval   GovernancePanel (budget, submit for approval)
//   4 Go live               the apply switch and the publish schedule
//   5 Preboarding documents DocumentRequirementsSection
//
// A stage's panel is mounted the first time it is opened and then kept hidden
// (so unsaved typing survives a trip to another stage), and a stage nobody has
// opened runs no queries. Stage 1 is four panels, so it is itself a short
// sequence of sub-steps (RoleStage below): one panel in view at a time, each
// reachable directly. The read-only (super admin) view gets the same rail with
// a summary in place of every control.

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { JourneyRail, type StageState } from '@/design/components/journey-rail';
import { SectionCard } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { Requisition } from '@/api/requisitions';
import type { OpeningStage, StageKey } from '@/lib/openingSetup';
import OpeningDetails from '@/components/OpeningDetails';
import DocumentRequirementsSection from '@/components/DocumentRequirementsSection';
import GovernancePanel from '@/components/workflow/GovernancePanel';
import JdVersionPanel from '@/components/workflow/JdVersionPanel';
import PostingEditor from '@/components/workflow/PostingEditor';
import QuestionEditor from '@/components/workflow/QuestionEditor';
import GoLivePanel from './GoLivePanel';

const STATE_LABELS: Record<StageState, string> = {
  todo: 'To do',
  active: 'In progress',
  blocked: 'Blocked',
  awaiting: 'Waiting',
  done: 'Done',
  optional: 'Optional',
};

function StageSummary({ stage }: { stage: OpeningStage }): JSX.Element {
  const who = [stage.actor, stage.at].filter(Boolean).join(' · ');
  return (
    <SectionCard title={stage.title} data-testid={`stage-summary-${stage.key}`}>
      <p className="text-[14px] text-foreground">
        <span className="font-semibold">{STATE_LABELS[stage.state]}</span>
        {stage.fact ? <> · {stage.fact}</> : null}
      </p>
      {who ? <p className="mt-1 text-[13px] text-muted-foreground">{who}</p> : null}
      {stage.state === 'blocked' && stage.reason ? (
        <p className="mt-1 text-[13px] text-muted-foreground">{stage.reason}</p>
      ) : null}
      <p className="mt-3 text-[13px] text-muted-foreground">
        Read-only - HR makes the changes for this stage.
      </p>
    </SectionCard>
  );
}

function ProcessPanel({ requisitionId }: { requisitionId: string }): JSX.Element {
  return (
    <SectionCard
      title="Hiring process"
      description="The rounds a candidate goes through, from the first screen to the decision."
      data-testid="stage-process"
    >
      <p className="max-w-[62ch] text-[13px] leading-relaxed text-muted-foreground">
        The steps listed under this stage describe the version being worked on. A round that uses an
        exam needs a published exam attached before the workflow can be published. The workflow is
        reviewed and approved on its own, separately from the budget approval; both are needed
        before applications can open.
      </p>
      <div className="mt-4">
        <Button asChild variant="outline">
          <Link to={`/hr/requisitions/${requisitionId}/workflow`}>Open the workflow builder</Link>
        </Button>
      </div>
    </SectionCard>
  );
}

/* ── Stage 1, one panel at a time ────────────────────────────────────────── */

export type RoleStep = 'details' | 'posting' | 'jd' | 'questions';

export interface RoleStepRequest {
  step: RoleStep;
  /** Bumped on every request, so asking twice for the same step still moves. */
  n: number;
}

const ROLE_STEPS: { key: RoleStep; label: string }[] = [
  { key: 'details', label: 'Opening details' },
  { key: 'posting', label: 'Public details' },
  { key: 'jd', label: 'Job description' },
  { key: 'questions', label: 'Application questions' },
];

/**
 * Role and posting as four sub-steps, shown one at a time.
 *
 * A panel is mounted the first time it is opened and then kept (hidden, not
 * unmounted), for two reasons: a person who has typed into the posting and
 * moves on to the job description must not lose what they typed, and a panel
 * nobody has opened has not asked the server for anything.
 */
function RoleStage({
  requisition,
  request,
}: {
  requisition: Requisition;
  /** Asks for a sub-step (the header's "Write the job description" wants the JD). */
  request?: RoleStepRequest;
}): JSX.Element {
  const [current, setCurrent] = useState<RoleStep>('details');
  const [visited, setVisited] = useState<Set<RoleStep>>(() => new Set<RoleStep>(['details']));
  const region = useRef<HTMLDivElement>(null);
  const moved = useRef(false);

  const index = ROLE_STEPS.findIndex((x) => x.key === current);
  const prev = ROLE_STEPS[index - 1];
  const next = ROLE_STEPS[index + 1];

  function go(key: RoleStep): void {
    moved.current = true;
    setVisited((v) => (v.has(key) ? v : new Set(v).add(key)));
    setCurrent(key);
  }

  const requestedN = request?.n;
  const requestedStep = request?.step;
  useEffect(() => {
    if (requestedStep) go(requestedStep);
    // `go` only sets state; the request's counter is what should re-run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedN, requestedStep]);

  // After a move, put keyboard and screen-reader users at the new panel.
  useEffect(() => {
    if (moved.current) region.current?.focus();
  }, [current]);

  const panel = (key: RoleStep, node: JSX.Element): JSX.Element | null =>
    visited.has(key) ? (
      <div key={key} hidden={key !== current} data-testid={`role-step-${key}`}>
        {node}
      </div>
    ) : null;

  return (
    <div className="flex flex-col gap-4" data-testid="role-stage">
      <nav aria-label="Opening and job description steps">
        <ol className="flex flex-wrap gap-2">
          {ROLE_STEPS.map((st, i) => {
            const on = st.key === current;
            return (
              <li key={st.key}>
                <button
                  type="button"
                  onClick={() => go(st.key)}
                  aria-current={on ? 'step' : undefined}
                  className={cn(
                    'flex min-h-10 items-center gap-2 rounded-pill border px-3.5 text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] motion-reduce:transition-none',
                    on
                      ? 'border-transparent bg-[var(--ui-highlight)] text-[var(--ui-highlight-ink)]'
                      : 'border-border bg-card text-muted-foreground hover:text-foreground',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      'flex h-5 w-5 items-center justify-center rounded-full text-[11.5px] font-bold',
                      on ? 'bg-card text-foreground' : 'border border-[var(--ui-line-strong)]',
                    )}
                  >
                    {i + 1}
                  </span>
                  {st.label}
                  <span className="sr-only">{on ? ' (current step)' : ''}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      <div ref={region} tabIndex={-1} className="focus:outline-none">
        {panel('details', <OpeningDetails requisition={requisition} />)}
        {panel('posting', <PostingEditor requisition={requisition} />)}
        {panel('jd', <JdVersionPanel requisition={requisition} />)}
        {panel('questions', <QuestionEditor requisitionId={requisition.id} />)}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {prev ? (
          <Button type="button" variant="outline" onClick={() => go(prev.key)}>
            Back: {prev.label}
          </Button>
        ) : (
          <span />
        )}
        {next ? (
          <Button type="button" variant="outline" onClick={() => go(next.key)}>
            Next: {next.label}
          </Button>
        ) : (
          <span className="text-[13px] text-muted-foreground">
            That is the last step of Opening and job description.
          </span>
        )}
      </div>
    </div>
  );
}

interface Props {
  stages: OpeningStage[];
  activeKey: StageKey;
  onSelect: (key: StageKey) => void;
  requisition: Requisition;
  hasPublishedWorkflow: boolean;
  readOnly: boolean;
  /** A sub-step of stage 1 to open on, e.g. the job description. */
  roleStepRequest?: RoleStepRequest;
}

export default function SetupTab({
  stages,
  activeKey,
  onSelect,
  requisition,
  hasPublishedWorkflow,
  readOnly,
  roleStepRequest,
}: Props): JSX.Element {
  const stage = stages.find((s) => s.key === activeKey) ?? stages[0];

  // A stage's panel is mounted the first time it is opened and then KEPT, hidden
  // rather than unmounted (the technique RoleStage uses for its sub-steps).
  // Choosing another stage in the rail used to unmount the panel and throw away
  // whatever was typed and not yet saved; a stage nobody has opened still asks
  // the server for nothing.
  const [visited, setVisited] = useState<ReadonlySet<StageKey>>(
    () => new Set<StageKey>(stage ? [stage.key] : []),
  );
  if (stage && !visited.has(stage.key)) setVisited(new Set(visited).add(stage.key));

  const panelFor = (key: StageKey): JSX.Element | null => {
    switch (key) {
      case 'role':
        return <RoleStage requisition={requisition} request={roleStepRequest} />;
      case 'process':
        return <ProcessPanel requisitionId={requisition.id} />;
      case 'approval':
        return <GovernancePanel requisition={requisition} section="approval" />;
      case 'golive':
        return <GoLivePanel requisition={requisition} hasWorkflow={hasPublishedWorkflow} />;
      case 'documents':
        return <DocumentRequirementsSection requisitionId={requisition.id} />;
    }
  };

  let panel: JSX.Element | null = null;
  if (stage) {
    panel = readOnly ? (
      <StageSummary stage={stage} />
    ) : (
      <>
        {stages
          .filter((s) => visited.has(s.key) || s.key === stage.key)
          .map((s) => (
            <div key={s.key} hidden={s.key !== stage.key} data-testid={`stage-panel-${s.key}`}>
              {panelFor(s.key)}
            </div>
          ))}
      </>
    );
  }

  return (
    <JourneyRail
      label="Opening setup"
      data-testid="setup-rail"
      stages={stages}
      activeKey={stage?.key ?? activeKey}
      onSelect={(key) => onSelect(key as StageKey)}
      stateLabels={STATE_LABELS}
    >
      {panel}
    </JourneyRail>
  );
}
