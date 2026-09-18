// CalibrationTab — PH4-O5. Read-only copy, the suppression security fix said
// in words (never a zero), flags in words, and the 7-day-minimum period
// control never offering less.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CalibrationResponse } from '../api/scheduling';

const schedulingApi = { getCalibration: vi.fn() };
vi.mock('../api/scheduling', () => ({
  getCalibration: (...a: unknown[]) => schedulingApi.getCalibration(...a) as unknown,
}));

const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
}));

const workflowsApi = { listWorkflows: vi.fn(), getWorkflow: vi.fn() };
vi.mock('../api/workflows', () => ({
  listWorkflows: (...a: unknown[]) => workflowsApi.listWorkflows(...a) as unknown,
  getWorkflow: (...a: unknown[]) => workflowsApi.getWorkflow(...a) as unknown,
}));

import CalibrationTab from '../components/panel/CalibrationTab';

function calibration(over: Partial<CalibrationResponse> = {}): CalibrationResponse {
  return {
    start: '2026-06-01T00:00:00.000Z',
    end: '2026-09-01T00:00:00.000Z',
    rules: { min_pairs: 5, meaningful_delta: 0.75, scale: '1-5', min_candidates: 5 },
    competencies: {},
    interviewers: [],
    ...over,
  };
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CalibrationTab />
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
  it('states the read-only invariant and the leave-out-unscored rule prominently', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();

    expect(
      await screen.findByText('Read-only. Calibration never changes a submitted scorecard or a hiring decision.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Candidates you still have to score are left out until you submit/),
    ).toBeInTheDocument();
  });

  it('states the flagging rule using the server’s own numbers', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();
    expect(
      await screen.findByText('Interviewers are flagged at ±0.75 over at least 5 shared judgements.'),
    ).toBeInTheDocument();
  });

  it('never offers a period shorter than 7 days', async () => {
    schedulingApi.getCalibration.mockResolvedValue(calibration());
    renderTab();
    await screen.findByText('Read-only. Calibration never changes a submitted scorecard or a hiring decision.');
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
          {
            user_id: 'iv-1',
            name: 'Rekha Iyer',
            scorecards: 2,
            scores: 4,
            candidates: 2,
            suppressed: true,
            mean: null,
            not_assessed_rate: null,
            distribution: null,
            by_competency: {},
            pairs: 0,
            mean_delta: null,
            flag: null,
          },
        ],
      }),
    );
    renderTab();

    expect(await screen.findByText('Too few candidates to compare (2 of the 5 needed).')).toBeInTheDocument();
    // Nothing numeric from a suppressed row leaks onto the page.
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
    expect(screen.queryByText('vs panel')).not.toBeInTheDocument();
  });
});

describe('CalibrationTab — flags in words', () => {
  it('states a higher-scoring flag in words, signed vs panel', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({
        interviewers: [
          {
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
            flag: 'higher',
          },
        ],
      }),
    );
    renderTab();

    expect(await screen.findByText('Rekha Iyer')).toBeInTheDocument();
    expect(
      screen.getByText('Scores consistently higher than the panel on the same candidates.'),
    ).toBeInTheDocument();
    expect(screen.getByText('+1.30')).toBeInTheDocument();
    expect(screen.getByText('Scores higher')).toBeInTheDocument();
  });

  it('names no candidates — the API sends none, and this tab does not invent any', async () => {
    schedulingApi.getCalibration.mockResolvedValue(
      calibration({
        interviewers: [
          {
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
            mean_delta: -0.8,
            flag: 'lower',
          },
        ],
      }),
    );
    const { container } = renderTab();
    await screen.findByText('Rekha Iyer');
    // No candidate-shaped fields ever appear.
    expect(container.textContent).not.toMatch(/candidate name/i);
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
