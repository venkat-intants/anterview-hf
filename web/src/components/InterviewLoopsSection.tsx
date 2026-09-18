// InterviewLoopsSection — PH4-A2. Everything about scheduling this
// application's human interviews: the loop(s) HR has created, each session's
// time in the reader's own zone AND the candidate's, and the controls to
// create, add to, send, reschedule, cancel and close them out.
//
// Nothing here changes a candidate's stage or status — same invariant as
// ExceptionsSection for exceptions: a loop and its sessions are logistics,
// never a decision (D-05). English-only by design (CLAUDE.md — the HR
// console is not translated); the candidate's own read of these same loops
// lives in Applications.tsx and IS translated EN/HI/TE.

import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import {
  addSession,
  cancelLoop,
  createLoop,
  downloadHrLoopIcs,
  getSessionSlots,
  listLoopsForEnrolment,
  rescheduleSession,
  sendLoop,
  setSessionOutcome,
  type InterviewLoop,
  type InterviewSession,
  type LoopStatus,
  type SessionOutcome,
} from '@/api/scheduling';
import { listInterviewers } from '@/api/scorecards';
import { getWorkflow, listWorkflows } from '@/api/workflows';
import { ApiError } from '@/api/client';
import { toast } from '@/lib/toast';
import { formatSessionWhen, browserTimezone } from '@/lib/timezone';
import { toLocalInputValue, localInputToIso } from '@/lib/localDatetime';
import { StatusTag, ToggleSwitch, type TagTone } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import {
  Calendar,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Send,
} from '@/design/components/icons';

const inputCls =
  'mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] ' +
  'text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';
const labelCls = 'text-[12px] font-medium text-[var(--ui-soft)]';

const COMMON_ZONES = [
  'Asia/Kolkata',
  'Asia/Kathmandu',
  'Asia/Dhaka',
  'Asia/Karachi',
  'Asia/Dubai',
  'Asia/Singapore',
  'Europe/London',
  'America/New_York',
  'America/Los_Angeles',
  'UTC',
];

const LOOP_TONE: Record<LoopStatus, TagTone> = {
  draft: 'neutral',
  scheduled: 'electric',
  completed: 'forest',
  cancelled: 'ember',
};

const SESSION_TONE: Record<InterviewSession['status'], TagTone> = {
  awaiting_slot: 'amber',
  scheduled: 'electric',
  completed: 'forest',
  no_show: 'ember',
  cancelled: 'neutral',
};

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** A 409 about availability offers "Schedule anyway"; every other refusal (a
 *  conflict, a closed candidacy, a bad range) is shown exactly as the server
 *  worded it, with no retry — those are not availability problems. */
function isAvailabilityConflict(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409 && /available/i.test(e.message);
}

function zoneOptions(current?: string): string[] {
  const zones = new Set(COMMON_ZONES);
  zones.add(browserTimezone());
  if (current) zones.add(current);
  return Array.from(zones).sort();
}

function hasStarted(session: InterviewSession): boolean {
  return (
    Boolean(session.starts_at) && new Date(session.starts_at as string).getTime() <= Date.now()
  );
}

function invalidateLoops(qc: QueryClient, enrolmentId: string): void {
  void qc.invalidateQueries({ queryKey: ['hr', 'enrolment', enrolmentId, 'loops'] });
}

/** The application's human-review rounds — same two-query approach the
 *  drawer's scorecard-assign form (HumanInterviewSection) already uses: the
 *  scorecards endpoint does not carry a round's `kind`, only the published
 *  workflow does. */
function useHumanReviewRounds(requisitionId: string | null) {
  const workflows = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId as string),
    enabled: Boolean(requisitionId),
  });
  const publishedId = workflows.data?.find((w) => w.status === 'published')?.id ?? null;
  const workflow = useQuery({
    queryKey: ['hr', 'workflow', publishedId],
    queryFn: () => getWorkflow(publishedId as string),
    enabled: Boolean(publishedId),
  });
  const rounds = (workflow.data?.rounds ?? [])
    .filter((r) => r.kind === 'human_review')
    .map((r) => ({ id: r.id, title: r.title }));
  const loading = workflows.isLoading || (Boolean(publishedId) && workflow.isLoading);
  const blocked: string | null = !requisitionId
    ? 'This application is not on an opening with a workflow, so there is no interview round to schedule.'
    : workflows.isError || workflow.isError
      ? "Could not load this opening's interview rounds. Try again in a moment."
      : loading
        ? null
        : rounds.length === 0
          ? "This opening's published workflow has no human interview round."
          : null;
  return { rounds, loading, blocked };
}

/* ── Create a loop ───────────────────────────────────────────────────────── */

function CreateLoopForm({ enrolmentId, onDone }: { enrolmentId: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState('Interviews');
  const [timezone, setTimezone] = useState('Asia/Kolkata');
  const [buffer, setBuffer] = useState(15);
  const [selfSchedule, setSelfSchedule] = useState(false);
  const uid = useId();

  const createMut = useMutation({
    mutationFn: () =>
      createLoop(enrolmentId, {
        title: title.trim() || 'Interviews',
        candidate_timezone: timezone,
        buffer_minutes: buffer,
        self_schedule: selfSchedule,
      }),
    onSuccess: () => {
      toast.success('Interview loop created');
      invalidateLoops(qc, enrolmentId);
      onDone();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not create this loop')),
  });

  return (
    <div className="mt-3 flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      <div>
        <label htmlFor={`${uid}-title`} className={labelCls}>
          Title
        </label>
        <input
          id={`${uid}-title`}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className={inputCls}
        />
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label htmlFor={`${uid}-tz`} className={labelCls}>
            Candidate timezone
          </label>
          <select
            id={`${uid}-tz`}
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            className={inputCls}
          >
            {zoneOptions(timezone).map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-buffer`} className={labelCls}>
            Gap between sessions (minutes)
          </label>
          <input
            id={`${uid}-buffer`}
            type="number"
            min={0}
            max={240}
            value={buffer}
            onChange={(e) => setBuffer(Number(e.target.value))}
            className={inputCls}
          />
        </div>
      </div>
      <label className="flex items-center justify-between gap-2 text-[12.5px] text-foreground">
        Let the candidate choose times
        <ToggleSwitch
          checked={selfSchedule}
          onChange={setSelfSchedule}
          label="Let the candidate choose times"
        />
      </label>
      <button
        type="button"
        disabled={createMut.isPending}
        onClick={() => createMut.mutate()}
        className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {createMut.isPending ? 'Creating…' : 'Create loop'}
      </button>
    </div>
  );
}

/* ── Add a session to a loop ─────────────────────────────────────────────── */

function AddSessionForm({
  loop,
  requisitionId,
  onDone,
}: {
  loop: InterviewLoop;
  requisitionId: string | null;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const { rounds, blocked } = useHumanReviewRounds(requisitionId);
  const interviewers = useQuery({ queryKey: ['hr', 'interviewers'], queryFn: listInterviewers });

  const [roundId, setRoundId] = useState('');
  const [title, setTitle] = useState('');
  const [duration, setDuration] = useState(45);
  const [selected, setSelected] = useState<string[]>([]);
  const [startLocal, setStartLocal] = useState('');
  const [location, setLocation] = useState('');
  const [conflict, setConflict] = useState<string | null>(null);

  const submitMut = useMutation({
    mutationFn: (allowOutside: boolean) =>
      addSession(loop.id, {
        round_id: roundId,
        title: title.trim() || null,
        duration_minutes: duration,
        interviewer_user_ids: selected,
        starts_at: startLocal ? localInputToIso(startLocal) : null,
        location: location.trim() || null,
        allow_outside_availability: allowOutside,
      }),
    onSuccess: () => {
      toast.success('Session added');
      setConflict(null);
      invalidateLoops(qc, loop.enrolment_id);
      onDone();
    },
    onError: (e: unknown) => {
      if (isAvailabilityConflict(e)) {
        setConflict(errText(e, 'Not everyone is available then.'));
        return;
      }
      toast.error(errText(e, 'Could not add this session'));
    },
  });

  if (blocked) return <p className="mt-2 text-[11.5px] text-[var(--ui-faint)]">{blocked}</p>;

  const ready =
    Boolean(roundId) && selected.length > 0 && Boolean(startLocal || loop.self_schedule);

  return (
    <div className="mt-3 flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      <div>
        <label htmlFor={`${uid}-round`} className={labelCls}>
          Round
        </label>
        <select
          id={`${uid}-round`}
          value={roundId}
          onChange={(e) => setRoundId(e.target.value)}
          className={inputCls}
        >
          <option value="">Choose a round…</option>
          {rounds.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor={`${uid}-title`} className={labelCls}>
          Title (optional)
        </label>
        <input
          id={`${uid}-title`}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Defaults to the round's title"
          className={inputCls}
        />
      </div>

      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label htmlFor={`${uid}-duration`} className={labelCls}>
            Duration (minutes)
          </label>
          <input
            id={`${uid}-duration`}
            type="number"
            min={15}
            max={480}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor={`${uid}-location`} className={labelCls}>
            Location
          </label>
          <input
            id={`${uid}-location`}
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="Room, or a video link"
            className={inputCls}
          />
        </div>
      </div>

      <fieldset className="flex flex-col gap-1.5">
        <legend className={labelCls}>Interviewers</legend>
        {interviewers.isLoading ? (
          <span className="text-[12px] text-muted-foreground">Loading…</span>
        ) : (interviewers.data ?? []).length === 0 ? (
          <span className="text-[12px] text-muted-foreground">No interviewers set up yet.</span>
        ) : (
          (interviewers.data ?? []).map((iv) => (
            <label
              key={iv.user_id}
              className="flex items-center gap-2 text-[12.5px] text-foreground"
            >
              <input
                type="checkbox"
                checked={selected.includes(iv.user_id)}
                onChange={(e) =>
                  setSelected((prev) =>
                    e.target.checked
                      ? [...prev, iv.user_id]
                      : prev.filter((id) => id !== iv.user_id),
                  )
                }
                className="h-4 w-4 accent-[var(--accent)]"
              />
              {iv.full_name}
            </label>
          ))
        )}
      </fieldset>

      <div>
        <label htmlFor={`${uid}-start`} className={labelCls}>
          Start time
          {loop.self_schedule ? ' (optional — leave blank for the candidate to choose)' : ''}
        </label>
        <input
          id={`${uid}-start`}
          type="datetime-local"
          value={startLocal}
          onChange={(e) => {
            setStartLocal(e.target.value);
            setConflict(null);
          }}
          className={inputCls}
        />
      </div>

      {conflict ? (
        <div
          role="alert"
          className="rounded-[10px] border border-[var(--ui-warn)]/40 bg-[rgba(255,183,100,0.1)] p-2.5 text-[12px] text-[var(--ui-soft)]"
        >
          <p>{conflict}</p>
          <button
            type="button"
            disabled={submitMut.isPending}
            onClick={() => submitMut.mutate(true)}
            className="mt-2 rounded-[8px] border border-[var(--ui-line-strong)] px-3 py-1 text-[12px] font-medium text-foreground disabled:opacity-40"
          >
            Schedule anyway
          </button>
        </div>
      ) : null}

      <button
        type="button"
        disabled={!ready || submitMut.isPending}
        onClick={() => submitMut.mutate(false)}
        className="self-start rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
      >
        {submitMut.isPending ? 'Adding…' : 'Add session'}
      </button>
    </div>
  );
}

/* ── One session, its time, and its controls ────────────────────────────── */

function FreeSlotsPanel({
  sessionId,
  candidateTimezone,
  onPick,
}: {
  sessionId: string;
  candidateTimezone: string;
  onPick: (iso: string) => void;
}) {
  const slots = useQuery({
    queryKey: ['hr', 'session', sessionId, 'slots'],
    queryFn: () => getSessionSlots(sessionId),
  });
  return (
    <div className="mt-2 rounded-[10px] border border-border p-2.5">
      {slots.isLoading ? (
        <p className="text-[12px] text-muted-foreground">Looking for free times…</p>
      ) : slots.isError ? (
        <p className="text-[12px] text-muted-foreground">Could not check free times.</p>
      ) : (slots.data ?? []).length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          No shared free time found in the next few weeks.
        </p>
      ) : (
        <ul className="flex max-h-[180px] flex-col gap-1 overflow-y-auto">
          {(slots.data ?? []).slice(0, 30).map((iso) => (
            <li key={iso}>
              <button
                type="button"
                onClick={() => onPick(iso)}
                className="w-full rounded-[8px] px-2 py-1 text-left text-[12px] text-[var(--ui-soft)] hover:bg-[var(--ui-inset-soft)] hover:text-foreground"
              >
                {formatSessionWhen(iso, candidateTimezone)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RescheduleForm({
  session,
  candidateTimezone,
  enrolmentId,
  onDone,
}: {
  session: InterviewSession;
  candidateTimezone: string;
  enrolmentId: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const [startLocal, setStartLocal] = useState(toLocalInputValue(session.starts_at));
  const [duration, setDuration] = useState(session.duration_minutes);
  const [location, setLocation] = useState(session.location ?? '');
  const [conflict, setConflict] = useState<string | null>(null);
  const [showSlots, setShowSlots] = useState(false);

  const rescheduleMut = useMutation({
    mutationFn: (allowOutside: boolean) =>
      rescheduleSession(session.id, {
        starts_at: localInputToIso(startLocal),
        duration_minutes: duration,
        location: location.trim() || null,
        allow_outside_availability: allowOutside,
      }),
    onSuccess: () => {
      toast.success('Interview rescheduled');
      setConflict(null);
      invalidateLoops(qc, enrolmentId);
      onDone();
    },
    onError: (e: unknown) => {
      if (isAvailabilityConflict(e)) {
        setConflict(errText(e, 'Not everyone is available then.'));
        return;
      }
      toast.error(errText(e, 'Could not reschedule this session'));
    },
  });

  return (
    <div className="mt-2 flex flex-col gap-2 rounded-[10px] border border-border p-2.5">
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label htmlFor={`${uid}-start`} className={labelCls}>
            New start time
          </label>
          <input
            id={`${uid}-start`}
            type="datetime-local"
            value={startLocal}
            onChange={(e) => {
              setStartLocal(e.target.value);
              setConflict(null);
            }}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor={`${uid}-duration`} className={labelCls}>
            Duration (minutes)
          </label>
          <input
            id={`${uid}-duration`}
            type="number"
            min={15}
            max={480}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
            className={inputCls}
          />
        </div>
      </div>
      <div>
        <label htmlFor={`${uid}-location`} className={labelCls}>
          Location
        </label>
        <input
          id={`${uid}-location`}
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          className={inputCls}
        />
      </div>

      <button
        type="button"
        onClick={() => setShowSlots((v) => !v)}
        className="self-start text-[12px] text-[var(--ui-info)] hover:underline"
      >
        {showSlots ? 'Hide free times' : 'Show free times'}
      </button>
      {showSlots ? (
        <FreeSlotsPanel
          sessionId={session.id}
          candidateTimezone={candidateTimezone}
          onPick={(iso) => {
            setStartLocal(toLocalInputValue(iso));
            setConflict(null);
          }}
        />
      ) : null}

      {conflict ? (
        <div
          role="alert"
          className="rounded-[10px] border border-[var(--ui-warn)]/40 bg-[rgba(255,183,100,0.1)] p-2.5 text-[12px] text-[var(--ui-soft)]"
        >
          <p>{conflict}</p>
          <button
            type="button"
            disabled={rescheduleMut.isPending}
            onClick={() => rescheduleMut.mutate(true)}
            className="mt-2 rounded-[8px] border border-[var(--ui-line-strong)] px-3 py-1 text-[12px] font-medium text-foreground disabled:opacity-40"
          >
            Schedule anyway
          </button>
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!startLocal || rescheduleMut.isPending}
          onClick={() => rescheduleMut.mutate(false)}
          className="rounded-[10px] bg-primary px-3.5 py-1.5 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
        >
          {rescheduleMut.isPending ? 'Saving…' : 'Save new time'}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded-[10px] px-3.5 py-1.5 text-[12.5px] font-medium text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function SessionRow({
  session,
  candidateTimezone,
  enrolmentId,
}: {
  session: InterviewSession;
  candidateTimezone: string;
  enrolmentId: string;
}) {
  const qc = useQueryClient();
  const [rescheduling, setRescheduling] = useState(false);
  const [cancelReason, setCancelReason] = useState('');

  const outcomeMut = useMutation({
    mutationFn: (outcome: SessionOutcome) =>
      setSessionOutcome(session.id, outcome, cancelReason || null),
    onSuccess: (res) => {
      toast.success(
        res.status === 'cancelled'
          ? 'Session cancelled'
          : res.status === 'completed'
            ? 'Marked completed'
            : 'Marked as no-show',
      );
      setCancelReason('');
      invalidateLoops(qc, enrolmentId);
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not update this session')),
  });

  const started = hasStarted(session);
  const live = session.status === 'scheduled' || session.status === 'awaiting_slot';

  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12.5px] font-medium text-foreground">
            {session.title} <span className="text-[var(--ui-faint)]">· {session.round_title}</span>
          </p>
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">
            {session.starts_at
              ? `${formatSessionWhen(session.starts_at, candidateTimezone)} · ${session.duration_minutes} min`
              : 'No time set yet — waiting on the candidate to choose one.'}
            {session.location ? ` · ${session.location}` : ''}
          </p>
        </div>
        <StatusTag tone={SESSION_TONE[session.status]} dot>
          {session.status.replace('_', ' ')}
        </StatusTag>
      </div>

      {session.interviewers.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {session.interviewers.map((iv) => (
            <li
              key={iv.user_id}
              className="flex items-center justify-between text-[11.5px] text-[var(--ui-soft)]"
            >
              <span>{iv.name}</span>
              <span className="text-[var(--ui-faint)]">
                {iv.scorecard_status ? iv.scorecard_status.replace('_', ' ') : 'no scorecard'}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {live ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {session.status === 'scheduled' ? (
            <button
              type="button"
              onClick={() => setRescheduling((v) => !v)}
              className="text-[12px] text-[var(--ui-info)] hover:underline"
            >
              {rescheduling ? 'Close' : 'Reschedule'}
            </button>
          ) : null}
          {session.status === 'scheduled' && started ? (
            <>
              <button
                type="button"
                disabled={outcomeMut.isPending}
                onClick={() => outcomeMut.mutate('completed')}
                className="rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-foreground disabled:opacity-40"
              >
                Mark completed
              </button>
              <button
                type="button"
                disabled={outcomeMut.isPending}
                onClick={() => outcomeMut.mutate('no_show')}
                className="rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-foreground disabled:opacity-40"
              >
                Mark no-show
              </button>
            </>
          ) : session.status === 'scheduled' ? (
            <span className="text-[11px] text-[var(--ui-faint)]">
              Completed/no-show can be recorded once it has started.
            </span>
          ) : null}
          <input
            type="text"
            value={cancelReason}
            onChange={(e) => setCancelReason(e.target.value)}
            placeholder="Cancel reason (optional)"
            aria-label={`Cancel reason for ${session.title}`}
            className="w-[180px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[11.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
          />
          <ConfirmDeleteButton
            label="Cancel"
            pending={outcomeMut.isPending}
            onConfirm={() => outcomeMut.mutate('cancelled')}
          />
        </div>
      ) : null}

      {rescheduling ? (
        <RescheduleForm
          session={session}
          candidateTimezone={candidateTimezone}
          enrolmentId={enrolmentId}
          onDone={() => setRescheduling(false)}
        />
      ) : null}
    </li>
  );
}

/* ── One loop ─────────────────────────────────────────────────────────────── */

function LoopCard({ loop, requisitionId }: { loop: InterviewLoop; requisitionId: string | null }) {
  const qc = useQueryClient();
  const [addingSession, setAddingSession] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const open = loop.status !== 'cancelled' && loop.status !== 'completed';

  const invalidate = () => invalidateLoops(qc, loop.enrolment_id);

  const sendMut = useMutation({
    mutationFn: () => sendLoop(loop.id),
    onSuccess: (res) => {
      toast.success(
        res.sent === 'itinerary'
          ? 'Sent the schedule to the candidate'
          : 'Asked the candidate to choose their times',
      );
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not send this loop')),
  });

  const cancelMut = useMutation({
    mutationFn: () => cancelLoop(loop.id, cancelReason || null),
    onSuccess: () => {
      toast.success('Loop cancelled');
      setCancelReason('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errText(e, 'Could not cancel this loop')),
  });

  const icsMut = useMutation({
    mutationFn: () => downloadHrLoopIcs(loop.id),
    onError: (e: unknown) => toast.error(errText(e, 'Could not download the calendar file')),
  });

  return (
    <div
      role="group"
      aria-label={`Interview loop: ${loop.title}`}
      className="rounded-[12px] border border-border p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12.5px] font-medium text-foreground">{loop.title}</p>
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">
            Candidate timezone {loop.candidate_timezone}
            {loop.sent_at ? ' · sent to the candidate' : ' · not sent yet'}
            {loop.self_schedule ? ' · candidate chooses times' : ''}
          </p>
        </div>
        <StatusTag tone={LOOP_TONE[loop.status]} dot>
          {loop.status}
        </StatusTag>
      </div>

      {loop.sessions.length > 0 ? (
        <ul className="mt-2.5 flex flex-col gap-2">
          {loop.sessions
            .filter((s) => s.status !== 'cancelled' || loop.status === 'cancelled')
            .map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                candidateTimezone={loop.candidate_timezone}
                enrolmentId={loop.enrolment_id}
              />
            ))}
        </ul>
      ) : (
        <p className="mt-2 text-[11.5px] text-muted-foreground">No sessions yet.</p>
      )}

      {open ? (
        <div className="mt-2.5">
          <button
            type="button"
            onClick={() => setAddingSession((v) => !v)}
            className="text-[12px] text-[var(--ui-info)] hover:underline"
          >
            {addingSession ? 'Close' : 'Add session'}
          </button>
          {addingSession ? (
            <AddSessionForm
              loop={loop}
              requisitionId={requisitionId}
              onDone={() => setAddingSession(false)}
            />
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-2.5">
        {open ? (
          <button
            type="button"
            disabled={sendMut.isPending}
            onClick={() => sendMut.mutate()}
            className="inline-flex items-center gap-1.5 rounded-[9px] border border-border px-3 py-1.5 text-[12px] font-medium text-foreground disabled:opacity-40"
          >
            {sendMut.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden="true" />
            ) : (
              <Send size={13} aria-hidden="true" />
            )}
            Send to candidate
          </button>
        ) : null}
        <button
          type="button"
          disabled={icsMut.isPending}
          onClick={() => icsMut.mutate()}
          className="inline-flex items-center gap-1.5 rounded-[9px] border border-border px-3 py-1.5 text-[12px] font-medium text-foreground disabled:opacity-40"
        >
          <Download size={13} aria-hidden="true" />
          Download calendar (.ics)
        </button>
        {open ? (
          <div className="ml-auto flex items-center gap-2">
            <input
              type="text"
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="Cancel reason (optional)"
              aria-label={`Cancel reason for ${loop.title}`}
              className="w-[180px] rounded-[8px] border border-border bg-secondary px-2 py-1 text-[11.5px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
            />
            <ConfirmDeleteButton
              label="Cancel loop"
              pending={cancelMut.isPending}
              onConfirm={() => cancelMut.mutate()}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ── Section ──────────────────────────────────────────────────────────────── */

export default function InterviewLoopsSection({
  enrolmentId,
  requisitionId,
}: {
  enrolmentId: string;
  requisitionId: string | null;
}) {
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState(true);

  const loops = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'loops'],
    queryFn: () => listLoopsForEnrolment(enrolmentId),
  });

  const rows = loops.data ?? [];

  return (
    <div className="mt-5">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="flex items-center gap-1.5 text-[13px] font-medium text-foreground"
        >
          {expanded ? (
            <ChevronDown size={14} aria-hidden="true" />
          ) : (
            <ChevronRight size={14} aria-hidden="true" />
          )}
          <Calendar size={13} aria-hidden="true" />
          Interviews{rows.length > 0 ? ` (${rows.length})` : ''}
        </button>
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
        >
          {creating ? 'Close' : 'New loop'}
        </button>
      </div>

      {!expanded ? null : (
        <>
          {creating ? (
            <CreateLoopForm enrolmentId={enrolmentId} onDone={() => setCreating(false)} />
          ) : null}

          {loops.isLoading ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">Loading…</p>
          ) : loops.isError ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
              Could not load this application&apos;s interview schedule.
            </p>
          ) : rows.length === 0 ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
              No interview loop yet for this application.
            </p>
          ) : (
            <div className="mt-2 flex flex-col gap-3">
              {rows.map((loop) => (
                <LoopCard key={loop.id} loop={loop} requisitionId={requisitionId} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
