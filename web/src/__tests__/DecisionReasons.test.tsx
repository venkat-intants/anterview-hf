// DecisionReasons (/superadmin/decision-reasons) — O4.
//
// The super admin's own hire/reject reason taxonomy. What matters:
//   • creating one sends exactly label + applies_to + requires_explanation;
//   • retiring one is a toggle, not a delete, and the page says historical
//     decisions are unaffected;
//   • active vs retired is visible at a glance.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CompanyDecisionReason } from '../api/hr';

const api = {
  listCompanyDecisionReasons: vi.fn(),
  createDecisionReason: vi.fn(),
  updateDecisionReason: vi.fn(),
};
vi.mock('../api/hr', () => ({
  listCompanyDecisionReasons: (...a: unknown[]) => api.listCompanyDecisionReasons(...a) as unknown,
  createDecisionReason: (...a: unknown[]) => api.createDecisionReason(...a) as unknown,
  updateDecisionReason: (...a: unknown[]) => api.updateDecisionReason(...a) as unknown,
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

import DecisionReasons from '../pages/superadmin/DecisionReasons';

const REASONS: CompanyDecisionReason[] = [
  { code: 'strong-fit', label: 'Strong fit', applies_to: 'hired', requires_explanation: false, active: true, is_default: true },
  { code: 'other', label: 'Other', applies_to: 'both', requires_explanation: true, active: false, is_default: false },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DecisionReasons />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listCompanyDecisionReasons.mockResolvedValue(REASONS);
  api.createDecisionReason.mockResolvedValue(REASONS[0]);
  api.updateDecisionReason.mockResolvedValue({ ...REASONS[0], active: false });
});

describe('DecisionReasons — listing', () => {
  it('shows each reason with what it applies to and whether it needs an explanation', async () => {
    renderPage();

    const strongFitRow = (await screen.findByText('Strong fit')).closest(
      'div[role="listitem"]',
    ) as HTMLElement;
    const otherRow = screen.getByText('Other').closest('div[role="listitem"]') as HTMLElement;
    // Scoped to each row — the create form's own "Applies to" select repeats
    // these same option labels ("Hire only" etc.), which an unscoped query
    // would also match.
    expect(within(strongFitRow).getByText(/hire only/i)).toBeInTheDocument();
    expect(within(otherRow).getByText(/needs an explanation/i)).toBeInTheDocument();
  });

  it('marks the default reason', async () => {
    renderPage();
    const row = (await screen.findByText('Strong fit')).closest('div[role="listitem"]') as HTMLElement;
    expect(within(row).getByText('default')).toBeInTheDocument();
  });

  it('shows active and retired distinctly', async () => {
    renderPage();
    await screen.findByText('Strong fit');
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('retired')).toBeInTheDocument();
  });

  it('prompts rather than an empty box when there are none yet', async () => {
    api.listCompanyDecisionReasons.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/no reasons yet/i)).toBeInTheDocument();
  });
});

describe('DecisionReasons — creating a reason', () => {
  it('sends exactly label, applies_to and requires_explanation', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Strong fit');
    await user.type(screen.getByLabelText(/label/i), 'Not enough experience');
    await user.selectOptions(screen.getByLabelText(/applies to/i), 'rejected');
    await user.click(screen.getByLabelText(/needs a written explanation/i));
    await user.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() =>
      expect(api.createDecisionReason).toHaveBeenCalledWith({
        label: 'Not enough experience',
        applies_to: 'rejected',
        requires_explanation: true,
      }),
    );
  });

  it('refuses an empty label', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Strong fit');
    await user.click(screen.getByRole('button', { name: /^add$/i }));

    expect(api.createDecisionReason).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('A label is required.');
  });
});

describe('DecisionReasons — retiring and reactivating', () => {
  it('retires an active reason without deleting it', async () => {
    const user = userEvent.setup();
    renderPage();

    const row = (await screen.findByText('Strong fit')).closest('div[role="listitem"]') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /retire/i }));

    await waitFor(() =>
      expect(api.updateDecisionReason).toHaveBeenCalledWith('strong-fit', { active: false }),
    );
  });

  it('reactivates a retired reason', async () => {
    const user = userEvent.setup();
    renderPage();

    const row = (await screen.findByText('Other')).closest('div[role="listitem"]') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /reactivate/i }));

    await waitFor(() =>
      expect(api.updateDecisionReason).toHaveBeenCalledWith('other', { active: true }),
    );
  });

  it('only disables the row being changed', async () => {
    api.updateDecisionReason.mockImplementation(() => new Promise(() => undefined));
    const user = userEvent.setup();
    renderPage();

    const strong = (await screen.findByText('Strong fit')).closest('div[role="listitem"]') as HTMLElement;
    const other = screen.getByText('Other').closest('div[role="listitem"]') as HTMLElement;
    await user.click(within(strong).getByRole('button', { name: /retire/i }));

    await waitFor(() => expect(within(strong).getByRole('button', { name: /retire/i })).toBeDisabled());
    expect(within(other).getByRole('button', { name: /reactivate/i })).not.toBeDisabled();
  });

  it('says historical decisions are unaffected by retiring a reason', async () => {
    renderPage();
    await screen.findByText('Strong fit');
    expect(
      screen.getByText(/past decisions keep whatever reason they were recorded with/i),
    ).toBeInTheDocument();
  });
});

describe('DecisionReasons — renaming', () => {
  it('renames a reason in place', async () => {
    const user = userEvent.setup();
    renderPage();

    const row = (await screen.findByText('Strong fit')).closest('div[role="listitem"]') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /rename strong fit/i }));
    const input = within(row).getByLabelText(/new label for strong fit/i);
    await user.clear(input);
    await user.type(input, 'Strong Fit');
    await user.click(within(row).getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(api.updateDecisionReason).toHaveBeenCalledWith('strong-fit', { label: 'Strong Fit' }),
    );
  });

  it("shows the server's refusal when a used reason would change meaning", async () => {
    api.updateDecisionReason.mockRejectedValue(
      new Error("'Strong fit' has already been given as the reason for 3 decision(s). Retire it and add a new reason instead."),
    );
    const user = userEvent.setup();
    renderPage();

    const row = (await screen.findByText('Strong fit')).closest('div[role="listitem"]') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /rename strong fit/i }));
    const input = within(row).getByLabelText(/new label for strong fit/i);
    await user.clear(input);
    await user.type(input, 'Relocation');
    await user.click(within(row).getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/Retire it/)));
    // The editor stays open with what they typed, so nothing is silently lost.
    expect(within(row).getByLabelText(/new label for strong fit/i)).toHaveValue('Relocation');
  });

  it("a rename finishing late does not close another row's editor", async () => {
    let finish: (v: unknown) => void = () => undefined;
    api.updateDecisionReason.mockImplementationOnce(
      () => new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const user = userEvent.setup();
    renderPage();

    const strong = (await screen.findByText('Strong fit')).closest('div[role="listitem"]') as HTMLElement;
    const other = screen.getByText('Other').closest('div[role="listitem"]') as HTMLElement;
    await user.click(within(strong).getByRole('button', { name: /rename strong fit/i }));
    const first = within(strong).getByLabelText(/new label for strong fit/i);
    await user.clear(first);
    await user.type(first, 'Strong Fit');
    await user.click(within(strong).getByRole('button', { name: /^save$/i }));

    // While that is in flight, start renaming another row.
    await user.click(within(other).getByRole('button', { name: /rename other/i }));
    const second = within(other).getByLabelText(/new label for other/i);
    await user.type(second, ' reasons');
    expect(within(other).getByRole('button', { name: /^save$/i })).not.toBeDisabled();

    finish(REASONS[0]);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('Reason renamed'));
    expect(within(other).getByLabelText(/new label for other/i)).toHaveValue('Other reasons');
  });

  it('explains the rename rule on the page', async () => {
    renderPage();
    await screen.findByText('Strong fit');
    expect(screen.getByText(/a rename can only fix its case or spacing/i)).toBeInTheDocument();
  });
});

