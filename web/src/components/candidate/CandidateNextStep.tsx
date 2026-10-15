// CandidateNextStep — the dashboard's one "do this next" strip, driven by the
// first item of the candidate's action queue.
//
// It never mints a link on render. The button it shows is the same
// mint-on-click control the application cards use (useCandidateActionControl),
// including the 409 "open it here?" question for an assessment open elsewhere.

import { useTranslation } from 'react-i18next';
import { NextStep } from '@/design/components/next-step';
import { isWaitingOnYou, type CandidateAction } from '@/lib/candidateActions';
import { actionContext, actionDateLine, actionTitle } from './actionText';
import { useCandidateActionControl } from './useCandidateActionControl';

interface Props {
  action: CandidateAction;
  /** How many items in the whole queue are waiting on the candidate (this one included). */
  waitingCount: number;
}

export default function CandidateNextStep({ action, waitingCount }: Props): JSX.Element {
  const { t } = useTranslation();
  const { button, error, confirming } = useCandidateActionControl(action);

  const practice = action.kind.startsWith('practice');
  const context =
    action.kind === 'with_team'
      ? t('nextStep.withTeamContext')
      : practice
        ? t('nextStep.practiceContext')
        : actionContext(action);

  const dateLine = error ? (
    <span role="alert" className="font-medium text-[var(--ui-danger)]">
      {error}
    </span>
  ) : (
    (confirming ?? actionDateLine(t, action))
  );

  const more = isWaitingOnYou(action) ? Math.max(0, waitingCount - 1) : 0;

  return (
    <NextStep
      data-testid="next-step"
      eyebrow={t('nextStep.eyebrow')}
      title={actionTitle(t, action)}
      context={context}
      dateLine={dateLine}
      action={button}
      moreCount={more}
      moreTo="/applications"
      moreLabel={t('nextStep.more', { count: more })}
    />
  );
}
