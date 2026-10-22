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
  UserMinus,
  Send,
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
  resendMyHrManagerInvite,
  resendMyInterviewerInvite,
  type HrManager,
  type ResendInviteResult,
} from '@/api/hr';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { QueryState } from '@/components/QueryState';
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

/** The account status every staff roster shows, worded once. */
const PENDING_FIRST_LOGIN = 'Pending first login';

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
  singular,
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
  resendFn,
  createOpen,
  onCreateOpenChange,
}: {
  idPrefix: string;
  icon: LucideIcon;
  heading: string;
  /** One person in this roster, in sentence case: "HR manager", "Interviewer". */
  singular: string;
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
  /** Shown once, under the list: what removing someone also does. */
  removalNote: string;
  queryKey: string;
  listFn: () => Promise<HrManager[]>;
  createFn: (body: { email: string; full_name: string }) => Promise<HrManager>;
  deleteFn: (userId: string) => Promise<void>;
  resendFn: (userId: string) => Promise<ResendInviteResult>;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  // "HR manager" keeps its acronym mid-sentence; "Interviewer" does not.
  const inSentence = singular.startsWith('HR') ? singular : singular.toLowerCase();
  const pluralInSentence = heading.startsWith('HR') ? heading : heading.toLowerCase();

  const roster = useQuery({ queryKey: [queryKey], queryFn: listFn });
  const [filter, setFilter] = useState('');
  const needle = filter.trim().toLowerCase();

  const createMut = useMutation({
    mutationFn: (v: { email: string; fullName: string }) =>
      createFn({ email: v.email.trim(), full_name: v.fullName.trim() }),
    onSuccess: (created, v) => {
      // The server says whether the set-password email was queued (AD-F8):
      // sending is best-effort, so never claim it went when it did not.
      if (created.email_queued === false) {
        toast.warning(
          `${singular} ${v.email} was created, but the set-password email could not be sent. Use Resend link on their row.`,
        );
      } else {
        toast.success(`${singular} ${v.email} created. A set-password link is on its way`);
      }
      onCreateOpenChange(false);
      void qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (err: unknown) => toast.error(errorMessage(err, `Could not add this ${inSentence}.`)),
  });

  const deleteMut = useMutation({
    mutationFn: (userId: string) => deleteFn(userId),
    onSuccess: () => {
      toast.success(`${singular} removed`);
      void qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (err: unknown) =>
      toast.error(errorMessage(err, `Could not remove this ${inSentence}.`)),
  });

  const resendMut = useMutation({
    mutationFn: (row: HrManager) => resendFn(row.user_id),
    onSuccess: (res, row) => {
      if (res.email_queued) {
        toast.success(`Set-password link sent again to ${row.email}`);
      } else {
        toast.warning(`Could not send the set-password email to ${row.email}. Try again later.`);
      }
    },
    onError: (err: unknown) =>
      toast.error(errorMessage(err, 'Could not resend the set-password link.')),
  });

  return (
    <Card
      as="section"
      title={heading}
      description={createBlurb(companyName)}
      actions={
        showOpenButton ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => onCreateOpenChange(true)}
            className="gap-1.5"
          >
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

      {/* A failed load is an error with a retry, never "No HR managers yet"
          (AD-F6): the summary above already says the count is unavailable. */}
      <QueryState
        query={roster}
        errorTitle={`Could not load ${pluralInSentence}.`}
        loading={
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-14 w-full rounded-[14px]" />
            <Skeleton className="h-14 w-full rounded-[14px]" />
          </div>
        }
        isEmpty={(rows) => rows.length === 0}
        empty={<EmptyState icon={icon} title={emptyText} bordered={false} />}
      >
        {(rows) => {
          const shown = rows.filter(
            (r) =>
              !needle ||
              r.full_name.toLowerCase().includes(needle) ||
              r.email.toLowerCase().includes(needle),
          );
          return (
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
                          <>
                            <Badge variant="outline" className="text-xs">
                              {PENDING_FIRST_LOGIN}
                            </Badge>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="gap-1"
                              aria-label={`Resend the set-password link to ${row.full_name}`}
                              disabled={
                                resendMut.isPending && resendMut.variables?.user_id === row.user_id
                              }
                              onClick={() => resendMut.mutate(row)}
                            >
                              <Send className="h-3.5 w-3.5" aria-hidden="true" />
                              Resend link
                            </Button>
                          </>
                        ) : (
                          <Badge variant="success" className="gap-1 text-xs">
                            <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                            Active
                          </Badge>
                        )}
                        <ConfirmDeleteButton
                          title={`Remove ${row.full_name}`}
                          confirmText="Remove"
                          confirmGroupLabel={`Permanently remove ${row.full_name}`}
                          icon={UserMinus}
                          pending={deleteMut.isPending && deleteMut.variables === row.user_id}
                          onConfirm={() => deleteMut.mutate(row.user_id)}
                        />
                      </>
                    }
                  />
                ))}
              </RowList>
            </>
          );
        }}
      </QueryState>

      <p className="mt-4 flex items-center gap-1.5 text-[13px] text-muted-foreground">
        <KeyRound className="h-3.5 w-3.5 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />A
        secure “set your password” link is emailed to them - no password is shared here. The link
        expires, so use Resend link if it did not reach them in time.
      </p>
      <p className="mt-2 text-[12.5px] text-muted-foreground">{removalNote}</p>
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
function useQueueCount(
  queryKey: readonly string[],
  queryFn: () => Promise<unknown[]>,
): {
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
        title={<span data-testid="page-title">Team</span>}
        description={
          <span className="flex items-center gap-1.5">
            <Building2 size={14} className="text-[var(--ui-info)]" aria-hidden="true" />
            HR managers and interviewers for {companyName}.
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
          label={PENDING_FIRST_LOGIN}
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
        singular="HR manager"
        createTitle="Create an HR manager"
        createBlurb={(name) => `They log in, reset the password, then run hiring for ${name}.`}
        addLabel="Add HR"
        openLabel="New HR manager"
        showOpenButton={false}
        emptyText="No HR managers yet - add your first one above."
        listLabel="HR managers"
        companyName={companyName}
        // Removal is permanent and, because HR managers interview too, takes
        // their open interview assignments with it (AD-F7): same note as below.
        removalNote="Removing an HR manager is permanent and also withdraws their unsubmitted interview assignments."
        queryKey="my-hr-managers"
        listFn={listMyHrManagers}
        createFn={createMyHrManager}
        deleteFn={deleteMyHrManager}
        resendFn={resendMyHrManagerInvite}
        createOpen={hrOpen}
        onCreateOpenChange={setHrOpen}
      />

      {/* ── Interviewers (D4-1) ──────────────────────────────────────────── */}
      <StaffRoster
        idPrefix="interviewer"
        icon={ClipboardCheck}
        heading="Interviewers"
        singular="Interviewer"
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
        removalNote="Removing an interviewer is permanent and also withdraws their unsubmitted interview assignments."
        queryKey="my-interviewers"
        listFn={listMyInterviewers}
        createFn={createMyInterviewer}
        deleteFn={deleteMyInterviewer}
        resendFn={resendMyInterviewerInvite}
        createOpen={interviewerOpen}
        onCreateOpenChange={setInterviewerOpen}
      />
    </Page>
  );
}
