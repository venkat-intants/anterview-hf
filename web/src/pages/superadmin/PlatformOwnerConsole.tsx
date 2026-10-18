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
  type Company,
  type FeatureFlag,
  type AuditEvent,
} from '@/api/hr';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CompanyDialog, AccountDialog } from '@/components/superadmin/CreateDialogs';
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
import { EmptyState, PageHeader, SectionCard, Toolbar } from '@/design/components/page';
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

  // getCompanyAdmin 404s when none exists yet — treat any error as "no admin".
  const { data: admin, isLoading } = useQuery({
    queryKey: ['company-admin', company.id],
    queryFn: () => getCompanyAdmin(company.id),
    retry: false,
    throwOnError: false,
  });

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
    onSuccess: (_created, v) => {
      toast.success(
        `Super admin ${v.email} created for ${company.name} - a set-password link was emailed.`,
      );
      setCreateOpen(false);
      void qc.invalidateQueries({ queryKey: ['company-admin', company.id] });
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not create super admin.'),
  });

  const removeAdminMut = useMutation({
    mutationFn: () => deleteCompanyAdmin(company.id),
    onSuccess: () => {
      toast.success('Super admin removed.');
      void qc.invalidateQueries({ queryKey: ['company-admin', company.id] });
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not remove super admin.'),
  });

  const deleteCompanyMut = useMutation({
    mutationFn: () => deleteCompany(company.id),
    onSuccess: () => {
      toast.success(`Company "${company.name}" deleted.`);
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
      onDeleted();
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not delete company.'),
  });

  const hasAdmin = !!admin;

  return (
    <SectionCard
      title={`Super admin - ${company.name}`}
      icon={ShieldCheck}
      description="One super admin per company. They log in, reset the password, then create HR managers."
      action={
        /* Delete the whole company (and all its member logins) */
        <ConfirmDeleteButton
          label="Delete company"
          pending={deleteCompanyMut.isPending}
          onConfirm={() => deleteCompanyMut.mutate()}
        />
      }
      className="space-y-4"
    >
      {isLoading ? (
        <Skeleton className="h-16 w-full rounded-[14px]" />
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
              title={`Remove super admin ${admin.email}`}
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
            description="They will manage the company's HR managers."
            action={
              <Button type="button" onClick={() => setCreateOpen(true)} className="gap-1.5">
                <UserPlus className="h-4 w-4" aria-hidden="true" />
                Add super admin
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
        description="One per company. They create the company's HR managers."
        submitLabel="Create super admin"
        emailPlaceholder="admin@company.com"
        namePlaceholder="Company Super Admin"
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
                    pending
                  </Badge>
                ) : (
                  <Badge variant="success" className="shrink-0 text-xs">
                    active
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

  const {
    data: flags,
    isLoading,
    isError,
  } = useQuery<FeatureFlag[]>({
    queryKey: ['feature-flags'],
    queryFn: listFeatureFlags,
    retry: false,
    throwOnError: false,
  });

  const toggleMut = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      setFeatureFlag(key, enabled),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['feature-flags'] });
      toast.success('Feature flag updated.');
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not update flag.'),
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

      {!isLoading && isError && (
        <GlassCard className="p-6">
          <div className="flex items-start gap-3">
            <AlertTriangle
              className="mt-0.5 h-5 w-5 shrink-0 text-[var(--ui-warn)]"
              aria-hidden="true"
            />
            <div>
              <p className="text-[14px] font-semibold text-foreground">
                Feature flags unavailable - run the latest migration.
              </p>
              <p className="mt-1 text-[12.5px] text-muted-foreground">
                The{' '}
                <code className="rounded bg-[var(--ui-inset)] px-1 py-0.5 font-mono text-[12px] text-foreground">
                  feature_flags
                </code>{' '}
                table does not exist yet. Apply the pending schema migration and reload.
              </p>
            </div>
          </div>
        </GlassCard>
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
              <ToggleSwitch
                checked={flag.enabled}
                label={`Toggle ${flag.label}`}
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
  const { data: events, isLoading } = useQuery<AuditEvent[]>({
    queryKey: ['audit-log'],
    queryFn: () => listAuditLog(100),
    retry: false,
    throwOnError: false,
  });

  return (
    <GlassCard className="mt-5 p-5">
      <h2 className="text-[17px] font-bold tracking-[-0.2px] text-foreground">DPDP audit log</h2>
      <p className="mb-4 mt-1 text-[13.5px] text-muted-foreground">
        The most recent 100 admin actions and consent changes across the platform.
      </p>

      {isLoading && (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-12 w-full rounded-[10px] bg-[var(--ui-inset)]" />
          ))}
        </div>
      )}

      {!isLoading && (!events || events.length === 0) && (
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
                <span className="text-[var(--ui-faint)]">· {ev.actor}</span>
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
  const { data: stats } = useQuery({
    queryKey: ['platform-stats'],
    queryFn: getPlatformStats,
  });

  const createMut = useMutation({
    mutationFn: (name: string) => createCompany(name.trim()),
    onSuccess: (c) => {
      toast.success(`Company "${c.name}" created.`);
      setCompanyDialogOpen(false);
      setSelectedId(c.id);
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not create company.'),
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

  const pendingAdmins = Math.max(0, (stats?.companies ?? 0) - (stats?.super_admins ?? 0));
  const statTiles: {
    label: string;
    value: string;
    delta: string;
    trend: 'up' | 'down' | 'flat';
  }[] = [
    {
      label: 'Companies',
      value: String(stats?.companies ?? 0),
      delta: `${stats?.super_admins ?? 0} with a super admin`,
      trend: 'flat',
    },
    {
      label: 'Super admins',
      value: String(stats?.super_admins ?? 0),
      delta: pendingAdmins > 0 ? `${pendingAdmins} company without one` : 'all companies covered',
      trend: pendingAdmins > 0 ? 'down' : 'flat',
    },
    {
      label: 'HR managers',
      value: String(stats?.hr_managers ?? 0),
      delta: 'across all tenants',
      trend: 'flat',
    },
    {
      label: 'Interviews',
      value: String(stats?.interviews_total ?? 0),
      delta: `${stats?.interviews_30d ?? 0} in last 30 days`,
      trend: (stats?.interviews_30d ?? 0) > 0 ? 'up' : 'flat',
    },
  ];

  return (
    <div className="mx-auto max-w-[1280px] space-y-6 px-0 py-2">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <PageHeader
        className="mb-0"
        title={<span data-testid="page-title">Platform Owner</span>}
        description="Tenants, company super admins and platform governance."
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
    </div>
  );
}
