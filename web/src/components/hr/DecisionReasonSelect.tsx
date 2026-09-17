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
      <label htmlFor={id} className="block text-[12px] text-[var(--ui-soft)]">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
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
