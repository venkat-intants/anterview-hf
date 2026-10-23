// PlatformOwnerConsole — the Intants core ("super super admin") view.
//
// Tier: platform_owner. Manages tenant COMPANIES and creates the ONE
// super admin per company. Company super admins then create their own HR
// managers (see CompanyAdminConsole).
//
// Layout:
//   • Page header (title + New company button, which opens a dialog)
//   • 4 platform-wide stat tiles (live counts)
//   • SegTabs: Companies / Feature flags / Audit log
//   • Companies tab: table (Tenant / Super admin / HR managers / Slug / Created /
//     Status) with row-click → company-admin panel below (its create form is a dialog too)
//   • Feature flags tab: live (listFeatureFlags + setFeatureFlag)
//   • Audit log tab: live (listAuditLog)
//
// Shell: bare content — AppShell is provided by the router (no double-wrap).

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Building2,
  UserPlus,
  Users,
  CheckCircle2,
  KeyRound,
  ShieldCheck,
  Plus,
  Search,
  ToggleLeft,
  AlertTriangle,
  ClipboardList,
  UserMinus,
  Send,
  Trash2,
} from '@/design/components/icons';
import {
  listCompanies,
  createCompany,
  deleteCompany,
  getCompanyAdmin,
  createCompanyAdmin,
  deleteCompanyAdmin,
  listCompanyHrManagers,
  getPlatformStats,
  listFeatureFlags,
  setFeatureFlag,
  listAuditLog,
  resendCompanyAdminInvite,
  type Company,
  type FeatureFlag,
  type AuditEvent,
} from '@/api/hr';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { errorMessage } from '@/lib/errorMessage';
import { ApiError } from '@/api/client';
import { QueryErrorPanel } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  CompanyDialog,
  AccountDialog,
  DeleteCompanyDialog,
} from '@/components/superadmin/CreateDialogs';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import {
  GlassCard,
  StatCard,
  Avatar,
  SegTabs,
  ToggleSwitch,
  StatusTag,
  type TagTone,
} from '@/design/components/primitives';
import { EmptyState, Page, PageHeader, SectionCard, Toolbar } from '@/design/components/page';
import { ProgressiveList } from '@/design/components/collapsible';
import { gradientFor, initialsOf } from '@/design/data/shared';

// ── Tab definitions ────────────────────────────────────────────────────────────

const VIEW_TABS = [
  { key: 'companies', label: 'Companies' },
  { key: 'flags', label: 'Feature flags' },
  { key: 'audit', label: 'Audit log' },
] as const;

type ViewKey = (typeof VIEW_TABS)[number]['key'];

const STATUS_TONE: Record<string, TagTone> = {
  Active: 'forest',
  Trial: 'amber',
  Suspended: 'ember',
};

// ── Company-admin panel (manage the ONE super admin for the selected company) ──

function CompanyAdminPanel({ company, onDeleted }: { company: Company; onDeleted: () => void }) {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // getCompanyAdmin 404s when none exists yet. Only that 404 means "no super
  // admin": any other failure is an error with a retry (AD-F5), because
  // offering "Add super admin" on a 500 leads straight to a 409.
  const adminQuery = useQuery({
    queryKey: ['company-admin', company.id],
    queryFn: async () => {
      try {
        return await getCompanyAdmin(company.id);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    retry: false,
    throwOnError: false,
  });
  const admin = adminQuery.data ?? null;

  // Read-only view of the company's HR managers (created by its super admin).
  const { data: hrs } = useQuery({
    queryKey: ['company-hr-managers', company.id],
    queryFn: () => listCompanyHrManagers(company.id),
    retry: false,
    throwOnError: false,
  });

  const createMut = useMutation({
    mutationFn: (v: { email: string; fullName: string }) =>
      // No password sent: the server generates a random bootstrap hash and
      // emails a "set your password" link. Never ship a known default.
      createCompanyAdmin(company.id, {
        email: v.email.trim(),
        full_name: v.fullName.trim(),
      }),
    onSuccess: (created, v) => {
      // Sending is best-effort; say so when it did not go (AD-F8).
      if (created.email_queued === false) {
        toast.warning(
          `Super admin ${v.email} was created for ${company.name}, but the set-password email could not be sent. Use Resend link.`,
        );
      } else {
        toast.success(
          `Super admin ${v.email} created for ${company.name}. A set-password link is on its way`,
        );
      }
      setCreateOpen(false);
      void qc.invalidateQueries({ queryKey: ['company-admin', company.id] });
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) => toast.error(errorMessage(err, 'Could not create the super admin.')),
  });

  const resendMut = useMutation({
    mutationFn: () => resendCompanyAdminInvite(company.id),
    onSuccess: (res) => {
      if (res.email_queued) toast.success('Set-password link sent again');
      else toast.warning('Could not send the set-password email. Try again later.');
    },
    onError: (err: unknown) =>
      toast.error(errorMessage(err, 'Could not resend the set-password link.')),
  });

  const removeAdminMut = useMutation({
    mutationFn: () => deleteCompanyAdmin(company.id),
    onSuccess: () => {
      toast.success('Super admin removed');
      void qc.invalidateQueries({ queryKey: ['company-admin', company.id] });
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) => toast.error(errorMessage(err, 'Could not remove the super admin.')),
  });

  const deleteCompanyMut = useMutation({
    mutationFn: () => deleteCompany(company.id),
    onSuccess: () => {
      toast.success(`Company "${company.name}" deleted`);
      setDeleteOpen(false);
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
      void qc.invalidateQueries({ queryKey: ['audit-log'] });
      onDeleted();
    },
    onError: (err: unknown) => toast.error(errorMessage(err, 'Could not delete the company.')),
  });

  const hasAdmin = admin !== null;
  const memberSummary = [
    company.has_admin ? '1 super admin' : null,
    `${company.hr_count} HR ${company.hr_count === 1 ? 'manager' : 'managers'}`,
    'and every interviewer',
  ]
    .filter(Boolean)
    .join(', ');

  return (
    <SectionCard
      title={`${company.name} · Super admin`}
      icon={ShieldCheck}
      description="One super admin per company. They sign in, set a password, then create HR managers and interviewers."
      action={
        /* Delete the whole company (and all its member logins): a dialog that
           states the consequences and asks for the name (AD-F13). */
        <Button
          type="button"
          variant="outline"
          className="gap-1.5 text-[var(--ui-danger)]"
          onClick={() => setDeleteOpen(true)}
        >
          <Trash2 className="h-4 w-4" aria-hidden="true" />
          Delete company
        </Button>
      }
      className="space-y-4"
    >
      <DeleteCompanyDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        companyName={company.name}
        memberSummary={memberSummary}
        pending={deleteCompanyMut.isPending}
        onConfirm={() => deleteCompanyMut.mutate()}
      />
      {adminQuery.isLoading ? (
        <Skeleton className="h-16 w-full rounded-[14px]" />
      ) : adminQuery.isError ? (
        <QueryErrorPanel
          title="Could not load this company's super admin."
          error={adminQuery.error}
          onRetry={() => void adminQuery.refetch()}
          retrying={adminQuery.isFetching}
        />
      ) : hasAdmin ? (
        /* Existing super admin — read-only card */
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-[14px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2.5"
          role="status"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <Avatar
              initials={initialsOf(admin.full_name)}
              gradient={gradientFor(admin.user_id.charCodeAt(0))}
              size={32}
            />
            <div className="min-w-0">
              <p className="truncate text-[14px] font-medium text-foreground">{admin.full_name}</p>
              <p className="truncate text-[12.5px] text-muted-foreground">{admin.email}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {admin.must_change_password ? (
              <>
                <Badge variant="outline" className="text-xs">
                  Pending first sign-in
                </Badge>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="gap-1"
                  aria-label={`Resend the set-password link to ${admin.full_name}`}
                  disabled={resendMut.isPending}
                  onClick={() => resendMut.mutate()}
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
              title={`Remove super admin ${admin.email}`}
              confirmText="Remove"
              icon={UserMinus}
              pending={removeAdminMut.isPending}
              onConfirm={() => removeAdminMut.mutate()}
            />
          </div>
        </div>
      ) : (
        <>
          <EmptyState
            icon={UserPlus}
            title="This company has no super admin yet"
            description="They will manage the company's HR managers and interviewers."
            action={
              <Button type="button" onClick={() => setCreateOpen(true)} className="gap-1.5">
                <UserPlus className="h-4 w-4" aria-hidden="true" />
                New super admin
              </Button>
            }
          />

          {/* Credential-delivery hint */}
          <p className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5 text-[var(--ui-info)]" aria-hidden="true" />A secure
            “set your password” link is emailed to them - no password is shared here.
          </p>
        </>
      )}

      <AccountDialog
        open={createOpen}
        onOpenChange={(next) => {
          if (next) createMut.reset();
          setCreateOpen(next);
        }}
        idPrefix="sa"
        title={`Add a super admin for ${company.name}`}
        description="One per company. They create the company's HR managers and interviewers."
        submitLabel="Create super admin"
        emailPlaceholder="admin@company.com"
        namePlaceholder="Company super admin"
        pending={createMut.isPending}
        error={createMut.isError ? createMut.error : null}
        onSubmit={(v) => createMut.mutate(v)}
      />

      {/* Read-only HR managers (the super admin creates these for the company) */}
      {hrs && hrs.length > 0 && (
        <div className="border-t border-border pt-4">
          <p className="mb-2 flex items-center gap-1.5 text-[12px] font-medium text-[var(--ui-soft)]">
            <Users className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            HR managers ({hrs.length})
          </p>
          <div role="list" aria-label={`HR managers for ${company.name}`}>
            {hrs.map((hr) => (
              <div
                key={hr.user_id}
                role="listitem"
                className="flex items-center justify-between rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 mb-1.5 last:mb-0"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Avatar
                    initials={initialsOf(hr.full_name)}
                    gradient={gradientFor(hr.user_id.charCodeAt(0))}
                    size={26}
                  />
                  <span className="text-[12.5px] text-foreground truncate">{hr.full_name}</span>
                  <span className="text-[12px] text-[var(--ui-faint)] truncate">{hr.email}</span>
                </div>
                {hr.must_change_password ? (
                  <Badge variant="outline" className="shrink-0 text-xs">
                    Pending first sign-in
                  </Badge>
                ) : (
                  <Badge variant="success" className="shrink-0 text-xs">
                    Active
                  </Badge>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

// ── Feature flags tab — live ──────────────────────────────────────────────────

function FeatureFlagsTab(): JSX.Element {
  const qc = useQueryClient();

  const flagsQuery = useQuery<FeatureFlag[]>({
    queryKey: ['feature-flags'],
    queryFn: listFeatureFlags,
    retry: false,
    throwOnError: false,
  });
  const { data: flags, isLoading, isError } = flagsQuery;

  const toggleMut = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      setFeatureFlag(key, enabled),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['feature-flags'] });
      void qc.invalidateQueries({ queryKey: ['audit-log'] });
      toast.success('Feature flag updated');
    },
    onError: (err: unknown) => toast.error(errorMessage(err, 'Could not update the feature flag.')),
  });

  return (
    <div className="mt-5 space-y-4" aria-live="polite">
      <div>
        <h2 className="text-[17px] font-bold tracking-[-0.2px] text-foreground">Feature flags</h2>
        <p className="mt-1 text-[13.5px] text-muted-foreground">
          Toggle platform features globally.
        </p>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 w-full rounded-[24px] bg-[var(--ui-inset)]" />
          ))}
        </div>
      )}

      {/* Any failure, said as it is (AD-F19): the old copy blamed a missing
          migration for every error, including a 403 or a dropped connection. */}
      {!isLoading && isError && (
        <QueryErrorPanel
          title="Could not load the feature flags."
          error={flagsQuery.error}
          onRetry={() => void flagsQuery.refetch()}
          retrying={flagsQuery.isFetching}
        />
      )}

      {!isLoading && !isError && flags && flags.length === 0 && (
        <GlassCard className="p-4">
          <EmptyState icon={ToggleLeft} title="No feature flags defined yet." />
        </GlassCard>
      )}

      {!isLoading && !isError && flags && flags.length > 0 && (
        <div
          className="grid grid-cols-1 gap-4 md:grid-cols-2"
          role="list"
          aria-label="Feature flags"
        >
          {flags.map((flag) => (
            <GlassCard key={flag.key} className="flex items-center gap-4 p-5">
              <span
                className="flex h-11 w-11 flex-none items-center justify-center rounded-[12px] bg-[var(--ui-inset)] text-muted-foreground"
                aria-hidden="true"
              >
                <Building2 size={20} />
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-[14.5px] font-semibold text-foreground truncate">{flag.label}</p>
                {flag.description && (
                  <p className="text-[12.5px] text-muted-foreground truncate">{flag.description}</p>
                )}
                <p className="mt-0.5 font-mono text-[12px] text-[var(--ui-faint)]">{flag.key}</p>
              </div>
              {/* Locked while its own update is in flight, so rapid clicks
                  cannot race each other to the server (AD-F19). */}
              <ToggleSwitch
                checked={flag.enabled}
                label={`Toggle ${flag.label}`}
                disabled={toggleMut.isPending && toggleMut.variables?.key === flag.key}
                onChange={(next) => toggleMut.mutate({ key: flag.key, enabled: next })}
              />
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Audit log tab helpers ─────────────────────────────────────────────────────

type AuditKind = 'admin_action' | 'consent_granted' | 'consent_denied' | 'consent_revoked';

const KIND_TONE: Record<AuditKind, TagTone> = {
  admin_action: 'electric',
  consent_granted: 'forest',
  consent_denied: 'ember',
  consent_revoked: 'ember',
};

const KIND_LABEL: Record<AuditKind, string> = {
  admin_action: 'Admin action',
  consent_granted: 'Consent granted',
  consent_denied: 'Consent denied',
  consent_revoked: 'Consent revoked',
};

function toneForKind(kind: string): TagTone {
  return (KIND_TONE as Record<string, TagTone>)[kind] ?? 'neutral';
}

function labelForKind(kind: string): string {
  return (KIND_LABEL as Record<string, string>)[kind] ?? kind;
}

/** Who acted, as a reader expects it: staff by name, others by what they are. */
function actorLabel(actor: string): string {
  if (actor === 'candidate') return 'Candidate';
  if (actor === 'system') return 'System';
  return actor;
}

function relativeTs(isoTs: string): string {
  const diff = Date.now() - new Date(isoTs).getTime();
  if (diff < 0) return 'just now';
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(isoTs).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

// ── Audit log tab — live ──────────────────────────────────────────────────────

function AuditLogTab(): JSX.Element {
  const auditQuery = useQuery<AuditEvent[]>({
    queryKey: ['audit-log'],
    queryFn: () => listAuditLog(100),
    retry: false,
    throwOnError: false,
  });
  const { data: events, isLoading, isError } = auditQuery;

  return (
    <GlassCard className="mt-5 p-5">
      <h2 className="text-[17px] font-bold tracking-[-0.2px] text-foreground">DPDP audit log</h2>
      <p className="mb-4 mt-1 text-[13.5px] text-muted-foreground">
        The most recent 100 events across the platform: companies, super admins, HR managers and
        interviewers added or removed, set-password links re-sent, feature flags switched, and
        candidate consent changes.
      </p>

      {isLoading && (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-12 w-full rounded-[10px] bg-[var(--ui-inset)]" />
          ))}
        </div>
      )}

      {/* A failed load is never "No audit events yet" (AD-F4): an empty
          compliance log is a claim someone may act on. */}
      {!isLoading && isError && !events && (
        <QueryErrorPanel
          title="Could not load the audit log."
          error={auditQuery.error}
          onRetry={() => void auditQuery.refetch()}
          retrying={auditQuery.isFetching}
        />
      )}

      {!isLoading && !isError && (!events || events.length === 0) && (
        <EmptyState icon={ClipboardList} title="No audit events yet." />
      )}

      {!isLoading && events && events.length > 0 && (
        <ProgressiveList
          items={events}
          initial={20}
          noun="events"
          aria-label="Audit event list"
          className="flex flex-col"
          getKey={(ev, idx) => `${ev.ts}-${idx}`}
          renderItem={(ev) => (
            <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border py-3 last:border-0">
              <span className="shrink-0 font-mono text-[12px] text-[var(--ui-faint)]">
                {relativeTs(ev.ts)}
              </span>
              <StatusTag tone={toneForKind(ev.kind)} dot>
                {labelForKind(ev.kind)}
              </StatusTag>
              <div className="min-w-0 flex-1 break-words text-[13px] max-sm:order-last max-sm:basis-full">
                <span className="text-foreground">{ev.summary}</span>{' '}
                <span className="text-[var(--ui-faint)]">· {actorLabel(ev.actor)}</span>
              </div>
              <time
                dateTime={ev.ts}
                className="shrink-0 font-mono text-[12px] text-muted-foreground tabular-nums"
                title={new Date(ev.ts).toLocaleString('en-IN')}
              >
                {new Date(ev.ts).toLocaleTimeString('en-IN', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </time>
            </li>
          )}
        />
      )}
    </GlassCard>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PlatformOwnerConsole() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [companyDialogOpen, setCompanyDialogOpen] = useState(false);
  const [view, setView] = useState<ViewKey>('companies');
  const [companyFilter, setCompanyFilter] = useState('');

  const {
    data: companies,
    isLoading,
    isError: companiesError,
  } = useQuery({
    queryKey: ['companies'],
    queryFn: listCompanies,
  });

  // Real platform-wide counts for the stat tiles (no dummy data).
  const { data: stats, isError: statsError } = useQuery({
    queryKey: ['platform-stats'],
    queryFn: getPlatformStats,
  });

  const createMut = useMutation({
    mutationFn: (name: string) => createCompany(name.trim()),
    onSuccess: (c) => {
      toast.success(`Company "${c.name}" created`);
      setCompanyDialogOpen(false);
      setSelectedId(c.id);
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) => toast.error(errorMessage(err, 'Could not create the company.')),
  });

  const openCompanyDialog = () => {
    createMut.reset();
    setView('companies');
    setCompanyDialogOpen(true);
  };

  const selected = companies?.find((c) => c.id === selectedId) ?? null;
  const filterNeedle = companyFilter.trim().toLowerCase();
  const visibleCompanies = (companies ?? []).filter(
    (c) =>
      !filterNeedle ||
      c.name.toLowerCase().includes(filterNeedle) ||
      c.slug.toLowerCase().includes(filterNeedle) ||
      (c.admin_email ?? '').toLowerCase().includes(filterNeedle),
  );

  // A count that is loading or failed is unknown, never 0 (AD-F24): "0
  // companies, all covered" is the good news, so it must not be the fallback.
  const known = stats !== undefined;
  const unknownDelta = statsError ? 'Count unavailable' : 'Loading';
  const n = (v: number | undefined): string => (v === undefined ? '-' : String(v));
  const pendingAdmins = known ? Math.max(0, stats.companies - stats.super_admins) : 0;
  const statTiles: {
    label: string;
    value: string;
    delta: string;
    trend: 'up' | 'down' | 'flat';
  }[] = [
    {
      label: 'Companies',
      value: n(stats?.companies),
      delta: known ? `${stats.super_admins} with a super admin` : unknownDelta,
      trend: 'flat',
    },
    {
      label: 'Super admins',
      value: n(stats?.super_admins),
      delta: !known
        ? unknownDelta
        : pendingAdmins > 0
          ? `${pendingAdmins} ${pendingAdmins === 1 ? 'company' : 'companies'} without one`
          : 'All companies covered',
      trend: known && pendingAdmins > 0 ? 'down' : 'flat',
    },
    {
      label: 'HR managers',
      value: n(stats?.hr_managers),
      delta: known ? 'Across all companies' : unknownDelta,
      trend: 'flat',
    },
    {
      label: 'Interviews',
      value: n(stats?.interviews_total),
      delta: known ? `${stats.interviews_30d} in the last 30 days` : unknownDelta,
      trend: known && stats.interviews_30d > 0 ? 'up' : 'flat',
    },
  ];

  return (
    <Page width="default">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <PageHeader
        title={<span data-testid="page-title">Companies</span>}
        description="Tenants, their super admins, feature flags and the platform audit log."
        actions={
          <Button type="button" onClick={openCompanyDialog}>
            <Plus aria-hidden="true" />
            New company
          </Button>
        }
      />

      <CompanyDialog
        open={companyDialogOpen}
        onOpenChange={(next) => {
          if (next) createMut.reset();
          setCompanyDialogOpen(next);
        }}
        pending={createMut.isPending}
        error={createMut.isError ? createMut.error : null}
        onSubmit={(name) => createMut.mutate(name)}
      />

      {/* ── Platform-wide stat tiles (live counts) ───────────────────────── */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {statTiles.map((s) => (
          <StatCard key={s.label} {...s} />
        ))}
      </div>

      {/* ── Tab switcher ────────────────────────────────────────────────── */}
      <div className="max-w-full overflow-x-auto">
        <SegTabs
          tabs={VIEW_TABS as unknown as { key: string; label: string }[]}
          active={view}
          onChange={(k) => setView(k as ViewKey)}
        />
      </div>

      {/* TAB: Companies */}
      {view === 'companies' && (
        <div className="space-y-5">
          {/* Companies table */}
          {isLoading ? (
            <div className="space-y-2" aria-busy="true">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-14 w-full rounded-card" />
              ))}
            </div>
          ) : companiesError ? (
            <SectionCard title="Companies">
              <EmptyState
                icon={AlertTriangle}
                title="Could not load the companies"
                description="Check your connection and reload the page."
              />
            </SectionCard>
          ) : !companies || companies.length === 0 ? (
            <SectionCard title="Companies">
              <EmptyState
                icon={Building2}
                title="No companies yet"
                description="Create your first one to get started."
                action={
                  <Button type="button" onClick={openCompanyDialog} className="gap-1.5">
                    <Plus className="h-4 w-4" aria-hidden="true" />
                    New company
                  </Button>
                }
              />
            </SectionCard>
          ) : (
            <div>
              <Toolbar
                trailing={
                  <span className="text-[13px] tabular-nums text-muted-foreground">
                    {filterNeedle
                      ? `${visibleCompanies.length} of ${companies.length}`
                      : companies.length}{' '}
                    {companies.length === 1 ? 'company' : 'companies'}
                  </span>
                }
              >
                <div className="relative w-full max-w-[340px]">
                  <Search
                    size={15}
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ui-faint)]"
                    aria-hidden="true"
                  />
                  <input
                    type="search"
                    value={companyFilter}
                    onChange={(e) => setCompanyFilter(e.target.value)}
                    aria-label="Search companies"
                    placeholder="Search by name, slug or admin email"
                    className="min-h-10 w-full rounded-[10px] border border-input bg-card pl-9 pr-3 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  />
                </div>
              </Toolbar>
              <GlassCard className="overflow-x-auto p-0">
                <div className="min-w-[820px]">
                  {/* Table header */}
                  <div className="grid grid-cols-[1.8fr_1.6fr_0.9fr_1fr_1.1fr_0.9fr] gap-3 border-b border-border bg-[var(--ui-inset-soft)] px-6 py-3.5 text-[12px] font-semibold uppercase tracking-[0.5px] text-muted-foreground">
                    <div>Tenant</div>
                    <div>Super admin</div>
                    <div>HR managers</div>
                    <div>Slug</div>
                    <div>Created</div>
                    <div>Status</div>
                  </div>

                  {/* Table rows */}
                  {visibleCompanies.length === 0 ? (
                    <p className="px-6 py-8 text-center text-[13.5px] text-muted-foreground">
                      No company matches “{companyFilter.trim()}”.
                    </p>
                  ) : null}
                  {visibleCompanies.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setSelectedId(c.id === selectedId ? null : c.id)}
                      className={cn(
                        'grid w-full grid-cols-[1.8fr_1.6fr_0.9fr_1fr_1.1fr_0.9fr] items-center gap-3 text-left',
                        'border-b border-border px-6 py-3.5 transition-colors last:border-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]',
                        c.id === selectedId
                          ? 'bg-[var(--ui-info-wash)]'
                          : 'hover:bg-[var(--ui-inset-soft)]',
                      )}
                      aria-pressed={c.id === selectedId}
                      aria-label={`Select ${c.name}`}
                    >
                      <div className="flex items-center gap-3">
                        <Avatar
                          initials={initialsOf(c.name)}
                          gradient={gradientFor(c.name.charCodeAt(0))}
                          size={34}
                        />
                        <div className="min-w-0">
                          <p className="truncate text-[14px] font-medium text-foreground">
                            {c.name}
                          </p>
                        </div>
                      </div>
                      {/* Super admin (email or "needs one") */}
                      <div className="min-w-0">
                        {c.has_admin ? (
                          <span className="flex items-center gap-1.5 truncate text-[13px] text-[var(--ui-soft)]">
                            <ShieldCheck
                              className="h-3.5 w-3.5 shrink-0 text-[var(--ui-ok)]"
                              aria-hidden="true"
                            />
                            <span className="truncate">{c.admin_email}</span>
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-[13px] text-[var(--ui-warn)]">
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                            Needs a super admin
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 text-[14px] tabular-nums text-[var(--ui-soft)]">
                        <Users className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                        {c.hr_count}
                      </div>
                      <div className="truncate font-mono text-[12.5px] text-muted-foreground">
                        {c.slug}
                      </div>
                      <div className="font-mono text-[12.5px] text-muted-foreground">
                        {new Date(c.created_at).toLocaleDateString('en-IN', {
                          day: 'numeric',
                          month: 'short',
                          year: 'numeric',
                        })}
                      </div>
                      <div>
                        <StatusTag
                          tone={c.is_active ? STATUS_TONE['Active'] : STATUS_TONE['Suspended']}
                          dot={c.is_active}
                        >
                          {c.is_active ? 'Active' : 'Suspended'}
                        </StatusTag>
                      </div>
                    </button>
                  ))}
                </div>
              </GlassCard>
            </div>
          )}

          {/* Company-admin panel — keyed so its dialog resets per company */}
          {selected ? (
            <CompanyAdminPanel
              key={selected.id}
              company={selected}
              onDeleted={() => setSelectedId(null)}
            />
          ) : companies && companies.length > 0 ? (
            <GlassCard className="p-4">
              <EmptyState icon={ShieldCheck} title="Click a row above to manage its super admin." />
            </GlassCard>
          ) : null}
        </div>
      )}

      {view === 'flags' && <FeatureFlagsTab />}
      {view === 'audit' && <AuditLogTab />}
    </Page>
  );
}
