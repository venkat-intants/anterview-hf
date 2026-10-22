// useCandidateActionControl — turns a queue item into the one button that does it.
//
// Shared by the dashboard's next-step strip and each application card's action
// row, so the two cannot drift apart in what a press does.
//
// THE LINK IS FETCHED ON CLICK, NEVER ON RENDER. Asking for an interview,
// assessment, task or offer link ROTATES its token: the emailed link, and any
// tab already open on it, stops working. That is the right trade for a
// deliberate press and the wrong one for a page view, which would silently
// break the email of everyone who merely looked at their dashboard.
//
// An assessment already open elsewhere answers 409. That is not a failure: the
// server is declining to close a tab the candidate may still be sitting a timed
// attempt in, and handing the decision back. The question replaces the date
// line, the button changes to "Open it here", and only a press that follows the
// question may close the other tab.
//
// Every URL the server returns is checked same-origin with the expected path
// before the tab navigates to it — a link carries a live bearer token in its
// fragment, and a server (or a misconfigured base URL) must not be able to send
// a signed-in candidate, token in hand, to another origin.

import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useCandidateError } from '@/lib/candidateCopy';
import { mintMyExamLink, mintMyInterviewLink, mintMyTaskLink } from '@/api/applications';
import { getMyOfferLink } from '@/api/offers';
import { ApiError } from '@/api/client';
import { sameOriginUrl } from '@/lib/safeUrl';
import type { CandidateAction } from '@/lib/candidateActions';
import { Button, buttonVariants } from '@/components/ui/button';

export interface CandidateActionControl {
  /** The button or link; null for an item with no action ("with the hiring team"). */
  button: ReactNode;
  /** A failure from pressing it, already worded. */
  error: string | null;
  /** The server's question when it will not close another open tab; replaces the date line. */
  confirming: string | null;
}

interface Options {
  /**
   * The page this control is on is /applications: an item that points at a place
   * on that page scrolls to it instead of navigating to the page it is already on.
   */
  onApplicationsPage?: boolean;
}

function scrollToAnchor(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.focus({ preventScroll: true });
}

export function useCandidateActionControl(
  action: CandidateAction,
  { onApplicationsPage = false }: Options = {},
): CandidateActionControl {
  const { t } = useTranslation();
  const errText = useCandidateError();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const { target } = action;

  const press = useMutation({
    // Resolves to the checked URL, or throws a worded Error.
    mutationFn: async (resumeAnyway: boolean): Promise<string> => {
      switch (target.type) {
        case 'mint_exam': {
          const link = await mintMyExamLink(target.assignmentId, resumeAnyway);
          const safe = sameOriginUrl(link.exam_url, '/exam');
          if (!safe) throw new Error(t('applications.couldNotOpenExam'));
          return safe;
        }
        case 'mint_interview': {
          const link = await mintMyInterviewLink(target.inviteId);
          const safe = sameOriginUrl(link.interview_url, '/interview-invite');
          if (!safe) throw new Error(t('applications.couldNotOpenInterview'));
          return safe;
        }
        case 'mint_task': {
          const link = await mintMyTaskLink(target.submissionId);
          const safe = sameOriginUrl(link.url, '/task');
          if (!safe) throw new Error(t('applications.couldNotOpenTask'));
          return safe;
        }
        case 'mint_offer': {
          const link = await getMyOfferLink(target.offerId);
          const safe = sameOriginUrl(link.url, '/offer');
          if (!safe) throw new Error(t('myOffers.openError'));
          return safe;
        }
        default:
          throw new Error(t('error.generic'));
      }
    },
    // Same tab: the candidate is starting the thing, not opening a reference,
    // and a popup is what a blocker eats.
    onSuccess: (url) => window.location.assign(url),
    onError: (e: unknown) => {
      if (target.type === 'mint_exam' && e instanceof ApiError && e.status === 409) {
        // The server's question, in the candidate's language (audit CA-M21).
        setConfirming(t('applications.examOpenElsewhere'));
        return;
      }
      setError(errText(e, t('error.generic')));
    },
  });

  if (target.type === 'none' || !action.ctaKey) {
    return { button: null, error: null, confirming: null };
  }

  if (target.type === 'route') {
    const { to, anchor } = target;
    if (onApplicationsPage && anchor) {
      return {
        button: (
          <Button type="button" onClick={() => scrollToAnchor(anchor)}>
            {t(action.ctaKey)}
          </Button>
        ),
        error: null,
        confirming: null,
      };
    }
    return {
      button: (
        <Link to={to} className={buttonVariants()}>
          {t(action.ctaKey)}
        </Link>
      ),
      error: null,
      confirming: null,
    };
  }

  const label = press.isPending
    ? t('applications.opening')
    : confirming
      ? t('applications.openHere')
      : t(action.ctaKey);

  return {
    button: (
      <Button
        type="button"
        disabled={press.isPending}
        onClick={() => {
          setError(null);
          // Only a press that followed the question may close the other tab.
          press.mutate(confirming !== null);
        }}
      >
        {label}
      </Button>
    ),
    error,
    confirming,
  };
}
