// ApplicationActionRow — one thing the candidate can do for one application,
// with its deadline and its button. Replaces the three near-identical call-to-
// action blocks the application card used to carry; the press behaviour (fetch
// the link on click, never on render, ask before closing another open tab) lives
// in useCandidateActionControl and is shared with the dashboard.

import { useTranslation } from 'react-i18next';
import { ActionRow } from '@/design/components/next-step';
import type { CandidateAction } from '@/lib/candidateActions';
import { actionDateLine, actionIcon, rowTitle } from './actionText';
import { useCandidateActionControl } from './useCandidateActionControl';

export default function ApplicationActionRow({ action }: { action: CandidateAction }): JSX.Element {
  const { t } = useTranslation();
  const { button, error, confirming } = useCandidateActionControl(action, {
    onApplicationsPage: true,
  });
  const isExam = action.kind === 'exam' || action.kind === 'exam_resume';

  return (
    <ActionRow
      data-testid={isExam ? 'exam-cta' : `action-${action.kind}`}
      icon={actionIcon(action)}
      title={rowTitle(t, action)}
      dateLine={confirming ?? actionDateLine(t, action)}
      action={button}
      error={error}
    />
  );
}
