// Words and icons for a queue item (see lib/candidateActions.ts). Kept apart
// from the pure queue so that file stays free of i18n and React.

import type { TFunction } from 'i18next';
import { formatDate } from '@/lib/formatters';
import type { CandidateAction } from '@/lib/candidateActions';
import {
  Briefcase,
  CalendarDays,
  ClipboardCheck,
  FileCheck2,
  Video,
  type LucideIcon,
} from '@/design/components/icons';

/** "Data Analyst · Northwind", skipping whichever half is unknown. */
export function actionContext(action: CandidateAction): string | null {
  const parts = [action.role, action.company].filter((p): p is string => Boolean(p));
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** The headline for the dashboard strip. */
export function actionTitle(t: TFunction, action: CandidateAction): string {
  return t(action.titleKey, action.titleValues);
}

/**
 * The headline for a row ON an application card. Where the card already names
 * the state ("Your assessment is ready") it keeps those words, which is what the
 * card said before the queue existed.
 */
export function rowTitle(t: TFunction, action: CandidateAction): string {
  switch (action.kind) {
    case 'exam_resume':
      return t('applications.examInProgress');
    case 'exam':
      return t('applications.examReady');
    case 'interview':
      return t('applications.interviewReady');
    case 'task':
      return t('applications.taskReady');
    default:
      return actionTitle(t, action);
  }
}

/** The one line under the headline: a deadline, a start time, or what to expect. */
export function actionDateLine(t: TFunction, action: CandidateAction): string | null {
  const date = action.date ? formatDate(action.date) : null;
  switch (action.kind) {
    case 'exam_resume':
      return t('applications.examResumeHint');
    case 'exam':
      if (date && action.dateKind === 'exam_opens') return t('applications.examOpensOn', { date });
      if (date && action.dateKind === 'exam_before')
        return t('applications.examTakeBefore', { date });
      return t('applications.examAnytime');
    case 'interview':
      return date ? t('applications.scheduledFor', { date }) : t('applications.interviewAnytime');
    case 'task':
      return date ? t('applications.due', { date }) : null;
    case 'offer':
      return date ? t('myOffers.expires', { date }) : null;
    default:
      return null;
  }
}

export function actionIcon(action: CandidateAction): LucideIcon {
  switch (action.kind) {
    case 'exam_resume':
    case 'exam':
      return ClipboardCheck;
    case 'interview':
      return Video;
    case 'task':
      return Briefcase;
    case 'choose_time':
      return CalendarDays;
    default:
      return FileCheck2;
  }
}
