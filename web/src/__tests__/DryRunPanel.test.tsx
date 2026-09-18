// DryRunPanel — PH4-O2. Running it, reading the result, and the stale notice.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DryRunPanel from '../components/workflow/DryRunPanel';
import type { SimulationResult } from '../api/workflowReview';

const getSimulation = vi.fn();
const runSimulation = vi.fn();
vi.mock('../api/workflowReview', () => ({
  getSimulation: (...a: unknown[]) => getSimulation(...a) as unknown,
  runSimulation: (...a: unknown[]) => runSimulation(...a) as unknown,
}));

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

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DryRunPanel workflowId="wf-1" />
    </QueryClientProvider>,
  );
}

const RESULT: SimulationResult = {
  simulation_id: 'sim-1', workflow_id: 'wf-1', version: 2, fingerprint: 'fp-1',
  created_at: '2026-09-10T00:00:00.000Z', stale: false, run_by_name: 'Rekha HR',
  status: 'warnings', errors: 0, warnings: 1,
  rounds: [
    {
      round_id: 'r-1', title: 'Fundamentals', kind: 'mcq', position: 0, state: 'warning',
      findings: [{ severity: 'warning', message: 'No interview kit.', round_id: 'r-1' }],
      branches: [{ branch: 'pass', round_id: 'r-2' }],
    },
  ],
  workflow_findings: [],
  scenarios: [
    {
      id: 'SIM-001', candidate: 'Simulated candidate 1', description: 'passes every round',
      end: 'decision',
      steps: [
        {
          round_id: 'r-1', round_title: 'Fundamentals', kind: 'mcq', outcome: 'pass',
          simulated_percent: 60, branch: 'pass',
          next: { kind: 'round', round_id: 'r-2', round_title: 'Conversation' },
        },
      ],
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('DryRunPanel — before anything has run', () => {
  it('says it has not run yet, and offers to run it', async () => {
    getSimulation.mockResolvedValue(null);
    renderPanel();
    expect(await screen.findByText('Not run yet for this version.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Run dry run' })).toBeTruthy();
  });
});

describe('DryRunPanel — a recorded result', () => {
  it('shows the status, counts, findings and who ran it', async () => {
    getSimulation.mockResolvedValue(RESULT);
    renderPanel();

    expect(await screen.findByText('Needs attention')).toBeTruthy();
    expect(screen.queryByText(/1 error\b/)).toBeNull(); // 0 errors — not "1 error"
    expect(screen.getByText(/0 errors.*1 warning/)).toBeTruthy();
    expect(screen.getByText('No interview kit.')).toBeTruthy();
    expect(screen.getByText(/by Rekha HR/)).toBeTruthy();
    // Once recorded, the button offers to run it again.
    expect(screen.getByRole('button', { name: 'Run again' })).toBeTruthy();
  });

  it('warns when the workflow changed since this run', async () => {
    getSimulation.mockResolvedValue({ ...RESULT, stale: true });
    renderPanel();
    expect(await screen.findByText(/Stale — the workflow changed since this run/)).toBeTruthy();
  });

  it('lists scenarios only once expanded, each with its steps', async () => {
    const user = userEvent.setup();
    getSimulation.mockResolvedValue(RESULT);
    renderPanel();
    await screen.findByText('Needs attention');

    expect(screen.queryByText(/passes every round/)).toBeNull();
    await user.click(screen.getByRole('button', { name: /show 1 scenario/i }));
    expect(screen.getByText(/passes every round/)).toBeTruthy();
    // The scenario itself collapses its steps too — expand it to see them.
    await user.click(screen.getByRole('button', { name: /SIM-001/ }));
    expect(screen.getByText(/Conversation/)).toBeTruthy();
  });

  it('says a candidate waits for a person when rounds do not advance automatically', async () => {
    const user = userEvent.setup();
    getSimulation.mockResolvedValue({
      ...RESULT,
      scenarios: [
        {
          ...RESULT.scenarios[0],
          end: 'waiting',
          steps: [{ ...RESULT.scenarios[0].steps[0], next: { kind: 'person' } }],
        },
      ],
    });
    renderPanel();
    await screen.findByText('Needs attention');
    await user.click(screen.getByRole('button', { name: /show 1 scenario/i }));
    expect(screen.getByText('waits for a person to move them on')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /SIM-001/ }));
    expect(screen.getByText(/stays until a person moves them/)).toBeTruthy();
  });

  it('runs again and shows the new result, making clear nothing else is published by it', async () => {
    const user = userEvent.setup();
    getSimulation.mockResolvedValue(RESULT);
    runSimulation.mockResolvedValue({ ...RESULT, status: 'passed', warnings: 0 });
    renderPanel();
    await screen.findByText('Needs attention');

    await user.click(screen.getByRole('button', { name: 'Run again' }));

    await waitFor(() => expect(runSimulation).toHaveBeenCalledWith('wf-1'));
    expect(await screen.findByText('Passed')).toBeTruthy();
    expect(toastSuccess).toHaveBeenCalled();
    // Said in the panel's own static copy, not just after a run.
    expect(screen.getByText(/A passing dry run publishes nothing/)).toBeTruthy();
  });

  it('says a failed load failed, rather than that it was never run', async () => {
    getSimulation.mockRejectedValue(new Error('down'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the last dry run.');
    expect(screen.queryByText('Not run yet for this version.')).not.toBeInTheDocument();
  });
});
