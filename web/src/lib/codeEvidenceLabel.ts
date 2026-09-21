// PH4-D3 -- the one sentence both the decision queue and the candidate drawer
// use for an application's code evidence. Counts only. A similarity SIGNAL is
// automated; it is "awaiting review" only until a person records a finding on
// it. A FINDING is a person's recorded judgement, and is named by its outcome
// -- so a candidate HR reviewed and cleared never reads as flagged. (An
// earlier version called every signal "unreviewed" and every finding
// "recorded", whatever it said: security review, D3 M3.)

import type { CodeEvidenceSummary } from '@/api/codeEvidence';

function n(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** True when there is anything worth a line: an unreviewed signal, or any
 *  live finding. A reviewed signal always has a finding, so it is covered. */
export function hasCodeEvidence(c: CodeEvidenceSummary | null | undefined): boolean {
  return Boolean(c && (c.unreviewed_signal_count > 0 || c.finding_count > 0));
}

export function codeEvidenceLabel(c: CodeEvidenceSummary): string {
  const parts: string[] = [];
  if (c.unreviewed_signal_count > 0) {
    parts.push(
      `${n(c.unreviewed_signal_count, 'similarity signal', 'similarity signals')} awaiting review`,
    );
  }
  if (c.confirmed_count > 0) {
    parts.push(`${n(c.confirmed_count, 'integrity concern', 'integrity concerns')} confirmed`);
  }
  if (c.follow_up_count > 0) {
    parts.push(`${n(c.follow_up_count, 'finding', 'findings')} flagged for follow-up`);
  }
  if (c.no_concern_count > 0) {
    parts.push(`${c.no_concern_count} reviewed: no concern`);
  }
  return parts.join(' · ');
}
