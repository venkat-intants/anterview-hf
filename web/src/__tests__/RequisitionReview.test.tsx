// Tests for the backfill review screen (Group B).
//
// This page is where a human confirms guesses a machine made about who applied
// for what, and both of its actions move somebody's application. Three
// properties are worth pinning:
//
//   • a merge folds the records the user CHOSE into the survivor they chose —
//     an off-by-one here permanently attaches one person's exam history to
//     another, and the operation cannot be undone;
//   • that merge is never one click away, because of the above;
//   • a split that would empty its source is refused in the UI, not just by
//     the server — the server calls it a rename, and finding that out after
//     filling in a form is a worse way to learn it.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BackfillReview } from '../api/requisitions';

const REVIEW: BackfillReview = {
  backfilled_requisitions: [
    {
      id: 'req-folded',
      title: 'Python Developer',
      status: 'open',
      enrolments: 9,
      distinct_source_titles: 2,
      source_titles: ['Python Developer', 'Senior Python Developer'],
    },
    {
      id: 'req-clean',
      title: 'QA Analyst',
      status: 'open',
      enrolments: 3,
      distinct_source_titles: 1,
      source_titles: ['QA Analyst'],
    },
  ],
  merge_candidates: [
    {
      email: 'asha@example.com',
      applicant_ids: ['ap-old', 'ap-new'],
      names: ['Asha Rao', 'Asha R'],
      enrolment_count: 2,
      has_history: true,
    },
  ],
};

const getBackfillReview = vi.fn();
const updateRequisition = vi.fn();
const mergeApplicants = vi.fn();
const splitRequisition = vi.fn();
vi.mock('../api/requisitions', () => ({
  getBackfillReview: (...a: unknown[]) => getBackfillReview(...a) as unknown,
  updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown,
  mergeApplicants: (...a: unknown[]) => mergeApplicants(...a) as unknown,
  splitRequisition: (...a: unknown[]) => splitRequisition(...a) as unknown,
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

import RequisitionReview from '../pages/hr/RequisitionReview';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RequisitionReview />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * The card for one opening, by id rather than by title.
 *
 * By title would be ambiguous by construction: a folded opening renders its
 * source titles verbatim, so "Python Developer" appears as the heading, as a
 * chip, as a checkbox label and as an input value on the same card.
 */
function openingCard(id: string): HTMLElement {
  return screen.getByTestId(`opening-${id}`);
}

beforeEach(() => {
  vi.clearAllMocks();
  getBackfillReview.mockResolvedValue(REVIEW);
  updateRequisition.mockResolvedValue({ id: 'req-folded' });
  mergeApplicants.mockResolvedValue({ survivor_id: 'ap-old', absorbed: 1, moved: {} });
  splitRequisition.mockResolvedValue({
    requisition_id: 'req-new',
    title: 'Senior Python Developer',
    moved: 4,
    left_behind: 5,
  });
});

describe('RequisitionReview — what the backfill guessed', () => {
  it('flags the opening that folded several spellings, and not the one that did not', async () => {
    renderPage();
    await screen.findByTestId('opening-req-folded');

    expect(within(openingCard('req-folded')).getByText(/2 spellings folded/)).toBeTruthy();
    expect(within(openingCard('req-clean')).getByText(/1 spelling/)).toBeTruthy();
  });

  it('offers a split only where the backfill actually folded something', async () => {
    renderPage();
    await screen.findByTestId('opening-req-folded');

    expect(within(openingCard('req-folded')).getByText('Split apart')).toBeTruthy();
    expect(within(openingCard('req-clean')).queryByText('Split apart')).toBeNull();
  });

  it('confirms an opening by writing its title back, which is what clears the flag', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-clean');

    await user.click(within(openingCard('req-clean')).getByText('Looks right'));

    await waitFor(() => expect(updateRequisition).toHaveBeenCalledTimes(1));
    expect(updateRequisition).toHaveBeenCalledWith('req-clean', { title: 'QA Analyst' });
  });
});

describe('RequisitionReview — splitting', () => {
  it('refuses to move every title out, because that is a rename', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Split apart'));

    for (const t of ['Python Developer', 'Senior Python Developer']) {
      await user.click(within(card).getByLabelText(t));
    }
    await user.type(within(card).getByLabelText('New opening title'), 'Anything');

    expect(within(card).getByText(/moves every candidate out/)).toBeTruthy();
    expect(
      within(card).getByRole('button', { name: /Create opening & move/ }),
    ).toHaveProperty('disabled', true);
    expect(splitRequisition).not.toHaveBeenCalled();
  });

  it('sends exactly the titles that were ticked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Split apart'));
    await user.click(within(card).getByLabelText('Senior Python Developer'));
    await user.type(
      within(card).getByLabelText('New opening title'),
      'Senior Python Developer',
    );
    await user.click(within(card).getByRole('button', { name: /Create opening & move/ }));

    await waitFor(() => expect(splitRequisition).toHaveBeenCalledTimes(1));
    expect(splitRequisition).toHaveBeenCalledWith('req-folded', {
      source_titles: ['Senior Python Developer'],
      new_title: 'Senior Python Developer',
    });
  });
});

describe('RequisitionReview — merging duplicate people', () => {
  it('does not merge on a single click', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('asha@example.com');

    await user.click(screen.getByText('Merge into one person…'));

    expect(mergeApplicants).not.toHaveBeenCalled();
    expect(screen.getByText(/cannot be undone/)).toBeTruthy();
  });

  it('keeps the record the user picked and absorbs the rest', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('asha@example.com');

    // Deliberately NOT the default: the second row must survive, the first
    // must be absorbed. Getting these the wrong way round is the bug this
    // whole screen exists to make impossible to commit by accident.
    await user.click(screen.getByRole('radio', { name: /Asha R\b/ }));
    await user.click(screen.getByText('Merge into one person…'));
    await user.click(screen.getByText('Yes, merge permanently'));

    await waitFor(() => expect(mergeApplicants).toHaveBeenCalledTimes(1));
    expect(mergeApplicants).toHaveBeenCalledWith('ap-new', ['ap-old']);
  });

  it('surfaces the server refusal verbatim, because it names what to resolve', async () => {
    const user = userEvent.setup();
    mergeApplicants.mockRejectedValue(
      new Error('both applicants are enrolled in the same opening (Python Developer)'),
    );
    renderPage();
    await screen.findByText('asha@example.com');

    await user.click(screen.getByText('Merge into one person…'));
    await user.click(screen.getByText('Yes, merge permanently'));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'both applicants are enrolled in the same opening (Python Developer)',
      ),
    );
  });
});

describe('RequisitionReview — nothing to do', () => {
  it('says so rather than rendering two empty sections', async () => {
    getBackfillReview.mockResolvedValue({
      backfilled_requisitions: [],
      merge_candidates: [],
    });
    renderPage();

    expect(await screen.findByText('Nothing left to review')).toBeTruthy();
  });
});
