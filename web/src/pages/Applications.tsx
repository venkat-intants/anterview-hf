// Applications — where a candidate stands on every job they applied to.
//
// This page exists because the information already did. An applicant's stage,
// the round they are on and every transition were recorded from the day they
// applied, and the only people who could see any of it were the hiring team.
//
// What it deliberately does NOT show, because the API does not send it: any
// score, any threshold, any note written about them. Progress and next steps
// only. The one place that shades toward tact is the internal `held` state,
// which reaches this page already worded as "Under review" — a candidate below
// a round threshold has not been rejected, a person decides that, and telling
// them they fell short of a bar that cannot reject them would be both unkind
// and untrue.
//
// The empty state carries real weight here. Two very different people see it:
// someone who only practises and has applied to nothing, and an applicant who
// signed in with an account that was never linked to their application. The
// second needs to know their application is safe and what to do about it.

import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  getMyApplication,
  listMyApplications,
  type MyApplication,
  mintMyExamLink,
  mintMyInterviewLink,
  mintMyTaskLink,
} from '@/api/applications';
import { ApiError } from '@/api/client';
import { sameOriginUrl } from '@/lib/safeUrl';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import YourInterviews from '@/components/candidate/YourInterviews';
import YourOffers from '@/components/candidate/YourOffers';
import YourRediscovery from '@/components/candidate/YourRediscovery';
import {
  AlertCircle,
  Briefcase,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Clock,
  User,
  Video,
} from '@/design/components/icons';

/**
 * Stage → visual tone. Keyed on the candidate-facing label the server sends,
 * so a stage this build has never heard of falls through to neutral rather
 * than crashing or being mislabelled.
 */
const TONE: Record<string, TagTone> = {
  'Application received': 'neutral',
  Shortlisted: 'electric',
  'Interview complete': 'lavender',
  'Under review': 'amber',
  Selected: 'forest',
  'Not progressing': 'ember',
};

function dateOf(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** "Round 2 of 4 · Coding Round", or null before a round is assigned. */
function roundLine(app: MyApplication): string | null {
  if (!app.current_round_title) return null;
  const counted =
    app.round_number && app.total_rounds
      ? `Round ${app.round_number} of ${app.total_rounds} · `
      : '';
  return `${counted}${app.current_round_title}`;
}

/**
 * The one thing on this page a candidate can act on.
 *
 * The invitation is emailed, and that used to be the ONLY way to reach it —
 * so a filtered or mistyped address left an interview that existed and could
 * not be started, while looking healthy from the hiring side. This is the
 * route that does not depend on mail.
 *
 * The link is fetched on click rather than rendered with the card, because
 * asking for it ROTATES the token: any previously issued link, including the
 * emailed one, stops working. That is the right trade for a deliberate press
 * and the wrong one for a page render, which would silently break the email
 * link of every candidate who merely looked at their applications.
 */
function InterviewCallToAction({
  inviteId,
  scheduledAt,
}: {
  inviteId: string;
  scheduledAt: string | null;
}) {
  const [error, setError] = useState<string | null>(null);

  const mint = useMutation({
    mutationFn: () => mintMyInterviewLink(inviteId),
    onSuccess: (link) => {
      // Same tab: this is the candidate starting their interview, not opening a
      // reference. A popup here would also be the kind of thing a blocker eats.
      // Same origin guard as the task and exam CTAs.
      const safe = sameOriginUrl(link.interview_url, '/interview-invite');
      if (!safe) {
        setError('Could not open your interview.');
        return;
      }
      window.location.assign(safe);
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : 'Could not open your interview.'),
  });

  return (
    <div className="mt-4 rounded-[10px] border border-[rgba(var(--accent-rgb),0.35)] bg-[rgba(var(--accent-rgb),0.06)] p-3.5">
      <p className="flex items-center gap-2 text-[13.5px] font-medium text-foreground">
        <Video size={14} className="shrink-0 text-[var(--accent)]" aria-hidden="true" />
        Your interview is ready
      </p>
      {scheduledAt ? (
        <p className="mt-1 pl-[22px] text-[12.5px] text-muted-foreground">
          Scheduled for {dateOf(scheduledAt)}
        </p>
      ) : (
        <p className="mt-1 pl-[22px] text-[12.5px] text-muted-foreground">
          Take it whenever you are ready — it takes about 10 minutes.
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          setError(null);
          mint.mutate();
        }}
        disabled={mint.isPending}
        className="mt-3 ml-[22px] inline-flex items-center gap-1.5 rounded-[8px] bg-primary px-3.5 py-1.5 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {mint.isPending ? 'Opening…' : 'Start interview'}
      </button>
      {error ? (
        <p className="mt-2 pl-[22px] text-[12.5px] text-[var(--ui-danger)]">{error}</p>
      ) : null}
    </div>
  );
}

/**
 * The task equivalent of InterviewCallToAction — PH4-D4, the same reasoning:
 * a job_simulation/portfolio link is emailed, and email is not dependable, so
 * this is the path that does not need it. Fetched on click because asking for
 * it ROTATES the token, the same trade as the interview link.
 */
function TaskCallToAction({ submissionId, dueAt }: { submissionId: string; dueAt: string | null }) {
  const [error, setError] = useState<string | null>(null);

  const mint = useMutation({
    mutationFn: () => mintMyTaskLink(submissionId),
    onSuccess: (link) => {
      // The link carries a fresh bearer token in its fragment — never
      // navigate to it unchecked (safeUrl.ts's own header names this exact
      // mistake). Same guard as YourOffers.tsx's "Open offer".
      const safe = sameOriginUrl(link.url, '/task');
      if (!safe) {
        setError('Could not open your task.');
        return;
      }
      window.location.assign(safe);
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : 'Could not open your task.'),
  });

  return (
    <div className="mt-4 rounded-[10px] border border-[rgba(var(--accent-rgb),0.35)] bg-[rgba(var(--accent-rgb),0.06)] p-3.5">
      <p className="flex items-center gap-2 text-[13.5px] font-medium text-foreground">
        <Briefcase size={14} className="shrink-0 text-[var(--accent)]" aria-hidden="true" />
        Your task is ready
      </p>
      {dueAt ? (
        <p className="mt-1 pl-[22px] text-[12.5px] text-muted-foreground">Due {dateOf(dueAt)}</p>
      ) : null}
      <button
        type="button"
        onClick={() => {
          setError(null);
          mint.mutate();
        }}
        disabled={mint.isPending}
        className="mt-3 ml-[22px] inline-flex items-center gap-1.5 rounded-[8px] bg-primary px-3.5 py-1.5 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {mint.isPending ? 'Opening…' : 'Open task'}
      </button>
      {error ? (
        <p className="mt-2 pl-[22px] text-[12.5px] text-[var(--ui-danger)]">{error}</p>
      ) : null}
    </div>
  );
}

/**
 * The assessment, reachable from here as well as from the email.
 *
 * The same gap the interview button closed, one round earlier: the card named
 * the round ("Round 1 of 1 · Aptitude") and the only way into it was a link in
 * an email that may never have arrived. Fetched on click for the same reason
 * too — asking ROTATES the token, which must be a deliberate press and never a
 * side effect of the page rendering.
 *
 * "Resume" once they have begun: /exam/start picks up the attempt already open,
 * with its original deadline, so coming back here costs no time and gains
 * none. What it does cost is the tab that attempt may still be open in, whose
 * link stops working the moment this one is minted.
 *
 * Which the server refuses to do unattended: with an attempt already open it
 * answers 409, and this asks the question before pressing again. A candidate
 * who has this page in a second tab, or who double-clicks, would otherwise
 * lose a timed assessment to a press they did not think about — the clock
 * keeps running in the tab that just went dead.
 */
function ExamCallToAction({
  assignmentId,
  inProgress,
  expiresAt,
  scheduledAt,
}: {
  assignmentId: string;
  inProgress: boolean;
  expiresAt: string | null;
  scheduledAt: string | null;
}) {
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const mint = useMutation({
    mutationFn: (resumeAnyway: boolean) => mintMyExamLink(assignmentId, resumeAnyway),
    onSuccess: (link) => {
      // Same tab, as with the interview: this is the candidate starting their
      // assessment, and a popup is the kind of thing a blocker eats. Checked
      // first, as the task CTA does: the URL is built server-side from
      // `exam_link_base_url`, so this is not attacker input — it is what stops
      // a misconfigured base URL walking the candidate to another origin with
      // a live exam token in the fragment.
      const safe = sameOriginUrl(link.exam_url, '/exam');
      if (!safe) {
        setError('Could not open your assessment.');
        return;
      }
      window.location.assign(safe);
    },
    onError: (e: unknown) => {
      // 409 is not a failure. It is the server declining to close a tab the
      // candidate may still be working in, and handing the decision back.
      if (e instanceof ApiError && e.status === 409) {
        setConfirming(e.message);
        return;
      }
      setError(e instanceof Error ? e.message : 'Could not open your assessment.');
    },
  });

  return (
    <div
      className="mt-4 rounded-[10px] border border-[rgba(var(--accent-rgb),0.35)] bg-[rgba(var(--accent-rgb),0.06)] p-3.5"
      data-testid="exam-cta"
    >
      <p className="flex items-center gap-2 text-[13.5px] font-medium text-foreground">
        <ClipboardCheck size={14} className="shrink-0 text-[var(--accent)]" aria-hidden="true" />
        {inProgress ? 'Your assessment is in progress' : 'Your assessment is ready'}
      </p>
      <p className="mt-1 pl-[22px] text-[12.5px] text-muted-foreground">
        {confirming
          ? confirming
          : inProgress
            ? 'You can pick up where you left off.'
            : // A scheduled round's own window is the deadline that binds, and
              // it closes long before the link expires. Naming the link's
              // expiry instead would state a deadline they do not have.
              scheduledAt
              ? `This round opens on ${dateOf(scheduledAt)}. Start it soon after — the window is short.`
              : expiresAt
                ? `Take it before ${dateOf(expiresAt)}.`
                : 'Take it whenever you are ready.'}
      </p>
      <button
        type="button"
        onClick={() => {
          setError(null);
          // Only a press that followed the question may close the other tab.
          mint.mutate(confirming !== null);
        }}
        disabled={mint.isPending}
        className="mt-3 ml-[22px] inline-flex items-center gap-1.5 rounded-[8px] bg-primary px-3.5 py-1.5 text-[13px] font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
      >
        {mint.isPending
          ? 'Opening…'
          : confirming
            ? 'Open it here'
            : inProgress
              ? 'Resume assessment'
              : 'Start assessment'}
      </button>
      {error ? (
        <p className="mt-2 pl-[22px] text-[12.5px] text-[var(--ui-danger)]">{error}</p>
      ) : null}
    </div>
  );
}

function ApplicationCard({ app }: { app: MyApplication }) {
  const [open, setOpen] = useState(false);
  const round = roundLine(app);

  // The history is only fetched when someone asks for it. Most people want the
  // headline; loading a timeline per card would be work nobody requested.
  const { data: detail, isLoading } = useQuery({
    queryKey: ['my-application', app.id],
    queryFn: () => getMyApplication(app.id),
    enabled: open,
    staleTime: 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  return (
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-[16px] font-semibold text-foreground">
            <Briefcase size={15} className="shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="truncate">{app.job_title}</span>
          </h2>
          <p className="mt-1 flex items-center gap-1.5 text-[13px] text-muted-foreground">
            <Building2 size={13} aria-hidden="true" />
            {app.company_name}
            <span aria-hidden="true">·</span>
            <span>Applied {dateOf(app.applied_at)}</span>
          </p>
        </div>
        <StatusTag tone={TONE[app.stage] ?? 'neutral'} dot>
          {app.stage}
        </StatusTag>
      </div>

      {round && (
        <p className="mt-4 flex items-center gap-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[13px] text-[var(--ui-soft)]">
          <Clock size={13} className="shrink-0 text-muted-foreground" aria-hidden="true" />
          {round}
        </p>
      )}

      <p className="mt-3 text-[13.5px] leading-relaxed text-muted-foreground">{app.next_step}</p>

      {app.interview_invite_id ? (
        <InterviewCallToAction
          inviteId={app.interview_invite_id}
          scheduledAt={app.interview_scheduled_at}
        />
      ) : app.task_submission_id ? (
        <TaskCallToAction submissionId={app.task_submission_id} dueAt={app.task_due_at} />
      ) : null}

      {app.exam_assignment_id ? (
        <ExamCallToAction
          assignmentId={app.exam_assignment_id}
          inProgress={app.exam_in_progress}
          expiresAt={app.exam_expires_at}
          scheduledAt={app.exam_scheduled_at}
        />
      ) : null}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mt-4 inline-flex items-center gap-1 text-[12.5px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline underline-offset-4"
      >
        {open ? (
          <ChevronDown size={13} aria-hidden="true" />
        ) : (
          <ChevronRight size={13} aria-hidden="true" />
        )}
        {open ? 'Hide history' : 'Show history'}
      </button>

      {open && (
        <div className="mt-3 border-t border-border pt-3">
          {isLoading && <p className="text-[13px] text-muted-foreground">Loading…</p>}
          {detail && detail.history.length === 0 && (
            <p className="text-[13px] text-muted-foreground">Nothing has changed yet.</p>
          )}
          {detail && detail.history.length > 0 && (
            <ol className="flex flex-col gap-2.5">
              {detail.history.map((e, i) => (
                <li key={`${e.occurred_at}-${i}`} className="flex items-start gap-2.5">
                  <span
                    className="mt-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.16)] text-[var(--ui-info)]"
                    aria-hidden="true"
                  >
                    <Check size={10} />
                  </span>
                  <span className="text-[13px] text-[var(--ui-soft)]">
                    {e.stage}
                    <span className="text-muted-foreground">
                      {' '}
                      · {dateOf(e.occurred_at)}
                      {/* Worth surfacing: it answers "did a person look at
                          this, or did the system move me?" — which is exactly
                          what someone waiting wants to know. */}
                      {e.by_a_person && (
                        <>
                          {' '}
                          <User size={10} className="inline align-baseline" aria-hidden="true" /> by
                          the hiring team
                        </>
                      )}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </GlassCard>
  );
}

function ErrorState() {
  return (
    <GlassCard className="p-8 text-center">
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(230,113,79,0.14)] text-[var(--ui-danger)]">
        <AlertCircle className="h-6 w-6" aria-hidden="true" />
      </span>
      <h2 className="mt-5 text-[18px] font-semibold text-foreground">
        Could not load your applications
      </h2>
      <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-muted-foreground">
        Something went wrong on our side, not with your applications. Refresh the page to try again.
      </p>
    </GlassCard>
  );
}

function EmptyState() {
  return (
    <GlassCard className="p-8 text-center">
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]">
        <Briefcase className="h-6 w-6" aria-hidden="true" />
      </span>
      <h2 className="mt-5 text-[18px] font-semibold text-foreground">No applications yet</h2>
      <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-muted-foreground">
        Jobs you apply to will appear here, with the stage you are at and what happens next.
      </p>
      <p className="mx-auto mt-4 max-w-md text-[13px] leading-relaxed text-muted-foreground">
        Already applied somewhere? Your application is safe either way — but it only shows up here
        once you have opened the “Set a password” link in the confirmation email we sent you. Check
        that email, including its spam folder.
      </p>
    </GlassCard>
  );
}

export default function Applications() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['my-applications'],
    queryFn: listMyApplications,
    staleTime: 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // One toast per distinct error, never one per render.
  useEffect(() => {
    if (isError) {
      toast.error(error instanceof Error ? error.message : 'Could not load your applications.');
    }
  }, [isError, error]);

  const apps = data ?? [];
  const live = apps.filter((a) => !a.closed);
  const closed = apps.filter((a) => a.closed);

  return (
    <div aria-labelledby="applications-heading" className="mx-auto max-w-[820px] px-6 py-8 lg:px-8">
      <Reveal>
        <h1
          id="applications-heading"
          className="text-[28px] font-semibold tracking-[-1px] text-foreground"
        >
          My applications
        </h1>
        <p className="mt-1 text-[14px] text-muted-foreground">
          Where you stand on every role you have applied for.
        </p>
      </Reveal>

      {/* PH4-A2 — interview loops the hiring team has scheduled or asked the
          candidate to choose a time for. */}
      <YourInterviews />

      {/* PH4-A3 — offers, once an application has been decided as a hire. */}
      <YourOffers />

      {/* PH5-E3 (D5-1) — one row per company applied to: whether this
          candidate has opted in to be found again for a future opening,
          above the application list itself. */}
      <YourRediscovery />

      <div className="mt-6 flex flex-col gap-4">
        {isLoading && (
          <>
            <GlassCard className="h-[132px] animate-pulse">
              <span className="sr-only">Loading your applications…</span>
            </GlassCard>
            <GlassCard className="h-[132px] animate-pulse">
              <span className="sr-only">Loading…</span>
            </GlassCard>
          </>
        )}

        {/* An error is NOT an empty list. Falling through to EmptyState here
            told an applicant they had applied to nothing because a fetch
            failed — the one message this page must never send by accident. */}
        {!isLoading && isError && <ErrorState />}
        {!isLoading && !isError && apps.length === 0 && <EmptyState />}

        {live.map((app) => (
          <ApplicationCard key={app.id} app={app} />
        ))}

        {/* Closed applications are kept, below a divider rather than hidden: a
            candidate looking for "did I hear back from that one?" needs them,
            and they should not compete with the live ones for attention. */}
        {closed.length > 0 && (
          <>
            <p className="mt-4 text-[11px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
              Closed
            </p>
            {closed.map((app) => (
              <ApplicationCard key={app.id} app={app} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
