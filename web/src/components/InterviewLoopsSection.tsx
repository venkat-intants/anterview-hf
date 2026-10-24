// InterviewLoopsSection — PH4-A2. Everything about scheduling this
// application's human interviews: the loop(s) HR has created, each
// interview's time in the reader's own zone AND the candidate's, and the
// controls to create, add to, send, reschedule, cancel and close them out.
//
// Nothing here changes a candidate's stage or status — same invariant as
// ExceptionsSection for exceptions: a loop and its interviews are logistics,
// never a decision (D-05). English-only by design (CLAUDE.md — the HR
// console is not translated); the candidate's own read of these same loops
// lives in Applications.tsx and IS translated EN/HI/TE.
//
// Wording: the API calls one scheduled meeting a "session"; on screen it is an
// "interview", because "session" already means a login session elsewhere in
// the product (audit E8).

import { useEffect, useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { formatSessionWhen, browserTimezone } from '@/lib/timezone';
import { toLocalInputValue, localInputToIso } from '@/lib/localDatetime';
import { StatusTag, ToggleSwitch, type TagTone } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Button } from '@/components/ui/button';
import { fieldLabelClass } from '@/components/ui/control-styles';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Calendar,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Send,
  Ban,
} from '@/design/components/icons';
import { statusLabel } from '@/lib/statusLabel';

/** A text action in the flow of a card (a disclosure toggle, "show more"):
 *  the link Button without its box. */
const TEXT_LINK = 'h-auto px-0 py-0 text-[12px] font-normal';

/* ── Limits the server enforces (routers/interview_scheduling.py) ─────────── */
// Mirrored here so a value out of range is explained beside the field rather
// than refused by the server after the click.

const DURATION_MIN = 15;
const DURATION_MAX = 480;
const MAX_INTERVIEWERS = 6;
const TITLE_MAX = 200;
const LOCATION_MAX = 500;
const REASON_MAX = 500;
const BUFFER_MAX = 240;
/** Free times shown per step of "Show more". */
const SLOT_PAGE = 30;

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

/** Application statuses after which the schedule can no longer change: the
 *  server refuses new loops, interviews, sends and moves with a 409. */
const DECIDED = new Set(['hired', 'rejected']);

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

/** The current time, refreshed every `ms`, so a control that depends on "has
 *  this started" appears when it starts rather than on the next refetch. */
function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

function hasStarted(session: InterviewSession, now: number): boolean {
  return Boolean(session.starts_at) && new Date(session.starts_at as string).getTime() <= now;
}

/** The value for a datetime-local `min`: now, in the reader's own wall time. */
function nowLocalInput(): string {
  return toLocalInputValue(new Date().toISOString());
}

/** "15 to 480" check on a number field kept as text, so a cleared field reads
 *  as empty instead of 0. Returns the problem, or null. */
function durationProblem(raw: string): string | null {
  const n = Number(raw);
  if (raw.trim() === '' || !Number.isInteger(n) || n < DURATION_MIN || n > DURATION_MAX) {
    return `Duration must be ${DURATION_MIN} to ${DURATION_MAX} minutes.`;
  }
  return null;
}

/** "Start time (your time, Asia/Kolkata)" plus, once a time is typed, what it
 *  is for the candidate. HR types in their own zone; the saved interview is
 *  shown candidate-zone first, so both are on screen while typing. */
function StartTimeField({
  id,
  label,
  value,
  onChange,
  candidateTimezone,
  optionalNote,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  candidateTimezone: string;
  optionalNote?: string;
}) {
  const tz = browserTimezone();
  const iso = value ? localInputToIso(value) : null;
  return (
    <div>
      <FormField
        id={id}
        label={`${label} (your time, ${tz})${optionalNote ? ` ${optionalNote}` : ''}`}
      >
        {(p) => (
          <Input
            {...p}
            type="datetime-local"
            min={nowLocalInput()}
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
      </FormField>
      {iso && candidateTimezone !== tz ? (
        <p className="mt-1 text-[12px] text-muted-foreground" aria-live="polite">
          For the candidate: {formatSessionWhen(iso, candidateTimezone)}
        </p>
      ) : null}
    </div>
  );
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
  const [buffer, setBuffer] = useState('15');
  const [selfSchedule, setSelfSchedule] = useState(false);
  const uid = useId();

  const bufferN = Number(buffer);
  const bufferProblem =
    buffer.trim() === '' || !Number.isInteger(bufferN) || bufferN < 0 || bufferN > BUFFER_MAX
      ? `Gap must be 0 to ${BUFFER_MAX} minutes.`
      : null;

  const createMut = useMutation({
    mutationFn: () =>
      createLoop(enrolmentId, {
        title: title.trim() || 'Interviews',
        candidate_timezone: timezone,
        buffer_minutes: bufferN,
        self_schedule: selfSchedule,
      }),
    onSuccess: () => {
      toast.success('Interview loop created');
      void invalidateAfterHrMutation(qc);
      onDone();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not create the loop.')),
  });

  return (
    <div className="mt-3 flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      <FormField id={`${uid}-title`} label="Title">
        {(p) => (
          <Input
            {...p}
            value={title}
            maxLength={TITLE_MAX}
            onChange={(e) => setTitle(e.target.value)}
          />
        )}
      </FormField>
      <div className="grid grid-cols-2 gap-2.5">
        <FormField id={`${uid}-tz`} label="Candidate timezone">
          {(p) => (
            <SelectControl {...p} value={timezone} onChange={(e) => setTimezone(e.target.value)}>
              {zoneOptions(timezone).map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </SelectControl>
          )}
        </FormField>
        <FormField
          id={`${uid}-buffer`}
          label="Gap between interviews (minutes)"
          error={bufferProblem ?? undefined}
        >
          {(p) => (
            <Input
              {...p}
              type="number"
              min={0}
              max={BUFFER_MAX}
              value={buffer}
              onChange={(e) => setBuffer(e.target.value)}
            />
          )}
        </FormField>
      </div>
      <label className="flex items-center justify-between gap-2 text-[12.5px] text-foreground">
        Let the candidate choose times
        <ToggleSwitch
          checked={selfSchedule}
          onChange={setSelfSchedule}
          label="Let the candidate choose times"
        />
      </label>
      <Button
        type="button"
        size="sm"
        className="self-start"
        disabled={createMut.isPending || bufferProblem !== null}
        onClick={() => createMut.mutate()}
      >
        {createMut.isPending ? 'Creating…' : 'Create loop'}
      </Button>
    </div>
  );
}

/* ── Interviewer checkboxes ──────────────────────────────────────────────── */

function InterviewerPicker({
  selected,
  setSelected,
}: {
  selected: string[];
  setSelected: (next: (prev: string[]) => string[]) => void;
}) {
  const interviewers = useQuery({ queryKey: ['hr', 'interviewers'], queryFn: listInterviewers });
  const full = selected.length >= MAX_INTERVIEWERS;
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className={fieldLabelClass}>Interviewers (up to {MAX_INTERVIEWERS})</legend>
      {interviewers.isLoading ? (
        <div className="flex flex-col gap-1.5" aria-busy="true" aria-label="Loading interviewers">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-32" />
        </div>
      ) : interviewers.isError ? (
        // A failed fetch is not "nobody set up yet".
        <QueryErrorPanel
          title="Could not load interviewers."
          error={interviewers.error}
          onRetry={() => void interviewers.refetch()}
          retrying={interviewers.isFetching}
        />
      ) : (interviewers.data ?? []).length === 0 ? (
        <span className="text-[12px] text-muted-foreground">No interviewers set up yet.</span>
      ) : (
        <>
          {(interviewers.data ?? []).map((iv) => {
            const checked = selected.includes(iv.user_id);
            return (
              <label
                key={iv.user_id}
                className="flex items-center gap-2 text-[12.5px] text-foreground"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && full}
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
            );
          })}
          {full ? (
            <span className="text-[12px] text-muted-foreground">
              An interview has at most {MAX_INTERVIEWERS} interviewers.
            </span>
          ) : null}
        </>
      )}
    </fieldset>
  );
}

/* ── Add an interview to a loop ──────────────────────────────────────────── */

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

  const [roundId, setRoundId] = useState('');
  const [title, setTitle] = useState('');
  const [duration, setDuration] = useState('45');
  const [selected, setSelected] = useState<string[]>([]);
  const [startLocal, setStartLocal] = useState('');
  const [location, setLocation] = useState('');
  const [conflict, setConflict] = useState<string | null>(null);

  const durationErr = durationProblem(duration);

  const submitMut = useMutation({
    mutationFn: (allowOutside: boolean) =>
      addSession(loop.id, {
        round_id: roundId,
        title: title.trim() || null,
        duration_minutes: Number(duration),
        interviewer_user_ids: selected,
        starts_at: startLocal ? localInputToIso(startLocal) : null,
        location: location.trim() || null,
        allow_outside_availability: allowOutside,
      }),
    onSuccess: () => {
      // The server re-sends the schedule itself when the loop was already
      // sent, so the toast says who heard about it.
      toast.success(
        loop.sent_at ? 'Interview added and the candidate was sent the update' : 'Interview added',
      );
      setConflict(null);
      void invalidateAfterHrMutation(qc);
      onDone();
    },
    onError: (e: unknown) => {
      if (isAvailabilityConflict(e)) {
        setConflict(errorMessage(e, 'Not everyone is available then.'));
        return;
      }
      toast.error(errorMessage(e, 'Could not add the interview.'));
    },
  });

  if (blocked) return <p className="mt-2 text-[12px] text-muted-foreground">{blocked}</p>;

  const ready =
    Boolean(roundId) &&
    selected.length > 0 &&
    selected.length <= MAX_INTERVIEWERS &&
    durationErr === null &&
    Boolean(startLocal || loop.self_schedule);

  return (
    <div className="mt-3 flex flex-col gap-2.5 rounded-[10px] border border-border p-3">
      <FormField id={`${uid}-round`} label="Round">
        {(p) => (
          <SelectControl {...p} value={roundId} onChange={(e) => setRoundId(e.target.value)}>
            <option value="">Choose a round…</option>
            {rounds.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </SelectControl>
        )}
      </FormField>

      <FormField id={`${uid}-title`} label="Title (optional)">
        {(p) => (
          <Input
            {...p}
            value={title}
            maxLength={TITLE_MAX}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Defaults to the round's title"
          />
        )}
      </FormField>

      <div className="grid grid-cols-2 gap-2.5">
        <FormField
          id={`${uid}-duration`}
          label="Duration (minutes)"
          error={durationErr ?? undefined}
        >
          {(p) => (
            <Input
              {...p}
              type="number"
              min={DURATION_MIN}
              max={DURATION_MAX}
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          )}
        </FormField>
        <FormField id={`${uid}-location`} label="Location">
          {(p) => (
            <Input
              {...p}
              value={location}
              maxLength={LOCATION_MAX}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Room, or a video link"
            />
          )}
        </FormField>
      </div>

      <InterviewerPicker selected={selected} setSelected={setSelected} />

      <StartTimeField
        id={`${uid}-start`}
        label="Start time"
        optionalNote={loop.self_schedule ? '(optional, leave blank for the candidate to choose)' : undefined}
        value={startLocal}
        onChange={(v) => {
          setStartLocal(v);
          setConflict(null);
        }}
        candidateTimezone={loop.candidate_timezone}
      />

      {conflict ? (
        <div
          role="alert"
          className="rounded-[10px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] p-2.5 text-[12px] text-[var(--ui-soft)]"
        >
          <p>{conflict}</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            disabled={submitMut.isPending}
            onClick={() => submitMut.mutate(true)}
          >
            Schedule anyway
          </Button>
        </div>
      ) : null}

      <Button
        type="button"
        size="sm"
        className="self-start"
        disabled={!ready || submitMut.isPending}
        onClick={() => submitMut.mutate(false)}
      >
        {submitMut.isPending ? 'Adding…' : 'Add interview'}
      </Button>
    </div>
  );
}

/* ── One interview, its time, and its controls ───────────────────────────── */

function FreeSlotsPanel({
  sessionId,
  candidateTimezone,
  durationMinutes,
  onPick,
}: {
  sessionId: string;
  candidateTimezone: string;
  /** The saved length the server computed the free times for. */
  durationMinutes: number;
  onPick: (iso: string) => void;
}) {
  const [shown, setShown] = useState(SLOT_PAGE);
  const slots = useQuery({
    queryKey: ['hr', 'session', sessionId, 'slots'],
    queryFn: () => getSessionSlots(sessionId),
  });
  const all = slots.data ?? [];
  return (
    <div className="mt-2 rounded-[10px] border border-border p-2.5">
      {slots.isLoading ? (
        <div className="flex flex-col gap-1" aria-busy="true" aria-label="Looking for free times">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : slots.isError ? (
        <QueryErrorPanel
          title="Could not check free times."
          error={slots.error}
          onRetry={() => void slots.refetch()}
          retrying={slots.isFetching}
        />
      ) : all.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          No shared free time found in the next few weeks.
        </p>
      ) : (
        <>
          <p className="mb-1.5 text-[12px] text-muted-foreground">
            Free times for a {durationMinutes}-minute interview. Showing{' '}
            {Math.min(shown, all.length)} of {all.length}.
          </p>
          <ul className="flex max-h-[180px] flex-col gap-1 overflow-y-auto">
            {all.slice(0, shown).map((iso) => (
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
          {shown < all.length ? (
            <Button
              type="button"
              variant="link"
              size="sm"
              className={`mt-1.5 ${TEXT_LINK}`}
              onClick={() => setShown((n) => n + SLOT_PAGE)}
            >
              Show more free times
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}

function RescheduleForm({
  session,
  candidateTimezone,
  onDone,
}: {
  session: InterviewSession;
  candidateTimezone: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const uid = useId();
  const [startLocal, setStartLocal] = useState(toLocalInputValue(session.starts_at));
  const [duration, setDuration] = useState(String(session.duration_minutes));
  const [location, setLocation] = useState(session.location ?? '');
  const [conflict, setConflict] = useState<string | null>(null);
  const [showSlots, setShowSlots] = useState(false);

  const durationErr = durationProblem(duration);
  const durationChanged = Number(duration) !== session.duration_minutes;

  const rescheduleMut = useMutation({
    mutationFn: (allowOutside: boolean) =>
      rescheduleSession(session.id, {
        starts_at: localInputToIso(startLocal),
        duration_minutes: Number(duration),
        location: location.trim() || null,
        allow_outside_availability: allowOutside,
      }),
    onSuccess: () => {
      toast.success(session.starts_at ? 'Interview rescheduled' : 'Interview time set');
      setConflict(null);
      void invalidateAfterHrMutation(qc);
      onDone();
    },
    onError: (e: unknown) => {
      if (isAvailabilityConflict(e)) {
        setConflict(errorMessage(e, 'Not everyone is available then.'));
        return;
      }
      toast.error(errorMessage(e, 'Could not reschedule the interview.'));
    },
  });

  return (
    <div className="mt-2 flex flex-col gap-2 rounded-[10px] border border-border p-2.5">
      <div className="grid grid-cols-2 gap-2.5">
        <StartTimeField
          id={`${uid}-start`}
          label="New start time"
          value={startLocal}
          onChange={(v) => {
            setStartLocal(v);
            setConflict(null);
          }}
          candidateTimezone={candidateTimezone}
        />
        <FormField
          id={`${uid}-duration`}
          label="Duration (minutes)"
          error={durationErr ?? undefined}
        >
          {(p) => (
            <Input
              {...p}
              type="number"
              min={DURATION_MIN}
              max={DURATION_MAX}
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          )}
        </FormField>
      </div>
      <FormField id={`${uid}-location`} label="Location">
        {(p) => (
          <Input
            {...p}
            value={location}
            maxLength={LOCATION_MAX}
            onChange={(e) => setLocation(e.target.value)}
          />
        )}
      </FormField>

      <Button
        type="button"
        variant="link"
        size="sm"
        onClick={() => setShowSlots((v) => !v)}
        aria-expanded={showSlots}
        className={`self-start ${TEXT_LINK}`}
      >
        {showSlots ? 'Hide free times' : 'Show free times'}
      </Button>
      {showSlots ? (
        <>
          {durationChanged ? (
            <p className="text-[12px] text-muted-foreground">
              These free times are for the saved {session.duration_minutes}-minute length. A
              longer interview may not fit.
            </p>
          ) : null}
          <FreeSlotsPanel
            sessionId={session.id}
            candidateTimezone={candidateTimezone}
            durationMinutes={session.duration_minutes}
            onPick={(iso) => {
              setStartLocal(toLocalInputValue(iso));
              setConflict(null);
            }}
          />
        </>
      ) : null}

      {conflict ? (
        <div
          role="alert"
          className="rounded-[10px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] p-2.5 text-[12px] text-[var(--ui-soft)]"
        >
          <p>{conflict}</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-2"
            disabled={rescheduleMut.isPending}
            onClick={() => rescheduleMut.mutate(true)}
          >
            Schedule anyway
          </Button>
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!startLocal || durationErr !== null || rescheduleMut.isPending}
          onClick={() => rescheduleMut.mutate(false)}
        >
          {rescheduleMut.isPending ? 'Saving…' : 'Save new time'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Close
        </Button>
      </div>
    </div>
  );
}

/** Cancelling an interview: the reason lives here, in the confirm step, so it
 *  is never silently sent with (and dropped from) a completed/no-show mark. */
function CancelSessionPanel({
  session,
  isLastPending,
  pending,
  onConfirm,
  onKeep,
}: {
  session: InterviewSession;
  isLastPending: boolean;
  pending: boolean;
  onConfirm: (reason: string) => void;
  onKeep: () => void;
}) {
  const uid = useId();
  const [reason, setReason] = useState('');
  return (
    <div
      role="group"
      aria-label={`Cancel ${session.title}`}
      className="mt-2 flex flex-col gap-2 rounded-[10px] border border-[var(--ui-danger)]/30 p-2.5"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onKeep();
      }}
    >
      <p className="text-[12.5px] text-foreground">
        Cancel this interview?
        {session.starts_at ? ' The candidate and interviewers are told if the schedule was sent.' : ''}
        {isLastPending ? ' It is the last one left in this loop, so the loop closes too.' : ''}
      </p>
      <FormField id={`${uid}-reason`} label="Reason (optional)">
        {(p) => (
          <Input
            {...p}
            type="text"
            value={reason}
            maxLength={REASON_MAX}
            onChange={(e) => setReason(e.target.value)}
          />
        )}
      </FormField>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="destructive"
          size="sm"
          disabled={pending}
          aria-busy={pending}
          onClick={() => onConfirm(reason.trim())}
        >
          {pending ? 'Cancelling…' : 'Cancel interview'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onKeep}>
          Keep
        </Button>
      </div>
    </div>
  );
}

function SessionRow({
  session,
  candidateTimezone,
  decided,
  isLastPending,
}: {
  session: InterviewSession;
  candidateTimezone: string;
  /** A final decision is recorded: no more moves (the server refuses them). */
  decided: boolean;
  /** No other interview in the loop is still scheduled or awaiting a time. */
  isLastPending: boolean;
}) {
  const qc = useQueryClient();
  const now = useNow();
  const [rescheduling, setRescheduling] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const outcomeMut = useMutation({
    mutationFn: ({ outcome, reason }: { outcome: SessionOutcome; reason: string | null }) =>
      setSessionOutcome(session.id, outcome, reason),
    onSuccess: (res) => {
      const what =
        res.status === 'cancelled'
          ? 'Interview cancelled'
          : res.status === 'completed'
            ? 'Marked completed'
            : 'Marked as no-show';
      // The loop closes by itself when nothing is left pending; say so, since
      // "Add interview" disappears with it.
      const loopNote =
        res.loop_status === 'cancelled'
          ? '. The loop is now cancelled.'
          : res.loop_status === 'completed'
            ? '. The loop is now completed.'
            : '';
      toast.success(`${what}${loopNote}`);
      setCancelling(false);
      void invalidateAfterHrMutation(qc);
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update the interview.')),
  });

  const started = hasStarted(session, now);
  const live = session.status === 'scheduled' || session.status === 'awaiting_slot';

  return (
    <li className="rounded-[10px] border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[12.5px] font-medium text-foreground">
            {session.title} <span className="text-muted-foreground">· {session.round_title}</span>
          </p>
          <p className="mt-0.5 text-[12px] text-muted-foreground">
            {session.starts_at
              ? `${formatSessionWhen(session.starts_at, candidateTimezone)} · ${session.duration_minutes} min`
              : 'No time set yet. Waiting on the candidate to choose one.'}
            {session.location ? ` · ${session.location}` : ''}
          </p>
        </div>
        <StatusTag tone={SESSION_TONE[session.status]} dot>
          {statusLabel(session.status, 'session')}
        </StatusTag>
      </div>

      {session.interviewers.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {session.interviewers.map((iv) => (
            <li
              key={iv.user_id}
              className="flex items-center justify-between text-[12px] text-[var(--ui-soft)]"
            >
              <span>{iv.name}</span>
              <span className="text-muted-foreground">
                {iv.scorecard_status
                  ? `Scorecard ${statusLabel(iv.scorecard_status, 'scorecard').toLowerCase()}`
                  : 'No scorecard'}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {live ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {/* An interview still waiting on the candidate can be given a time by
              HR too — the candidate may never choose one. Not once a decision
              is recorded: the server refuses the move. */}
          {!decided ? (
            <Button
              type="button"
              variant="link"
              size="sm"
              onClick={() => setRescheduling((v) => !v)}
              aria-expanded={rescheduling}
              className={TEXT_LINK}
            >
              {rescheduling
                ? 'Close'
                : session.status === 'awaiting_slot'
                  ? 'Set a time'
                  : 'Reschedule'}
            </Button>
          ) : null}
          {session.status === 'scheduled' && started ? (
            <>
              {/* Both are permanent: once set, the server refuses any other
                  outcome. A misclick would record a no-show for good. */}
              <ConfirmDeleteButton
                label="Mark completed"
                confirmText="Mark completed"
                icon={null}
                pending={outcomeMut.isPending && outcomeMut.variables?.outcome === 'completed'}
                onConfirm={() => outcomeMut.mutate({ outcome: 'completed', reason: null })}
              />
              <ConfirmDeleteButton
                label="Mark no-show"
                confirmText="Mark no-show"
                confirmGroupLabel={`Confirm: record ${session.title} as a no-show. This cannot be changed.`}
                icon={null}
                pending={outcomeMut.isPending && outcomeMut.variables?.outcome === 'no_show'}
                onConfirm={() => outcomeMut.mutate({ outcome: 'no_show', reason: null })}
              />
            </>
          ) : session.status === 'scheduled' ? (
            <span className="text-[12px] text-muted-foreground">
              Completed or no-show can be recorded once it has started.
            </span>
          ) : null}
          {!cancelling ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setCancelling(true)}
              className="text-[var(--ui-danger)]"
            >
              <Ban aria-hidden="true" /> Cancel interview
            </Button>
          ) : null}
        </div>
      ) : null}

      {live && cancelling ? (
        <CancelSessionPanel
          session={session}
          isLastPending={isLastPending}
          pending={outcomeMut.isPending && outcomeMut.variables?.outcome === 'cancelled'}
          onConfirm={(reason) => outcomeMut.mutate({ outcome: 'cancelled', reason: reason || null })}
          onKeep={() => setCancelling(false)}
        />
      ) : null}

      {rescheduling && !decided ? (
        <RescheduleForm
          session={session}
          candidateTimezone={candidateTimezone}
          onDone={() => setRescheduling(false)}
        />
      ) : null}
    </li>
  );
}

/* ── One loop ─────────────────────────────────────────────────────────────── */

function LoopCard({
  loop,
  requisitionId,
  decided,
}: {
  loop: InterviewLoop;
  requisitionId: string | null;
  decided: boolean;
}) {
  const qc = useQueryClient();
  const [addingSession, setAddingSession] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const open = loop.status !== 'cancelled' && loop.status !== 'completed';
  const canChange = open && !decided;
  const pendingCount = loop.sessions.filter(
    (s) => s.status === 'scheduled' || s.status === 'awaiting_slot',
  ).length;
  // A calendar file only has something in it once an interview has a time.
  const hasCalendar = loop.sessions.some(
    (s) => Boolean(s.starts_at) && (s.status === 'scheduled' || s.status === 'cancelled'),
  );

  const invalidate = () => void invalidateAfterHrMutation(qc);

  const sendMut = useMutation({
    mutationFn: () => sendLoop(loop.id),
    onSuccess: (res) => {
      toast.success(
        res.sent === 'itinerary'
          ? loop.sent_at
            ? 'Sent the updated schedule to the candidate'
            : 'Sent the schedule to the candidate'
          : 'Asked the candidate to choose their times',
      );
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not send the schedule.')),
  });

  const cancelMut = useMutation({
    mutationFn: () => cancelLoop(loop.id, cancelReason.trim() || null),
    onSuccess: () => {
      toast.success('Loop cancelled');
      setCancelReason('');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not cancel the loop.')),
  });

  const icsMut = useMutation({
    mutationFn: () => downloadHrLoopIcs(loop.id),
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not download the calendar file.')),
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
          <p className="mt-0.5 text-[12px] text-muted-foreground">
            Candidate timezone {loop.candidate_timezone}
            {loop.sent_at ? ' · sent to the candidate' : ' · not sent yet'}
            {loop.self_schedule ? ' · candidate chooses times' : ''}
          </p>
        </div>
        <StatusTag tone={LOOP_TONE[loop.status]} dot>
          {statusLabel(loop.status, 'loop')}
        </StatusTag>
      </div>

      {loop.sessions.length > 0 ? (
        // Cancelled interviews stay listed (with their pill and no controls),
        // so the loop keeps its history.
        <ul className="mt-2.5 flex flex-col gap-2">
          {loop.sessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              candidateTimezone={loop.candidate_timezone}
              decided={decided}
              isLastPending={
                pendingCount === 1 && (s.status === 'scheduled' || s.status === 'awaiting_slot')
              }
            />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-[12px] text-muted-foreground">No interviews in this loop yet.</p>
      )}

      {canChange ? (
        <div className="mt-2.5">
          <Button
            type="button"
            variant="link"
            size="sm"
            onClick={() => setAddingSession((v) => !v)}
            aria-expanded={addingSession}
            className={TEXT_LINK}
          >
            {addingSession ? 'Cancel' : 'Add interview'}
          </Button>
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
        {canChange ? (
          loop.sent_at ? (
            // Each send emails the candidate and notifies every interviewer
            // again, so a repeat is confirmed and named as a resend.
            <ConfirmDeleteButton
              label="Resend schedule"
              confirmText="Resend to candidate"
              confirmGroupLabel="Confirm: email the schedule to the candidate again"
              icon={Send}
              pending={sendMut.isPending}
              onConfirm={() => sendMut.mutate()}
            />
          ) : (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={sendMut.isPending}
              onClick={() => sendMut.mutate()}
            >
              {sendMut.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <Send aria-hidden="true" />
              )}
              Send to candidate
            </Button>
          )
        ) : null}
        {hasCalendar ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={icsMut.isPending}
            aria-busy={icsMut.isPending}
            onClick={() => icsMut.mutate()}
          >
            {icsMut.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <Download aria-hidden="true" />
            )}
            Download calendar (.ics)
          </Button>
        ) : null}
        {open ? (
          <div className="ml-auto flex items-center gap-2">
            <Input
              type="text"
              size="sm"
              value={cancelReason}
              maxLength={REASON_MAX}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="Cancel reason (optional)"
              aria-label={`Cancel reason for ${loop.title}`}
              className="w-[200px]"
            />
            <ConfirmDeleteButton
              label="Cancel loop"
              icon={Ban}
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
  applicationStatus,
}: {
  enrolmentId: string;
  requisitionId: string | null;
  /** The application's status. Once hired or rejected, nothing here can be
   *  created, sent or moved (the server refuses with a 409); recording what
   *  happened at an interview that took place still can. */
  applicationStatus?: string | null;
}) {
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const decided = DECIDED.has(applicationStatus ?? '');

  const loops = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'loops'],
    queryFn: () => listLoopsForEnrolment(enrolmentId),
  });

  const rows = loops.data ?? [];

  return (
    <div className="mt-6 border-t border-border pt-5">
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
          Interview schedule{rows.length > 0 ? ` (${rows.length})` : ''}
        </button>
        {!decided ? (
          <Button
            type="button"
            variant="link"
            size="sm"
            onClick={() => setCreating((v) => !v)}
            aria-expanded={creating}
            className={TEXT_LINK}
          >
            {creating ? 'Cancel' : 'New loop'}
          </Button>
        ) : null}
      </div>

      {!expanded ? null : (
        <>
          {decided ? (
            <p className="mt-2 text-[12px] text-muted-foreground">
              A final decision ({statusLabel(applicationStatus, 'candidate').toLowerCase()}) is
              recorded, so this schedule can no longer change. You can still record what happened
              at an interview that took place.
            </p>
          ) : null}

          {creating && !decided ? (
            <CreateLoopForm enrolmentId={enrolmentId} onDone={() => setCreating(false)} />
          ) : null}

          {loops.isLoading ? (
            <div
              className="mt-2 flex flex-col gap-3"
              aria-busy="true"
              aria-label="Loading the interview schedule"
            >
              <Skeleton className="h-24 w-full rounded-[12px]" />
              <Skeleton className="h-24 w-full rounded-[12px]" />
            </div>
          ) : loops.isError ? (
            <QueryErrorPanel
              className="mt-2"
              title="Could not load this application's interview schedule."
              error={loops.error}
              onRetry={() => void loops.refetch()}
              retrying={loops.isFetching}
            />
          ) : rows.length === 0 ? (
            <p className="mt-2 text-[12.5px] text-muted-foreground">
              No interview loop yet for this application.
            </p>
          ) : (
            <div className="mt-2 flex flex-col gap-3">
              {rows.map((loop) => (
                <LoopCard
                  key={loop.id}
                  loop={loop}
                  requisitionId={requisitionId}
                  decided={decided}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
