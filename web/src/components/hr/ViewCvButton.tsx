// ViewCvButton — open the candidate's CV beside their score.
//
// HR-only surfaces (the applicant modal, the candidate drawer). The CV opens in
// a new tab from an authenticated fetch (api/applicants.openApplicantCv); every
// view is audited server-side. English-only by design (staff console).

import { useState } from 'react';
import { FileText, Loader2 } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { openApplicantCv } from '@/api/applicants';
import { toast } from '@/lib/toast';

export default function ViewCvButton({
  applicantId,
  enrolmentId,
  label = 'View CV',
  ariaLabel,
}: {
  applicantId: string;
  /** The application whose CV to show — the one its score was computed from. */
  enrolmentId?: string | null;
  label?: string;
  ariaLabel?: string;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={busy}
      aria-label={ariaLabel ?? label}
      onClick={() => {
        setBusy(true);
        openApplicantCv(applicantId, enrolmentId)
          .catch((e: unknown) =>
            toast.error(e instanceof Error && e.message ? e.message : 'The CV could not be opened.'),
          )
          .finally(() => setBusy(false));
      }}
    >
      {busy ? (
        <Loader2 className="animate-spin" aria-hidden="true" />
      ) : (
        <FileText aria-hidden="true" />
      )}
      {label}
    </Button>
  );
}
