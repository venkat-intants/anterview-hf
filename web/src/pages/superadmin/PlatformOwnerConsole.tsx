// PlatformOwnerConsole — the Intants core ("super super admin") view.
//
// Tier: platform_owner. Manages tenant COMPANIES and creates the ONE
// super admin per company. Company super admins then create their own HR
// managers (see CompanyAdminConsole).
//
// Layout:
//   • Page header (title + Add tenant pill)
//   • 4 platform-wide stat tiles (design data)
//   • SegTabs: Companies / Feature flags / Audit log
//   • Companies tab: table (Tenant / Super admin / HR managers / Slug / Created /
//     Status) with row-click → company-admin panel below + create-company form
//   • Feature flags tab: live (listFeatureFlags + setFeatureFlag)
//   • Audit log tab: live (listAuditLog)
//
// Shell: bare content — AppShell is provided by the router (no double-wrap).

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  Building2,
  UserPlus,
  Users,
  CheckCircle2,
  KeyRound,
  ShieldCheck,
  Plus,
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
import { Input } from '@/components/ui/input';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import {
  GlassCard,
  StatCard,
  Avatar,
  SegTabs,
  Pill,
  ToggleSwitch,
  StatusTag,
  type TagTone,
} from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
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

function CompanyAdminPanel({
  company,
  onDeleted,
}: {
  company: Company;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');

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
    mutationFn: () =>
      // No password sent: the server generates a random bootstrap hash and
      // emails a "set your password" link. Never ship a known default.
      createCompanyAdmin(company.id, {
        email: email.trim(),
        full_name: fullName.trim(),
      }),
    onSuccess: () => {
      toast.success(`Super admin ${email} created for ${company.name} — a set-password link was emailed.`);
      setEmail('');
      setFullName('');
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
    <GlassCard className="p-5 space-y-4">
      {/* Panel header */}
      <div className="flex items-start gap-2">
        <span
          className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.12)] text-[#60a5fa]"
          aria-hidden="true"
        >
          <ShieldCheck size={17} />
        </span>
        <div className="flex-1 min-w-0">
          <h3 className="text-[15px] font-semibold text-white">
            Super admin — {company.name}
          </h3>
          <p className="text-[12px] text-[#888b91]">
            One super admin per company. They log in, reset the password, then create HR managers.
          </p>
        </div>
        {/* Delete the whole company (and all its member logins) */}
        <ConfirmDeleteButton
          label="Delete company"
          pending={deleteCompanyMut.isPending}
          onConfirm={() => deleteCompanyMut.mutate()}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-16 w-full rounded-[12px] bg-white/[0.05]" />
      ) : hasAdmin ? (
        /* Existing super admin — read-only card */
        <div
          className="flex items-center justify-between rounded-[14px] border border-white/[0.07] bg-white/[0.03] px-3 py-2.5"
          role="status"
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <Avatar
              initials={initialsOf(admin.full_name)}
              gradient={gradientFor(admin.user_id.charCodeAt(0))}
              size={30}
            />
            <div className="min-w-0">
              <p className="text-[13.5px] font-medium text-white truncate">{admin.full_name}</p>
              <p className="text-[12px] text-[#888b91] truncate">{admin.email}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {admin.must_change_password ? (
              <Badge variant="outline" className="text-xs">
                pending first login
              </Badge>
            ) : (
              <Badge variant="success" className="text-xs gap-1">
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
          {/* Create super-admin form */}
          <form
            className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (!email.trim() || !fullName.trim()) {
                toast.error('Email and name are required.');
                return;
              }
              createMut.mutate();
            }}
          >
            <div className="flex flex-col gap-1.5">
              <label htmlFor="sa-email" className="text-[12px] font-medium text-[#b8babf]">
                Email
              </label>
              <Input
                id="sa-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@company.com"
                aria-required="true"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="sa-name" className="text-[12px] font-medium text-[#b8babf]">
                Full name
              </label>
              <Input
                id="sa-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Company Super Admin"
                aria-required="true"
              />
            </div>
            <Button
              type="submit"
              disabled={createMut.isPending}
              className="gap-1.5"
              aria-busy={createMut.isPending}
            >
              <UserPlus className="h-4 w-4" aria-hidden="true" />
              {createMut.isPending ? 'Adding…' : 'Create super admin'}
            </Button>
          </form>

          {/* Credential-delivery hint */}
          <p className="flex items-center gap-1.5 text-[12px] text-[#888b91]">
            <KeyRound className="h-3 w-3 text-[#60a5fa]" aria-hidden="true" />
            A secure “set your password” link is emailed to them — no password is
            shared here.
          </p>
        </>
      )}

      {/* Read-only HR managers (the super admin creates these for the company) */}
      {hrs && hrs.length > 0 && (
        <div className="border-t border-white/[0.06] pt-4">
          <p className="mb-2 flex items-center gap-1.5 text-[12px] font-medium text-[#b8babf]">
            <Users className="h-3.5 w-3.5 text-[#888b91]" aria-hidden="true" />
            HR managers ({hrs.length})
          </p>
          <div role="list" aria-label={`HR managers for ${company.name}`}>
            {hrs.map((hr) => (
              <div
                key={hr.user_id}
                role="listitem"
                className="flex items-center justify-between rounded-[12px] border border-white/[0.06] bg-white/[0.02] px-3 py-2 mb-1.5 last:mb-0"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Avatar
                    initials={initialsOf(hr.full_name)}
                    gradient={gradientFor(hr.user_id.charCodeAt(0))}
                    size={26}
                  />
                  <span className="text-[12.5px] text-white truncate">{hr.full_name}</span>
                  <span className="text-[11.5px] text-[#70757c] truncate">{hr.email}</span>
                </div>
                {hr.must_change_password ? (
                  <Badge variant="outline" className="text-[10px] shrink-0">
                    pending
                  </Badge>
                ) : (
                  <Badge variant="success" className="text-[10px] shrink-0">
                    active
                  </Badge>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
}

// ── Feature flags tab — live ──────────────────────────────────────────────────

function FeatureFlagsTab(): JSX.Element {
  const qc = useQueryClient();

  const { data: flags, isLoading, isError } = useQuery<FeatureFlag[]>({
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
      <div className="flex items-center gap-2">
        <span
          className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.12)] text-[#60a5fa]"
          aria-hidden="true"
        >
          <ToggleLeft size={17} />
        </span>
        <div>
          <h2 className="text-[15px] font-semibold text-white">Feature flags</h2>
          <p className="text-[12px] text-[#888b91]">Toggle platform features globally.</p>
        </div>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 w-full rounded-[24px] bg-white/[0.05]" />
          ))}
        </div>
      )}

      {!isLoading && isError && (
        <GlassCard className="p-6">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[#ffb764]" aria-hidden="true" />
            <div>
              <p className="text-[14px] font-semibold text-white">
                Feature flags unavailable — run the latest migration.
              </p>
              <p className="mt-1 text-[12.5px] text-[#888b91]">
                The{' '}
                <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[11px] text-white">
                  feature_flags
                </code>{' '}
                table does not exist yet. Apply the pending schema migration and reload.
              </p>
            </div>
          </div>
        </GlassCard>
      )}

      {!isLoading && !isError && flags && flags.length === 0 && (
        <GlassCard className="p-8 text-center">
          <p className="text-[13px] text-[#888b91]">No feature flags defined yet.</p>
        </GlassCard>
      )}

      {!isLoading && !isError && flags && flags.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2" role="list" aria-label="Feature flags">
          {flags.map((flag) => (
            <GlassCard key={flag.key} className="flex items-center gap-4 p-5">
              <span
                className="flex h-11 w-11 flex-none items-center justify-center rounded-[12px] bg-white/[0.05] text-[#60a5fa]"
                aria-hidden="true"
              >
                <Building2 size={20} />
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-[14.5px] font-semibold text-white truncate">{flag.label}</p>
                {flag.description && (
                  <p className="text-[12.5px] text-[#888b91] truncate">{flag.description}</p>
                )}
                <p className="mt-0.5 font-mono text-[11px] text-[#5a5f66]">{flag.key}</p>
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
      <h3 className="mb-4 flex items-center gap-2 text-[16px] font-semibold text-white">
        <ShieldCheck size={17} className="text-[#27c93f]" aria-hidden="true" />
        DPDP audit log
      </h3>

      {isLoading && (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-12 w-full rounded-[10px] bg-white/[0.05]" />
          ))}
        </div>
      )}

      {!isLoading && (!events || events.length === 0) && (
        <div className="py-8 text-center">
          <ClipboardList className="mx-auto mb-3 h-8 w-8 text-[#5a5f66]" aria-hidden="true" />
          <p className="text-[13px] text-[#888b91]">No audit events yet.</p>
        </div>
      )}

      {!isLoading && events && events.length > 0 && (
        <div className="flex flex-col" aria-label="Audit event list" role="list">
          {events.map((ev, idx) => (
            <div
              key={idx}
              role="listitem"
              className="flex items-center gap-3 border-b border-white/[0.05] py-3 last:border-0"
            >
              <span className="font-mono text-[11.5px] text-[#5a5f66] shrink-0">
                {relativeTs(ev.ts)}
              </span>
              <StatusTag tone={toneForKind(ev.kind)} dot>
                {labelForKind(ev.kind)}
              </StatusTag>
              <div className="flex-1 text-[13px] min-w-0">
                <span className="text-white">{ev.summary}</span>{' '}
                <span className="text-[#70757c]">· {ev.actor}</span>
              </div>
              <time
                dateTime={ev.ts}
                className="shrink-0 font-mono text-[11.5px] text-[#888b91] tabular-nums"
                title={new Date(ev.ts).toLocaleString('en-IN')}
              >
                {new Date(ev.ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
              </time>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PlatformOwnerConsole() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newCompany, setNewCompany] = useState('');
  const [view, setView] = useState<ViewKey>('companies');

  const { data: companies, isLoading } = useQuery({
    queryKey: ['companies'],
    queryFn: listCompanies,
  });

  // Real platform-wide counts for the stat tiles (no dummy data).
  const { data: stats } = useQuery({
    queryKey: ['platform-stats'],
    queryFn: getPlatformStats,
  });

  const createMut = useMutation({
    mutationFn: () => createCompany(newCompany.trim()),
    onSuccess: (c) => {
      toast.success(`Company "${c.name}" created.`);
      setNewCompany('');
      setSelectedId(c.id);
      void qc.invalidateQueries({ queryKey: ['companies'] });
      void qc.invalidateQueries({ queryKey: ['platform-stats'] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not create company.'),
  });

  const selected = companies?.find((c) => c.id === selectedId) ?? null;

  const pendingAdmins = Math.max(0, (stats?.companies ?? 0) - (stats?.super_admins ?? 0));
  const statTiles: { label: string; value: string; delta: string; trend: 'up' | 'down' | 'flat' }[] = [
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
    <div className="mx-auto max-w-[1280px] px-0 py-2 space-y-6">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <Reveal>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">
              Platform Owner
            </h1>
            <p className="mt-1 text-[14px] text-[#888b91]">
              Tenants, company super admins and platform governance.
            </p>
          </div>
          <Pill
            variant="primary"
            className="px-5 py-2.5"
            type="button"
            onClick={() => {
              setView('companies');
              setTimeout(
                () =>
                  (
                    document.getElementById('new-company-input') as HTMLInputElement | null
                  )?.focus(),
                50,
              );
            }}
            aria-label="Add a new tenant company"
          >
            <Plus size={16} aria-hidden="true" />
            Add tenant
          </Pill>
        </div>
      </Reveal>

      {/* ── Platform-wide stat tiles (live counts) ───────────────────────── */}
      <Reveal delay={0.05}>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {statTiles.map((s) => (
            <StatCard key={s.label} {...s} />
          ))}
        </div>
      </Reveal>

      {/* ── Tab switcher ────────────────────────────────────────────────── */}
      <Reveal delay={0.08}>
        <SegTabs
          tabs={VIEW_TABS as unknown as { key: string; label: string }[]}
          active={view}
          onChange={(k) => setView(k as ViewKey)}
        />
      </Reveal>

      {/* TAB: Companies */}
      {view === 'companies' && (
        <div className="space-y-5">
          {/* Create company form */}
          <GlassCard className="p-4">
            <form
              className="flex gap-2 items-center"
              onSubmit={(e) => {
                e.preventDefault();
                if (!newCompany.trim()) {
                  toast.error('Company name is required.');
                  return;
                }
                createMut.mutate();
              }}
            >
              <Building2 className="h-4 w-4 text-[#60a5fa] shrink-0" aria-hidden="true" />
              <Input
                id="new-company-input"
                value={newCompany}
                onChange={(e) => setNewCompany(e.target.value)}
                placeholder="New company name"
                aria-label="New company name"
                className="flex-1"
              />
              <Button
                type="submit"
                disabled={createMut.isPending}
                className="gap-1.5 shrink-0"
                aria-busy={createMut.isPending}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                {createMut.isPending ? '…' : 'Create'}
              </Button>
            </form>
          </GlassCard>

          {/* Companies table */}
          {isLoading ? (
            <Skeleton className="h-48 w-full rounded-[24px] bg-white/[0.04]" />
          ) : !companies || companies.length === 0 ? (
            <GlassCard className="p-8 text-center">
              <p className="text-[13px] text-[#888b91]">
                No companies yet — create your first one above.
              </p>
            </GlassCard>
          ) : (
            <GlassCard className="overflow-hidden p-0">
              {/* Table header */}
              <div className="grid grid-cols-[1.8fr_1.6fr_0.9fr_1fr_1.1fr_0.9fr] gap-3 border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                <div>Tenant</div>
                <div>Super admin</div>
                <div>HR managers</div>
                <div>Slug</div>
                <div>Created</div>
                <div>Status</div>
              </div>

              {/* Table rows */}
              {companies.map((c) => (
                <motion.button
                  key={c.id}
                  type="button"
                  whileTap={{ scale: 0.995 }}
                  onClick={() => setSelectedId(c.id === selectedId ? null : c.id)}
                  className={cn(
                    'grid grid-cols-[1.8fr_1.6fr_0.9fr_1fr_1.1fr_0.9fr] items-center gap-3 w-full text-left',
                    'border-b border-white/[0.04] px-6 py-3.5 last:border-0 transition-colors',
                    c.id === selectedId
                      ? 'bg-[rgba(var(--accent-rgb),0.06)]'
                      : 'hover:bg-white/[0.02]',
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
                      <p className="text-[13.5px] font-medium text-white truncate">{c.name}</p>
                    </div>
                  </div>
                  {/* Super admin (email or "needs one") */}
                  <div className="min-w-0">
                    {c.has_admin ? (
                      <span className="flex items-center gap-1.5 text-[12.5px] text-[#b8babf] truncate">
                        <ShieldCheck className="h-3 w-3 shrink-0 text-[#27c93f]" aria-hidden="true" />
                        <span className="truncate">{c.admin_email}</span>
                      </span>
                    ) : (
                      <span className="flex items-center gap-1.5 text-[12.5px] text-[#ffb764]">
                        <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
                        Needs a super admin
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 text-[13.5px] text-[#b8babf]">
                    <Users className="h-3 w-3 text-[#888b91]" aria-hidden="true" />
                    {c.hr_count}
                  </div>
                  <div className="font-mono text-[12.5px] text-[#888b91] truncate">{c.slug}</div>
                  <div className="font-mono text-[12.5px] text-[#888b91]">
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
                </motion.button>
              ))}
            </GlassCard>
          )}

          {/* Company-admin panel — keyed so the form resets per company */}
          {selected ? (
            <CompanyAdminPanel
              key={selected.id}
              company={selected}
              onDeleted={() => setSelectedId(null)}
            />
          ) : companies && companies.length > 0 ? (
            <GlassCard className="p-5">
              <div className="py-6 text-center space-y-2">
                <ShieldCheck className="mx-auto h-7 w-7 text-[#5a5f66]" aria-hidden="true" />
                <p className="text-[13px] text-[#888b91]">
                  Click a row above to manage its super admin.
                </p>
              </div>
            </GlassCard>
          ) : null}
        </div>
      )}

      {view === 'flags' && <FeatureFlagsTab />}
      {view === 'audit' && <AuditLogTab />}
    </div>
  );
}
