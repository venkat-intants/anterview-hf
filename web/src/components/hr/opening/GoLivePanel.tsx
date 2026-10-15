// GoLivePanel — stage 4: open the opening to applications, now or on a schedule.
//
// The server refuses to turn applications on (409) or to schedule publishing
// while the opening is not approved. That is shown BEFORE the click: the switch
// is disabled and the reason sits beside it, wired with aria-describedby, so
// nobody discovers the rule through an error toast. Turning applications OFF is
// never gated.
//
// A missing workflow is not a server refusal (applicants are recorded and
// publishing attaches them), so it stays a warning rather than a lock.

import { useId } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Copy, ExternalLink, Globe } from '@/design/components/icons';
import { SectionCard } from '@/design/components/page';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { queryKeys } from '@/lib/queryKeys';
import { updateRequisition, type Requisition } from '@/api/requisitions';
import GovernancePanel from '@/components/workflow/GovernancePanel';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** The apply switch. Same look as `ToggleSwitch`, which cannot be disabled. */
function ApplySwitch({
  checked,
  disabled,
  describedBy,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  describedBy?: string;
  onChange: (next: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label="Accept public applications"
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative h-6 w-11 flex-none rounded-pill border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50',
        checked
          ? 'border-[var(--accent)] bg-[var(--accent)]'
          : 'border-[var(--ui-line-strong)] bg-[var(--ui-inset-strong)]',
      )}
    >
      <span
        data-testid="toggle-thumb"
        className={cn(
          'absolute top-px h-5 w-5 rounded-full transition-all',
          checked ? 'left-[21px] bg-primary-foreground' : 'left-px bg-muted-foreground',
        )}
      />
    </button>
  );
}

export function PublicApplyCard({
  requisitionId,
  enabled,
  status,
  approved,
  hasWorkflow,
}: {
  requisitionId: string;
  enabled: boolean;
  status: string;
  /** `approval_status === 'approved'`. The server refuses to turn applications on otherwise. */
  approved: boolean;
  /** No published workflow: the link refuses applications until there is one. */
  hasWorkflow: boolean;
}): JSX.Element {
  const qc = useQueryClient();
  const gateId = useId();
  const link = `${window.location.origin}/apply/${requisitionId}`;
  const gated = !enabled && !approved;

  const mut = useMutation({
    mutationFn: (next: boolean) => updateRequisition(requisitionId, { public_apply_enabled: next }),
    onSuccess: (_r, next) => {
      toast.success(next ? 'Applications are open' : 'Applications closed');
      void qc.invalidateQueries({ queryKey: ['hr', 'requisition-dashboard', requisitionId] });
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
      void qc.invalidateQueries({ queryKey: queryKeys.requisition(requisitionId) });
    },
    onError: (e) => toast.error(errText(e, 'Could not change that')),
  });

  return (
    <SectionCard
      title="Accept applications"
      icon={Globe}
      action={
        <ApplySwitch
          checked={enabled}
          disabled={gated || mut.isPending}
          describedBy={gated ? gateId : undefined}
          onChange={(next) => mut.mutate(next)}
        />
      }
    >
      <p className="text-[13px] leading-relaxed text-muted-foreground">
        Publishes a page anyone with the link can apply through. Off by default — a link that leaks
        should not open a channel you did not choose to open.
      </p>

      {gated ? (
        <p
          id={gateId}
          data-testid="apply-needs-approval"
          className="mt-3 flex items-start gap-1.5 text-[13px] text-[var(--ui-warn)]"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          This switch is off until the opening is approved (stage 3). The server refuses to open
          applications on an unapproved opening.
        </p>
      ) : null}

      {enabled ? (
        status === 'open' && !hasWorkflow ? (
          // Said here so HR does not share a link that turns everyone away.
          <p
            className="mt-3 flex items-start gap-1.5 text-[13px] text-[var(--ui-warn)]"
            data-testid="apply-needs-workflow"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            The link will not accept applications until a workflow is published, so nobody is taken
            into a process that does not exist yet.
          </p>
        ) : status === 'open' ? (
          <div className="mt-3 flex items-center gap-1 rounded-[12px] bg-[var(--ui-inset)] py-1 pl-3 pr-1">
            <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-[var(--ui-soft)]">
              {link}
            </span>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(link);
                toast.success('Link copied');
              }}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-[var(--ui-inset-strong)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              aria-label="Copy the application link"
            >
              <Copy className="h-4 w-4" aria-hidden="true" />
            </button>
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-[var(--ui-inset-strong)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              aria-label="Open the application page"
            >
              <ExternalLink className="h-4 w-4" aria-hidden="true" />
            </a>
          </div>
        ) : (
          // The toggle is on but the opening is paused or closed, so the page
          // 404s. Saying so beats letting someone share a dead link.
          <p className="mt-3 flex items-start gap-1.5 text-[13px] text-[var(--ui-warn)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This opening is {status}, so the link will not accept applications until you reopen it.
          </p>
        )
      ) : null}
    </SectionCard>
  );
}

/** Stage 4's panel: the apply switch, then the publish schedule. */
export default function GoLivePanel({
  requisition,
  hasWorkflow,
}: {
  requisition: Requisition;
  hasWorkflow: boolean;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <PublicApplyCard
        requisitionId={requisition.id}
        enabled={requisition.public_apply_enabled}
        status={requisition.status}
        approved={requisition.approval_status === 'approved'}
        hasWorkflow={hasWorkflow}
      />
      <GovernancePanel requisition={requisition} section="schedule" />
    </div>
  );
}
