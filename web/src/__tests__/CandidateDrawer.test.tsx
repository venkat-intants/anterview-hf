// The candidate drawer.
//
// Two things it shows that nothing else does, and both are the point:
//
//   • the name the candidate typed, when the CV disagrees. The reconciler no
//     longer overwrites a typed name, so the two can differ — and if the
//     drawer showed one of them the discrepancy would be invisible to the only
//     person who could resolve it.
//   • their answers to the opening's questions, which are otherwise
//     write-only.
//
// And one thing it must not grow: an Advance button. Moving a candidate goes
// through the enrolment endpoint, which records who moved them and why.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Applicant } from '../api/applicants';
import type { ApplicationAnswer } from '../api/questions';

const getApplicant = vi.fn();
const listRoundResults = vi.fn();
vi.mock('../api/applicants', () => ({
  getApplicant: (...a: unknown[]) => getApplicant(...a) as unknown,
  listRoundResults: (...a: unknown[]) => listRoundResults(...a) as unknown,
}));

const listAnswers = vi.fn();
vi.mock('../api/questions', () => ({
  listAnswers: (...a: unknown[]) => listAnswers(...a) as unknown,
}));

import CandidateDrawer from '../components/CandidateDrawer';

function applicant(over: Partial<Applicant> = {}): Applicant {
  return {
    id: 'ap-1',
    full_name: 'Nadia Newbie',
    email: 'nadia@example.com',
    target_job_title: 'Backend Engineer',
    target_level: 'mid',
    status: 'shortlisted',
    ats_overall: 78,
    ats_breakdown: null,
    ats_strengths: ['Strong Python background'],
    ats_concerns: ['No Kubernetes experience'],
    ats_recommendation: 'interview',
    ats_summary: 'A solid backend match.',
    created_at: '2026-09-01T10:00:00Z',
    ...over,
  } as Applicant;
}

function renderDrawer(props: {
  applicantId?: string | null;
  enrolmentId?: string | null;
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <CandidateDrawer
        applicantId={props.applicantId === undefined ? 'ap-1' : props.applicantId}
        enrolmentId={props.enrolmentId}
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return { ...view, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  getApplicant.mockResolvedValue(applicant());
  listAnswers.mockResolvedValue([]);
  listRoundResults.mockResolvedValue([]);
});

describe('CandidateDrawer', () => {
  it('renders nothing at all when closed', () => {
    const { container } = renderDrawer({ applicantId: null });
    expect(container).toBeEmptyDOMElement();
    expect(getApplicant).not.toHaveBeenCalled();
  });

  it('shows who the candidate is', async () => {
    renderDrawer();
    expect(await screen.findByText('Nadia Newbie')).toBeInTheDocument();
    expect(screen.getByText(/Backend Engineer/)).toBeInTheDocument();
  });

  it('shows the resume match with its reasoning', async () => {
    renderDrawer();
    expect(await screen.findByText('78')).toBeInTheDocument();
    expect(screen.getByText(/Strong Python background/)).toBeInTheDocument();
    expect(screen.getByText(/No Kubernetes experience/)).toBeInTheDocument();
  });

  it('surfaces a name the CV disagrees with', async () => {
    // The whole reason parsed_full_name is on the API. Without this the
    // discrepancy is invisible to the only person who can resolve it.
    getApplicant.mockResolvedValue(
      applicant({ parsed_full_name: 'Priya Sharma', full_name_source: 'candidate' }),
    );
    renderDrawer();
    expect(await screen.findByText('Priya Sharma')).toBeInTheDocument();
    expect(screen.getByText(/they typed/)).toBeInTheDocument();
  });

  it('says nothing about the name when the two agree', async () => {
    getApplicant.mockResolvedValue(applicant({ parsed_full_name: 'Nadia Newbie' }));
    renderDrawer();
    await screen.findByText('Nadia Newbie');
    expect(screen.queryByText(/Their CV reads/)).not.toBeInTheDocument();
  });

  it('shows the details a multi-step application collected', async () => {
    getApplicant.mockResolvedValue(
      applicant({
        phone: '+91 90000 00000',
        years_experience: 6,
        current_company: 'Globex',
        current_title: 'Senior Engineer',
      }),
    );
    renderDrawer();
    expect(await screen.findByText('+91 90000 00000')).toBeInTheDocument();
    expect(screen.getByText('6 years')).toBeInTheDocument();
    expect(screen.getByText('Globex')).toBeInTheDocument();
  });

  it('omits a detail that was never given, rather than showing it blank', async () => {
    renderDrawer();
    await screen.findByText('Nadia Newbie');
    expect(screen.queryByText('Phone')).not.toBeInTheDocument();
  });

  it('reads back the answers, which nothing else shows', async () => {
    const answers: ApplicationAnswer[] = [
      {
        question_id: 'q1',
        prompt: 'Why are you interested?',
        kind: 'long_text',
        retired: false,
        answer: 'The problems are interesting.',
      },
      {
        question_id: 'q2',
        prompt: 'Which do you know?',
        kind: 'multi_choice',
        retired: false,
        answer: ['Python', 'Rust'],
      },
      {
        question_id: 'q3',
        prompt: 'Willing to relocate?',
        kind: 'yes_no',
        retired: false,
        answer: true,
      },
    ];
    listAnswers.mockResolvedValue(answers);
    renderDrawer({ enrolmentId: 'en-1' });

    expect(await screen.findByText('The problems are interesting.')).toBeInTheDocument();
    expect(screen.getByText('Python, Rust')).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('still shows an answer to a question no longer asked', async () => {
    // Hiding it would leave a decision partly based on something nobody can
    // see any more.
    listAnswers.mockResolvedValue([
      {
        question_id: 'q1',
        prompt: 'Willing to relocate?',
        kind: 'yes_no',
        retired: true,
        answer: true,
      },
    ]);
    renderDrawer({ enrolmentId: 'en-1' });
    expect(await screen.findByText(/no longer asked/)).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('does not look up answers when it has no application to look them up for', async () => {
    // "No answers" and "we cannot look" are different statements, and the
    // second must not be rendered as the first.
    renderDrawer({ enrolmentId: null });
    await screen.findByText('Nadia Newbie');
    expect(listAnswers).not.toHaveBeenCalled();
    expect(screen.queryByText('Application answers')).not.toBeInTheDocument();
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    const { onClose } = renderDrawer();
    await screen.findByText('Nadia Newbie');
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  it('closes from the button', async () => {
    const user = userEvent.setup();
    const { onClose } = renderDrawer();
    await screen.findByText('Nadia Newbie');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('says so when the candidate could not be loaded', async () => {
    getApplicant.mockRejectedValue(new Error('boom'));
    renderDrawer();
    expect(await screen.findByText(/Could not load this candidate/)).toBeInTheDocument();
  });

  it('carries no action that would bypass the transition ledger', async () => {
    // Moving a candidate records who moved them and why. A shortcut on a read
    // surface is how that ledger acquires gaps.
    renderDrawer({ enrolmentId: 'en-1' });
    await screen.findByText('Nadia Newbie');
    for (const label of [/advance/i, /reject/i, /hire/i, /shortlist/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
    }
  });

  // ── Why a score is what it is (§7, C8) ──────────────────────────────────
  describe('per-round scores', () => {
    const round = {
      round_id: 'r-1',
      round_title: 'Technical Interview',
      position: 2,
      kind: 'ai_interview',
      percent: 84,
      passed: true,
      graded_by: 'ai',
      evidence: null,
      criteria: [
        {
          competency_id: 'c-sysdesign',
          name: 'System design',
          score: 88,
          evidence: 'Walked through a sharded write path unprompted.',
        },
      ],
      axes: { communication: 76 },
      created_at: new Date().toISOString(),
    };

    it('shows the criterion behind a score, and its evidence', async () => {
      // The whole point of §7: a score must not be an unexplained number.
      listRoundResults.mockResolvedValue([round]);
      renderDrawer();
      expect(await screen.findByText('System design')).toBeInTheDocument();
      expect(screen.getByText(/sharded write path/)).toBeInTheDocument();
      expect(screen.getByText('3. Technical Interview')).toBeInTheDocument();
    });

    it('separates the frozen axes from the criteria that decided progression', async () => {
      // Reading the axes as the reason for a decision would be wrong — they are
      // the cross-role comparison (D-02), not the evaluation.
      listRoundResults.mockResolvedValue([round]);
      renderDrawer();
      expect(await screen.findByText('Comparable axes')).toBeInTheDocument();
    });

    it('never says a candidate failed', async () => {
      // D-05: below a threshold is held, not rejected. The word must not appear.
      listRoundResults.mockResolvedValue([{ ...round, passed: false, percent: 41 }]);
      renderDrawer();
      expect(await screen.findByText(/held for your decision/)).toBeInTheDocument();
      expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
    });

    it('shows nothing at all when no round has been scored', async () => {
      // A candidate who has not sat a round yet is a normal state, not an
      // empty-state worth a heading.
      //
      // waitFor, not a bare assertion: the section renders its heading over a
      // skeleton while the read is in flight, which is deliberate (it reserves
      // the space rather than shifting the drawer when scores land). The claim
      // being made here is about the settled state.
      renderDrawer();
      await screen.findByText('Nadia Newbie');
      await waitFor(() =>
        expect(screen.queryByText('Assessment')).not.toBeInTheDocument(),
      );
    });

    it('distinguishes "could not load" from "not assessed"', async () => {
      // Rendering a failed read as "no scores" would mislead a decision.
      listRoundResults.mockRejectedValue(new Error('boom'));
      renderDrawer();
      expect(await screen.findByText(/Scores could not be loaded/)).toBeInTheDocument();
    });
  });
});
