// PH4-D3 -- the one sentence both the decision queue and the candidate drawer
// use for an application's code evidence. Counts only. A similarity SIGNAL is
// automated and unreviewed; a FINDING is a person's recorded judgement -- the
// wording keeps them apart, because the product's whole position is that a
// signal is not evidence of misconduct on its own.

export function codeEvidenceLabel(c: { signal_count: number; finding_count: number }): string {
  const parts: string[] = [];
  if (c.signal_count > 0) {
    parts.push(`${c.signal_count} similarity signal${c.signal_count === 1 ? '' : 's'} (unreviewed)`);
  }
  if (c.finding_count > 0) {
    parts.push(`${c.finding_count} integrity finding${c.finding_count === 1 ? '' : 's'} recorded`);
  }
  return parts.join(' · ');
}
