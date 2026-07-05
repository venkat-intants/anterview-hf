// HRInterviews — invite eligible applicants to the AI interview + track results.
// Layout: design screen HRInterviews.tsx (GlassCard table, SegTabs, Avatar, StatusTag).
// Behavior: all live logic — listEligibleApplicants + listInvites queries,
//           createInvite (once-shown magic link + copy), revokeInvite (invited-only),
//           eligibility gating, status taxonomy, real composite_score + /scorecard links.

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Video,
  Send,
  Copy,
  Ban,
  Loader2,
  BarChart3,
  Calendar,
  Pencil,
  Check,
  X,
} from '@/design/components/icons';
import {
  listEligibleApplicants,
  listInvites,
  createInvite,
  revokeInvite,
  rescheduleInvite,
  type InterviewInvite,
} from '@/api/interviewInvites';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  GlassCard,
  Pill,
  StatusTag,
  Avatar,
  SegTabs,
  type TagTone,
} from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { initialsOf, gradientFor } from '@/design/data/shared';

/* ── Field styling ────────────────────────────────────────────────────────── */

const inputCls =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 ' +
  'text-[14px] text-white placeholder:text-[#5a5f66] focus:outline-none ' +
  'focus:border-[var(--accent)] transition-colors';

/* ── Status taxonomy → design StatusTag tone ──────────────────────────────── */

interface StatusDisplay {
  label: string;
  tone: TagTone;
  dot: boolean;
}

function statusDisplay(s: string): StatusDisplay {
  switch (s) {
    case 'completed':
      return { label: 'Completed', tone: 'forest', dot: false };
    case 'consumed':
      return { label: 'In progress', tone: 'electric', dot: true };
    case 'revoked':
      return { label: 'Revoked', tone: 'ember', dot: false };
    case 'expired':
      return { label: 'Expired', tone: 'neutral', dot: false };
    default:
      return { label: 'Invited', tone: 'amber', dot: false };
  }
}

/* ── Filter tabs ─────────────────────────────────────────────────────────── */

const FILTER_TABS = [
  { key: 'all', label: 'All' },
  { key: 'invited', label: 'Invited' },
  { key: 'consumed', label: 'In progress' },
  { key: 'completed', label: 'Completed' },
  { key: 'expired', label: 'Expired' },
  { key: 'revoked', label: 'Revoked' },
] as const;

type FilterKey = (typeof FILTER_TABS)[number]['key'];

/* ── Language labels ─────────────────────────────────────────────────────── */

const LANG_LABEL: Record<string, string> = {
  en: 'English',
  hi: 'हिंदी',
  te: 'తెలుగు',
};

/* ── Stable gradient seed from id ───────────────────────────────────────── */

function seedFrom(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (Math.imul(31, h) + id.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/* ── InviteRow ───────────────────────────────────────────────────────────── */

function InviteRow({
  inv,
  onRevoke,
  revoking,
  onReschedule,
  rescheduling,
}: {
  inv: InterviewInvite;
  onRevoke: (id: string) => void;
  revoking: boolean;
  onReschedule: (id: string, iso: string) => void;
  rescheduling: boolean;
}) {
  const { label, tone, dot } = statusDisplay(inv.status);
  const initials = initialsOf(inv.applicant_name);
  const gradient = gradientFor(seedFrom(inv.applicant_id));
  const [editingSched, setEditingSched] = useState(false);
  const [schedDraft, setSchedDraft] = useState(
    inv.scheduled_at
      ? new Date(inv.scheduled_at).toISOString().slice(0, 16)
      : '',
  );

  const canReschedule = inv.status === 'invited' || inv.status === 'consumed';

  return (
    <div className="border-b border-white/[0.04] px-6 py-3.5 last:border-0 hover:bg-white/[0.02] transition-colors">
      <div className="grid grid-cols-[2fr_1.2fr_1.2fr_1fr_0.8fr] items-center gap-3">
        {/* Candidate */}
        <div className="flex min-w-0 items-center gap-3">
          <Avatar initials={initials} gradient={gradient} size={34} />
          <p className="truncate text-[13.5px] font-medium text-white">{inv.applicant_name}</p>
        </div>

        {/* Role */}
        <p className="truncate text-[13px] text-[#b8babf]">{inv.job_title}</p>

        {/* When */}
        <div className="flex items-center gap-1.5 text-[13px] text-[#888b91]">
          <Calendar size={13} className="shrink-0" aria-hidden="true" />
          {inv.scheduled_at
            ? new Date(inv.scheduled_at).toLocaleDateString('en-IN', {
                day: 'numeric',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
              })
            : <span className="text-[#5a5f66]">—</span>}
        </div>

        {/* Status */}
        <div>
          <StatusTag tone={tone} dot={dot}>
            {label}
          </StatusTag>
        </div>

        {/* Action */}
        <div className="flex items-center justify-end gap-2">
          {inv.composite_score !== null && (
            <span
              className="shrink-0 text-center"
              aria-label={`Score: ${inv.composite_score.toFixed(1)} out of 10`}
            >
              <span className="text-[13px] font-semibold text-[#27c93f]">
                {inv.composite_score.toFixed(1)}
              </span>
              <span className="text-[10px] text-[#888b91]">/10</span>
            </span>
          )}
          {inv.scorecard_id ? (
            <Link
              to={`/scorecard/${inv.scorecard_id}`}
              aria-label={`View scorecard for ${inv.applicant_name}`}
            >
              <Pill variant="ghost" className="py-1.5 px-3 gap-1.5 text-[12px]">
                <BarChart3 size={13} aria-hidden="true" /> Result
              </Pill>
            </Link>
          ) : (
            <span className="text-[12px] text-[#70757c]">
              {LANG_LABEL[inv.language] ?? inv.language}
            </span>
          )}
          {canReschedule && (
            <button
              type="button"
              aria-label={`Reschedule interview for ${inv.applicant_name}`}
              className="shrink-0 text-[#888b91] hover:text-[#60a5fa] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              disabled={rescheduling}
              onClick={() => setEditingSched((v) => !v)}
            >
              <Pencil size={14} aria-hidden="true" />
            </button>
          )}
          {inv.status === 'invited' && (
            <button
              type="button"
              aria-label={`Revoke interview link for ${inv.applicant_name}`}
              className="shrink-0 text-[#888b91] hover:text-[#e6714f] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              disabled={revoking}
              onClick={() => onRevoke(inv.invite_id)}
            >
              <Ban size={15} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      {/* Reschedule inline form */}
      {editingSched && (
        <div className="mt-2 flex items-center gap-2 pl-[2.375rem]">
          <input
            type="datetime-local"
            value={schedDraft}
            onChange={(e) => setSchedDraft(e.target.value)}
            className="rounded-[9px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2.5 py-1.5 text-[13px] text-white focus:outline-none focus:border-[var(--accent)]"
            aria-label="New scheduled time"
          />
          <button
            type="button"
            aria-label="Confirm reschedule"
            disabled={!schedDraft || rescheduling}
            className="flex h-7 w-7 items-center justify-center rounded-[8px] border border-[rgba(39,201,63,0.4)] text-[#27c93f] hover:bg-[rgba(39,201,63,0.12)] disabled:opacity-40 transition-colors"
            onClick={() => {
              if (!schedDraft) return;
              onReschedule(inv.invite_id, new Date(schedDraft).toISOString());
              setEditingSched(false);
            }}
          >
            {rescheduling ? (
              <Loader2 size={13} className="animate-spin" aria-hidden="true" />
            ) : (
              <Check size={13} aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            aria-label="Cancel reschedule"
            className="flex h-7 w-7 items-center justify-center rounded-[8px] text-[#888b91] hover:text-white transition-colors"
            onClick={() => setEditingSched(false)}
          >
            <X size={13} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function HRInterviews() {
  const qc = useQueryClient();

  // Form state
  const [applicantId, setApplicantId] = useState('');
  const [language, setLanguage] = useState<'en' | 'hi' | 'te'>('en');
  const [scheduledAt, setScheduledAt] = useState('');
  const [mintedLink, setMintedLink] = useState<string | null>(null);

  // Filter
  const [filter, setFilter] = useState<FilterKey>('all');

  // ── Queries ──────────────────────────────────────────────────────────────
  const { data: eligible } = useQuery({
    queryKey: ['hr', 'interviews', 'eligible'],
    queryFn: () => listEligibleApplicants('any'),
  });
  const { data: invites, isLoading } = useQuery({
    queryKey: ['hr', 'interviews'],
    queryFn: () => listInvites(),
  });

  // ── Mutations ─────────────────────────────────────────────────────────────
  const inviteMut = useMutation({
    mutationFn: () =>
      createInvite({
        applicant_id: applicantId,
        language,
        scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
      }),
    onSuccess: (res) => {
      setMintedLink(res.magic_link);
      setApplicantId('');
      setScheduledAt('');
      toast.success('Interview link created');
      void qc.invalidateQueries({ queryKey: ['hr', 'interviews'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Invite failed'),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    onSuccess: () => {
      toast.success('Link revoked');
      void qc.invalidateQueries({ queryKey: ['hr', 'interviews'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Revoke failed'),
  });

  const rescheduleMut = useMutation({
    mutationFn: ({ id, scheduledAt }: { id: string; scheduledAt: string }) =>
      rescheduleInvite(id, scheduledAt),
    onSuccess: () => {
      toast.success('Interview rescheduled');
      void qc.invalidateQueries({ queryKey: ['hr', 'interviews'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Reschedule failed'),
  });

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Link copied');
    } catch {
      toast.error('Could not copy — select and copy manually.');
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────
  const allInvites = useMemo(() => invites ?? [], [invites]);
  const elig = eligible ?? [];

  const filteredInvites = useMemo(
    () =>
      filter === 'all'
        ? allInvites
        : allInvites.filter((inv) => inv.status === filter),
    [allInvites, filter],
  );

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="mx-auto max-w-[1120px] px-6 py-8 lg:px-8">
      {/* Page header */}
      <Reveal>
        <div className="flex items-center gap-4">
          <span className="relative inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
            <Video size={22} aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">Interviews</h1>
            <p className="mt-1 text-[14px] text-[#888b91]">
              Invite shortlisted or exam-passed applicants to a voice interview with the AI avatar.
              Share the private link — they need no account.
            </p>
          </div>
        </div>
      </Reveal>

      {/* Invite form */}
      <Reveal delay={0.05}>
        <GlassCard className="mt-6 p-6">
          <div className="mb-4 flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
              <Send size={15} aria-hidden="true" />
            </div>
            <div>
              <p className="text-[15px] font-semibold text-white">Invite a candidate</p>
              <p className="text-[12.5px] text-[#888b91]">
                Only shortlisted or exam-passed applicants are eligible.
              </p>
            </div>
          </div>

          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <select
                className={cn(inputCls, 'sm:col-span-2')}
                value={applicantId}
                onChange={(e) => setApplicantId(e.target.value)}
                aria-label="Applicant"
              >
                <option value="">Select an eligible applicant…</option>
                {elig.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.full_name} — {a.passed_exam ? 'exam passed' : 'shortlisted'}
                    {a.has_active_invite ? ' (already invited)' : ''}
                  </option>
                ))}
              </select>

              <select
                className={inputCls}
                value={language}
                onChange={(e) => setLanguage(e.target.value as 'en' | 'hi' | 'te')}
                aria-label="Interview language"
              >
                <option value="en">English</option>
                <option value="hi">हिंदी</option>
                <option value="te">తెలుగు</option>
              </select>
            </div>

            <label className="block text-sm">
              <span className="mb-1 block text-[12px] font-medium uppercase tracking-[0.5px] text-[#70757c]">
                Schedule (optional — link works any time before expiry)
              </span>
              <input
                type="datetime-local"
                className={inputCls}
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                aria-label="Scheduled time"
              />
            </label>

            <Pill
              variant="primary"
              className="gap-1.5 px-5 py-2.5"
              disabled={!applicantId || inviteMut.isPending}
              onClick={() => inviteMut.mutate()}
            >
              {inviteMut.isPending ? (
                <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              ) : (
                <Send size={16} aria-hidden="true" />
              )}
              Generate interview link
            </Pill>

            {/* Once-shown minted link */}
            {mintedLink && (
              <div
                className="space-y-1.5 rounded-[14px] border border-[rgba(39,201,63,0.3)] bg-[rgba(39,201,63,0.08)] p-3.5"
                role="alert"
              >
                <p className="text-[12.5px] font-semibold text-[#27c93f]">
                  Share this link with the candidate — copy it now (shown once):
                </p>
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    value={mintedLink}
                    className="min-w-0 flex-1 rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-2 py-1 text-[13px] text-white focus:outline-none"
                    aria-label="Magic interview link"
                  />
                  <button
                    type="button"
                    aria-label="Copy interview link"
                    className="shrink-0 flex h-7 w-7 items-center justify-center rounded-[8px] text-[#27c93f] hover:bg-[rgba(39,201,63,0.15)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                    onClick={() => void copyLink(mintedLink)}
                  >
                    <Copy size={14} aria-hidden="true" />
                  </button>
                </div>
              </div>
            )}
          </div>
        </GlassCard>
      </Reveal>

      {/* Invites list */}
      <div className="mt-7 space-y-4">
        <Reveal delay={0.1}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-[14px] font-semibold text-white">
              Interviews ({allInvites.length})
            </h2>
            <SegTabs
              tabs={FILTER_TABS as unknown as { key: string; label: string }[]}
              active={filter}
              onChange={(k) => setFilter(k as FilterKey)}
            />
          </div>
        </Reveal>

        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-16 w-full rounded-[16px] bg-white/[0.05] animate-pulse" />
            ))}
          </div>
        ) : allInvites.length === 0 ? (
          <Reveal delay={0.15}>
            <div className="flex flex-col items-center gap-3 rounded-[24px] border border-dashed border-white/[0.1] bg-[rgba(15,15,16,0.6)] py-14 text-center">
              <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.06] text-[#888b91]">
                <Video size={22} aria-hidden="true" />
              </span>
              <p className="text-[14px] text-[#888b91]">
                No interviews yet — invite an eligible applicant above.
              </p>
            </div>
          </Reveal>
        ) : filteredInvites.length === 0 ? (
          <Reveal delay={0.12}>
            <p className="py-8 text-center text-[14px] text-[#888b91]">
              No interviews match this filter.
            </p>
          </Reveal>
        ) : (
          <>
            {/* Table header */}
            <div className="hidden grid-cols-[2fr_1.2fr_1.2fr_1fr_0.8fr] gap-3 border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c] sm:grid">
              <div>Candidate</div>
              <div>Role</div>
              <div>Scheduled</div>
              <div>Status</div>
              <div className="text-right">Action</div>
            </div>

            <GlassCard className="overflow-hidden p-0">
              <Stagger>
                {filteredInvites.map((inv) => (
                  <StaggerItem key={inv.invite_id}>
                    <InviteRow
                      inv={inv}
                      onRevoke={(id) => revokeMut.mutate(id)}
                      revoking={revokeMut.isPending}
                      onReschedule={(id, scheduledAt) =>
                        rescheduleMut.mutate({ id, scheduledAt })
                      }
                      rescheduling={rescheduleMut.isPending}
                    />
                  </StaggerItem>
                ))}
              </Stagger>
            </GlassCard>
          </>
        )}
      </div>
    </div>
  );
}
