// InterviewPanel (/hr/panel) — PH4 Wave 3 (O5). The three-tab shell: each
// tab's own behaviour is covered in WorkloadTab.test.tsx,
// PanelAvailabilityTab.test.tsx and CalibrationTab.test.tsx.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const schedulingApi = {
  getWorkload: vi.fn(),
  getInterviewerAvailability: vi.fn(),
  getCalibration: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  getWorkload: (...a: unknown[]) => schedulingApi.getWorkload(...a) as unknown,
  getInterviewerAvailability: (...a: unknown[]) =>
    schedulingApi.getInterviewerAvailability(...a) as unknown,
  getCalibration: (...a: unknown[]) => schedulingApi.getCalibration(...a) as unknown,
}));

const listInterviewers = vi.fn();
vi.mock('../api/scorecards', () => ({
  listInterviewers: (...a: unknown[]) => listInterviewers(...a) as unknown,
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

import InterviewPanel from '../pages/hr/InterviewPanel';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InterviewPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  schedulingApi.getWorkload.mockResolvedValue({
    start: '',
    end: '',
    timezone: 'Asia/Kolkata',
    defaults: { max_per_day: 4, max_per_week: 15 },
    interviewers: [],
  });
  schedulingApi.getInterviewerAvailability.mockResolvedValue([]);
  schedulingApi.getCalibration.mockResolvedValue({
    spec: { name: 'interviewer_calibration', version: 1 },
    registry_hash: 'test-hash',
    cohort: { basis: 'scorecard_submitted', from: '', to: '' },
    filters: { requisition_id: null, round_id: null },
    start: '',
    end: '',
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
  });
  listInterviewers.mockResolvedValue([]);
  listRequisitions.mockResolvedValue([]);
  workflowsApi.listWorkflows.mockResolvedValue([]);
});

describe('InterviewPanel — tabs', () => {
  it('opens on Workload', async () => {
    renderPage();
    expect(
      await screen.findByText(
        'No one can be booked for interviews yet. Your super admin adds interviewers under Team.',
      ),
    ).toBeInTheDocument();
  });

  it('switches to Availability', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'Availability' }));
    expect(
      await screen.findByText('Choose an interviewer to see and edit their availability.'),
    ).toBeInTheDocument();
  });

  it('switches to Calibration', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('tab', { name: 'Calibration' }));
    expect(
      await screen.findByText(
        'These are patterns in scoring, not assessments of any interviewer. Differences can have good reasons. Use them to start a calibration conversation. Nothing here changes a scorecard or a decision.',
      ),
    ).toBeInTheDocument();
  });
});
