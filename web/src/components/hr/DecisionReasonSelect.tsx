// DecisionReasonSelect — O4. The one hire/reject reason dropdown, shared by
// DecisionQueue, HRPipeline and the Applicants reject flow rather than a
// third copy. The filtering-by-outcome and length-validation logic it needs
// lives in lib/decisionReasons.ts (import that directly) — kept out of this
// file so react-refresh/only-export-components (this repo lints with
// --max-warnings 0) has only a component to see here.

import type { DecisionReason } from '@/api/scorecards';
import { FormField } from '@/components/ui/form-field';
import { SelectControl } from '@/components/ui/select-control';

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
    <FormField id={id} label={label}>
      {(p) => (
        <SelectControl {...p} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose a reason…</option>
          {reasons.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </SelectControl>
      )}
    </FormField>
  );
}
