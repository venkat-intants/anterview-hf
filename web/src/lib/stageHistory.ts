// One transition-ledger entry in words (B2): what moved, and who moved it.
//
// Status changes read "shortlisted → held"; round moves read "Aptitude →
// Interview" (the status does not change between rounds, which is exactly why
// the ledger used to miss them). A system move says "automatic"; a person's
// names them, so the drawer answers "did somebody decide this?" at a glance.

import type { StageHistoryEntry } from '@/api/requisitions';

export function describeMove(h: StageHistoryEntry): string {
  let what: string;
  if (h.from_status === null) {
    what = `Applied — ${h.to_status}`;
  } else if (h.from_status !== h.to_status) {
    what = `${h.from_status} → ${h.to_status}`;
  } else if (h.to_round) {
    what = h.from_round ? `${h.from_round} → ${h.to_round}` : `Started ${h.to_round}`;
  } else if (h.from_round) {
    what = `Finished ${h.from_round}`;
  } else {
    what = h.to_status;
  }
  return `${what} · ${h.automated ? 'automatic' : `by ${h.actor ?? 'a reviewer'}`}`;
}
