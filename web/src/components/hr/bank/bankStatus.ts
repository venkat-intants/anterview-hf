// The words and tones for a bank question's status, in one place so the drafts
// list, the detail pane and the stage panels never say them differently.

import type { TagTone } from '@/design/components/primitives';
import type { BankQuestionStatus } from '@/api/questionBanks';

export const STATUS_TONE: Record<BankQuestionStatus, TagTone> = {
  draft: 'neutral',
  in_review: 'amber',
  approved: 'forest',
  retired: 'ember',
};

export const STATUS_WORD: Record<BankQuestionStatus, string> = {
  draft: 'Draft',
  in_review: 'In review',
  approved: 'Approved',
  retired: 'Retired',
};
