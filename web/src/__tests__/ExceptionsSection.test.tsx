// ExceptionsSection — PH4-O1. Raising, resolving and reassigning an exception,
// and that every one of them shows up without a manual refresh.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ExceptionsSection from '../components/ExceptionsSection';
import type { StageException } from '../api/stageSla';

const api = {
  listExceptions: vi.fn(),
  raiseException: vi.fn(),
  resolveException: vi.fn(),
  reassignException: vi.fn(),
  listStageOwners: vi.fn(),
};
vi.mock('../api/stageSla', () => ({
  listExceptions: (...a: unknown[]) => api.listExceptions(...a) as unknown,
  raiseException: (...a: unknown[]) => api.raiseException(...a) as unknown,
  resolveException: (...a: unknown[]) => api.resolveException(...a) as unknown,
  reassignException: (...a: unknown[]) => api.reassignException(...a) as unknown,
  listStageOwners: (...a: unknown[]) => api.listStageOwners(...a) as unknown,
}));

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

function exc(over: Partial<StageException> = {}): StageException {
  return {
    exception_id: 'x-1', enrolment_id: 'en-1', stage: 'Panel', status: 'open',
    reason: 'Candidate asked to reschedule the panel', owner_user_id: 'u-1', owner_name: 'Rekha',
    raised_by_name: 'Rekha', raised_at: '2026-09-18T10:00:00.000Z',
    resolved_by_name: null, resolved_at: null, resolution_note: null, ...over,
  };
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <ExceptionsSection enrolmentId="en-1" />
    </QueryClientProvider>,
  );
  return { invalidate };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listStageOwners.mockResolvedValue([{ user_id: 'u-1', full_name: 'Rekha' }]);
});

describe('ExceptionsSection', () => {
  it('lists open exceptions before resolved ones, whatever order they arrive in', async () => {
    api.listExceptions.mockResolvedValue([
      exc({ exception_id: 'old', status: 'resolved', stage: 'Screen', resolved_at: '2026-09-18T12:00:00.000Z' }),
      exc({ exception_id: 'new', stage: 'Panel' }),
    ]);
    renderSection();
    const items = await screen.findAllByRole('listitem');
    expect(within(items[0]).getByText('Open')).toBeInTheDocument();
    expect(within(items[1]).getByText('Resolved')).toBeInTheDocument();
    expect(screen.getByText('Exceptions (1 open)')).toBeInTheDocument();
  });

  it('shows a raised exception straight away, without a refresh', async () => {
    api.listExceptions.mockResolvedValueOnce([]).mockResolvedValue([exc()]);
    api.raiseException.mockResolvedValue({
      exception_id: 'x-1', status: 'open', stage: 'Panel', owner_user_id: 'u-1',
    });
    const { invalidate } = renderSection();
    const user = userEvent.setup();
    expect(await screen.findByText('No exceptions raised for this application.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Raise exception' }));
    await user.type(screen.getByLabelText(/what is blocking this application/i), 'Candidate asked to reschedule the panel');
    await user.click(screen.getByRole('button', { name: /^raise exception$/i }));

    expect(await screen.findByText('Candidate asked to reschedule the panel')).toBeInTheDocument();
    // The counts elsewhere — the decision queue badge, the at-risk widget — too.
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toEqual(
      expect.arrayContaining([
        JSON.stringify(['hr', 'enrolment', 'en-1', 'exceptions']),
        JSON.stringify(['hr', 'decision-queue']),
        JSON.stringify(['hr', 'stage-sla']),
      ]),
    );
  });

  it('resolves with the note written, and the row turns resolved', async () => {
    api.listExceptions
      .mockResolvedValueOnce([exc()])
      .mockResolvedValue([exc({ status: 'resolved', resolved_by_name: 'Rekha', resolution_note: 'Moved to Friday' })]);
    api.resolveException.mockResolvedValue({ exception_id: 'x-1', status: 'resolved' });
    renderSection();
    const user = userEvent.setup();
    await user.type(await screen.findByRole('textbox', { name: /resolution note/i }), 'Moved to Friday');
    await user.click(screen.getByRole('button', { name: 'Resolve' }));

    await waitFor(() => expect(api.resolveException).toHaveBeenCalledWith('x-1', 'Moved to Friday'));
    expect(await screen.findByText('Resolved')).toBeInTheDocument();
    expect(screen.getByText(/Moved to Friday/)).toBeInTheDocument();
  });

  it('reassigns to the chosen owner', async () => {
    api.listStageOwners.mockResolvedValue([
      { user_id: 'u-1', full_name: 'Rekha' },
      { user_id: 'u-2', full_name: 'Arjun' },
    ]);
    api.listExceptions.mockResolvedValue([exc()]);
    api.reassignException.mockResolvedValue({ exception_id: 'x-1', owner_user_id: 'u-2' });
    renderSection();
    const user = userEvent.setup();
    const select = await screen.findByRole('combobox', { name: 'Reassign to' });
    await screen.findByRole('option', { name: 'Arjun' });
    await user.selectOptions(select, 'u-2');
    await waitFor(() => expect(api.reassignException).toHaveBeenCalledWith('x-1', 'u-2'));
  });

  it('says so when the list cannot be loaded, rather than claiming there are none', async () => {
    api.listExceptions.mockRejectedValue(new Error('boom'));
    renderSection();
    expect(await screen.findByText('Could not load exceptions.')).toBeInTheDocument();
    expect(screen.queryByText('No exceptions raised for this application.')).not.toBeInTheDocument();
  });

  it('surfaces a refused raise as an error, and keeps the form', async () => {
    api.listExceptions.mockResolvedValue([]);
    api.raiseException.mockRejectedValue(new Error('A final decision is already recorded'));
    renderSection();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Raise exception' }));
    await user.type(screen.getByLabelText(/what is blocking this application/i), 'Candidate asked to reschedule the panel');
    await user.click(screen.getByRole('button', { name: /^raise exception$/i }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.getByLabelText(/what is blocking this application/i)).toBeInTheDocument();
  });
});
