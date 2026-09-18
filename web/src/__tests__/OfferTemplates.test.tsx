// OfferTemplates — PH4-A3. List, create, edit and delete reusable offer
// starting points. Compensation is never part of a template.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { OfferTemplate } from '../api/offers';

const listOfferTemplates = vi.fn();
const createOfferTemplate = vi.fn();
const updateOfferTemplate = vi.fn();
const deleteOfferTemplate = vi.fn();
vi.mock('../api/offers', () => ({
  listOfferTemplates: (...a: unknown[]) => listOfferTemplates(...a) as unknown,
  createOfferTemplate: (...a: unknown[]) => createOfferTemplate(...a) as unknown,
  updateOfferTemplate: (...a: unknown[]) => updateOfferTemplate(...a) as unknown,
  deleteOfferTemplate: (...a: unknown[]) => deleteOfferTemplate(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import OfferTemplates from '../pages/hr/OfferTemplates';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OfferTemplates />
    </QueryClientProvider>,
  );
}

const TEMPLATE: OfferTemplate = {
  id: 'tpl-1',
  name: 'Standard engineer',
  employment_type: 'full_time',
  currency: 'INR',
  pay_period: 'annual',
  probation_months: 3,
  notice_period_days: 30,
  benefits: 'Health insurance',
  terms: null,
  valid_days: 7,
};

beforeEach(() => {
  vi.clearAllMocks();
  listOfferTemplates.mockResolvedValue([TEMPLATE]);
});

describe('OfferTemplates', () => {
  it('lists a template with its details', async () => {
    renderPage();
    await screen.findByText('Standard engineer');
    expect(screen.getByText(/full time/)).toBeInTheDocument();
    expect(screen.getByText(/7-day offer window/)).toBeInTheDocument();
  });

  it('creates a template', async () => {
    const user = userEvent.setup();
    createOfferTemplate.mockResolvedValue({ ...TEMPLATE, id: 'tpl-2', name: 'Senior engineer' });
    renderPage();
    await screen.findByText('Standard engineer');

    await user.click(screen.getByRole('button', { name: 'New template' }));
    await user.type(screen.getByLabelText('Name'), 'Senior engineer');
    await user.click(screen.getByRole('button', { name: 'Create template' }));

    await waitFor(() =>
      expect(createOfferTemplate).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Senior engineer' }),
      ),
    );
  });

  it('deletes a template with confirmation', async () => {
    const user = userEvent.setup();
    deleteOfferTemplate.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('Standard engineer');

    await user.click(screen.getByRole('button', { name: 'Delete Standard engineer' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(deleteOfferTemplate).toHaveBeenCalledWith('tpl-1'));
  });

  it('shows an empty state', async () => {
    listOfferTemplates.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('No templates yet.')).toBeInTheDocument();
  });

  it('shows an error state', async () => {
    listOfferTemplates.mockRejectedValue(new Error('down'));
    renderPage();
    expect(await screen.findByText('Could not load templates.')).toBeInTheDocument();
  });
});
