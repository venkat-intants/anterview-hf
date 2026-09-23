// CalibrationTab — PH4-O5, extended PH5-E4. Read-only copy, the suppression
// security fix said in words (never a zero), the new per-criterion baseline
// table, the judgements drill-down (including its 422/truncation), the
// widened round picker (job_simulation/portfolio + archived versions), and
// that interviewers are never ranked — the list is always alphabetical.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  CalibrationResponse,
  CalibrationRow,
  CalibrationJudgementsResponse,
} from '../api/scheduling';

const schedulingApi = { getCalibration: vi.fn(), getCalibrationJudgements: vi.fn() };
vi.mock('../api/scheduling', async () => {
  const actual = await vi.importActual<typeof import('../api/scheduling')>('../api/scheduling');
  return {
    ...actual,
    getCalibration: (...a: unknown[]) => schedulingApi.getCalibration(...a) as unknown,
    getCalibrationJudgements: (...a: unknown[]) =>
      schedulingApi.getCalibrationJudgements(...a) as unknown,
  };
});

const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
}));

const workflowsApi = { listWorkflows: vi.fn(), getWorkflow: vi.fn() };
vi.mock('../api/workflows', async () => {
  const actual = await vi.importActual<typeof import('../api/workflows')>('../api/workflows');
  return {
    ...actual,
    listWorkflows: (...a: unknown[]) => workflowsApi.listWorkflows(...a) as unknown,
    getWorkflow: (...a: unknown[]) => workflowsApi.getWorkflow(...a) as unknown,
  };
});

import { ApiError } from '../api/client';
import CalibrationTab from '../components/panel/CalibrationTab';

function calibration(over: Partial<CalibrationResponse> = {}): CalibrationResponse {
  return {
    spec: { name: 'interviewer_calibration', version: 1 },
    registry_hash: 'abc123def4567890',
    cohort: {
      basis: 'scorecard_submitted',
      from: '2026-06-01T00:00:00.000Z',
      to: '2026-09-01T00:00:00.000Z',
    },
    filters: { requisition_id: null, round_id: null },
    start: '2026-06-01T00:00:00.000Z',
    end: '2026-09-01T00:00:00.000Z',
    rules: {
      min_pairs: 5,
      meaningful_delta: 0.75,
      scale: '1-5',
      min_candidates: 5,
      min_same_direction_share: 0.7,
      min_interviewers_for_baseline: 2,
      wide_disagreement_range: 1.5,
      min_span_days: 7,
      max_span_days: 366,
    },
    competencies: {},
    criteria: [],
    interviewers: [],
    ...over,
  };
}

function interviewerRow(over: Partial<CalibrationRow> = {}): CalibrationRow {
  return {
    user_id: 'iv-1',
    name: 'Rekha Iyer',
    scorecards: 12,
    scores: 40,
    candidates: 8,
    suppressed: false,
    mean: 4.2,
    not_assessed_rate: 0.05,
    distribution: { '1': 0, '2': 1, '3': 5, '4': 20, '5': 14 },
    by_competency: {},
    pairs: 9,
    mean_delta: 1.3,
    same_direction_share: 0.857,
    flag: 'higher',
    by_criterion: [],
    ...over,
  };
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CalibrationTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listRequisitions.mockResolvedValue([{ id: 'req-1', title: 'Backend Engineer' }]);
  workflowsApi.listWorkflows.mockResolvedValue([]);
  workflowsApi.getWorkflow.mockResolvedValue(undefined);
});

describe('CalibrationTab — read-only copy', () => {
  it('states the banner: patterns not assessments, and never changes a scorecard or decision', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();

    expect(
      await screen.findByText(
        'These are patterns in scoring, not assessments of any interviewer. Differences can have good reasons. Use them to start a calibration conversation. Nothing here changes a scorecard or a decision.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Candidates you still have to score are left out until you submit/),
    ).toBeInTheDocument();
  });

  it('states the flagging rule with the sign-consistency share', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();
    expect(
      await screen.findByText(
        'Interviewers are flagged at ±0.75 over at least 5 shared judgements, pointing the same way at least 70% of the time.',
      ),
    ).toBeInTheDocument();
  });

  it('never offers a period shorter than 7 days', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();
    await screen.findByText(/These are patterns in scoring/);
    const options = screen.getAllByRole<HTMLOptionElement>('option', { name: /days/i });
    for (const o of options) {
      expect(Number(o.value)).toBeGreaterThanOrEqual(7);
    }
  });
});

describe('CalibrationTab — states', () => {
  it('shows a loading state', () => {
    schedulingApi.getCalibration.mockImplementation(() => new Promise(() => undefined));
    renderTab();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('shows an error state', async () => {
    schedulingApi.getCalibration.mockRejectedValue(new Error('boom'));
    renderTab();
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });

  it('shows an empty state', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();
    expect(
      await screen.findByText('No submitted scorecards to compare in this period.'),
    ).toBeInTheDocument();
  });
});

describe('CalibrationTab — suppression (security fix)', () => {
  it('says so in words, and never falls back to showing a zero', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({
        interviewers: [
          interviewerRow({
            candidates: 2,
            suppressed: true,
            scores: null,
            mean: null,
            not_assessed_rate: null,
            distribution: null,
            pairs: 0,
            mean_delta: null,
            same_direction_share: null,
            flag: null,
          }),
        ],
      }),
    );
    renderTab();

    expect(
      await screen.findByText('too few candidates to compare (2 of the 5 needed)'),
    ).toBeInTheDocument();
    // Nothing numeric from a suppressed row leaks onto the page.
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
    expect(screen.queryByText('vs panel (paired)')).not.toBeInTheDocument();
  });
});

describe('CalibrationTab — flags in words (PH5-E4 §4.3 wording)', () => {
  it('states the paired-gap sentence with the panel size and same-direction count', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({
        interviewers: [
          interviewerRow({
            pairs: 7,
            mean_delta: 0.9,
            same_direction_share: 6 / 7,
            flag: 'higher',
          }),
        ],
      }),
    );
    renderTab();

    expect(await screen.findByText('Rekha Iyer')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Scores higher than the rest of the panel on the same candidates, by 0.9 on average across 7 shared judgements; in 6 of 7 the gap was the same way.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Same direction in 86% of shared judgements.')).toBeInTheDocument();
    expect(screen.getByText('Scores higher')).toBeInTheDocument();
  });

  it('names no candidates — the API sends none, and this tab does not invent any', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({ interviewers: [interviewerRow({ mean_delta: -0.8, flag: 'lower' })] }),
    );
    const { container } = renderTab();
    await screen.findByText('Rekha Iyer');
    expect(container.textContent).not.toMatch(/candidate name/i);
  });
});

describe('CalibrationTab — never ranks interviewers', () => {
  it('always lists interviewers alphabetically, regardless of the API order', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({
        interviewers: [
          interviewerRow({
            user_id: 'iv-z',
            name: 'Zoya Khan',
            flag: null,
            mean_delta: null,
            same_direction_share: null,
          }),
          interviewerRow({
            user_id: 'iv-a',
            name: 'Arjun Nair',
            flag: null,
            mean_delta: null,
            same_direction_share: null,
          }),
        ],
      }),
    );
    renderTab();
    const names = (await screen.findAllByText(/Khan|Nair/)).map((el) => el.textContent);
    expect(names).toEqual(['Arjun Nair', 'Zoya Khan']);
  });
});

describe('CalibrationTab — criteria table', () => {
  it('shows the panel distribution, mean and a suppressed cell as "too few to compare"', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({
        criteria: [
          {
            criterion_key: 'round-1:communication',
            round_id: 'round-1',
            competency_id: 'communication',
            competency_name: 'Communication',
            round_title: 'Panel interview',
            round_kind: 'human_review',
            workflow_version: 2,
            requisition_id: 'req-1',
            requisition_title: 'Backend Engineer',
            suppressed: false,
            candidates: 12,
            interviewers: 3,
            scores: 30,
            not_assessed: 2,
            distribution: { '1': 0, '2': 3, '3': 12, '4': 11, '5': 4 },
            mean: 3.53,
            shared_judgements: 9,
            disagreement: 1.8,
            signal: 'wide_disagreement',
          },
          {
            criterion_key: 'round-1:ownership',
            round_id: 'round-1',
            competency_id: 'ownership',
            competency_name: 'Ownership',
            round_title: 'Panel interview',
            round_kind: 'human_review',
            workflow_version: 2,
            requisition_id: 'req-1',
            requisition_title: 'Backend Engineer',
            suppressed: true,
            candidates: 2,
            interviewers: 1,
            scores: null,
            not_assessed: 0,
            distribution: null,
            mean: null,
            shared_judgements: 0,
            disagreement: null,
            signal: null,
          },
        ],
        interviewers: [interviewerRow()],
      }),
    );
    renderTab();

    expect(await screen.findByText('Communication')).toBeInTheDocument();
    expect(screen.getByText('wide disagreement')).toBeInTheDocument();
    expect(
      screen.getByText(
        'On this criterion, interviewers scoring the same candidate were 1.8 points apart on average. The anchors may need discussing.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Ownership')).toBeInTheDocument();
    expect(screen.getByText('too few to compare (2 of the 5 needed)')).toBeInTheDocument();
    // Banned word check on this screen's own copy.
    const container = screen.getByText('Communication').closest('table');
    expect(container?.textContent ?? '').not.toMatch(
      /\b(bias|harsh|lenient|outlier|poor|recommend)\b/i,
    );
  });
});

describe('CalibrationTab — judgements drill-down', () => {
  function judgements(
    over: Partial<CalibrationJudgementsResponse> = {},
  ): CalibrationJudgementsResponse {
    return {
      spec: { name: 'interviewer_calibration', version: 1 },
      cohort: {
        basis: 'scorecard_submitted',
        from: '2026-06-01T00:00:00.000Z',
        to: '2026-09-01T00:00:00.000Z',
      },
      filters: { requisition_id: null, round_id: null, criterion_key: null },
      interviewer: { user_id: 'iv-1', name: 'Rekha Iyer' },
      total: 1,
      truncated: false,
      rows: [
        {
          enrolment_id: 'en-1',
          applicant_id: 'ap-1',
          candidate_name: 'Asha Rao',
          requisition_title: 'Backend Engineer',
          round_id: 'round-1',
          round_title: 'Panel interview',
          criterion_key: 'round-1:communication',
          competency_name: 'Communication',
          score: 5,
          panel_mean: 3.5,
          panel_size: 3,
          gap: 1.5,
          scorecard_id: 'sc-1',
          submitted_at: '2026-08-01T00:00:00.000Z',
          evidence_href: '/hr/enrolments/en-1/evidence',
        },
      ],
      ...over,
    };
  }

  it('opens the drill-down and lists the named judgements', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({ interviewers: [interviewerRow()] }),
    );
    schedulingApi.getCalibrationJudgements.mockResolvedValue(judgements());
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole('button', { name: 'Show judgements' }));

    expect(schedulingApi.getCalibrationJudgements).toHaveBeenCalledWith(
      expect.objectContaining({ interviewerId: 'iv-1' }),
    );
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
    expect(
      screen.getByText('Backend Engineer · Panel interview · Communication'),
    ).toBeInTheDocument();
  });

  it('shows the truncation notice', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({ interviewers: [interviewerRow()] }),
    );
    schedulingApi.getCalibrationJudgements.mockResolvedValue(
      judgements({ total: 250, truncated: true }),
    );
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole('button', { name: 'Show judgements' }));
    expect(await screen.findByText('Showing 1 of 250, newest first')).toBeInTheDocument();
  });

  it('shows the 422 "too few candidates" message plainly, not as an error', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({ interviewers: [interviewerRow()] }),
    );
    schedulingApi.getCalibrationJudgements.mockRejectedValue(
      new ApiError('Too few candidates to show.', 422),
    );
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole('button', { name: 'Show judgements' }));
    expect(await screen.findByText('Too few candidates to show.')).toBeInTheDocument();
    expect(screen.queryByText(/could not load/i)).not.toBeInTheDocument();
  });

  it('closes on Escape and focuses inside the dialog on open', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({ interviewers: [interviewerRow()] }),
    );
    schedulingApi.getCalibrationJudgements.mockResolvedValue(judgements());
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole('button', { name: 'Show judgements' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Close')).toHaveFocus());

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('CalibrationTab — "How is this calculated?"', () => {
  it('shows the spec, thresholds and registry hash, and closes on Escape', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({ interviewers: [interviewerRow()] }),
    );
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole('button', { name: 'How is this calculated?' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('interviewer_calibration@1')).toBeInTheDocument();
    expect(within(dialog).getByText(/registry abc123def4/)).toBeInTheDocument();
    expect(within(dialog).getByText('0.75')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('CalibrationTab — round picker', () => {
  it('lists job_simulation and portfolio rounds, and labels an archived version', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    workflowsApi.listWorkflows.mockResolvedValue([
      {
        id: 'wf-2',
        version: 2,
        status: 'published',
        name: null,
        rounds: 2,
        enrolled_candidates: 5,
        published_at: '2026-08-01T00:00:00.000Z',
        created_at: '2026-07-01T00:00:00.000Z',
      },
      {
        id: 'wf-1',
        version: 1,
        status: 'archived',
        name: null,
        rounds: 1,
        enrolled_candidates: 3,
        published_at: '2026-05-01T00:00:00.000Z',
        created_at: '2026-04-01T00:00:00.000Z',
      },
    ]);
    workflowsApi.getWorkflow.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'wf-2'
          ? {
              id: 'wf-2',
              requisition_id: 'req-1',
              version: 2,
              status: 'published',
              name: null,
              editable: false,
              review_status: 'approved',
              role_profile_id: null,
              settings: {},
              published_at: null,
              rounds: [
                {
                  id: 'round-sim',
                  position: 0,
                  title: 'Take-home task',
                  kind: 'job_simulation',
                  pass_threshold: null,
                  time_limit_seconds: null,
                  deadline_days: 5,
                  on_pass_next_round_id: null,
                  on_fail_next_round_id: null,
                  fast_track_min_percent: null,
                  on_fast_track_next_round_id: null,
                  exam_round_id: null,
                  needs_questions: false,
                  criteria: [],
                },
              ],
            }
          : {
              id: 'wf-1',
              requisition_id: 'req-1',
              version: 1,
              status: 'archived',
              name: null,
              editable: false,
              review_status: 'approved',
              role_profile_id: null,
              settings: {},
              published_at: null,
              rounds: [
                {
                  id: 'round-portfolio',
                  position: 0,
                  title: 'Portfolio review',
                  kind: 'portfolio',
                  pass_threshold: null,
                  time_limit_seconds: null,
                  deadline_days: 5,
                  on_pass_next_round_id: null,
                  on_fail_next_round_id: null,
                  fast_track_min_percent: null,
                  on_fast_track_next_round_id: null,
                  exam_round_id: null,
                  needs_questions: false,
                  criteria: [],
                },
              ],
            },
      ),
    );
    const user = userEvent.setup();
    renderTab();

    await screen.findByRole('option', { name: 'Backend Engineer' });
    await user.selectOptions(screen.getByLabelText('Opening'), 'req-1');

    expect(await screen.findByRole('option', { name: 'Take-home task' })).toBeInTheDocument();
    expect(
      await screen.findByRole('option', { name: 'Portfolio review (v1, archived)' }),
    ).toBeInTheDocument();
  });
});

describe('CalibrationTab — filters', () => {
  it('sends the chosen opening as a filter, and clears the round with it', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    const user = userEvent.setup();
    renderTab();

    await screen.findByRole('option', { name: 'Backend Engineer' });
    await user.selectOptions(screen.getByLabelText('Opening'), 'req-1');

    expect(schedulingApi.getCalibration).toHaveBeenLastCalledWith(
      expect.objectContaining({ requisitionId: 'req-1', roundId: null }),
    );
  });
});
