// ProctorIndicator — the candidate-visible "you are being watched, and here
// is what the system currently thinks" indicator (camera-proctoring contract
// §4). Not decoration: someone under camera proctoring must be able to see
// that it is on and what state it is in, or it is surveillance rather than
// proctoring.
//
// Deliberately never reports a state that could read as an accusation —
// "warning" copy mirrors the same wording the interview already shows
// (interview.warn_*), which is worded as a neutral nudge ("please look at
// the screen"), not a verdict. `gaze_away` in particular is the least
// reliable signal (see the camera-proctoring contract §2) and the most
// likely to fire on someone thinking, or using assistive technology — the
// indicator says what the system currently sees, nothing stronger.
//
// role="status" + aria-live="polite": screen-reader users get the same
// information sighted candidates get, without it interrupting anything.
//
// The state derivation itself lives in ./proctorIndicatorStatus (pure,
// unit-tested without a DOM) — this file is rendering only.

import { useTranslation } from 'react-i18next';
import { Loader2, AlertTriangle, ShieldCheck, VideoOff } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import {
  deriveProctorIndicatorStatus,
  type ProctorIndicatorSignals,
  type ProctorIndicatorStatus,
} from './proctorIndicatorStatus';

export type { ProctorIndicatorSignals, ProctorIndicatorStatus };

const TONE: Record<ProctorIndicatorStatus, string> = {
  unavailable: 'text-fog',
  starting: 'text-amber-glow',
  calibrating: 'text-amber-glow',
  warning: 'text-ember',
  ok: 'text-vivid-mint',
};

export default function ProctorIndicator(signals: ProctorIndicatorSignals) {
  const { t } = useTranslation();
  const status = deriveProctorIndicatorStatus(signals);

  const label =
    status === 'warning' && signals.cameraWarning
      ? t(`publicExam.cameraWarn_${signals.cameraWarning}`)
      : t(`publicExam.cameraIndicator_${status}`);

  const Icon =
    status === 'starting' || status === 'calibrating'
      ? Loader2
      : status === 'warning'
        ? AlertTriangle
        : status === 'unavailable'
          ? VideoOff
          : ShieldCheck;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="proctor-indicator"
      className={cn(
        'flex items-center gap-1.5 rounded-full border border-border bg-[var(--ui-inset-soft)] px-3 py-1.5 text-caption',
        TONE[status],
      )}
    >
      <Icon
        className={cn(
          'h-3.5 w-3.5 shrink-0',
          (status === 'starting' || status === 'calibrating') && 'animate-spin',
        )}
        aria-hidden="true"
      />
      <span>{label}</span>
    </div>
  );
}
