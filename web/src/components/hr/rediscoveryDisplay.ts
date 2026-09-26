// rediscoveryDisplay.ts — PH5-E3. Copy and tone lookups shared by the
// TalentPools and Rediscovery screens (and the WhyPanel both embed), kept in
// one place so a chip does not read differently on the two screens that show
// the same vocabulary.
//
// Every lookup has a fallback for a value this build does not (yet)
// recognise, on the `corpusFailureSentence` precedent (`api/corpus.ts`) —
// a server-sent string outside today's map must never render as nothing.

import type { TagTone } from '@/design/components/primitives';
import type { RediscoveryHeaderFreshness, RediscoverySignal } from '@/api/rediscovery';
import type { PoolIneligibleReason } from '@/api/pools';

export const FRESHNESS_LABEL: Record<RediscoveryHeaderFreshness, string> = {
  fresh: 'Fresh',
  ageing: 'Ageing',
  stale: 'Stale',
  unverifiable: "Can't verify",
  none: 'No evidence',
};

export const FRESHNESS_TONE: Record<RediscoveryHeaderFreshness, TagTone> = {
  fresh: 'forest',
  ageing: 'amber',
  stale: 'ember',
  unverifiable: 'ember',
  none: 'neutral',
};

/**
 * A per-item chip's label. When `freshness` is absent, or is
 * `unverifiable`, the server sends `freshness_reason` — an undated item, or
 * content that no longer exists — and the honest-degradation rule this
 * feature follows everywhere else applies here too: say why, never show a
 * bare gap or invent "unknown".
 */
export function freshnessChipLabel(
  freshness: RediscoveryHeaderFreshness | undefined,
  freshnessReason?: string,
): string {
  const label = freshness ? (FRESHNESS_LABEL[freshness] ?? freshness) : "Can't verify";
  return freshnessReason ? `${label} — ${freshnessReason}` : label;
}

export const SIGNAL_LABEL: Record<RediscoverySignal, string> = {
  resume_similarity: 'CV similarity',
  resume_terms: 'Matched terms',
  interviewer_scorecard: 'Interview scorecard',
  round_result: 'Round result',
  exam_attempt: 'Assessment',
  ai_interview: 'AI interview',
};

export function signalLabel(signal: string): string {
  return SIGNAL_LABEL[signal as RediscoverySignal] ?? signal;
}

export const INELIGIBLE_REASON_LABEL: Record<PoolIneligibleReason, string> = {
  consent_withdrawn: 'Consent withdrawn',
  consent_expired: 'Consent expired',
  erasure_requested: 'Erasure requested',
};

export function ineligibleReasonLabel(reason: string | undefined): string {
  if (!reason) return 'Not eligible';
  return INELIGIBLE_REASON_LABEL[reason as PoolIneligibleReason] ?? reason;
}

export function fmtDate(iso: string | undefined): string {
  return iso ? new Date(iso).toLocaleDateString() : '—';
}

export function fmtDateTime(iso: string | undefined): string {
  return iso ? new Date(iso).toLocaleString() : '—';
}
