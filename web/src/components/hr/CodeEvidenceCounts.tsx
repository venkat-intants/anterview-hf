// CodeEvidenceCounts -- PH4-D3. The candidate drawer's line saying how much
// code evidence an application has, with the way to it. Counts only: the
// evidence itself -- source, similarity, findings -- lives on the exam
// attempt's "Code evidence" tab, where every read of candidate code is
// audited. Renders nothing when there is none, so most drawers are unchanged.

import { useQuery } from '@tanstack/react-query';
import { getCodeEvidenceSummary } from '@/api/codeEvidence';
import { codeEvidenceLabel } from '@/lib/codeEvidenceLabel';

export default function CodeEvidenceCounts({ enrolmentId }: { enrolmentId: string }) {
  const summary = useQuery({
    queryKey: ['hr', 'code-evidence-summary', enrolmentId],
    queryFn: () => getCodeEvidenceSummary(enrolmentId),
  });
  const c = summary.data;
  if (!c || (c.signal_count === 0 && c.finding_count === 0)) return null;
  return (
    <section aria-label="Code evidence" className="rounded-[12px] border border-border p-3">
      <h3 className="text-[12.5px] font-semibold text-foreground">Code evidence</h3>
      <p className="mt-1 text-[12.5px] text-muted-foreground">{codeEvidenceLabel(c)}</p>
      <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
        Open the exam attempt to review it. Similar code is not evidence of misconduct on its own.
      </p>
    </section>
  );
}
