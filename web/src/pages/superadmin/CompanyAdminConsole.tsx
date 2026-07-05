// CompanyAdminConsole — a company's super admin ("super admin", one per company).
//
// Tier: super_admin (company-scoped). Creates and manages the HR managers for
// its OWN company only. Company + tenant boundary are resolved server-side from
// the caller's account — this page never sends a company id.
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
} from '@/design/components/icons';
import { getMe } from '@/api/auth';
import { listMyHrManagers, createMyHrManager, deleteMyHrManager } from '@/api/hr';
import { toast } from '@/lib/toast';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { GlassCard, Avatar, Pill } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { gradientFor, initialsOf } from '@/design/data/shared';

export default function CompanyAdminConsole() {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => getMe() });

  const { data: hrs, isLoading } = useQuery({
    queryKey: ['my-hr-managers'],
    queryFn: listMyHrManagers,
  });

  const createMut = useMutation({
    mutationFn: () =>
      // No password sent: the server generates a random bootstrap hash and
      // emails a "set your password" link. Never ship a known default — an
      // attacker could log in during the pre-reset window (must_change_password
      // lets a bootstrap login set a new password without the current one).
      createMyHrManager({
        email: email.trim(),
        full_name: fullName.trim(),
      }),
    onSuccess: () => {
      toast.success(`HR manager ${email} created — a set-password link was emailed.`);
      setEmail('');
      setFullName('');
      void qc.invalidateQueries({ queryKey: ['my-hr-managers'] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not create HR manager.'),
  });

  const deleteMut = useMutation({
    mutationFn: (userId: string) => deleteMyHrManager(userId),
    onSuccess: () => {
      toast.success('HR manager removed.');
      void qc.invalidateQueries({ queryKey: ['my-hr-managers'] });
    },
    onError: (err: unknown) =>
      toast.error(err instanceof Error ? err.message : 'Could not remove HR manager.'),
  });

  const companyName = me?.company_name ?? 'your company';

  return (
    <div className="mx-auto max-w-[960px] px-0 py-2 space-y-6">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <Reveal>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">Super Admin</h1>
            <p className="mt-1 flex items-center gap-1.5 text-[14px] text-[#888b91]">
              <Building2 size={14} className="text-[#60a5fa]" aria-hidden="true" />
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

      {/* ── Create HR form ───────────────────────────────────────────────── */}
      <Reveal delay={0.05}>
        <GlassCard className="p-5 space-y-4">
          <div className="flex items-center gap-2">
            <span
              className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.12)] text-[#60a5fa]"
              aria-hidden="true"
            >
              <Users size={17} />
            </span>
            <div>
              <h3 className="text-[15px] font-semibold text-white">Create an HR manager</h3>
              <p className="text-[12px] text-[#888b91]">
                They log in, reset the password, then run hiring for {companyName}.
              </p>
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
              <label htmlFor="hr-email" className="text-[12px] font-medium text-[#b8babf]">
                Email
              </label>
              <Input
                id="hr-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="hr@company.com"
                aria-required="true"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="hr-name" className="text-[12px] font-medium text-[#b8babf]">
                Full name
              </label>
              <Input
                id="hr-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="HR Manager"
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
              {createMut.isPending ? 'Adding…' : 'Add HR'}
            </Button>
          </form>

          <p className="flex items-center gap-1.5 text-[12px] text-[#888b91]">
            <KeyRound className="h-3 w-3 text-[#60a5fa]" aria-hidden="true" />
            A secure “set your password” link is emailed to them — no password is
            shared here.
          </p>
        </GlassCard>
      </Reveal>

      {/* ── HR list ──────────────────────────────────────────────────────── */}
      <Reveal delay={0.08}>
        <GlassCard className="p-5">
          <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold text-white">
            <Users size={17} className="text-[#60a5fa]" aria-hidden="true" />
            HR managers
          </h3>

          {isLoading ? (
            <Skeleton className="h-16 w-full rounded-[12px] bg-white/[0.05]" />
          ) : !hrs || hrs.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-[#888b91]">
              No HR managers yet — add your first one above.
            </p>
          ) : (
            <div role="list" aria-label="HR managers">
              {hrs.map((hr) => (
                <div
                  key={hr.user_id}
                  role="listitem"
                  className="flex items-center justify-between rounded-[14px] border border-white/[0.07] bg-white/[0.03] px-3 py-2.5 mb-2 last:mb-0"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Avatar
                      initials={initialsOf(hr.full_name)}
                      gradient={gradientFor(hr.user_id.charCodeAt(0))}
                      size={30}
                    />
                    <div className="min-w-0">
                      <p className="text-[13.5px] font-medium text-white truncate">
                        {hr.full_name}
                      </p>
                      <p className="text-[12px] text-[#888b91] truncate">{hr.email}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {hr.must_change_password ? (
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
                      title={`Remove ${hr.full_name}`}
                      pending={deleteMut.isPending && deleteMut.variables === hr.user_id}
                      onConfirm={() => deleteMut.mutate(hr.user_id)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}
