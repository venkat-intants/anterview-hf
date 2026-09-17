// CompanyAdminConsole — a company's super admin ("super admin", one per company).
//
// Tier: super_admin (company-scoped). Creates and manages the HR managers AND
// the interviewers (D4-1) for its OWN company only. Company + tenant boundary
// are resolved server-side from the caller's account — this page never sends
// a company id.
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
} from '@/design/components/icons';
import { getMe } from '@/api/auth';
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
import { Input } from '@/components/ui/input';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { GlassCard, Avatar, Pill } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { gradientFor, initialsOf } from '@/design/data/shared';

/**
 * One roster (HR managers, or interviewers) — the create form plus the list.
 * Both rosters share the exact same server-row shape (HrManager), so this is
 * the single implementation the page renders twice rather than two near-
 * identical 150-line blocks that would drift apart the first time one of them
 * changed.
 */
function StaffRoster({
  idPrefix,
  icon,
  heading,
  createTitle,
  createBlurb,
  addLabel,
  emptyText,
  listLabel,
  companyName,
  removalNote,
  queryKey,
  listFn,
  createFn,
  deleteFn,
}: {
  idPrefix: string;
  icon: React.ReactNode;
  heading: string;
  createTitle: string;
  createBlurb: (companyName: string) => string;
  addLabel: string;
  emptyText: string;
  listLabel: string;
  companyName: string;
  /** Shown once, under the list — e.g. what removing someone also does. */
  removalNote?: string;
  queryKey: string;
  listFn: () => Promise<HrManager[]>;
  createFn: (body: { email: string; full_name: string }) => Promise<HrManager>;
  deleteFn: (userId: string) => Promise<void>;
}) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');

  const { data: rows, isLoading } = useQuery({ queryKey: [queryKey], queryFn: listFn });

  const createMut = useMutation({
    mutationFn: () => createFn({ email: email.trim(), full_name: fullName.trim() }),
    onSuccess: () => {
      toast.success(`${heading.replace(/s$/, '')} ${email} created — a set-password link was emailed.`);
      setEmail('');
      setFullName('');
      void qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : `Could not create this ${heading.toLowerCase().replace(/s$/, '')}.`),
  });

  const deleteMut = useMutation({
    mutationFn: (userId: string) => deleteFn(userId),
    onSuccess: () => {
      toast.success(`${heading.replace(/s$/, '')} removed.`);
      void qc.invalidateQueries({ queryKey: [queryKey] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : `Could not remove this ${heading.toLowerCase().replace(/s$/, '')}.`),
  });

  return (
    <>
      <Reveal delay={0.05}>
        <GlassCard className="p-5 space-y-4">
          <div className="flex items-center gap-2">
            <span
              className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.12)] text-[var(--ui-info)]"
              aria-hidden="true"
            >
              {icon}
            </span>
            <div>
              <h3 className="text-[15px] font-semibold text-foreground">{createTitle}</h3>
              <p className="text-[12px] text-muted-foreground">{createBlurb(companyName)}</p>
            </div>
          </div>

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
              <label htmlFor={`${idPrefix}-email`} className="text-[12px] font-medium text-[var(--ui-soft)]">
                Email
              </label>
              <Input
                id={`${idPrefix}-email`}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@company.com"
                aria-required="true"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor={`${idPrefix}-name`} className="text-[12px] font-medium text-[var(--ui-soft)]">
                Full name
              </label>
              <Input
                id={`${idPrefix}-name`}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Full name"
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
              {createMut.isPending ? 'Adding…' : addLabel}
            </Button>
          </form>

          <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <KeyRound className="h-3 w-3 text-[var(--ui-info)]" aria-hidden="true" />
            A secure “set your password” link is emailed to them — no password is
            shared here.
          </p>
        </GlassCard>
      </Reveal>

      <Reveal delay={0.08}>
        <GlassCard className="p-5">
          <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-foreground">
            {icon}
            {heading}
          </h3>

          {isLoading ? (
            <Skeleton className="h-16 w-full rounded-[12px] bg-[var(--ui-inset)]" />
          ) : !rows || rows.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">{emptyText}</p>
          ) : (
            <div role="list" aria-label={listLabel}>
              {rows.map((row) => (
                <div
                  key={row.user_id}
                  role="listitem"
                  className="flex items-center justify-between rounded-[14px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2.5 mb-2 last:mb-0"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Avatar
                      initials={initialsOf(row.full_name)}
                      gradient={gradientFor(row.user_id.charCodeAt(0))}
                      size={30}
                    />
                    <div className="min-w-0">
                      <p className="text-[13.5px] font-medium text-foreground truncate">
                        {row.full_name}
                      </p>
                      <p className="text-[12px] text-muted-foreground truncate">{row.email}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {row.must_change_password ? (
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
                      title={`Remove ${row.full_name}`}
                      pending={deleteMut.isPending && deleteMut.variables === row.user_id}
                      onConfirm={() => deleteMut.mutate(row.user_id)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
          {removalNote ? (
            <p className="mt-3 text-[11.5px] text-muted-foreground">{removalNote}</p>
          ) : null}
        </GlassCard>
      </Reveal>
    </>
  );
}

export default function CompanyAdminConsole() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => getMe() });
  const companyName = me?.company_name ?? 'your company';

  return (
    <div className="mx-auto max-w-[960px] px-0 py-2 space-y-6">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <Reveal>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1
              data-testid="page-title"
              className="text-[28px] font-semibold tracking-[-1px] text-foreground"
            >
              Super Admin
            </h1>
            <p className="mt-1 flex items-center gap-1.5 text-[14px] text-muted-foreground">
              <Building2 size={14} className="text-[var(--ui-info)]" aria-hidden="true" />
              HR managers for {companyName}.
            </p>
          </div>
          <Pill
            variant="primary"
            className="px-5 py-2.5"
            type="button"
            onClick={() =>
              setTimeout(
                () =>
                  (document.getElementById('hr-email') as HTMLInputElement | null)?.focus(),
                50,
              )
            }
            aria-label="Add an HR manager"
          >
            <UserPlus size={16} aria-hidden="true" />
            Add HR
          </Pill>
        </div>
      </Reveal>

      <StaffRoster
        idPrefix="hr"
        icon={<Users size={17} aria-hidden="true" />}
        heading="HR managers"
        createTitle="Create an HR manager"
        createBlurb={(name) => `They log in, reset the password, then run hiring for ${name}.`}
        addLabel="Add HR"
        emptyText="No HR managers yet — add your first one above."
        listLabel="HR managers"
        companyName={companyName}
        queryKey="my-hr-managers"
        listFn={listMyHrManagers}
        createFn={createMyHrManager}
        deleteFn={deleteMyHrManager}
      />

      {/* ── Interviewers (D4-1) ──────────────────────────────────────────── */}
      <StaffRoster
        idPrefix="interviewer"
        icon={<ClipboardCheck size={17} aria-hidden="true" />}
        heading="Interviewers"
        createTitle="Create an interviewer"
        createBlurb={(name) => `They see only the interviews assigned to them at ${name}.`}
        addLabel="Add interviewer"
        emptyText="No interviewers yet — add your first one above."
        listLabel="Interviewers"
        companyName={companyName}
        // Removing an interviewer is destructive beyond the roster row: say so
        // where the confirm control for it lives.
        removalNote="Removing an interviewer also withdraws their unsubmitted interview assignments."
        queryKey="my-interviewers"
        listFn={listMyInterviewers}
        createFn={createMyInterviewer}
        deleteFn={deleteMyInterviewer}
      />
    </div>
  );
}
