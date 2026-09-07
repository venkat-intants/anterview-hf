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
import { useQuery } from '@tanstack/react-query';
import {
  getMyApplication,
  listMyApplications,
  type MyApplication,
} from '@/api/applications';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import {
  AlertCircle,
  Briefcase,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  User,
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
          <h2 className="flex items-center gap-2 text-[16px] font-semibold text-white">
            <Briefcase size={15} className="shrink-0 text-[#888b91]" aria-hidden="true" />
            <span className="truncate">{app.job_title}</span>
          </h2>
          <p className="mt-1 flex items-center gap-1.5 text-[13px] text-[#888b91]">
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
        <p className="mt-4 flex items-center gap-2 rounded-[10px] border border-white/8 bg-white/[0.03] px-3 py-2 text-[13px] text-[#c6c8cc]">
          <Clock size={13} className="shrink-0 text-[#888b91]" aria-hidden="true" />
          {round}
        </p>
      )}

      <p className="mt-3 text-[13.5px] leading-relaxed text-[#888b91]">{app.next_step}</p>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mt-4 inline-flex items-center gap-1 text-[12.5px] text-[#60a5fa] hover:underline focus:outline-none focus-visible:underline underline-offset-4"
      >
        {open ? (
          <ChevronDown size={13} aria-hidden="true" />
        ) : (
          <ChevronRight size={13} aria-hidden="true" />
        )}
        {open ? 'Hide history' : 'Show history'}
      </button>

      {open && (
        <div className="mt-3 border-t border-white/8 pt-3">
          {isLoading && <p className="text-[13px] text-[#888b91]">Loading…</p>}
          {detail && detail.history.length === 0 && (
            <p className="text-[13px] text-[#888b91]">Nothing has changed yet.</p>
          )}
          {detail && detail.history.length > 0 && (
            <ol className="flex flex-col gap-2.5">
              {detail.history.map((e, i) => (
                <li key={`${e.occurred_at}-${i}`} className="flex items-start gap-2.5">
                  <span
                    className="mt-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.16)] text-[#60a5fa]"
                    aria-hidden="true"
                  >
                    <Check size={10} />
                  </span>
                  <span className="text-[13px] text-[#c6c8cc]">
                    {e.stage}
                    <span className="text-[#888b91]">
                      {' '}
                      · {dateOf(e.occurred_at)}
                      {/* Worth surfacing: it answers "did a person look at
                          this, or did the system move me?" — which is exactly
                          what someone waiting wants to know. */}
                      {e.by_a_person && (
                        <>
                          {' '}
                          <User size={10} className="inline align-baseline" aria-hidden="true" />{' '}
                          by the hiring team
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
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(230,113,79,0.14)] text-[#e6714f]">
        <AlertCircle className="h-6 w-6" aria-hidden="true" />
      </span>
      <h2 className="mt-5 text-[18px] font-semibold text-white">
        Could not load your applications
      </h2>
      <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-[#888b91]">
        Something went wrong on our side, not with your applications. Refresh
        the page to try again.
      </p>
    </GlassCard>
  );
}

function EmptyState() {
  return (
    <GlassCard className="p-8 text-center">
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-[14px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
        <Briefcase className="h-6 w-6" aria-hidden="true" />
      </span>
      <h2 className="mt-5 text-[18px] font-semibold text-white">No applications yet</h2>
      <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-[#888b91]">
        Jobs you apply to will appear here, with the stage you are at and what
        happens next.
      </p>
      <p className="mx-auto mt-4 max-w-md text-[13px] leading-relaxed text-[#888b91]">
        Already applied somewhere? Your application is safe either way — but it
        only shows up here once you have opened the “Set a password” link in the
        confirmation email we sent you. Check that email, including its spam
        folder.
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
      toast.error(
        error instanceof Error ? error.message : 'Could not load your applications.',
      );
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
          className="text-[28px] font-semibold tracking-[-1px] text-white"
        >
          My applications
        </h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          Where you stand on every role you have applied for.
        </p>
      </Reveal>

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
            <p className="mt-4 text-[11px] font-medium uppercase tracking-[0.1em] text-[#888b91]">
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
