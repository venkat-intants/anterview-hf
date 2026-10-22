// ConsentDisclosure: what the sign-up consent box is agreeing to, in full,
// next to the box.
//
// The box used to say "I agree to the Terms and the DPDP-compliant Privacy
// Policy" and link to nothing: no such pages exist (audit CA-M15). A consent
// is only meaningful if the person can read what it covers, so the box now
// names the processing itself and this note carries the same cross-border
// disclosure the interview consent modal shows, plus where to write.

import { useTranslation } from 'react-i18next';
import { SUPPORT_EMAIL } from '@/lib/contact';

export function ConsentDisclosure({ id }: { id: string }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div id={id} className="mt-2 space-y-1 text-[12.5px] leading-relaxed text-muted-foreground">
      <p>{t('consent.dataFlowNote')}</p>
      <p>{t('auth.consentContact', { supportEmail: SUPPORT_EMAIL })}</p>
    </div>
  );
}
