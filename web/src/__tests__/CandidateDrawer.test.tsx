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
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Applicant } from '../api/applicants';
import type { ApplicationAnswer } from '../api/questions';

const getApplicant = vi.fn();
const listRoundResults = vi.fn();
const listApplications = vi.fn();
vi.mock('../api/applicants', () => ({
  getApplicant: (...a: unknown[]) => getApplicant(...a) as unknown,
  listRoundResults: (...a: unknown[]) => listRoundResults(...a) as unknown,
  listApplications: (...a: unknown[]) => listApplications(...a) as unknown,
}));

describe('CandidateDrawer — opened from one application (B5)', () => {
  it("shows that application's resume match, not the latest one's", async () => {
    // The person's row describes their LATEST application (Backend, 78). The
    // pipeline opened their older one (Data Analyst), which scored 41.
    getApplicant.mockResolvedValue(applicant());
    listRoundResults.mockResolvedValue([]);
    listAnswers.mockResolvedValue([]);
    listApplications.mockResolvedValue([
      {
        enrolment_id: 'en-old',
        requisition_id: 'r1',
        opening_title: 'Data Analyst',
        status: 'new',
        stored_status: 'new',
        ats_overall: 41,
        ats_breakdown: null,
        ats_strengths: ['SQL'],
        ats_concerns: ['No dashboards'],
        ats_recommendation: 'maybe',
        ats_summary: 'A partial fit for analysis.',
        best_exam_percent: null,
        exam_passed: null,
        interview_score: null,
        scorecard_id: null,
        applied_at: '2026-09-01T00:00:00Z',
        is_latest: false,
      },
    ]);
    renderDrawer({ enrolmentId: 'en-old' });

    expect(await screen.findByText('A partial fit for analysis.')).toBeInTheDocument();
    expect(screen.getByText(/No dashboards/)).toBeInTheDocument();
    expect(screen.queryByText('A solid backend match.')).not.toBeInTheDocument();
  });
});

const listAnswers = vi.fn();
vi.mock('../api/questions', () => ({
  listAnswers: (...a: unknown[]) => listAnswers(...a) as unknown,
}));

const getEnrolmentHistory = vi.fn();
vi.mock('../api/requisitions', () => ({
  getEnrolmentHistory: (...a: unknown[]) => getEnrolmentHistory(...a) as unknown,
}));

// D4-1 — the human interview section. Mocked so every drawer test stays
// deterministic even though this section fires its own queries whenever an
// enrolmentId is present.
const scorecardsApi = {
  getEnrolmentScorecards: vi.fn(),
  assignInterviewers: vi.fn(),
  withdrawScorecard: vi.fn(),
  listInterviewers: vi.fn(),
};
vi.mock('../api/scorecards', () => ({
  getEnrolmentScorecards: (...a: unknown[]) =>
    scorecardsApi.getEnrolmentScorecards(...a) as unknown,
  assignInterviewers: (...a: unknown[]) => scorecardsApi.assignInterviewers(...a) as unknown,
  withdrawScorecard: (...a: unknown[]) => scorecardsApi.withdrawScorecard(...a) as unknown,
  listInterviewers: (...a: unknown[]) => scorecardsApi.listInterviewers(...a) as unknown,
}));

const workflowsApi = {
  listWorkflows: vi.fn(),
  getWorkflow: vi.fn(),
};
vi.mock('../api/workflows', async () => {
  // PH4-D4 — the real exports, not a hand-copied literal: HumanInterviewSection
  // filters the round picker against HUMAN_EVALUATED_KINDS, and a literal here
  // would keep passing even if that constant's own set ever changed.
  const actual = await vi.importActual<typeof import('../api/workflows')>('../api/workflows');
  return {
    ...actual,
    listWorkflows: (...a: unknown[]) => workflowsApi.listWorkflows(...a) as unknown,
    getWorkflow: (...a: unknown[]) => workflowsApi.getWorkflow(...a) as unknown,
  };
});

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

// PH4-O1 — the Exceptions section always mounts alongside the human interview
// section whenever an enrolmentId is present.
const stageSlaApi = {
  listExceptions: vi.fn(),
  raiseException: vi.fn(),
  resolveException: vi.fn(),
  reassignException: vi.fn(),
  listStageOwners: vi.fn(),
};
vi.mock('../api/stageSla', () => ({
  listExceptions: (...a: unknown[]) => stageSlaApi.listExceptions(...a) as unknown,
  raiseException: (...a: unknown[]) => stageSlaApi.raiseException(...a) as unknown,
  resolveException: (...a: unknown[]) => stageSlaApi.resolveException(...a) as unknown,
  reassignException: (...a: unknown[]) => stageSlaApi.reassignException(...a) as unknown,
  listStageOwners: (...a: unknown[]) => stageSlaApi.listStageOwners(...a) as unknown,
}));

// PH4-A2 — the Interviews (loops) section always mounts alongside the human
// interview section whenever an enrolmentId is present. Mocked to an empty
// list so every existing drawer test stays deterministic.
const schedulingApi = {
  listLoopsForEnrolment: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  listLoopsForEnrolment: (...a: unknown[]) => schedulingApi.listLoopsForEnrolment(...a) as unknown,
}));

// PH4-D2 — the Accommodations section always mounts alongside the rest of the
// drawer once the applicant has loaded (it is scoped to the applicant, not
// the application, so it renders even without an enrolmentId). Mocked to an
// empty history so every existing drawer test stays deterministic; its own
// behaviour is covered in AccommodationsSection.test.tsx.
const accommodationsApi = {
  listAccommodations: vi.fn(),
  recordAccommodation: vi.fn(),
  reviseAccommodation: vi.fn(),
  revokeAccommodation: vi.fn(),
  getEffectiveAccommodation: vi.fn(),
};
vi.mock('../api/accommodations', () => ({
  listAccommodations: (...a: unknown[]) => accommodationsApi.listAccommodations(...a) as unknown,
  recordAccommodation: (...a: unknown[]) => accommodationsApi.recordAccommodation(...a) as unknown,
  reviseAccommodation: (...a: unknown[]) => accommodationsApi.reviseAccommodation(...a) as unknown,
  revokeAccommodation: (...a: unknown[]) => accommodationsApi.revokeAccommodation(...a) as unknown,
  getEffectiveAccommodation: (...a: unknown[]) =>
    accommodationsApi.getEffectiveAccommodation(...a) as unknown,
}));

// PH4-D4 — the job simulation / portfolio submission section always mounts
// alongside the rest of the drawer whenever an enrolmentId is present.
// Mocked to an empty list so every existing drawer test stays deterministic;
// its own behaviour is covered in TaskSubmissionSection.test.tsx.
const jobTasksApi = {
  listEnrolmentTasks: vi.fn(),
  reissueTaskSubmission: vi.fn(),
  withdrawTaskSubmission: vi.fn(),
};
vi.mock('../api/jobTasks', () => ({
  listEnrolmentTasks: (...a: unknown[]) => jobTasksApi.listEnrolmentTasks(...a) as unknown,
  reissueTaskSubmission: (...a: unknown[]) => jobTasksApi.reissueTaskSubmission(...a) as unknown,
  withdrawTaskSubmission: (...a: unknown[]) => jobTasksApi.withdrawTaskSubmission(...a) as unknown,
}));

// PH4-D3 — the drawer reads an application's code-evidence counts. Without
// this mock the call went out unmocked and failed quietly into "render
// nothing", which passed but was a real request from a unit test.
vi.mock('../api/codeEvidence', () => ({
  getCodeEvidenceSummary: () =>
    Promise.resolve({
      signal_count: 0,
      unreviewed_signal_count: 0,
      finding_count: 0,
      no_concern_count: 0,
      follow_up_count: 0,
      confirmed_count: 0,
    }),
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

function renderDrawer(
  props: {
    applicantId?: string | null;
    enrolmentId?: string | null;
  } = {},
) {
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
  getEnrolmentHistory.mockResolvedValue([]);
  scorecardsApi.getEnrolmentScorecards.mockResolvedValue({ rounds: [] });
  scorecardsApi.listInterviewers.mockResolvedValue([]);
  workflowsApi.listWorkflows.mockResolvedValue([]);
  workflowsApi.getWorkflow.mockResolvedValue(undefined);
  stageSlaApi.listExceptions.mockResolvedValue([]);
  stageSlaApi.listStageOwners.mockResolvedValue([]);
  schedulingApi.listLoopsForEnrolment.mockResolvedValue([]);
  accommodationsApi.listAccommodations.mockResolvedValue([]);
  accommodationsApi.getEffectiveAccommodation.mockResolvedValue({ effective: false });
  jobTasksApi.listEnrolmentTasks.mockResolvedValue([]);
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
      await waitFor(() => expect(screen.queryByText('Assessment')).not.toBeInTheDocument());
    });

    it('distinguishes "could not load" from "not assessed"', async () => {
      // Rendering a failed read as "no scores" would mislead a decision.
      listRoundResults.mockRejectedValue(new Error('boom'));
      renderDrawer();
      expect(await screen.findByText(/Scores could not be loaded/)).toBeInTheDocument();
    });
  });

  // ── Human interview (D4-1) ──────────────────────────────────────────────
  describe('human interview', () => {
    it('is not shown at all without an enrolment to scope it to', async () => {
      renderDrawer({ enrolmentId: null });
      await screen.findByText('Nadia Newbie');
      expect(scorecardsApi.getEnrolmentScorecards).not.toHaveBeenCalled();
      expect(screen.queryByText('Human interview')).not.toBeInTheDocument();
    });

    it('shows each interviewer and their state for the round', async () => {
      scorecardsApi.getEnrolmentScorecards.mockResolvedValue({
        rounds: [
          {
            round_id: 'r-panel',
            round_title: 'Panel interview',
            position: 1,
            hidden_until_you_submit: false,
            criteria: [
              { competency_id: 'c-sysdesign', competency_name: 'System design', weight: 0.6 },
            ],
            scorecards: [
              {
                scorecard_id: 'sc-1',
                interviewer_user_id: 'u-iv-1',
                interviewer_name: 'Farah Khan',
                state: 'submitted',
                due_at: null,
                submitted_at: '2026-09-05T00:00:00.000Z',
                superseded: false,
                is_correction: false,
                correction_reason: null,
                withdrawn_reason: null,
                redacted: false,
                summary: 'Strong on system design.',
                scores: {
                  'c-sysdesign': {
                    score: 4,
                    not_assessed: false,
                    evidence: 'Handled the tradeoffs well.',
                  },
                },
              },
              {
                scorecard_id: 'sc-2',
                interviewer_user_id: 'u-iv-2',
                interviewer_name: 'Girish Rao',
                state: 'assigned',
                due_at: '2026-09-20T00:00:00.000Z',
                submitted_at: null,
                superseded: false,
                is_correction: false,
                correction_reason: null,
                withdrawn_reason: null,
                redacted: false,
                summary: null,
                scores: null,
              },
            ],
          },
        ],
      });
      renderDrawer({ enrolmentId: 'en-1' });

      expect(await screen.findByText('Panel interview')).toBeInTheDocument();
      expect(screen.getByText('Farah Khan')).toBeInTheDocument();
      expect(screen.getByText('submitted')).toBeInTheDocument();
      expect(screen.getByText('Girish Rao')).toBeInTheDocument();
      expect(screen.getByText('assigned')).toBeInTheDocument();
      expect(screen.getByText('Strong on system design.')).toBeInTheDocument();
      expect(screen.getByText(/Handled the tradeoffs well/)).toBeInTheDocument();
      expect(screen.getByText('Not submitted yet.')).toBeInTheDocument();
    });

    it('says scores are hidden until you submit your own, rather than showing nothing', async () => {
      scorecardsApi.getEnrolmentScorecards.mockResolvedValue({
        rounds: [
          {
            round_id: 'r-panel',
            round_title: 'Panel interview',
            position: 1,
            hidden_until_you_submit: true,
            criteria: [],
            scorecards: [
              {
                scorecard_id: 'sc-1',
                interviewer_user_id: 'u-iv-1',
                interviewer_name: 'Farah Khan',
                state: 'submitted',
                due_at: null,
                submitted_at: '2026-09-05T00:00:00.000Z',
                superseded: false,
                is_correction: false,
                correction_reason: null,
                corrected_after_peers_visible: false,
                withdrawn_reason: null,
                redacted: false,
                summary: null,
                scores: null,
              },
            ],
          },
        ],
      });
      renderDrawer({ enrolmentId: 'en-1' });

      expect(await screen.findByText('Panel interview')).toBeInTheDocument();
      // Once for the round, once on the peer's submitted card — never "Not submitted yet".
      expect(screen.getAllByText(/Hidden until you submit your own scorecard/)).toHaveLength(2);
      expect(screen.queryByText('Not submitted yet.')).toBeNull();
      expect(screen.getByText(/^Submitted /)).toBeInTheDocument();
    });

    it('shows the scores of a scorecard redacted by a data erasure, and says what was removed', async () => {
      scorecardsApi.getEnrolmentScorecards.mockResolvedValue({
        rounds: [
          {
            round_id: 'r-panel',
            round_title: 'Panel interview',
            position: 1,
            hidden_until_you_submit: false,
            criteria: [
              { competency_id: 'c-sysdesign', competency_name: 'System design', weight: 1 },
            ],
            scorecards: [
              {
                scorecard_id: 'sc-1',
                interviewer_user_id: 'u-iv-1',
                interviewer_name: 'Farah Khan',
                state: 'submitted',
                due_at: null,
                submitted_at: '2026-09-05T00:00:00.000Z',
                superseded: false,
                is_correction: false,
                correction_reason: null,
                corrected_after_peers_visible: false,
                withdrawn_reason: null,
                redacted: true,
                summary: null,
                scores: { 'c-sysdesign': { score: 4, not_assessed: false, evidence: null } },
              },
            ],
          },
        ],
      });
      renderDrawer({ enrolmentId: 'en-1' });

      expect(
        await screen.findByText(/Written evidence removed after a data-erasure request/),
      ).toBeInTheDocument();
      expect(screen.getByText('4')).toBeInTheDocument();
      expect(screen.queryByText(/Hidden until you submit/)).toBeNull();
    });

    it('shows a superseded scorecard collapsed, with the correction reason on the replacement', async () => {
      scorecardsApi.getEnrolmentScorecards.mockResolvedValue({
        rounds: [
          {
            round_id: 'r-panel',
            round_title: 'Panel interview',
            position: 1,
            hidden_until_you_submit: false,
            criteria: [],
            scorecards: [
              {
                scorecard_id: 'sc-2',
                interviewer_user_id: 'u-iv-1',
                interviewer_name: 'Farah Khan',
                state: 'submitted',
                due_at: null,
                submitted_at: '2026-09-06T00:00:00.000Z',
                superseded: false,
                is_correction: true,
                correction_reason: 'Realised the wrong evidence was recorded.',
                corrected_after_peers_visible: true,
                withdrawn_reason: null,
                redacted: false,
                summary: null,
                scores: {},
              },
              {
                scorecard_id: 'sc-1',
                interviewer_user_id: 'u-iv-1',
                interviewer_name: 'Farah Khan',
                state: 'submitted',
                due_at: null,
                submitted_at: '2026-09-05T00:00:00.000Z',
                superseded: true,
                is_correction: false,
                correction_reason: null,
                corrected_after_peers_visible: false,
                withdrawn_reason: null,
                redacted: false,
                summary: null,
                scores: {},
              },
            ],
          },
        ],
      });
      renderDrawer({ enrolmentId: 'en-1' });

      await screen.findByText('Panel interview');
      expect(screen.getByText(/Realised the wrong evidence was recorded/)).toBeInTheDocument();
      // Flagged neutrally — worth knowing, not implying anything improper.
      expect(screen.getByText(/Corrected after other scorecards were visible/)).toBeInTheDocument();
      // The superseded one is collapsed under a <details>, not shown open.
      const summary = screen.getByText(/1 corrected scorecard/);
      expect(summary.closest('details')?.open).toBe(false);
    });

    it('assigns interviewers to a human_review round and refetches', async () => {
      workflowsApi.listWorkflows.mockResolvedValue([
        {
          id: 'wf-1',
          version: 1,
          status: 'published',
          name: null,
          rounds: 1,
          enrolled_candidates: 1,
          published_at: '2026-01-01T00:00:00Z',
          created_at: '2026-01-01T00:00:00Z',
        },
      ]);
      workflowsApi.getWorkflow.mockResolvedValue({
        id: 'wf-1',
        requisition_id: 'req-1',
        version: 1,
        status: 'published',
        name: null,
        editable: false,
        role_profile_id: null,
        settings: {} as never,
        published_at: '2026-01-01T00:00:00Z',
        rounds: [
          {
            id: 'r-panel',
            position: 0,
            title: 'Panel interview',
            kind: 'human_review',
            pass_threshold: null,
            time_limit_seconds: null,
            deadline_days: 5,
            on_pass_next_round_id: null,
            exam_round_id: null,
            needs_questions: false,
            criteria: [],
          },
          {
            id: 'r-mcq',
            position: 1,
            title: 'Aptitude',
            kind: 'mcq',
            pass_threshold: 60,
            time_limit_seconds: null,
            deadline_days: 5,
            on_pass_next_round_id: null,
            exam_round_id: null,
            needs_questions: false,
            criteria: [],
          },
        ],
      });
      scorecardsApi.listInterviewers.mockResolvedValue([
        {
          user_id: 'u-iv-1',
          full_name: 'Farah Khan',
          email: 'farah@acme.edu',
          role: 'interviewer',
        },
      ]);
      scorecardsApi.assignInterviewers.mockResolvedValue({
        created: [{ scorecard_id: 'sc-1', interviewer_user_id: 'u-iv-1' }],
        already_assigned: [],
        due_at: '2026-09-20T00:00:00.000Z',
      });
      // The round picker needs the workflow, which needs the requisition id —
      // read off this application (B5), the same way the drawer already does
      // for the resume-match section.
      listApplications.mockResolvedValue([
        { enrolment_id: 'en-1', requisition_id: 'req-1', opening_title: 'Backend Engineer' },
      ]);

      const user = userEvent.setup();
      renderDrawer({ applicantId: 'ap-1', enrolmentId: 'en-1' });
      await screen.findByText('Nadia Newbie');

      await user.click(screen.getByRole('button', { name: /assign interviewers/i }));
      // Only the human_review round is offered — not the MCQ round.
      const roundSelect = await screen.findByLabelText('Round');
      expect(within(roundSelect).getByText('Panel interview')).toBeInTheDocument();
      expect(within(roundSelect).queryByText('Aptitude')).not.toBeInTheDocument();

      await user.selectOptions(roundSelect, 'r-panel');
      await user.click(await screen.findByLabelText(/Farah Khan/));
      await user.click(screen.getByRole('button', { name: /^assign$/i }));

      await waitFor(() =>
        expect(scorecardsApi.assignInterviewers).toHaveBeenCalledWith('en-1', {
          round_id: 'r-panel',
          interviewer_user_ids: ['u-iv-1'],
        }),
      );
      expect(toastSuccess).toHaveBeenCalledWith('Assigned 1 interviewer');
    });

    it('explains when there is no interview round to assign, instead of an empty form', async () => {
      workflowsApi.listWorkflows.mockResolvedValue([]);
      listApplications.mockResolvedValue([
        { enrolment_id: 'en-1', requisition_id: 'req-1', opening_title: 'Backend Engineer' },
      ]);
      renderDrawer({ applicantId: 'ap-1', enrolmentId: 'en-1' });
      await screen.findByText('Nadia Newbie');

      expect(await screen.findByText(/has no human interview round/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /assign interviewers/i })).toBeDisabled();
    });

    it('says the rounds could not be loaded, rather than showing none', async () => {
      workflowsApi.listWorkflows.mockRejectedValue(new Error('boom'));
      listApplications.mockResolvedValue([
        { enrolment_id: 'en-1', requisition_id: 'req-1', opening_title: 'Backend Engineer' },
      ]);
      renderDrawer({ applicantId: 'ap-1', enrolmentId: 'en-1' });
      await screen.findByText('Nadia Newbie');

      expect(
        await screen.findByText(/could not load this opening.s interview rounds/i),
      ).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /assign interviewers/i })).toBeDisabled();
    });

    it('withdraws an unsubmitted assignment after a confirm click', async () => {
      scorecardsApi.getEnrolmentScorecards.mockResolvedValue({
        rounds: [
          {
            round_id: 'r-panel',
            round_title: 'Panel interview',
            position: 1,
            hidden_until_you_submit: false,
            criteria: [],
            scorecards: [
              {
                scorecard_id: 'sc-1',
                interviewer_user_id: 'u-iv-1',
                interviewer_name: 'Farah Khan',
                state: 'assigned',
                due_at: '2026-09-20T00:00:00.000Z',
                submitted_at: null,
                superseded: false,
                is_correction: false,
                correction_reason: null,
                withdrawn_reason: null,
                redacted: false,
                summary: null,
                scores: null,
              },
            ],
          },
        ],
      });
      scorecardsApi.withdrawScorecard.mockResolvedValue(undefined);

      const user = userEvent.setup();
      renderDrawer({ enrolmentId: 'en-1' });
      await screen.findByText('Farah Khan');

      await user.click(screen.getByRole('button', { name: /withdraw/i }));
      await user.click(
        within(screen.getByRole('group', { name: /confirm deletion/i })).getByRole('button', {
          name: /^delete$/i,
        }),
      );

      await waitFor(() =>
        expect(scorecardsApi.withdrawScorecard).toHaveBeenCalledWith('sc-1', undefined),
      );
    });
  });

  // ── Exceptions (PH4-O1) ──────────────────────────────────────────────────
  describe('exceptions', () => {
    it('says nothing changes a candidate status, and lists nothing when there are none', async () => {
      renderDrawer({ enrolmentId: 'en-1' });
      await screen.findByText('Nadia Newbie');

      expect(await screen.findByText(/never changes the candidate.s status/)).toBeInTheDocument();
      expect(screen.getByText('No exceptions raised for this application.')).toBeInTheDocument();
    });

    it('raises an exception with a reason and an owner', async () => {
      stageSlaApi.listStageOwners.mockResolvedValue([
        { user_id: 'u-hr-1', full_name: 'Priya HR', email: 'priya@acme.edu' },
      ]);
      stageSlaApi.raiseException.mockResolvedValue({
        exception_id: 'exc-1',
        status: 'open',
        stage: 'Fundamentals',
        owner_user_id: 'u-hr-1',
      });
      const user = userEvent.setup();
      renderDrawer({ enrolmentId: 'en-1' });
      await screen.findByText('Nadia Newbie');

      await user.click(screen.getByRole('button', { name: /raise exception/i }));
      await user.type(
        screen.getByLabelText(/what is blocking this application/i),
        'Interviewer had to reschedule twice',
      );
      await user.selectOptions(screen.getByLabelText('Owner'), 'u-hr-1');
      await user.click(screen.getByRole('button', { name: /^raise exception$/i }));

      await waitFor(() =>
        expect(stageSlaApi.raiseException).toHaveBeenCalledWith('en-1', {
          reason: 'Interviewer had to reschedule twice',
          owner_user_id: 'u-hr-1',
        }),
      );
    });

    it('will not raise with fewer than 10 characters', async () => {
      const user = userEvent.setup();
      renderDrawer({ enrolmentId: 'en-1' });
      await screen.findByText('Nadia Newbie');

      await user.click(screen.getByRole('button', { name: /raise exception/i }));
      await user.type(screen.getByLabelText(/what is blocking this application/i), 'too short');
      expect(screen.getByRole('button', { name: /^raise exception$/i })).toBeDisabled();
      expect(stageSlaApi.raiseException).not.toHaveBeenCalled();
    });

    it('resolves an open exception with an optional note', async () => {
      stageSlaApi.listExceptions.mockResolvedValue([
        {
          exception_id: 'exc-1',
          enrolment_id: 'en-1',
          stage: 'Fundamentals',
          reason: 'Interviewer had to reschedule twice',
          status: 'open',
          owner_user_id: 'u-hr-1',
          owner_name: 'Priya HR',
          raised_by_name: 'Priya HR',
          raised_at: '2026-09-01T00:00:00.000Z',
          resolved_by_name: null,
          resolved_at: null,
          resolution_note: null,
        },
      ]);
      stageSlaApi.resolveException.mockResolvedValue({ exception_id: 'exc-1', status: 'resolved' });
      const user = userEvent.setup();
      renderDrawer({ enrolmentId: 'en-1' });

      await screen.findByText('Interviewer had to reschedule twice');
      await user.type(
        screen.getByLabelText(/resolution note for fundamentals exception/i),
        'Rescheduled and completed',
      );
      await user.click(screen.getByRole('button', { name: /^resolve$/i }));

      await waitFor(() =>
        expect(stageSlaApi.resolveException).toHaveBeenCalledWith(
          'exc-1',
          'Rescheduled and completed',
        ),
      );
    });

    it('reassigns an open exception to another owner', async () => {
      stageSlaApi.listExceptions.mockResolvedValue([
        {
          exception_id: 'exc-1',
          enrolment_id: 'en-1',
          stage: 'Fundamentals',
          reason: 'Interviewer had to reschedule twice',
          status: 'open',
          owner_user_id: 'u-hr-1',
          owner_name: 'Priya HR',
          raised_by_name: 'Priya HR',
          raised_at: '2026-09-01T00:00:00.000Z',
          resolved_by_name: null,
          resolved_at: null,
          resolution_note: null,
        },
      ]);
      stageSlaApi.listStageOwners.mockResolvedValue([
        { user_id: 'u-hr-1', full_name: 'Priya HR', email: 'priya@acme.edu' },
        { user_id: 'u-hr-2', full_name: 'Ravi HR', email: 'ravi@acme.edu' },
      ]);
      stageSlaApi.reassignException.mockResolvedValue({
        exception_id: 'exc-1',
        owner_user_id: 'u-hr-2',
      });
      const user = userEvent.setup();
      renderDrawer({ enrolmentId: 'en-1' });

      await screen.findByText('Interviewer had to reschedule twice');
      const reassignSelect = screen.getByLabelText(/reassign to/i);
      await within(reassignSelect).findByText('Ravi HR');
      await user.selectOptions(reassignSelect, 'u-hr-2');

      await waitFor(() =>
        expect(stageSlaApi.reassignException).toHaveBeenCalledWith('exc-1', 'u-hr-2'),
      );
    });
  });
});
