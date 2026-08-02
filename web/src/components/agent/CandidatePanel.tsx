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

const SEVERITY_STYLE: Record<Contradiction['severity'], { bg: string; fg: string; label: string }> =
  {
    high: { bg: 'rgba(255,107,107,0.12)', fg: '#ff6b6b', label: 'Worth checking' },
    medium: { bg: 'rgba(255,183,100,0.12)', fg: '#ffb764', label: 'Notable' },
    low: { bg: 'rgba(255,255,255,0.06)', fg: 'inherit', label: 'Minor' },
  };

function SignalRow({ signal }: { signal: SignalAssessment }): JSX.Element {
  if (!signal.available) {
    return (
      <div className="flex items-center justify-between py-2 opacity-50">
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
            {signal.score_0_100 === null ? '—' : `${Math.round(signal.score_0_100)}/100`}
          </span>
          {/* Confidence is shown per signal because a 3-question exam and a
              10-question interview do not deserve equal weight, and the
              numbers alone hide that. */}
          <span className="text-xs opacity-50" title="How much weight this evidence deserves">
            conf {signal.confidence.toFixed(2)}
          </span>
        </div>
      </div>
      {signal.strengths.length > 0 && (
        <ul className="text-xs opacity-70 list-disc pl-4">
          {signal.strengths.slice(0, 2).map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      )}
      {signal.concerns.length > 0 && (
        <ul className="text-xs opacity-70 list-disc pl-4" style={{ color: '#ffb764' }}>
          {signal.concerns.slice(0, 2).map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
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
      setError(err instanceof Error ? err.message : 'Assessment failed');
    } finally {
      setLoading(false);
    }
  }

  if (!verdict) {
    return (
      <GlassCard className="p-4 space-y-2">
        <h3 className="font-semibold text-[15px]">Cross-signal assessment</h3>
        <p className="text-sm opacity-70">
          Reads {applicantName ? `${applicantName}'s` : 'this candidate’s'} resume, exam, coding
          test and interview independently, then reports where they disagree.
        </p>
        {error && (
          <p className="text-sm" style={{ color: '#ff6b6b' }} role="alert">
            {error}
          </p>
        )}
        <button
          type="button"
          onClick={() => void run()}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50 transition"
          style={{ background: 'var(--accent)', color: '#08131f' }}
        >
          {loading ? 'Assessing…' : 'Run assessment'}
        </button>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-[15px]">Cross-signal assessment</h3>
          <p className="text-xs opacity-60 mt-0.5">
            Overall confidence {verdict.confidence.toFixed(2)} · four independent reads
          </p>
        </div>
        {/* Stated on the card, not just in a doc: this is advice, not a decision. */}
        <StatusTag tone="neutral">Advisory only</StatusTag>
      </div>

      {verdict.contradictions.length > 0 && (
        <div className="space-y-2">
          {verdict.contradictions.map((c, i) => {
            const style = SEVERITY_STYLE[c.severity];
            return (
              <div
                key={i}
                className="rounded-lg px-3 py-2 text-sm space-y-1"
                style={{ background: style.bg, color: style.fg }}
              >
                <div className="font-medium">{style.label}</div>
                <p>{c.detail}</p>
                <p className="opacity-80 text-xs">→ {c.suggested_check}</p>
              </div>
            );
          })}
        </div>
      )}

      {verdict.summary && <p className="text-sm leading-relaxed">{verdict.summary}</p>}

      <div className="divide-y divide-white/5">
        {verdict.signals.map((s) => (
          <SignalRow key={s.signal} signal={s} />
        ))}
      </div>

      {verdict.coverage_gaps.length > 0 && (
        <div className="text-xs opacity-60 space-y-1">
          <div className="font-medium opacity-80">Not covered</div>
          <ul className="list-disc pl-4">
            {verdict.coverage_gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}

      {verdict.suggested_next_step && (
        <div className="text-sm border-l-2 border-[var(--accent)] pl-3">
          <span className="opacity-60">Suggested next step: </span>
          {verdict.suggested_next_step}
        </div>
      )}

      <p className="text-xs opacity-50">
        This is evidence, not a decision. Hiring outcomes are yours to make.
      </p>
    </GlassCard>
  );
}
