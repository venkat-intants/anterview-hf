// HRInterviews — invite eligible applicants to the AI interview + track results.
// Layout: design screen HRInterviews.tsx (GlassCard table, Avatar, StatusTag).
// Behavior: all live logic — listEligibleApplicants + listInvites queries,
//           createInvite (once-shown magic link + copy; a live link is only
//           replaced after a confirm), revokeInvite (confirmed, unstarted only),
//           endStartedInterview (a started one, with a reason, after a confirm),
//           eligibility gating, status taxonomy, the result read inline.

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
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
  XCircle,
} from '@/design/components/icons';
import {
  listEligibleApplicants,
  listInvites,
  createInvite,
  getInviteResult,
  setInviteAvatarMode,
  revokeInvite,
  endStartedInterview,
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
import { errorMessage } from '@/lib/errorMessage';
import { formatDateTime, formatScore } from '@/lib/formatters';
import { statusLabel } from '@/lib/statusLabel';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { browserTimezone } from '@/lib/timezone';
import { StatusTag, Avatar, type TagTone } from '@/design/components/primitives';
import { PageHeader, SectionCard, EmptyState, Toolbar, Page } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel, QueryState } from '@/components/QueryState';
import { initialsOf } from '@/design/data/shared';

/* ── Field styling ────────────────────────────────────────────────────────── */

const inputCls =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 ' +
  'text-[14px] text-foreground placeholder:text-[var(--ui-faint)] transition-colors ' +
  'focus-visible:outline-none focus-visible:border-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

const labelCls = 'mb-1 block text-[13px] font-semibold text-foreground';

/** The row grid shared by the header and every invite row. Every track is
 *  bounded (minmax(0, …)) and the action column has a fixed width, so a row
 *  with more controls cannot widen its own tracks and drift out of line with
 *  the header and the other rows. */
const ROW_GRID =
  'grid grid-cols-[minmax(0,1.6fr)_minmax(0,1.1fr)_minmax(0,1.5fr)_minmax(0,0.9fr)_19rem] items-center gap-3';

/** First start is allowed from the scheduled time until this many minutes
 *  after it. Mirrors the server's `interview_join_window_minutes`. */
const JOIN_WINDOW_MINUTES = 10;

/* ── Status taxonomy → design StatusTag tone ──────────────────────────────── */

interface StatusDisplay {
  label: string;
  tone: TagTone;
  dot: boolean;
}

function statusDisplay(inv: InterviewInvite): StatusDisplay {
  // The candidate can no longer start: the join window closed while the link
  // was still alive. "Invited" (amber) would read as "waiting on them".
  if (inv.missed_slot) return { label: 'Missed slot', tone: 'ember', dot: false };
  const label = statusLabel(inv.status, 'invite');
  switch (inv.status) {
    case 'completed':
      return { label, tone: 'forest', dot: false };
    case 'consumed':
      return { label, tone: 'electric', dot: true };
    case 'revoked':
      return { label, tone: 'ember', dot: false };
    case 'expired':
      return { label, tone: 'neutral', dot: false };
    default:
      return { label, tone: 'amber', dot: false };
  }
}

/* ── Filters ─────────────────────────────────────────────────────────────── */

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'invited', label: 'Invited' },
  { key: 'consumed', label: 'In progress' },
  { key: 'completed', label: 'Completed' },
  { key: 'expired', label: 'Expired' },
  { key: 'revoked', label: 'Revoked' },
] as const;

type FilterKey = (typeof FILTERS)[number]['key'];

/** A row of filter toggles. These narrow one list, they do not switch between
 *  panels, so they are pressed buttons in a labelled group, not tabs. */
function StatusFilter({
  active,
  onChange,
}: {
  active: FilterKey;
  onChange: (k: FilterKey) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Filter by status"
      className="inline-flex items-center gap-1 rounded-pill bg-[var(--ui-inset-strong)] p-1"
    >
      {FILTERS.map((f) => {
        const on = f.key === active;
        return (
          <button
            key={f.key}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(f.key)}
            className={cn(
              'whitespace-nowrap rounded-pill px-3.5 py-1.5 text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              on ? 'bg-card text-foreground shadow-card' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {f.label}
          </button>
        );
      })}
    </div>
  );
}

/* ── Language labels ─────────────────────────────────────────────────────── */

const LANG_LABEL: Record<string, string> = {
  en: 'English',
  hi: 'हिंदी',
  te: 'తెలుగు',
};

/* ── Deep-linked interview that is not in this list ───────────────────────── */

const CITED_INTERVIEW_MISSING_TEXT =
  'That interview is not in this list. AI interviews appear here; an interview ' +
  'with a human interviewer is on the candidate’s own record, under “Interview schedule”.';

/** The value for a datetime-local `min`: now, in the reader's own wall time. */
function nowLocalInput(): string {
  return toLocalInputValue(new Date().toISOString());
}

/* ── Result (read inline, inside the console) ─────────────────────────────── */

const AXIS_LABEL: Record<string, string> = {
  communication: 'Communication',
  technical: 'Technical',
  problem_solving: 'Problem solving',
  confidence: 'Confidence',
};

function improvementText(item: unknown): string {
  if (typeof item === 'string') return item;
  if (item && typeof item === 'object') {
    const { area, suggestion } = item as { area?: unknown; suggestion?: unknown };
    const a = typeof area === 'string' ? area : '';
    const s = typeof suggestion === 'string' ? suggestion : '';
    if (a && s) return `${a}: ${s}`;
    return a || s;
  }
  return '';
}

function InterviewResult({ inviteId, name }: { inviteId: string; name: string }) {
  const result = useQuery({
    queryKey: ['hr', 'interviews', inviteId, 'result'],
    queryFn: () => getInviteResult(inviteId),
  });
  return (
    <div
      className="mt-3 rounded-[14px] border border-border bg-[var(--ui-inset-soft)] p-4"
      aria-label={`Interview result for ${name}`}
      role="region"
    >
      <QueryState query={result} errorTitle="Could not load the interview result.">
        {(r) => (
          <div className="space-y-3 text-[13.5px]">
            <p className="font-semibold text-foreground">
              Overall {formatScore('interview', r.composite_score)}
            </p>
            {r.scores && Object.keys(r.scores).length > 0 ? (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
                {Object.entries(r.scores).map(([axis, value]) => (
                  <div key={axis}>
                    <dt className="text-[12px] text-muted-foreground">
                      {AXIS_LABEL[axis] ?? axis}
                    </dt>
                    <dd className="font-semibold text-foreground">
                      {formatScore('interview', typeof value === 'number' ? value : null)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {r.summary ? <p className="leading-relaxed text-foreground">{r.summary}</p> : null}
            {r.strengths && r.strengths.length > 0 ? (
              <div>
                <p className="text-[12px] font-semibold text-muted-foreground">Strengths</p>
                <ul className="ml-4 list-disc text-foreground">
                  {r.strengths.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {r.improvements && r.improvements.length > 0 ? (
              <div>
                <p className="text-[12px] font-semibold text-muted-foreground">
                  Areas to improve
                </p>
                <ul className="ml-4 list-disc text-foreground">
                  {r.improvements.map((item, i) => (
                    <li key={i}>{improvementText(item)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </QueryState>
    </div>
  );
}

/* ── InviteRow ───────────────────────────────────────────────────────────── */

/** DOM id for one invite row, so an interview citation can land on it. */
function rowDomId(inviteId: string): string {
  return `interview-${inviteId}`;
}

function InviteRow({
  inv,
  onRevoke,
  revoking,
  onEnd,
  ending,
  onReschedule,
  rescheduling,
  onSetMode,
  pendingMode,
  cited,
}: {
  inv: InterviewInvite;
  onRevoke: (id: string) => void;
  /** True while THIS row's revoke is in flight. */
  revoking: boolean;
  onEnd: (id: string, reason: string, done: () => void) => void;
  /** True while THIS row's end-interview request is in flight. */
  ending: boolean;
  onReschedule: (id: string, iso: string, done: () => void) => void;
  /** True while THIS row's reschedule is in flight. */
  rescheduling: boolean;
  onSetMode: (id: string, mode: AvatarMode) => void;
  /** The mode this row is being switched to, while that request is in flight. */
  pendingMode: AvatarMode | null;
  /** True for the row a `/hr/interviews/{id}` citation names. */
  cited: boolean;
}) {
  const { label, tone, dot } = statusDisplay(inv);
  const initials = initialsOf(inv.applicant_name);
  const [editingSched, setEditingSched] = useState(false);
  const [endingOpen, setEndingOpen] = useState(false);
  const [endReason, setEndReason] = useState('');
  const endInputRef = useRef<HTMLInputElement>(null);
  const endId = useId();
  const [showResult, setShowResult] = useState(false);
  // toLocalInputValue reads the LOCAL wall-clock fields, unlike
  // toISOString().slice(0, 16) — which is always UTC and, shown in a
  // datetime-local input, silently displayed a UTC time as though it were
  // the reader's own, off by their UTC offset.
  const [schedDraft, setSchedDraft] = useState(toLocalInputValue(inv.scheduled_at));
  const schedInputRef = useRef<HTMLInputElement>(null);
  const schedId = useId();
  const tz = browserTimezone();

  // The draft is taken from the row each time the form opens, so a time
  // changed elsewhere since the page loaded is what HR edits.
  function openReschedule() {
    setSchedDraft(toLocalInputValue(inv.scheduled_at));
    setEditingSched(true);
  }
  useEffect(() => {
    if (editingSched) schedInputRef.current?.focus();
  }, [editingSched]);
  useEffect(() => {
    if (endingOpen) endInputRef.current?.focus();
  }, [endingOpen]);

  const isLive = inv.status === 'invited';
  // Started and not finished: the link still lets the candidate reconnect, so
  // it is not revoked like an unused one. It can be ended on purpose instead.
  const isStarted = inv.status === 'consumed';
  const modeValue = pendingMode ?? inv.avatar_mode;

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

        {/* When: the slot, and when the link stops working */}
        <div className="min-w-0 text-[13.5px] text-muted-foreground">
          <p className="flex items-center gap-1.5">
            <Calendar size={13} className="shrink-0" aria-hidden="true" />
            {inv.scheduled_at ? (
              <span className="text-foreground">{formatDateTime(inv.scheduled_at)}</span>
            ) : (
              <span>Any time</span>
            )}
          </p>
          {isLive ? (
            <p className="mt-0.5 text-[12px]">Link expires {formatDateTime(inv.expires_at)}</p>
          ) : null}
        </div>

        {/* Status */}
        <div className="min-w-0">
          <StatusTag tone={tone} dot={dot}>
            {label}
          </StatusTag>
          {inv.missed_slot ? (
            <p className="mt-0.5 text-[12px] text-muted-foreground">
              Reschedule to give a new time
            </p>
          ) : null}
        </div>

        {/* Action */}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {inv.composite_score !== null && (
            <span
              className="shrink-0 text-center"
              aria-label={`Score: ${formatScore('interview', inv.composite_score, { unit: false })} out of 10`}
            >
              <span className="text-[13px] font-semibold text-[var(--ui-ok)]">
                {formatScore('interview', inv.composite_score, { unit: false })}
              </span>
              <span className="text-[12px] text-muted-foreground">/10</span>
            </span>
          )}
          {inv.scorecard_id ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-expanded={showResult}
              aria-label={`${showResult ? 'Hide' : 'Show'} result for ${inv.applicant_name}`}
              onClick={() => setShowResult((v) => !v)}
            >
              <BarChart3 aria-hidden="true" /> Result
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
          {isLive ? (
            <select
              className="h-9 shrink-0 rounded-[10px] border border-border bg-secondary px-2 text-[12.5px] text-foreground focus:border-[var(--accent)] focus:outline-none disabled:opacity-40"
              value={modeValue}
              disabled={pendingMode !== null}
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
          {inv.can_reschedule && (
            <button
              type="button"
              aria-label={`Reschedule interview for ${inv.applicant_name}`}
              aria-expanded={editingSched}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40 hover:bg-[var(--ui-inset)] hover:text-[var(--ui-info)]"
              disabled={rescheduling}
              onClick={() => (editingSched ? setEditingSched(false) : openReschedule())}
            >
              <Pencil size={14} aria-hidden="true" />
            </button>
          )}
          {isLive && (
            <ConfirmDeleteButton
              title={`Revoke interview link for ${inv.applicant_name}`}
              confirmText="Revoke link"
              confirmGroupLabel={`Confirm: revoke the interview link for ${inv.applicant_name}. The link stops working.`}
              icon={Ban}
              pending={revoking}
              onConfirm={() => onRevoke(inv.invite_id)}
            />
          )}
          {isStarted && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-expanded={endingOpen}
              aria-label={`End started interview for ${inv.applicant_name}`}
              onClick={() => {
                setEndReason('');
                setEndingOpen((v) => !v);
              }}
            >
              <XCircle aria-hidden="true" /> End interview
            </Button>
          )}
        </div>
      </div>

      {/* End a started interview: a reason, then a confirm */}
      {isStarted && endingOpen && (
        <div className="mt-3 space-y-1.5 pl-[2.875rem]">
          <label htmlFor={endId} className={labelCls}>
            Why are you ending this interview?
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <input
              id={endId}
              ref={endInputRef}
              type="text"
              maxLength={500}
              value={endReason}
              onChange={(e) => setEndReason(e.target.value)}
              placeholder="For example, the candidate dropped out and will not return"
              className="h-10 min-w-[18rem] flex-1 rounded-[12px] border border-input bg-card px-3 text-[14px] text-foreground focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30"
              aria-describedby={`${endId}-hint`}
            />
            {endReason.trim() ? (
              <ConfirmDeleteButton
                label="End interview"
                confirmGroupLabel={`Confirm: end the started interview for ${inv.applicant_name}. Their link stops working and they cannot reconnect.`}
                icon={XCircle}
                pending={ending}
                onConfirm={() =>
                  onEnd(inv.invite_id, endReason.trim(), () => setEndingOpen(false))
                }
              />
            ) : null}
            <button
              type="button"
              aria-label="Close end interview"
              className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              onClick={() => setEndingOpen(false)}
            >
              <X size={13} aria-hidden="true" />
            </button>
          </div>
          <p id={`${endId}-hint`} className="text-[12px] text-muted-foreground">
            Their link stops working at once, so they cannot reconnect. The reason is kept in
            the audit log. You can then send a new link.
          </p>
        </div>
      )}

      {/* Reschedule inline form */}
      {editingSched && (
        <div className="mt-3 space-y-1.5 pl-[2.875rem]">
          <label htmlFor={schedId} className={labelCls}>
            New interview time (your time, {tz})
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <input
              id={schedId}
              ref={schedInputRef}
              type="datetime-local"
              min={nowLocalInput()}
              value={schedDraft}
              onChange={(e) => setSchedDraft(e.target.value)}
              className="h-10 rounded-[12px] border border-input bg-card px-3 text-[14px] text-foreground focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30"
              aria-describedby={`${schedId}-hint`}
            />
            <button
              type="button"
              aria-label={`Save new time for ${inv.applicant_name}`}
              disabled={!schedDraft || rescheduling}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-[var(--ui-ok)]/45 text-[var(--ui-ok)] transition-colors hover:bg-[var(--ui-ok-wash)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40"
              onClick={() => {
                if (!schedDraft) return;
                // Closed only once the server has accepted it, so a refusal
                // leaves the time HR typed on screen to correct.
                onReschedule(inv.invite_id, localInputToIso(schedDraft), () =>
                  setEditingSched(false),
                );
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
              aria-label="Close reschedule"
              className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              onClick={() => setEditingSched(false)}
            >
              <X size={13} aria-hidden="true" />
            </button>
          </div>
          <p id={`${schedId}-hint`} className="text-[12px] text-muted-foreground">
            The candidate can start within {JOIN_WINDOW_MINUTES} minutes of this time. We email
            them the new time.
          </p>
        </div>
      )}

      {showResult && inv.scorecard_id ? (
        <InterviewResult inviteId={inv.invite_id} name={inv.applicant_name} />
      ) : null}
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function HRInterviews() {
  const qc = useQueryClient();
  // Set when this is an interview citation (`/hr/interviews/{id}`, the
  // copilot's CITATION_ROUTES.interview) — absent on the plain list route.
  const { interviewId: citedInviteId } = useParams<{ interviewId?: string }>();
  const formId = useId();
  const tz = browserTimezone();

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
  const eligibleQuery = useQuery({
    queryKey: ['hr', 'interviews', 'eligible'],
    queryFn: () => listEligibleApplicants('any'),
    refetchInterval: LIVE_POLL_MS,
  });
  const invitesQuery = useQuery({
    queryKey: ['hr', 'interviews'],
    queryFn: () => listInvites(),
    refetchInterval: LIVE_POLL_MS,
  });
  const invites = invitesQuery.data;
  const invitesLoaded = invitesQuery.isSuccess;

  // ── Mutations ─────────────────────────────────────────────────────────────
  const inviteMut = useMutation({
    mutationFn: (replace: boolean) =>
      createInvite({
        applicant_id: chosen?.id ?? '',
        enrolment_id: chosen?.enrolment_id ?? null,
        language,
        avatar_mode: avatarMode,
        scheduled_at: scheduledAt ? localInputToIso(scheduledAt) : null,
        // Only after HR confirmed replacing the live link; the server refuses
        // a silent replacement.
        ...(replace ? { replace_existing: true } : {}),
      }),
    onSuccess: (res) => {
      setChoice('');
      setScheduledAt('');
      if (res.reused) {
        // An identical request a moment ago already made this link and sent
        // the email; the server sent nothing new and cannot show the link again.
        setMintedLink(null);
        toast.info(
          `${res.applicant_name} was sent this interview link a few minutes ago. No new email was sent. To send a new link, choose Replace interview link.`,
        );
      } else {
        setMintedLink(res.magic_link);
        toast.success('Interview link created');
      }
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not create the interview link.')),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    onSuccess: () => {
      toast.success('Link revoked');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not revoke the link.')),
  });

  const endMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      endStartedInterview(id, reason),
    onSuccess: (inv) => {
      toast.success(`Interview ended for ${inv.applicant_name}. You can now send a new link.`);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not end the interview.')),
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
      void invalidateAfterHrMutation(qc);
    },
    // The server's 409 names the status and says what to do instead, so it is
    // shown verbatim rather than replaced by a generic failure.
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not change the interview mode.')),
  });

  const rescheduleMut = useMutation({
    mutationFn: ({ id, scheduledAt }: { id: string; scheduledAt: string }) =>
      rescheduleInvite(id, scheduledAt),
    onSuccess: () => {
      toast.success('Interview rescheduled');
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not reschedule the interview.')),
  });

  async function copyLink(link: string) {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Link copied');
    } catch {
      toast.error('Could not copy. Select the link and copy it manually.');
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────
  const allInvites = useMemo(() => invites ?? [], [invites]);
  const elig = eligibleQuery.data ?? [];
  const keyOf = applicationKey;
  const chosen = elig.find((a) => keyOf(a) === choice);

  const filteredInvites = useMemo(
    () => (filter === 'all' ? allInvites : allInvites.filter((inv) => inv.status === filter)),
    [allInvites, filter],
  );

  // ── An interview citation (`/hr/interviews/{id}`) ──────────────────────────
  //
  // This list IS the screen for an AI interview: one row per invite, with its
  // status, score and result. So the citation lands on the row rather than a
  // page of its own, and says so when the id is not one of these.
  //
  // The id can also be an interview_sessions id — a HUMAN panel interview,
  // which the evidence graph cites with the same `interview` kind. No HR screen
  // lists those; they live on the candidate's own record (CandidateDrawer's
  // "Interview schedule" section). Such an id therefore lands here with the
  // notice below rather than on a blank highlight, which is why the copy names
  // where else to look instead of claiming the record is gone.
  const citedInvite = citedInviteId
    ? allInvites.find((inv) => inv.invite_id === citedInviteId)
    : undefined;
  useDeepLinkedRow(citedInvite ? rowDomId(citedInvite.invite_id) : null, invitesLoaded);
  const citedInviteMissing = Boolean(citedInviteId) && invitesLoaded && citedInvite === undefined;

  const countLabel =
    filter === 'all'
      ? `Interviews (${allInvites.length})`
      : `Interviews (${filteredInvites.length} of ${allInvites.length})`;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <Page width="default">
      <PageHeader
        title="AI interviews"
        description="Invite shortlisted or exam-passed applicants to a voice interview with the AI avatar. We email them a private link; they need no account."
      />

      {/* Invite form */}
      <SectionCard
        title="Invite a candidate"
        icon={Send}
        description="Only shortlisted or exam-passed applicants are eligible."
      >
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="sm:col-span-2">
              <label htmlFor={`${formId}-applicant`} className={labelCls}>
                Applicant
              </label>
              <select
                id={`${formId}-applicant`}
                className={inputCls}
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
                disabled={eligibleQuery.isError && !eligibleQuery.data}
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
            </div>

            <div>
              <label htmlFor={`${formId}-language`} className={labelCls}>
                Interview language
              </label>
              <select
                id={`${formId}-language`}
                className={inputCls}
                value={language}
                onChange={(e) => setLanguage(e.target.value as 'en' | 'hi' | 'te')}
              >
                <option value="en">English</option>
                <option value="hi">हिंदी</option>
                <option value="te">తెలుగు</option>
              </select>
            </div>

            <div>
              <label htmlFor={`${formId}-mode`} className={labelCls}>
                Interview mode
              </label>
              <select
                id={`${formId}-mode`}
                className={inputCls}
                value={avatarMode}
                onChange={(e) => setAvatarMode(e.target.value as AvatarMode)}
              >
                <option value="avatar">Video avatar</option>
                <option value="audio">Audio only</option>
              </select>
            </div>
          </div>

          {eligibleQuery.isError && !eligibleQuery.data ? (
            <QueryErrorPanel
              title="Could not load eligible applicants."
              error={eligibleQuery.error}
              onRetry={() => void eligibleQuery.refetch()}
              retrying={eligibleQuery.isFetching}
            />
          ) : null}

          <p className="text-[12.5px] leading-relaxed text-muted-foreground">
            <strong className="font-semibold text-foreground">Audio only</strong> drops the
            video avatar and runs the interview as voice. It costs less per minute and holds
            up better on a weak connection. The questions, scoring and scorecard are
            identical either way.
          </p>

          <div>
            <label htmlFor={`${formId}-when`} className={labelCls}>
              Interview time (optional, your time, {tz})
            </label>
            <input
              id={`${formId}-when`}
              type="datetime-local"
              className={inputCls}
              min={nowLocalInput()}
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
              aria-describedby={`${formId}-when-hint`}
            />
            <p id={`${formId}-when-hint`} className="mt-1 text-[12px] text-muted-foreground">
              With a time set, the candidate can start within {JOIN_WINDOW_MINUTES} minutes of
              it. Leave it empty to let them start any time before the link expires.
            </p>
          </div>

          {chosen?.has_active_invite ? (
            <div className="space-y-2">
              <p className="text-[13px] text-[var(--ui-warn)]" role="status">
                {chosen.full_name} already has an active interview link for this opening. A new
                link replaces it, and the old one stops working.
              </p>
              <ConfirmDeleteButton
                label="Replace interview link"
                confirmText="Replace link and send"
                confirmGroupLabel={`Confirm: replace ${chosen.full_name}'s interview link`}
                icon={Send}
                pending={inviteMut.isPending}
                onConfirm={() => inviteMut.mutate(true)}
              />
            </div>
          ) : (
            <Button
              type="button"
              disabled={!chosen || inviteMut.isPending}
              onClick={() => inviteMut.mutate(false)}
            >
              {inviteMut.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <Send aria-hidden="true" />
              )}
              Generate interview link
            </Button>
          )}

          {/* Once-shown minted link */}
          {mintedLink && (
            <div
              className="space-y-1.5 rounded-[16px] border border-[var(--ui-ok)]/40 bg-[var(--ui-ok-wash)] p-3.5"
              role="alert"
            >
              <p className="text-[13px] font-semibold text-[var(--ui-ok)]">
                We have emailed this link to the candidate (when they have an email address)
                and added it to their applications page. Here is a copy, shown once:
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
              {countLabel}
            </h2>
          }
        >
          <div className="max-w-full overflow-x-auto">
            <StatusFilter active={filter} onChange={setFilter} />
          </div>
        </Toolbar>

        <QueryState
          query={invitesQuery}
          errorTitle="Could not load interviews."
          loading={
            <div className="space-y-2" role="status" aria-label="Loading interviews">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-16 w-full rounded-card" />
              ))}
            </div>
          }
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              size="page"
              icon={Video}
              title="No interviews yet"
              description="Invite an eligible applicant above."
            />
          }
        >
          {() =>
            filteredInvites.length === 0 ? (
              <EmptyState title="No interviews match this filter." />
            ) : (
              <div className="overflow-hidden rounded-card border border-border bg-card shadow-card">
                <div className="overflow-x-auto">
                  <div className="min-w-[980px]">
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
                        revoking={revokeMut.isPending && revokeMut.variables === inv.invite_id}
                        onEnd={(id, reason, done) =>
                          endMut.mutate({ id, reason }, { onSuccess: done })
                        }
                        ending={endMut.isPending && endMut.variables?.id === inv.invite_id}
                        onReschedule={(id, iso, done) =>
                          rescheduleMut.mutate({ id, scheduledAt: iso }, { onSuccess: done })
                        }
                        rescheduling={
                          rescheduleMut.isPending && rescheduleMut.variables?.id === inv.invite_id
                        }
                        onSetMode={(id, mode) => modeMut.mutate({ id, mode })}
                        pendingMode={
                          modeMut.isPending && modeMut.variables?.id === inv.invite_id
                            ? modeMut.variables.mode
                            : null
                        }
                        cited={citedInvite?.invite_id === inv.invite_id}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )
          }
        </QueryState>
      </div>
    </Page>
  );
}
