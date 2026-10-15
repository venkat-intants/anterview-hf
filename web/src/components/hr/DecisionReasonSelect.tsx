// DecisionReasonSelect — O4. The one hire/reject reason dropdown, shared by
// DecisionQueue, HRPipeline and the Applicants reject flow rather than a
// third copy. The filtering-by-outcome and length-validation logic it needs
// lives in lib/decisionReasons.ts (import that directly) — kept out of this
// file so react-refresh/only-export-components (this repo lints with
// --max-warnings 0) has only a component to see here.

import type { DecisionReason } from '@/api/scorecards';

export function DecisionReasonSelect({
  id,
  reasons,
  value,
  onChange,
  label = 'Reason',
}: {
  id: string;
  reasons: DecisionReason[];
  value: string;
  onChange: (code: string) => void;
  label?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-[13px] font-semibold text-foreground">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 h-10 w-full rounded-[12px] border border-input bg-card px-3.5 text-[14px] text-foreground focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30"
      >
        <option value="">Choose a reason…</option>
        {reasons.map((r) => (
          <option key={r.code} value={r.code}>
            {r.label}
          </option>
        ))}
      </select>
    </div>
  );
}
