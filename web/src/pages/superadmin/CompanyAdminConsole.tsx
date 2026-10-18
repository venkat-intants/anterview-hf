// CompanyAdminConsole — a company's super admin ("super admin", one per company).
//
// Tier: super_admin (company-scoped). Creates and manages the HR managers AND
// the interviewers (D4-1) for its OWN company only. Company + tenant boundary
// are resolved server-side from the caller's account — this page never sends
// a company id.
//
// Creating someone happens in a dialog opened from the page header (HR manager)
// or the interviewers card. The "set-password link was emailed" outcome is a
// toast plus a hint that stays on the page, so it is not lost when the dialog
// closes.
//
// Shell: bare content — AppShell is provided by the router (no double-wrap).

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users,
  UserPlus,
  CheckCircle2,
  KeyRound,
  Building2,
  ClipboardCheck,
  Search,
  type LucideIcon,
} from '@/design/components/icons';
import { getMe } from '@/api/auth';
import CareersLinkCard from '@/components/hr/CareersLinkCard';
import {
  listMyHrManagers,
  createMyHrManager,
  deleteMyHrManager,
  listMyInterviewers,
  createMyInterviewer,
  deleteMyInterviewer,
  type HrManager,
} from '@/api/hr';
import { toast } from '@/lib/toast';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { AccountDialog } from '@/components/superadmin/CreateDialogs';
import { Avatar } from '@/design/components/primitives';
import { EmptyState, Page, PageHeader } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { Row, RowList, Section } from '@/design/components/rows';
import { Stat, StatGroup } from '@/design/components/stat-group';
import { gradientFor, initialsOf } from '@/design/data/shared';
import { listPendingApprovals } from '@/api/requisitions';
import { listPendingWorkflowReviews } from '@/api/workflowReview';
import { listOfferApprovals } from '@/api/offers';
import { listAdminQuestionReviews } from '@/api/questionBanks';

/**
 * One roster (HR managers, or interviewers) — the list plus its create dialog.
 * Both rosters share the exact same server-row shape (HrManager), so this is
 * the single implementation the page renders twice rather than two near-
 * identical 150-line blocks that would drift apart the first time one of them
 * changed.
 *
 * The dialog's open state is owned by the page, because the HR manager roster
 * is opened from the page header as well.
 */
function StaffRoster({
  idPrefix,
  icon,
  heading,
  createTitle,
  createBlurb,
  addLabel,
  openLabel,
  showOpenButton,
  emptyText,
  listLabel,
  companyName,
  removalNote,
  queryKey,
  listFn,
  createFn,
  deleteFn,
  createOpen,
  onCreateOpenChange,
}: {
  idPrefix: string;
  icon: LucideIcon;
  heading: string;
  createTitle: string;
  createBlurb: (companyName: string) => string;
  /** The dialog's submit button. */
  addLabel: string;
  /** The button that opens the dialog. */
  openLabel: string;
  /** False when the page header already carries the opening button. */
  showOpenButton: boolean;
  emptyText: string;
  listLabel: string;
  companyName: string;
  /** Shown once, under the list — e.g. what removing someone also does. */
  removalNote?: string;
  queryKey: string;
  listFn: () => Promise<HrManager[]>;
  createFn: (body: { email: string; full_name: string }) => Promise<HrManager>;
  deleteFn: (userId: string) => Promise<void>;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();

  const { data: rows, isLoading } = useQuery({ queryKey: [queryKey], queryFn: listFn });
  const [filter, setFilter] = useState('');
  const needle = filter.trim().toLowerCase();
  const shown = (rows ?? []).filter(
    (r) =>
      !needle ||
      r.full_name.toLowerCase().includes(needle) ||
      r.email.toLowerCase().includes(needle),
  );

  const createMut = useMutation({
    mutationFn: (v: { email: string; fullName: string }) =>
      createFn({ email: v.email.trim(), full_name: v.fullName.trim() }),
    onSuccess: (_created, v) => {
      toast.success(
        `${heading.replace(/s$/, '')} ${v.email} created - a set-password link was emailed.`,
      );
      onCreateOpenChange(false);
      void qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (err: unknown) =>
      toast.error(
        err instanceof Error
          ? err.message
          : `Could not create this ${heading.toLowerCase().replace(/s$/, '')}.`,
      ),
  });

  const deleteMut = useMutation({
    mutationFn: (userId: string) => deleteFn(userId),
    onSuccess: () => {
      toast.success(`${heading.replace(/s$/, '')} removed.`);
      void qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (err: unknown) =>
      toast.error(
        err instanceof Error
          ? err.message
          : `Could not remove this ${heading.toLowerCase().replace(/s$/, '')}.`,
      ),
  });

  return (
    <Card
      as="section"
      title={heading}
      description={createBlurb(companyName)}
      actions={
        showOpenButton ? (
          <Button type="button" variant="outline" onClick={() => onCreateOpenChange(true)} className="gap-1.5">
            <UserPlus className="h-4 w-4" aria-hidden="true" />
            {openLabel}
          </Button>
        ) : undefined
      }
    >
      <AccountDialog
        open={createOpen}
        onOpenChange={(next) => {
          if (next) createMut.reset();
          onCreateOpenChange(next);
        }}
        idPrefix={idPrefix}
        title={createTitle}
        description={createBlurb(companyName)}
        submitLabel={addLabel}
        emailPlaceholder="name@company.com"
        namePlaceholder="Full name"
        pending={createMut.isPending}
        error={createMut.isError ? createMut.error : null}
        onSubmit={(v) => createMut.mutate(v)}
      />

      {isLoading ? (
        <div className="space-y-2" aria-busy="true">
          <Skeleton className="h-14 w-full rounded-[14px]" />
          <Skeleton className="h-14 w-full rounded-[14px]" />
        </div>
      ) : !rows || rows.length === 0 ? (
        <EmptyState icon={icon} title={emptyText} bordered={false} />
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
            <div className="relative w-full max-w-[320px]">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ui-faint)]"
                aria-hidden="true"
              />
              <input
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-label={`Search ${listLabel.toLowerCase()}`}
                placeholder="Search by name or email"
                className="min-h-10 w-full rounded-[10px] border border-input bg-card pl-9 pr-3 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              />
            </div>
            <span className="text-[13px] tabular-nums text-muted-foreground">
              {needle ? `${shown.length} of ${rows.length}` : rows.length}{' '}
              {rows.length === 1 ? 'person' : 'people'}
            </span>
          </div>
          {shown.length === 0 ? (
            <p className="py-6 text-center text-[13.5px] text-muted-foreground">
              No one matches “{filter.trim()}”.
            </p>
          ) : null}
          <RowList aria-label={listLabel}>
            {shown.map((row) => (
              <Row
                key={row.user_id}
                leading={
                  <Avatar
                    initials={initialsOf(row.full_name)}
                    gradient={gradientFor(row.user_id.charCodeAt(0))}
                    size={32}
                  />
                }
                title={<span className="block truncate">{row.full_name}</span>}
                meta={<span className="block truncate">{row.email}</span>}
                trailing={
                  <>
                    {row.must_change_password ? (
                      <Badge variant="outline" className="text-xs">
                        pending first login
                      </Badge>
                    ) : (
                      <Badge variant="success" className="gap-1 text-xs">
                        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                        active
                      </Badge>
                    )}
                    <ConfirmDeleteButton
                      title={`Remove ${row.full_name}`}
                      pending={deleteMut.isPending && deleteMut.variables === row.user_id}
                      onConfirm={() => deleteMut.mutate(row.user_id)}
                    />
                  </>
                }
              />
            ))}
          </RowList>
        </>
      )}

      <p className="mt-4 flex items-center gap-1.5 text-[13px] text-muted-foreground">
        <KeyRound className="h-3.5 w-3.5 text-[var(--ui-info)]" aria-hidden="true" />A secure “set
        your password” link is emailed to them - no password is shared here.
      </p>
      {removalNote ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">{removalNote}</p>
      ) : null}
    </Card>
  );
}

/**
 * One cell of "Waiting on you": the length of a queue, or null when it could not
 * be fetched. A failed or still-loading queue must never read as 0 — that is
 * the one wrong answer, because it is also the good news. The query keys and
 * functions are the ones the queue pages themselves use, so the cache serves
 * both and nothing here polls.
 */
function useQueueCount(queryKey: readonly string[], queryFn: () => Promise<unknown[]>): {
  count: number | null;
  failed: boolean;
} {
  const q = useQuery({ queryKey, queryFn, retry: false });
  return { count: q.isSuccess ? q.data.length : null, failed: q.isError };
}

function WaitingOnYou(): JSX.Element {
  const openings = useQueueCount(['pending-approvals'], listPendingApprovals);
  const workflows = useQueueCount(['admin', 'workflow-reviews'], listPendingWorkflowReviews);
  const offers = useQueueCount(['admin', 'offer-approvals'], listOfferApprovals);
  const questions = useQueueCount(['admin', 'bank-review-queue'], listAdminQuestionReviews);
  const allFailed = [openings, workflows, offers, questions].every((q) => q.failed);

  return (
    <Section
      title="Waiting on you"
      description="Items your HR managers have submitted for a decision. Each opens its queue."
      data-testid="waiting-on-you"
    >
      {allFailed ? (
        <p role="alert" className="text-[13.5px] text-muted-foreground">
          Could not load the approval queues just now. Refresh to try again.
        </p>
      ) : (
        <StatGroup aria-label="Waiting on you" missingLabel="Count unavailable">
          <Stat
            label="Openings"
            value={openings.count}
            to="/superadmin/approvals"
            data-testid="waiting-openings"
          />
          <Stat
            label="Workflows"
            value={workflows.count}
            to="/superadmin/workflow-reviews"
            data-testid="waiting-workflows"
          />
          <Stat
            label="Offers"
            value={offers.count}
            to="/superadmin/offer-approvals"
            data-testid="waiting-offers"
          />
          <Stat
            label="Questions"
            value={questions.count}
            to="/superadmin/question-reviews"
            data-testid="waiting-questions"
          />
        </StatGroup>
      )}
    </Section>
  );
}

export default function CompanyAdminConsole() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => getMe() });
  const companyName = me?.company_name ?? 'your company';
  const { data: hrRows } = useQuery({ queryKey: ['my-hr-managers'], queryFn: listMyHrManagers });
  const { data: ivRows } = useQuery({ queryKey: ['my-interviewers'], queryFn: listMyInterviewers });
  const [hrOpen, setHrOpen] = useState(false);
  const [interviewerOpen, setInterviewerOpen] = useState(false);

  return (
    <Page width="default">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <PageHeader
        title={<span data-testid="page-title">Super Admin</span>}
        description={
          <span className="flex items-center gap-1.5">
            <Building2 size={14} className="text-[var(--ui-info)]" aria-hidden="true" />
            HR managers for {companyName}.
          </span>
        }
        actions={
          <Button type="button" onClick={() => setHrOpen(true)}>
            <UserPlus aria-hidden="true" />
            New HR manager
          </Button>
        }
      />

      {/* The first thing a super admin needs: what is waiting for a decision. */}
      <WaitingOnYou />

      {/* The company's public careers board address. The slug is per-COMPANY, so
          this console is its other natural owner alongside /hr — and it has to be
          its own instance rather than a shared link, because HRRoute admits only
          hr_manager and SuperAdminRoute only super_admin. Same pattern the repo
          already uses for /superadmin/library. */}
      <CareersLinkCard slug={me?.company_slug} companyName={me?.company_name} />

      {/* Headcounts come from the two roster queries below (same keys, so the
          cache serves both — no extra requests). A roster still loading is
          unknown, not zero. */}
      <StatGroup aria-label="Team summary">
        <Stat label="HR managers" value={hrRows?.length} />
        <Stat label="Interviewers" value={ivRows?.length} />
        <Stat
          label="Awaiting first login"
          value={
            hrRows && ivRows
              ? [...hrRows, ...ivRows].filter((r) => r.must_change_password).length
              : undefined
          }
        />
      </StatGroup>

      <StaffRoster
        idPrefix="hr"
        icon={Users}
        heading="HR managers"
        createTitle="Create an HR manager"
        createBlurb={(name) => `They log in, reset the password, then run hiring for ${name}.`}
        addLabel="Add HR"
        openLabel="New HR manager"
        showOpenButton={false}
        emptyText="No HR managers yet - add your first one above."
        listLabel="HR managers"
        companyName={companyName}
        queryKey="my-hr-managers"
        listFn={listMyHrManagers}
        createFn={createMyHrManager}
        deleteFn={deleteMyHrManager}
        createOpen={hrOpen}
        onCreateOpenChange={setHrOpen}
      />

      {/* ── Interviewers (D4-1) ──────────────────────────────────────────── */}
      <StaffRoster
        idPrefix="interviewer"
        icon={ClipboardCheck}
        heading="Interviewers"
        createTitle="Create an interviewer"
        createBlurb={(name) => `They see only the interviews assigned to them at ${name}.`}
        addLabel="Add interviewer"
        openLabel="New interviewer"
        showOpenButton
        emptyText="No interviewers yet - add your first one above."
        listLabel="Interviewers"
        companyName={companyName}
        // Removing an interviewer is destructive beyond the roster row: say so
        // where the confirm control for it lives.
        removalNote="Removing an interviewer also withdraws their unsubmitted interview assignments."
        queryKey="my-interviewers"
        listFn={listMyInterviewers}
        createFn={createMyInterviewer}
        deleteFn={deleteMyInterviewer}
        createOpen={interviewerOpen}
        onCreateOpenChange={setInterviewerOpen}
      />
    </Page>
  );
}
