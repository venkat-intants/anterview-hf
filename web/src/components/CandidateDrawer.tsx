// CandidateDrawer — one applicant, everything about them, without leaving the board.
//
// §6.5. The content all existed already; what did not exist was a place to
// read it together. A recruiter deciding on somebody was opening three pages
// and holding the fourth in their head.
//
// TWO THINGS THIS SURFACES THAT NOTHING ELSE DOES
//
// The name the candidate typed, when the CV disagrees. The reconciler no
// longer overwrites a typed name with the parsed one, so the two can differ —
// and if the drawer showed only one of them, the discrepancy would be
// invisible to the only person who could resolve it.
//
// Their answers to the opening's questions, which are otherwise write-only:
// candidates fill them in and nobody could read them back.
//
// AND ONE THING IT DELIBERATELY DOES NOT DO
// There is no Advance button here. Moving a candidate goes through the
// enrolment endpoint, which records who moved them and why; a shortcut on a
// read surface is how that ledger acquires gaps.

import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getApplicant } from '@/api/applicants';
import { listAnswers, type ApplicationAnswer } from '@/api/questions';
import { listRoundResults, type RoundResult } from '@/api/applicants';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { AlertTriangle, Info, User, X } from '@/design/components/icons';
import { cn } from '@/lib/utils';

export interface DrawerCandidate {
  applicant_id: string;
  full_name: string;
  email?: string | null;
  target_job_title?: string;
  status?: string;
  ats_overall?: number | null;
  ats_recommendation?: string | null;
  ats_strengths?: string[] | null;
  ats_concerns?: string[] | null;
  ats_summary?: string | null;
  /** Present only when the CV disagrees with the name on file. */
  parsed_full_name?: string | null;
  full_name_source?: string | null;
  phone?: string | null;
  years_experience?: number | null;
  current_company?: string | null;
  current_title?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  current_round_title?: string | null;
}

/* ── Why a score is what it is (§7, C8) ─────────────────────────────────── */

/** Colour by band, but never colour ALONE — the number is always present. */
function scoreTone(pct: number | null): string {
  if (pct === null) return 'text-[#888b91]';
  if (pct >= 75) return 'text-[#5ec27a]';
  if (pct >= 50) return 'text-[#ffb764]';
  return 'text-[#ff8f8f]';
}

/**
 * Per-round scores, criterion by criterion.
 *
 * The specification's §7 is explicit that a score must not be presented as an
 * unexplained truth, and the data to explain it has existed since C8 — per
 * criterion, each with the evidence the model cited. Nothing read it back, so
 * the console showed a composite and no way to ask what produced it.
 *
 * Two layers, shown in that order and labelled differently on purpose. The
 * criteria decided whether this candidate advanced; the axes are the frozen
 * comparison that makes composites mean the same thing across roles. Reading
 * the axes as the reason for a decision would be wrong, so they are visually
 * secondary.
 *
 * `passed` is rendered as "advanced / held", never as pass/fail: under D-05 a
 * candidate below a threshold is held and nothing here has ended their
 * candidacy.
 */
function RoundScores({ applicantId }: { applicantId: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['hr', 'applicant', applicantId, 'round-results'],
    queryFn: () => listRoundResults(applicantId),
  });

  if (isLoading) {
    return (
      <div className="mt-5">
        <h3 className="text-[13px] font-medium text-white">Assessment</h3>
        <div className="mt-2 h-16 animate-pulse rounded-[10px] bg-white/[0.04]" />
      </div>
    );
  }
  // A failed read must not imply "not assessed" — those are different facts and
  // one of them would mislead a decision.
  if (isError) {
    return (
      <div className="mt-5">
        <h3 className="text-[13px] font-medium text-white">Assessment</h3>
        <p className="mt-1.5 text-[12.5px] text-[#888b91]">
          Scores could not be loaded. Reopen to retry.
        </p>
      </div>
    );
  }
  if (!data?.length) return null;

  return (
    <div className="mt-5">
      <h3 className="text-[13px] font-medium text-white">Assessment</h3>
      <ul className="mt-2 flex flex-col gap-3">
        {data.map((r: RoundResult) => (
          <li key={r.round_id} className="rounded-[10px] border border-white/[0.08] p-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-[12.5px] text-white">
                {r.position + 1}. {r.round_title}
              </span>
              <span className={cn('text-[15px] font-semibold', scoreTone(r.percent))}>
                {r.percent === null ? '—' : `${Math.round(r.percent)}%`}
              </span>
            </div>

            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-[#70757c]">
              <span>
                {r.graded_by === 'ai'
                  ? 'AI-graded'
                  : r.graded_by === 'human'
                    ? 'Reviewed by a person'
                    : 'Auto-graded'}
              </span>
              {r.passed !== null ? (
                <span className={r.passed ? 'text-[#5ec27a]' : 'text-[#ffb764]'}>
                  · {r.passed ? 'advanced' : 'held for your decision'}
                </span>
              ) : null}
            </div>

            {r.criteria.length ? (
              <ul className="mt-2.5 flex flex-col gap-1.5">
                {r.criteria.map((c) => (
                  <li key={c.competency_id}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 truncate text-[12px] text-[#d5d7da]">
                        {c.name}
                      </span>
                      <span className={cn('text-[12px]', scoreTone(c.score))}>
                        {c.score === null ? '—' : Math.round(c.score)}
                      </span>
                    </div>
                    {c.evidence ? (
                      <p className="mt-0.5 text-[11.5px] leading-relaxed text-[#70757c]">
                        {c.evidence}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}

            {Object.keys(r.axes).length ? (
              <div className="mt-2.5 border-t border-white/[0.06] pt-2">
                <p className="text-[11px] uppercase tracking-wide text-[#5a5f66]">
                  Comparable axes
                </p>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  {Object.entries(r.axes).map(([axis, v]) => (
                    <span key={axis} className="text-[11.5px] text-[#888b91]">
                      {axis} <span className="text-[#d5d7da]">{Math.round(v)}</span>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

const STATUS_TONE: Record<string, TagTone> = {
  new: 'neutral',
  shortlisted: 'electric',
  held: 'amber',
  interviewed: 'lavender',
  hired: 'forest',
  rejected: 'ember',
};

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-white/[0.06] py-1.5 last:border-b-0">
      <span className="text-[12px] text-[#888b91]">{label}</span>
      <span className="text-right text-[13px] text-[#d5d7da]">{value}</span>
    </div>
  );
}

function answerText(a: ApplicationAnswer): string {
  if (Array.isArray(a.answer)) return a.answer.join(', ');
  if (typeof a.answer === 'boolean') return a.answer ? 'Yes' : 'No';
  return String(a.answer);
}

export default function CandidateDrawer({
  applicantId,
  enrolmentId,
  onClose,
}: {
  /** Null closes the drawer. */
  applicantId: string | null;
  /**
   * Needed to load their answers, and absent on surfaces that only know the
   * person — the applicant board, for one. Without it the answers section is
   * not shown rather than shown empty, because "no answers" and "we cannot
   * look" are different statements.
   */
  enrolmentId?: string | null;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  const detail = useQuery({
    queryKey: ['applicant', applicantId],
    queryFn: () => getApplicant(applicantId as string),
    enabled: Boolean(applicantId),
    retry: false,
    throwOnError: false,
  });
  const candidate = detail.data as DrawerCandidate | undefined;

  // Escape closes it, and focus lands somewhere inside rather than staying on
  // whatever row opened it — a panel a keyboard user cannot leave is worse than
  // no panel.
  useEffect(() => {
    if (!applicantId) return undefined;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [applicantId, onClose]);

  const answers = useQuery({
    queryKey: ['answers', enrolmentId],
    queryFn: () => listAnswers(enrolmentId as string),
    enabled: Boolean(enrolmentId),
    retry: false,
    throwOnError: false,
  });

  if (!applicantId) return null;

  const nameDiffers =
    Boolean(candidate?.parsed_full_name) &&
    candidate?.parsed_full_name !== candidate?.full_name;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close candidate details"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-name"
        className="relative flex h-full w-full max-w-[440px] flex-col overflow-y-auto border-l border-white/[0.08] bg-[#0f0f10] p-6"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id="drawer-name" className="text-[20px] font-semibold text-white">
              {candidate?.full_name ?? 'Loading…'}
            </h2>
            <p className="mt-0.5 text-[13px] text-[#888b91]">
              {[candidate?.target_job_title, candidate?.email].filter(Boolean).join(' · ')}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-[8px] p-1.5 text-[#888b91] hover:text-white focus:outline-none focus-visible:text-white"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {detail.isError ? (
          <p className="mt-4 text-[13px] text-[#888b91]">
            Could not load this candidate. Close and try again.
          </p>
        ) : null}

        {candidate ? (
          <>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {candidate.status ? (
            <StatusTag tone={STATUS_TONE[candidate.status] ?? 'neutral'} dot>
              {candidate.status}
            </StatusTag>
          ) : null}
          {candidate.current_round_title ? (
            <span className="text-[12px] text-[#888b91]">
              on {candidate.current_round_title}
            </span>
          ) : null}
        </div>

        {/* The discrepancy nobody else can see. Stated plainly rather than as a
            warning: neither name is wrong, they simply disagree, and only a
            person can decide which the candidate meant. */}
        {nameDiffers ? (
          <p className="mt-4 flex items-start gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] p-3 text-[12px] leading-relaxed text-[#b8babf]">
            <Info size={13} className="mt-0.5 shrink-0 text-[#60a5fa]" aria-hidden="true" />
            <span>
              Their CV reads{' '}
              <span className="text-white">{candidate.parsed_full_name}</span>. The name
              above is what
              {candidate.full_name_source === 'candidate' ? ' they typed' : ' is on file'}.
            </span>
          </p>
        ) : null}

        {candidate.ats_overall != null ? (
          <div className="mt-5">
            <div className="flex items-baseline justify-between">
              <h3 className="text-[13px] font-medium text-white">Resume match</h3>
              <span className="text-[20px] font-semibold text-white">
                {candidate.ats_overall}
                <span className="text-[13px] text-[#70757c]">/100</span>
              </span>
            </div>
            {candidate.ats_summary ? (
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-[#888b91]">
                {candidate.ats_summary}
              </p>
            ) : null}
            {candidate.ats_strengths?.length ? (
              <ul className="mt-2 flex flex-col gap-1">
                {candidate.ats_strengths.map((s) => (
                  <li key={s} className="text-[12.5px] text-[#d5d7da]">
                    ✓ {s}
                  </li>
                ))}
              </ul>
            ) : null}
            {candidate.ats_concerns?.length ? (
              <ul className="mt-1.5 flex flex-col gap-1">
                {candidate.ats_concerns.map((c) => (
                  <li
                    key={c}
                    className="flex items-start gap-1.5 text-[12.5px] text-[#b8babf]"
                  >
                    <AlertTriangle
                      size={12}
                      className="mt-0.5 shrink-0 text-[#ffb764]"
                      aria-hidden="true"
                    />
                    {c}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <RoundScores applicantId={candidate.applicant_id} />

        <div className="mt-5">
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[13px] font-medium text-white">
            <User size={13} aria-hidden="true" />
            Details
          </h3>
          <Row label="Phone" value={candidate.phone} />
          <Row
            label="Experience"
            value={
              candidate.years_experience != null
                ? `${candidate.years_experience} years`
                : null
            }
          />
          <Row label="Current company" value={candidate.current_company} />
          <Row label="Current role" value={candidate.current_title} />
          <Row label="LinkedIn" value={candidate.linkedin_url} />
          <Row label="GitHub" value={candidate.github_url} />
        </div>

        {/* Otherwise write-only: candidates fill these in and nobody reads them. */}
        {enrolmentId ? (
          <div className="mt-5">
            <h3 className="mb-2 text-[13px] font-medium text-white">
              Application answers
            </h3>
            {answers.isLoading ? (
              <p className="text-[12.5px] text-[#888b91]">Loading…</p>
            ) : null}
            {!answers.isLoading && (answers.data?.length ?? 0) === 0 ? (
              <p className="text-[12.5px] text-[#888b91]">
                This opening did not ask any questions.
              </p>
            ) : null}
            <div className="flex flex-col gap-3">
              {answers.data?.map((a) => (
                <div key={a.question_id}>
                  <p
                    className={cn(
                      'text-[12px]',
                      a.retired ? 'text-[#70757c]' : 'text-[#888b91]',
                    )}
                  >
                    {a.prompt}
                    {/* An answer to a question no longer asked still counts —
                        hiding it would leave a decision partly based on
                        something nobody can see. */}
                    {a.retired ? (
                      <span className="ml-1.5 text-[11px] italic">no longer asked</span>
                    ) : null}
                  </p>
                  <p className="mt-0.5 whitespace-pre-wrap text-[13px] text-[#d5d7da]">
                    {answerText(a)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
          </>
        ) : null}
      </aside>
    </div>
  );
}
