// OfferTemplates — PH4-A3. List, create, edit and delete reusable offer
// starting points. Compensation is never part of a template.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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

/* ── Audit wave 2 (offers) ───────────────────────────────────────────────── */

describe('OfferTemplates - clearing a field clears it (OF-F3, M9)', () => {
  it('sends null for cleared optional fields, so the old value does not come back', async () => {
    const user = userEvent.setup();
    updateOfferTemplate.mockResolvedValue({ ...TEMPLATE, benefits: null, probation_months: null });
    renderPage();
    await screen.findByText('Standard engineer');

    await user.click(screen.getByRole('button', { name: 'Edit Standard engineer' }));
    await user.clear(screen.getByLabelText('Benefits'));
    await user.clear(screen.getByLabelText('Probation (months)'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(updateOfferTemplate).toHaveBeenCalledWith(
        'tpl-1',
        expect.objectContaining({ benefits: null, probation_months: null, terms: null }),
      ),
    );
  });

  it('offers no empty choice for employment type or pay period', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Standard engineer');
    await user.click(screen.getByRole('button', { name: 'New template' }));
    expect(screen.getByLabelText('Employment type')).toHaveValue('full_time');
    expect(screen.getByLabelText('Pay period')).toHaveValue('annual');
    expect(
      within(screen.getByLabelText('Employment type')).queryByRole('option', { name: '-' }),
    ).not.toBeInTheDocument();
  });
});

describe('OfferTemplates - limits are caught before saving (OF-L15)', () => {
  it('a two-letter currency keeps Save off and says why', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Standard engineer');
    await user.click(screen.getByRole('button', { name: 'New template' }));
    await user.type(screen.getByLabelText('Name'), 'Contractors');
    await user.clear(screen.getByLabelText('Currency'));
    await user.type(screen.getByLabelText('Currency'), 'US');

    expect(screen.getByRole('button', { name: 'Create template' })).toBeDisabled();
    expect(screen.getByText('Currency is a three-letter code, such as INR.')).toBeInTheDocument();
    expect(createOfferTemplate).not.toHaveBeenCalled();
  });

  it('a cleared offer window keeps Save off', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Standard engineer');
    await user.click(screen.getByRole('button', { name: 'New template' }));
    await user.type(screen.getByLabelText('Name'), 'Contractors');
    await user.clear(screen.getByLabelText('Valid for (days)'));
    expect(screen.getByRole('button', { name: 'Create template' })).toBeDisabled();
    expect(screen.getByText('An offer stays open for 1 to 60 days.')).toBeInTheDocument();
  });
});

describe('OfferTemplates - accessible names (OF-L14)', () => {
  it('each Edit button names its template', async () => {
    renderPage();
    await screen.findByText('Standard engineer');
    const edit = screen.getByRole('button', { name: 'Edit Standard engineer' });
    expect(edit).toHaveAttribute('aria-expanded', 'false');
  });

  it('a failed load offers a retry', async () => {
    const user = userEvent.setup();
    listOfferTemplates.mockRejectedValueOnce(new Error('down'));
    renderPage();
    await screen.findByText('Could not load templates.');
    await user.click(screen.getByRole('button', { name: /try again/i }));
    expect(await screen.findByText('Standard engineer')).toBeInTheDocument();
  });
});
