// CandidatePreview — the workflow as the candidate lives it (D5).
//
// The canvas describes the process from HR's side: thresholds, criteria, gates.
// This is the other side: what arrives in their inbox, what they are asked to
// do, and what their application page says while they wait. Every line here is
// what the product actually does — the stage labels are the ones the candidate
// portal shows (candidate_applications._STAGE_LABELS), and the emails are the
// ones sent — so the preview cannot promise an experience that does not exist.
//
// Deliberately absent: scores and thresholds. Candidates never see them, and a
// preview that showed "advance at 60%" would misrepresent the thing it previews.

import type { Round } from '@/api/workflows';
import { ROUND_KIND_META } from './roundKinds';

function minutes(seconds: number | null): string | null {
  if (!seconds) return null;
  const m = Math.round(seconds / 60);
  return m >= 60 && m % 60 === 0 ? `${m / 60} hour${m === 60 ? '' : 's'}` : `${m} minutes`;
}

function whatHappens(round: Round): { receive: string; asked: string } {
  const limit = minutes(round.time_limit_seconds);
  switch (round.kind) {
    case 'mcq':
    case 'coding':
      return {
        receive: `An email with a private link to the ${
          round.kind === 'coding' ? 'coding test' : 'assessment'
        }, in their language.`,
        asked: `Complete it within ${round.deadline_days} day${
          round.deadline_days === 1 ? '' : 's'
        }${limit ? `, with ${limit} once they start` : ''}.`,
      };
    case 'ai_interview':
      return {
        receive: 'An email inviting them to a voice interview with an AI interviewer.',
        asked: `Start it any time before the link expires — within ${round.deadline_days} day${
          round.deadline_days === 1 ? '' : 's'
        }.`,
      };
    case 'job_simulation':
      return {
        receive:
          'An email with a private link to a brief and a set of written items, in their language where you gave a translation.',
        asked: `Complete it within ${round.deadline_days} day${
          round.deadline_days === 1 ? '' : 's'
        }${limit ? `, with ${limit} once they start` : ''}. They see the brief, never a score.`,
      };
    case 'portfolio':
      return {
        receive:
          'An email with a private link asking for files and approved links as evidence of their work.',
        asked: `Submit their portfolio within ${round.deadline_days} day${
          round.deadline_days === 1 ? '' : 's'
        }. They see the brief, never a score.`,
      };
    default:
      return {
        receive: 'Nothing — this round is a person on your team reviewing them.',
        asked: 'Nothing to do. They wait while you review.',
      };
  }
}

export default function CandidatePreview({
  title,
  rounds,
}: {
  title: string;
  rounds: Round[];
}): JSX.Element {
  const ordered = [...rounds].sort((a, b) => a.position - b.position);
  return (
    <section aria-label="What candidates experience" className="flex flex-col gap-3">
      <p className="text-[12.5px] leading-relaxed text-muted-foreground">
        What someone applying to {title} goes through. They never see scores or thresholds.
      </p>

      <ol className="flex flex-col gap-2">
        <li className="rounded-[12px] border border-border p-3">
          <p className="text-[13px] font-medium text-foreground">They apply</p>
          <p className="mt-1 text-[12px] text-muted-foreground">
            A confirmation email. Their application page reads “Application received”, and nothing
            more happens until you shortlist them.
          </p>
        </li>

        {ordered.map((round, i) => {
          const { receive, asked } = whatHappens(round);
          return (
            <li key={round.id} className="rounded-[12px] border border-border p-3">
              <p className="text-[13px] font-medium text-foreground">
                Round {i + 1} of {ordered.length} · {round.title}
                <span className="ml-2 text-[11.5px] font-normal text-[var(--ui-faint)]">
                  {ROUND_KIND_META[round.kind].label}
                </span>
              </p>
              <dl className="mt-1.5 grid gap-1 text-[12px]">
                <div>
                  <dt className="inline text-[var(--ui-faint)]">They receive: </dt>
                  <dd className="inline text-[var(--ui-soft)]">{receive}</dd>
                </div>
                <div>
                  <dt className="inline text-[var(--ui-faint)]">They are asked: </dt>
                  <dd className="inline text-[var(--ui-soft)]">{asked}</dd>
                </div>
                <div>
                  <dt className="inline text-[var(--ui-faint)]">Their page says: </dt>
                  <dd className="inline text-[var(--ui-soft)]">
                    “Round {i + 1} of {ordered.length} · {round.title}”
                  </dd>
                </div>
              </dl>
            </li>
          );
        })}

        <li className="rounded-[12px] border border-border p-3">
          <p className="text-[13px] font-medium text-foreground">If they fall short</p>
          <p className="mt-1 text-[12px] text-muted-foreground">
            Their page reads “Under review”. They are not told they failed, and nobody is rejected
            automatically — a person decides.
          </p>
        </li>

        <li className="rounded-[12px] border border-border p-3">
          <p className="text-[13px] font-medium text-foreground">Your decision</p>
          <p className="mt-1 text-[12px] text-muted-foreground">
            They hear from you only when a person decides: “Selected” or “Not progressing”, with an
            email saying so.
          </p>
        </li>
      </ol>
    </section>
  );
}
