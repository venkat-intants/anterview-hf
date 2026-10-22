// CandidatePanel — the specialist panel's read of one candidate.
//
// Renders four independent signal assessments, and leads with where they
// DISAGREE. The disagreement is the whole reason this view exists: a candidate
// scoring 91 on a coding test and 52 on the technical interview is the single
// most important case for a human to look at, and no other screen in the app
// puts those two numbers next to each other.
//
// Two things this component must never do:
//   1. Render a hire/reject verdict. The API has no field for one
//      (decision_authority is the literal 'human_only'); the UI must not
//      invent one by, say, colouring a high average green and calling it a
//      recommendation.
//   2. Show an absent round as a zero. "Has not sat the exam" and "failed the
//      exam" are different facts about a person.

import { useState } from 'react';
import {
  assessCandidate,
  SIGNAL_LABEL,
  type Contradiction,
  type PanelVerdict,
  type SignalAssessment,
} from '@/api/agent';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/errorMessage';
import CitationChips from './CitationChips';
import EvidenceBanner from './EvidenceBanner';

const SEVERITY_STYLE: Record<Contradiction['severity'], { cls: string; label: string }> = {
  high: { cls: 'bg-[var(--ui-danger-wash)] text-[var(--ui-danger)]', label: 'Worth checking' },
  medium: { cls: 'bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]', label: 'Notable' },
  low: { cls: 'bg-[var(--ui-inset)] text-foreground', label: 'Minor' },
};

function SignalRow({ signal }: { signal: SignalAssessment }): JSX.Element {
  if (!signal.available) {
    return (
      <div className="flex items-center justify-between py-2 text-muted-foreground">
        <span className="text-sm">{SIGNAL_LABEL[signal.signal]}</span>
        {/* Explicitly "not taken", never a 0. */}
        <span className="text-sm">Not taken yet</span>
      </div>
    );
  }

  return (
    <div className="py-2 space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{SIGNAL_LABEL[signal.signal]}</span>
        <div className="flex items-center gap-2">
          <span className="text-sm tabular-nums">
            {signal.score_0_100 === null ? '-' : `${Math.round(signal.score_0_100)}/100`}
          </span>
          {/* Confidence is shown per signal because a 3-question exam and a
              10-question interview do not deserve equal weight, and the
              numbers alone hide that. */}
          <span
            className="text-xs text-muted-foreground"
            title="How much weight this evidence deserves"
          >
            conf {signal.confidence.toFixed(2)}
          </span>
        </div>
      </div>
      {signal.strengths.length > 0 && (
        <ul className="list-disc pl-4 text-xs text-muted-foreground">
          {signal.strengths.slice(0, 2).map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      )}
      {signal.concerns.length > 0 && (
        <ul className="list-disc pl-4 text-xs text-[var(--ui-warn)]">
          {signal.concerns.slice(0, 2).map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
      {/* PH5-E1 criteria 4/5's banner. Omitted for an unavailable signal on
          purpose — there is no narrative at all there ("Not taken yet" above
          is the whole row), so a banner about text that does not exist would
          be noise rather than a caution. */}
      {signal.available && <EvidenceBanner citationState={signal.citation_state} />}
      <CitationChips citations={signal.citations} variant="strip" />
    </div>
  );
}

interface CandidatePanelProps {
  applicantId: string;
  applicantName?: string;
}

export default function CandidatePanel({
  applicantId,
  applicantName,
}: CandidatePanelProps): JSX.Element {
  const [verdict, setVerdict] = useState<PanelVerdict | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Explicitly user-triggered, not a useQuery on mount: the panel runs four
  // LLM calls, so firing it for every applicant a recruiter merely scrolls
  // past would be a real and pointless cost.
  async function run(): Promise<void> {
    setLoading(true);
    setError('');
    try {
      setVerdict(await assessCandidate(applicantId));
    } catch (err) {
      // A failed re-run keeps the previous read on screen, with the reason
      // above it, rather than throwing away evidence the user already has.
      setError(errorMessage(err, 'Could not run the assessment.'));
    } finally {
      setLoading(false);
    }
  }

  const errorLine = error ? (
    <p className="text-sm font-medium text-[var(--ui-danger)]" role="alert">
      {error}
    </p>
  ) : null;

  if (!verdict) {
    return (
      <GlassCard className="space-y-2 p-5">
        <h3 className="text-[17px] font-bold">Cross-signal assessment</h3>
        <p className="text-sm text-muted-foreground">
          Reads {applicantName ? `${applicantName}'s` : 'this candidate’s'} resume, exam, coding
          test and interview independently, then reports where they disagree.
        </p>
        {errorLine}
        <Button type="button" onClick={() => void run()} disabled={loading} aria-busy={loading}>
          {loading ? 'Assessing…' : 'Run assessment'}
        </Button>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="space-y-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[17px] font-bold">Cross-signal assessment</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Overall confidence {verdict.confidence.toFixed(2)} · four independent reads
          </p>
        </div>
        <div className="flex flex-none flex-wrap items-center justify-end gap-2">
          {/* Stated on the card, not just in a doc: this is advice, not a decision. */}
          <StatusTag tone="neutral">Advisory only</StatusTag>
          {/* New evidence (an exam taken, an interview scored) arrives after a
              read, and without this the only way to see it was a page reload
              (audit CP-L9). Still an explicit click: each run is several
              model calls. */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void run()}
            disabled={loading}
            aria-busy={loading}
          >
            {loading ? 'Assessing…' : 'Run again'}
          </Button>
        </div>
      </div>

      {errorLine}

      {verdict.contradictions.length > 0 && (
        <div className="space-y-2">
          {verdict.contradictions.map((c, i) => {
            const style = SEVERITY_STYLE[c.severity];
            return (
              <div
                key={i}
                className={`space-y-1 rounded-[12px] px-3.5 py-2.5 text-sm ${style.cls}`}
              >
                <div className="font-medium">{style.label}</div>
                <p>{c.detail}</p>
                <p className="text-xs">→ {c.suggested_check}</p>
              </div>
            );
          })}
        </div>
      )}

      {verdict.summary && <p className="text-sm leading-relaxed">{verdict.summary}</p>}

      <div className="divide-y divide-border">
        {verdict.signals.map((s) => (
          <SignalRow key={s.signal} signal={s} />
        ))}
      </div>

      {verdict.coverage_gaps.length > 0 && (
        <div className="space-y-1 text-xs text-muted-foreground">
          <div className="font-semibold text-foreground">Not covered</div>
          <ul className="list-disc pl-4">
            {verdict.coverage_gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}

      {verdict.suggested_next_step && (
        <div className="text-sm border-l-2 border-[var(--accent)] pl-3">
          <span className="text-muted-foreground">Suggested next step: </span>
          {verdict.suggested_next_step}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        This is evidence, not a decision. Hiring outcomes are yours to make.
      </p>
    </GlassCard>
  );
}
