// HRInterviews — invite eligible applicants to the AI interview + track results.
// Layout: design screen HRInterviews.tsx (GlassCard table, SegTabs, Avatar, StatusTag).
// Behavior: all live logic — listEligibleApplicants + listInvites queries,
//           createInvite (once-shown magic link + copy), revokeInvite (invited-only),
//           eligibility gating, status taxonomy, real composite_score + /scorecard links.

import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
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
  setInviteAvatarMode,
  revokeInvite,
  rescheduleInvite,
  type InterviewInvite,
  type AvatarMode,
} from '@/api/interviewInvites';
import { applicationKey } from '@/lib/applicationKey';
import { useDeepLinkedRow } from '@/hooks/useDeepLinkedRow';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import { localInputToIso, toLocalInputValue } from '@/lib/localDatetime';
import { StatusTag, Avatar, SegTabs, type TagTone } from '@/design/components/primitives';
import { PageHeader, SectionCard, EmptyState, Toolbar, Page } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { initialsOf } from '@/design/data/shared';

/* ── Field styling ────────────────────────────────────────────────────────── */

const inputCls =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 ' +
  'text-[14px] text-foreground placeholder:text-[var(--ui-faint)] transition-colors ' +
  'focus-visible:outline-none focus-visible:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

/** The row grid shared by the header and every invite row. */
const ROW_GRID = 'grid grid-cols-[2fr_1.2fr_1.2fr_1fr_1.1fr] items-center gap-3';

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

/* ── Deep-linked interview that is not in this list ───────────────────────── */

const CITED_INTERVIEW_MISSING_TEXT =
  'That interview is not in this list. AI interviews appear here; a panel interview ' +
  'with a human interviewer is on the candidate’s own record, under “Human interview”.';

/* ── InviteRow ───────────────────────────────────────────────────────────── */

/** DOM id for one invite row, so an interview citation can land on it. */
function rowDomId(inviteId: string): string {
  return `interview-${inviteId}`;
}

function InviteRow({
  inv,
  onRevoke,
  revoking,
  onReschedule,
  rescheduling,
  onSetMode,
  settingMode,
  cited,
}: {
  inv: InterviewInvite;
  onRevoke: (id: string) => void;
  revoking: boolean;
  onReschedule: (id: string, iso: string) => void;
  rescheduling: boolean;
  onSetMode: (id: string, mode: AvatarMode) => void;
  settingMode: boolean;
  /** True for the row a `/hr/interviews/{id}` citation names. */
  cited: boolean;
}) {
  const { label, tone, dot } = statusDisplay(inv.status);
  const initials = initialsOf(inv.applicant_name);
  const [editingSched, setEditingSched] = useState(false);
  // toLocalInputValue reads the LOCAL wall-clock fields, unlike
  // toISOString().slice(0, 16) — which is always UTC and, shown in a
  // datetime-local input, silently displayed a UTC time as though it were
  // the reader's own, off by their UTC offset.
  const [schedDraft, setSchedDraft] = useState(toLocalInputValue(inv.scheduled_at));

  const canReschedule = inv.status === 'invited' || inv.status === 'consumed';

  return (
    <div
      // The id + tabIndex are the landing target for an interview citation
      // (useDeepLinkedRow); the ring says "this is the interview you followed a
      // link to" rather than leaving it to the scroll position.
      id={rowDomId(inv.invite_id)}
      tabIndex={-1}
      className={cn(
        'border-b border-border px-6 py-3 last:border-0 outline-none transition-colors',
        cited
          ? 'bg-[var(--ui-inset-soft)] ring-1 ring-inset ring-[var(--accent)]'
          : 'hover:bg-[var(--ui-inset-soft)]',
      )}
    >
      <div className={ROW_GRID}>
        {/* Candidate */}
        <div className="flex min-w-0 items-center gap-3">
          <Avatar initials={initials} size={34} />
          <p className="truncate text-[14px] font-semibold text-foreground">{inv.applicant_name}</p>
        </div>

        {/* Role */}
        <p className="truncate text-[14px] text-[var(--ui-soft)]">{inv.job_title}</p>

        {/* When */}
        <div className="flex items-center gap-1.5 text-[14px] text-muted-foreground">
          <Calendar size={13} className="shrink-0" aria-hidden="true" />
          {inv.scheduled_at ? (
            new Date(inv.scheduled_at).toLocaleDateString('en-IN', {
              day: 'numeric',
              month: 'short',
              hour: '2-digit',
              minute: '2-digit',
            })
          ) : (
            <span className="text-muted-foreground">-</span>
          )}
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
              <span className="text-[13px] font-semibold text-[var(--ui-ok)]">
                {inv.composite_score.toFixed(1)}
              </span>
              <span className="text-[12px] text-muted-foreground">/10</span>
            </span>
          )}
          {inv.scorecard_id ? (
            <Button asChild variant="outline" size="sm">
              <Link
                to={`/scorecard/${inv.scorecard_id}`}
                aria-label={`View scorecard for ${inv.applicant_name}`}
              >
                <BarChart3 aria-hidden="true" /> Result
              </Link>
            </Button>
          ) : (
            <span className="text-[13px] text-muted-foreground">
              {LANG_LABEL[inv.language] ?? inv.language}
            </span>
          )}
          {/* THE TOGGLE HAS TO LIVE HERE, not only on the create form. Most
              invites are minted by the workflow with nobody choosing a mode, so
              a create-only control would be unreachable for the majority of
              interviews. Editable only while 'invited': after the candidate
              starts, the session already carries the mode and the server 409s,
              so showing a live control would be a lie. */}
          {inv.status === 'invited' ? (
            <select
              className="h-9 shrink-0 rounded-[10px] border border-border bg-secondary px-2 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-40"
              value={inv.avatar_mode}
              disabled={settingMode}
              onChange={(e) => onSetMode(inv.invite_id, e.target.value as AvatarMode)}
              aria-label={`Interview mode for ${inv.applicant_name}`}
            >
              <option value="avatar">Video avatar</option>
              <option value="audio">Audio only</option>
            </select>
          ) : (
            <span
              className="shrink-0 text-[12.5px] text-muted-foreground"
              title="Fixed once the candidate starts"
            >
              {inv.avatar_mode === 'audio' ? 'Audio only' : 'Video avatar'}
            </span>
          )}
          {canReschedule && (
            <button
              type="button"
              aria-label={`Reschedule interview for ${inv.applicant_name}`}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40 hover:bg-[var(--ui-inset)] hover:text-[var(--ui-info)]"
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
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40 hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]"
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
        <div className="mt-2 flex flex-wrap items-center gap-2 pl-[2.375rem]">
          <input
            type="datetime-local"
            value={schedDraft}
            onChange={(e) => setSchedDraft(e.target.value)}
            className="h-10 rounded-[12px] border border-input bg-card px-3 text-[14px] text-foreground focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30"
            aria-label="New scheduled time"
          />
          <button
            type="button"
            aria-label="Confirm reschedule"
            disabled={!schedDraft || rescheduling}
            className="flex h-10 w-10 items-center justify-center rounded-full border border-[var(--ui-ok)]/45 text-[var(--ui-ok)] transition-colors hover:bg-[var(--ui-ok-wash)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40"
            onClick={() => {
              if (!schedDraft) return;
              onReschedule(inv.invite_id, localInputToIso(schedDraft));
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
            className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
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
  // Set when this is an interview citation (`/hr/interviews/{id}`, the
  // copilot's CITATION_ROUTES.interview) — absent on the plain list route.
  const { interviewId: citedInviteId } = useParams<{ interviewId?: string }>();

  // Form state
  // The chosen APPLICATION (its enrolment id, or the person for someone filed
  // under no opening) — a person can be eligible in two openings (B5).
  const [choice, setChoice] = useState('');
  const [language, setLanguage] = useState<'en' | 'hi' | 'te'>('en');
  const [avatarMode, setAvatarMode] = useState<AvatarMode>('avatar');
  const [scheduledAt, setScheduledAt] = useState('');
  const [mintedLink, setMintedLink] = useState<string | null>(null);

  // Filter
  const [filter, setFilter] = useState<FilterKey>('all');

  // ── Queries ──────────────────────────────────────────────────────────────
  // Both change with no action on this page — a candidate finishes, a link
  // lapses, an exam pass makes someone eligible — so both poll like every other
  // live HR list. A new notification also refreshes them (lib/liveRefresh).
  const { data: eligible } = useQuery({
    queryKey: ['hr', 'interviews', 'eligible'],
    queryFn: () => listEligibleApplicants('any'),
    refetchInterval: LIVE_POLL_MS,
  });
  const {
    data: invites,
    isLoading,
    isSuccess: invitesLoaded,
  } = useQuery({
    queryKey: ['hr', 'interviews'],
    queryFn: () => listInvites(),
    refetchInterval: LIVE_POLL_MS,
  });

  // ── Mutations ─────────────────────────────────────────────────────────────
  const inviteMut = useMutation({
    mutationFn: () =>
      createInvite({
        applicant_id: chosen?.id ?? '',
        enrolment_id: chosen?.enrolment_id ?? null,
        language,
        avatar_mode: avatarMode,
        scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
      }),
    onSuccess: (res) => {
      setMintedLink(res.magic_link);
      setChoice('');
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

  const modeMut = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: AvatarMode }) =>
      setInviteAvatarMode(id, mode),
    onSuccess: (inv) => {
      toast.success(
        inv.avatar_mode === 'audio'
          ? `${inv.applicant_name} will have an audio-only interview`
          : `${inv.applicant_name} will meet the video avatar`,
      );
      void qc.invalidateQueries({ queryKey: ['hr', 'interviews'] });
    },
    // The server's 409 names the status and says what to do instead, so it is
    // shown verbatim rather than replaced by a generic failure.
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Could not change the interview mode'),
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
      toast.error('Could not copy - select and copy manually.');
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────
  const allInvites = useMemo(() => invites ?? [], [invites]);
  const elig = eligible ?? [];
  const keyOf = applicationKey;
  const chosen = elig.find((a) => keyOf(a) === choice);

  const filteredInvites = useMemo(
    () => (filter === 'all' ? allInvites : allInvites.filter((inv) => inv.status === filter)),
    [allInvites, filter],
  );

  // ── An interview citation (`/hr/interviews/{id}`) ──────────────────────────
  //
  // This list IS the screen for an AI interview: one row per invite, with its
  // status, score and scorecard. So the citation lands on the row rather than a
  // page of its own, and says so when the id is not one of these.
  //
  // The id can also be an interview_sessions id — a HUMAN panel interview,
  // which the evidence graph cites with the same `interview` kind. No HR screen
  // lists those; they live on the candidate's own record (CandidateDrawer's
  // "Human interview" section). Such an id therefore lands here with the notice
  // below rather than on a blank highlight, which is why the copy names where
  // else to look instead of claiming the record is gone.
  const citedInvite = citedInviteId
    ? allInvites.find((inv) => inv.invite_id === citedInviteId)
    : undefined;
  useDeepLinkedRow(citedInvite ? rowDomId(citedInvite.invite_id) : null, invitesLoaded);
  const citedInviteMissing = Boolean(citedInviteId) && invitesLoaded && citedInvite === undefined;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <Page width="default">
      <PageHeader
        title="Interviews"
        description="Invite shortlisted or exam-passed applicants to a voice interview with the AI avatar. Share the private link - they need no account."
      />

      {/* Invite form */}
      <SectionCard
        title="Invite a candidate"
        icon={Send}
        description="Only shortlisted or exam-passed applicants are eligible."
      >
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <select
              className={cn(inputCls, 'sm:col-span-2')}
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              aria-label="Applicant"
            >
              <option value="">Select an eligible applicant…</option>
              {elig.map((a) => (
                <option key={keyOf(a)} value={keyOf(a)}>
                  {a.full_name} - {a.passed_exam ? 'exam passed' : 'shortlisted'}
                  {a.has_active_invite ? ' (already invited)' : ''}
                  {a.opening_title ? ` · for ${a.opening_title}` : ''}
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

            <select
              className={inputCls}
              value={avatarMode}
              onChange={(e) => setAvatarMode(e.target.value as AvatarMode)}
              aria-label="Interview mode"
            >
              <option value="avatar">Video avatar</option>
              <option value="audio">Audio only</option>
            </select>
          </div>

          <p className="text-[12.5px] leading-relaxed text-muted-foreground">
            <strong className="font-semibold text-foreground">Audio only</strong> drops the
            video avatar and runs the interview as voice. It costs less per minute and holds
            up better on a weak connection - the questions, scoring and scorecard are
            identical either way.
          </p>

          <label className="block text-sm">
            <span className="mb-1 block text-[13px] font-semibold text-foreground">
              Schedule (optional - link works any time before expiry)
            </span>
            <input
              type="datetime-local"
              className={inputCls}
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
              aria-label="Scheduled time"
            />
          </label>

          <Button
            type="button"
            disabled={!chosen || inviteMut.isPending}
            onClick={() => inviteMut.mutate()}
          >
            {inviteMut.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <Send aria-hidden="true" />
            )}
            Generate interview link
          </Button>

          {/* Once-shown minted link */}
          {mintedLink && (
            <div
              className="space-y-1.5 rounded-[16px] border border-[var(--ui-ok)]/40 bg-[var(--ui-ok-wash)] p-3.5"
              role="alert"
            >
              <p className="text-[13px] font-semibold text-[var(--ui-ok)]">
                Share this link with the candidate - copy it now (shown once):
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={mintedLink}
                  className="h-10 min-w-0 flex-1 rounded-[12px] border border-input bg-card px-3 text-[13px] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  aria-label="Magic interview link"
                />
                <button
                  type="button"
                  aria-label="Copy interview link"
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[var(--ui-ok)] transition-colors hover:bg-[var(--ui-ok-wash)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  onClick={() => void copyLink(mintedLink)}
                >
                  <Copy size={16} aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* Invites list */}
      <div className="space-y-4">
        {citedInviteMissing ? (
          <div
            role="status"
            className="flex items-start gap-2 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] px-4 py-3 text-[13px] text-muted-foreground"
          >
            <Video size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            {CITED_INTERVIEW_MISSING_TEXT}
          </div>
        ) : null}

        <Toolbar
          className="mb-0"
          trailing={
            <h2 className="text-[13px] font-semibold tabular-nums text-muted-foreground">
              Interviews ({allInvites.length})
            </h2>
          }
        >
          <div className="max-w-full overflow-x-auto">
            <SegTabs
              tabs={FILTER_TABS as unknown as { key: string; label: string }[]}
              active={filter}
              onChange={(k) => setFilter(k as FilterKey)}
            />
          </div>
        </Toolbar>

        {isLoading ? (
          <div className="space-y-2" role="status" aria-label="Loading interviews">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-16 w-full rounded-card" />
            ))}
          </div>
        ) : allInvites.length === 0 ? (
          <EmptyState
            size="page"
            icon={Video}
            title="No interviews yet"
            description="Invite an eligible applicant above."
          />
        ) : filteredInvites.length === 0 ? (
          <EmptyState title="No interviews match this filter." />
        ) : (
          <div className="overflow-hidden rounded-card border border-border bg-card shadow-card">
            <div className="overflow-x-auto">
              <div className="min-w-[760px]">
                <div
                  className={cn(
                    ROW_GRID,
                    'border-b border-border bg-[var(--ui-inset-soft)] px-6 py-3 text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground',
                  )}
                >
                  <div>Candidate</div>
                  <div>Role</div>
                  <div>Scheduled</div>
                  <div>Status</div>
                  <div className="text-right">Action</div>
                </div>
                {filteredInvites.map((inv) => (
                  <InviteRow
                    key={inv.invite_id}
                    inv={inv}
                    onRevoke={(id) => revokeMut.mutate(id)}
                    revoking={revokeMut.isPending}
                    onReschedule={(id, scheduledAt) => rescheduleMut.mutate({ id, scheduledAt })}
                    rescheduling={rescheduleMut.isPending}
                    onSetMode={(id, mode) => modeMut.mutate({ id, mode })}
                    settingMode={modeMut.isPending}
                    cited={citedInvite?.invite_id === inv.invite_id}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </Page>
  );
}
