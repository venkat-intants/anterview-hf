// Creating an opening: three short steps in a dialog.
//
//   1 Role basics    2 Budget (optional, all four or none)    3 Review & create
//
// What matters: each step validates only itself, Back never loses input, the
// budget is all-or-none the way the API requires (and goes in the create
// request, with a PATCH fallback when an older server does not echo it back),
// the payload carries only what was filled in, and success lands on the new
// opening's Setup tab.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { newOpeningSchema, EMPTY_OPENING, toBudgetPatch, toCreatePayload } from '../components/hr/opening/newOpeningSchema';

const m = vi.hoisted(() => ({
  listRequisitions: vi.fn(),
  getBackfillReview: vi.fn(),
  listTeam: vi.fn(),
  createRequisition: vi.fn(),
  updateRequisition: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    listRequisitions: m.listRequisitions,
    getBackfillReview: m.getBackfillReview,
    listTeam: m.listTeam,
    createRequisition: m.createRequisition,
    updateRequisition: m.updateRequisition,
  };
});
vi.mock('../lib/toast', () => ({ toast: m.toast }));

import Requisitions from '../pages/hr/Requisitions';

function Landed() {
  const l = useLocation();
  return <p data-testid="landed">{`${l.pathname}${l.search}`}</p>;
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/hr/requisitions']}>
        <Routes>
          <Route path="/hr/requisitions" element={<Requisitions />} />
          <Route path="/hr/requisitions/:id" element={<Landed />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openDialog() {
  await userEvent.click(await screen.findByTestId('new-opening'));
  return screen.findByRole('dialog');
}

const continueBtn = () => screen.getByRole('button', { name: 'Continue' });
const step = () => screen.getByRole('navigation', { name: 'New opening progress' });

beforeEach(() => {
  vi.clearAllMocks();
  m.listRequisitions.mockResolvedValue([]);
  m.getBackfillReview.mockResolvedValue({ backfilled_requisitions: [], merge_candidates: [] });
  m.listTeam.mockResolvedValue([{ id: 'u2', full_name: 'Asha Rao', email: 'asha@example.com' }]);
  m.createRequisition.mockResolvedValue({ id: 'new-1', title: 'Operations Analyst' });
  m.updateRequisition.mockResolvedValue({ id: 'new-1' });
});

describe('the contract', () => {
  it('sends only what was filled in; the budget rides in the create payload, all four or none', () => {
    const v = { ...EMPTY_OPENING, title: '  Operations Analyst ', budgetAmount: '900000', budgetCurrency: 'inr', budgetBasis: 'per_hire' as const, budgetPeriod: 'annual' as const };
    const budget = {
      budget_amount: 900000, budget_currency: 'INR', budget_basis: 'per_hire', budget_period: 'annual',
    };
    expect(toCreatePayload(v)).toEqual({ title: 'Operations Analyst', level: 'mid', ...budget });
    expect(toBudgetPatch(v)).toEqual(budget);
    expect(toBudgetPatch(EMPTY_OPENING)).toBeNull();
    expect(toCreatePayload({ ...EMPTY_OPENING, title: 'Operations Analyst' })).toEqual({
      title: 'Operations Analyst', level: 'mid',
    });
  });

  it('refuses a partial budget, naming each missing part', () => {
    const r = newOpeningSchema.safeParse({ ...EMPTY_OPENING, title: 'Role', budgetAmount: '500' });
    expect(r.success).toBe(false);
    const paths = r.success ? [] : r.error.issues.map((i) => i.path[0]);
    expect(paths).toEqual(expect.arrayContaining(['budgetCurrency', 'budgetBasis', 'budgetPeriod']));
    expect(paths).not.toContain('budgetAmount');
  });

  it('accepts all four, or none', () => {
    expect(newOpeningSchema.safeParse({ ...EMPTY_OPENING, title: 'Role' }).success).toBe(true);
    expect(
      newOpeningSchema.safeParse({
        ...EMPTY_OPENING, title: 'Role', budgetAmount: '5', budgetCurrency: 'INR', budgetBasis: 'total', budgetPeriod: 'monthly',
      }).success,
    ).toBe(true);
  });
});

describe('step 1 — role basics', () => {
  it('opens on step 1 with the title focused, and one primary: Continue', async () => {
    renderList();
    const dialog = await openDialog();
    expect(within(step()).getByText('Role basics').closest('li')?.getAttribute('aria-current')).toBe('step');
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('Job title')));
    expect(within(dialog).getByRole('button', { name: 'Continue' })).toBeTruthy();
    expect(within(dialog).queryByTestId('create-opening')).toBeNull();
  });

  it('will not leave step 1 without a title of two characters', async () => {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'A');
    await user.click(continueBtn());
    expect((await screen.findByRole('alert')).textContent).toMatch(/title of at least 2 characters/);
    expect(screen.getByTestId('new-opening-step-basics')).toBeTruthy();
    expect(screen.queryByTestId('new-opening-step-budget')).toBeNull();
  });

  it('checks hires wanted as a whole number in range', async () => {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.type(screen.getByLabelText('Hires wanted'), '0');
    await user.click(continueBtn());
    expect((await screen.findByRole('alert')).textContent).toMatch(/whole number from 1 to 10,000/);
  });

  it('offers the team as owners, defaulting to the person creating it', async () => {
    renderList();
    await openDialog();
    const owner = screen.getByLabelText<HTMLSelectElement>('Owner');
    expect(owner.value).toBe('');
    await waitFor(() => expect(within(owner).getByRole('option', { name: 'Asha Rao' })).toBeTruthy());
  });
});

describe('Back never loses input', () => {
  it('keeps every field across Back and Continue', async () => {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.selectOptions(screen.getByLabelText('Level'), 'senior');
    await user.type(screen.getByLabelText(/Department/), 'Operations');
    await user.click(continueBtn());

    await user.type(await screen.findByLabelText('Budget amount'), '900000');
    await user.click(screen.getByRole('button', { name: 'Back' }));

    expect(screen.getByLabelText<HTMLInputElement>('Job title').value).toBe('Operations Analyst');
    expect(screen.getByLabelText<HTMLInputElement>('Level').value).toBe('senior');
    expect(screen.getByLabelText<HTMLInputElement>(/Department/).value).toBe('Operations');

    await user.click(continueBtn());
    expect((await screen.findByLabelText<HTMLInputElement>('Budget amount')).value).toBe('900000');
  });
});

describe('step 2 — budget, all four or none', () => {
  async function toBudget() {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await screen.findByTestId('new-opening-step-budget');
    return user;
  }

  it('can be skipped entirely', async () => {
    const user = await toBudget();
    await user.click(continueBtn());
    expect(await screen.findByTestId('new-opening-step-review')).toBeTruthy();
    expect(screen.getByText('Not set — add it in Setup')).toBeTruthy();
  });

  it('an amount alone is refused, with the missing parts named', async () => {
    const user = await toBudget();
    await user.type(screen.getByLabelText('Budget amount'), '900000');
    await user.click(continueBtn());
    const alerts = (await screen.findAllByRole('alert')).map((a) => a.textContent);
    expect(alerts).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/3-letter currency code/),
        expect.stringMatching(/how the budget is counted/),
        expect.stringMatching(/period it covers/),
      ]),
    );
    expect(screen.queryByTestId('new-opening-step-review')).toBeNull();
    expect(m.createRequisition).not.toHaveBeenCalled();
    expect(m.updateRequisition).not.toHaveBeenCalled();
  });

  it('a currency alone is refused too: the four go together', async () => {
    const user = await toBudget();
    await user.type(screen.getByLabelText('Currency'), 'INR');
    await user.click(continueBtn());
    expect((await screen.findAllByRole('alert')).map((a) => a.textContent).join(' ')).toMatch(
      /amount as a number above 0/,
    );
  });

  it('all four proceeds, and the review shows the budget in words', async () => {
    const user = await toBudget();
    await user.type(screen.getByLabelText('Budget amount'), '900000');
    await user.type(screen.getByLabelText('Currency'), 'inr');
    await user.selectOptions(screen.getByLabelText('Counted'), 'per_hire');
    await user.selectOptions(screen.getByLabelText('Period'), 'annual');
    await user.click(continueBtn());
    const review = await screen.findByTestId('new-opening-step-review');
    expect(review.textContent).toMatch(/INR 9,00,000, per hire, annual/);
  });
});

describe('step 3 — review and create', () => {
  it('creates with only what was entered, then lands on the new opening\'s Setup tab', async () => {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    await user.click(await screen.findByTestId('create-opening'));

    await waitFor(() => expect(m.createRequisition).toHaveBeenCalledTimes(1));
    expect(m.createRequisition).toHaveBeenCalledWith({ title: 'Operations Analyst', level: 'mid' });
    expect(m.updateRequisition).not.toHaveBeenCalled();
    expect((await screen.findByTestId('landed')).textContent).toBe('/hr/requisitions/new-1?tab=setup');
    expect(m.toast.success).toHaveBeenCalledWith('Opened “Operations Analyst”');
  });

  async function fillBudgetAndCreate(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await user.type(await screen.findByLabelText('Budget amount'), '900000');
    await user.type(screen.getByLabelText('Currency'), 'INR');
    await user.selectOptions(screen.getByLabelText('Counted'), 'total');
    await user.selectOptions(screen.getByLabelText('Period'), 'one_time');
    await user.click(continueBtn());
    await user.click(await screen.findByTestId('create-opening'));
  }
  const BUDGET = {
    budget_amount: 900000, budget_currency: 'INR', budget_basis: 'total', budget_period: 'one_time',
  };

  it('sends the budget in the one create request, and makes no PATCH when the server echoes it', async () => {
    const user = userEvent.setup();
    m.createRequisition.mockResolvedValue({ id: 'new-1', title: 'Operations Analyst', ...BUDGET });
    renderList();
    await fillBudgetAndCreate(user);

    await waitFor(() => expect(m.createRequisition).toHaveBeenCalledTimes(1));
    expect(m.createRequisition).toHaveBeenCalledWith({
      title: 'Operations Analyst', level: 'mid', ...BUDGET,
    });
    expect((await screen.findByTestId('landed')).textContent).toBe('/hr/requisitions/new-1?tab=setup');
    expect(m.updateRequisition).not.toHaveBeenCalled();
    expect(m.toast.success).toHaveBeenCalledWith('Opened “Operations Analyst”');
  });

  it('falls back to a PATCH when an older server did not echo the budget', async () => {
    const user = userEvent.setup();
    // Default mock: the response carries no budget fields at all.
    renderList();
    await fillBudgetAndCreate(user);

    await waitFor(() => expect(m.updateRequisition).toHaveBeenCalledTimes(1));
    expect(m.createRequisition).toHaveBeenCalledWith({
      title: 'Operations Analyst', level: 'mid', ...BUDGET,
    });
    expect(m.updateRequisition).toHaveBeenCalledWith('new-1', BUDGET);
  });

  it('falls back too when the echo differs from what was sent', async () => {
    const user = userEvent.setup();
    m.createRequisition.mockResolvedValue({
      id: 'new-1', title: 'Operations Analyst', ...BUDGET, budget_amount: null,
    });
    renderList();
    await fillBudgetAndCreate(user);
    await waitFor(() => expect(m.updateRequisition).toHaveBeenCalledWith('new-1', BUDGET));
  });

  it('sends the optional fields when given, with the budget in the create request', async () => {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.type(screen.getByLabelText(/Department/), 'Operations');
    await user.type(screen.getByLabelText(/Location/), 'Hyderabad');
    await user.type(screen.getByLabelText('Hires wanted'), '3');
    await waitFor(() => expect(within(screen.getByLabelText('Owner')).getByRole('option', { name: 'Asha Rao' })).toBeTruthy());
    await user.selectOptions(screen.getByLabelText('Owner'), 'u2');
    await user.click(continueBtn());

    await user.type(await screen.findByLabelText('Budget amount'), '900000');
    await user.type(screen.getByLabelText('Currency'), 'INR');
    await user.selectOptions(screen.getByLabelText('Counted'), 'total');
    await user.selectOptions(screen.getByLabelText('Period'), 'one_time');
    await user.click(continueBtn());
    await user.click(await screen.findByTestId('create-opening'));

    await waitFor(() => expect(m.updateRequisition).toHaveBeenCalledTimes(1));
    expect(m.createRequisition).toHaveBeenCalledWith({
      title: 'Operations Analyst', level: 'mid', department: 'Operations', location: 'Hyderabad',
      owner_user_id: 'u2', target_hires: 3, ...BUDGET,
    });
    // The default mock does not echo the budget, so the fallback PATCH fires.
    expect(m.updateRequisition).toHaveBeenCalledWith('new-1', BUDGET);
    expect((await screen.findByTestId('landed')).textContent).toBe('/hr/requisitions/new-1?tab=setup');
  });

  it('a budget that fails to save does not undo the opening, and says so', async () => {
    const user = userEvent.setup();
    m.updateRequisition.mockRejectedValue(new Error('a budget needs an amount'));
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await user.type(await screen.findByLabelText('Budget amount'), '5');
    await user.type(screen.getByLabelText('Currency'), 'INR');
    await user.selectOptions(screen.getByLabelText('Counted'), 'total');
    await user.selectOptions(screen.getByLabelText('Period'), 'annual');
    await user.click(continueBtn());
    await user.click(await screen.findByTestId('create-opening'));

    await waitFor(() => expect(m.toast.warning).toHaveBeenCalled());
    expect(String(m.toast.warning.mock.calls[0][0])).toMatch(/budget was not saved/);
    expect((await screen.findByTestId('landed')).textContent).toBe('/hr/requisitions/new-1?tab=setup');
  });

  it('a duplicate title sends them back to the title, with the reason beside it', async () => {
    const user = userEvent.setup();
    m.createRequisition.mockRejectedValue(new Error("An open requisition for 'Operations Analyst' already exists."));
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    await user.click(await screen.findByTestId('create-opening'));

    expect((await screen.findByRole('alert')).textContent).toMatch(/already exists/);
    expect(screen.getByTestId('new-opening-step-basics')).toBeTruthy();
    expect(screen.getByLabelText<HTMLInputElement>('Job title').value).toBe('Operations Analyst');
  });

  it('keys the duplicate on HTTP 409, whatever the server words it', async () => {
    const user = userEvent.setup();
    m.createRequisition.mockRejectedValue(new ApiError('Ya existe una vacante abierta con ese título.', 409));
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    await user.click(await screen.findByTestId('create-opening'));

    expect((await screen.findByRole('alert')).textContent).toMatch(/Ya existe/);
    expect(screen.getByTestId('new-opening-step-basics')).toBeTruthy();
  });

  it('does not treat another failure as a duplicate', async () => {
    const user = userEvent.setup();
    m.createRequisition.mockRejectedValue(new ApiError('Something broke.', 500));
    renderList();
    await openDialog();
    await user.type(screen.getByLabelText('Job title'), 'Operations Analyst');
    await user.click(continueBtn());
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    await user.click(await screen.findByTestId('create-opening'));

    await waitFor(() => expect(m.toast.error).toHaveBeenCalled());
    expect(screen.queryByTestId('new-opening-step-basics')).toBeNull();
  });

  it('Cancel on step 1 closes the dialog without creating anything', async () => {
    const user = userEvent.setup();
    renderList();
    await openDialog();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(m.createRequisition).not.toHaveBeenCalled();
  });
});
