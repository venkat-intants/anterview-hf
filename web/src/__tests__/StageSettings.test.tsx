// StageSettings — PH4-O1. One stage's owner and SLA, editable on a live
// version too (like the interview kit): it never moves a candidate.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StageSettings from '../components/workflow/StageSettings';

const getStages = vi.fn();
const listStageOwners = vi.fn();
const setStage = vi.fn();
vi.mock('../api/stageSla', () => ({
  getStages: (...a: unknown[]) => getStages(...a) as unknown,
  listStageOwners: (...a: unknown[]) => listStageOwners(...a) as unknown,
  setStage: (...a: unknown[]) => setStage(...a) as unknown,
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

function renderPanel(roundId: string | null, label: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StageSettings workflowId="wf-1" roundId={roundId} label={label} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listStageOwners.mockResolvedValue([
    { user_id: 'u-1', full_name: 'Priya HR', email: 'priya@acme.edu' },
  ]);
});

describe('StageSettings', () => {
  it('starts empty for a stage with no owner or SLA yet', async () => {
    getStages.mockResolvedValue([
      { round_id: 'r-1', stage: 'Fundamentals', kind: 'mcq', owner_user_id: null, owner_name: null, sla_hours: null },
    ]);
    renderPanel('r-1', 'Fundamentals');

    await screen.findByText('Priya HR');
    expect(screen.getByLabelText(/owner/i)).toHaveValue('');
    expect(screen.getByLabelText(/sla \(hours\)/i)).toHaveValue(null);
  });

  it('hydrates from the saved owner and SLA', async () => {
    getStages.mockResolvedValue([
      { round_id: 'r-1', stage: 'Fundamentals', kind: 'mcq', owner_user_id: 'u-1', owner_name: 'Priya HR', sla_hours: 48 },
    ]);
    renderPanel('r-1', 'Fundamentals');

    await waitFor(() => expect(screen.getByLabelText(/owner/i)).toHaveValue('u-1'));
    expect(screen.getByLabelText(/sla \(hours\)/i)).toHaveValue(48);
  });

  it('saves the chosen owner and SLA for this exact stage', async () => {
    getStages.mockResolvedValue([
      { round_id: 'r-1', stage: 'Fundamentals', kind: 'mcq', owner_user_id: null, owner_name: null, sla_hours: null },
    ]);
    setStage.mockResolvedValue([
      { round_id: 'r-1', stage: 'Fundamentals', kind: 'mcq', owner_user_id: 'u-1', owner_name: 'Priya HR', sla_hours: 24 },
    ]);
    const user = userEvent.setup();
    renderPanel('r-1', 'Fundamentals');
    await screen.findByText('Priya HR');

    await user.selectOptions(screen.getByLabelText(/owner/i), 'u-1');
    await user.type(screen.getByLabelText(/sla \(hours\)/i), '24');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(setStage).toHaveBeenCalledWith('wf-1', {
        round_id: 'r-1',
        owner_user_id: 'u-1',
        sla_hours: 24,
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Saved for Fundamentals');
  });

  it('saves against the final-decision stage with a null round_id', async () => {
    getStages.mockResolvedValue([
      { round_id: null, stage: 'Final decision', kind: 'decision', owner_user_id: null, owner_name: null, sla_hours: null },
    ]);
    setStage.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPanel(null, 'the final decision');
    await screen.findByText('Priya HR');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(setStage).toHaveBeenCalledWith('wf-1', {
        round_id: null,
        owner_user_id: null,
        sla_hours: null,
      }),
    );
  });

  it('reports a save failure', async () => {
    getStages.mockResolvedValue([
      { round_id: 'r-1', stage: 'Fundamentals', kind: 'mcq', owner_user_id: null, owner_name: null, sla_hours: null },
    ]);
    setStage.mockRejectedValue(new Error('An SLA is between 1 and 8760 hours.'));
    const user = userEvent.setup();
    renderPanel('r-1', 'Fundamentals');
    await screen.findByText('Priya HR');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('An SLA is between 1 and 8760 hours.'),
    );
  });
});
