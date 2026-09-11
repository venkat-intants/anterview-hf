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
const confirmRequisition = vi.fn();
const mergeRequisition = vi.fn();
const listEnrolments = vi.fn();
const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  getBackfillReview: (...a: unknown[]) => getBackfillReview(...a) as unknown,
  updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown,
  mergeApplicants: (...a: unknown[]) => mergeApplicants(...a) as unknown,
  splitRequisition: (...a: unknown[]) => splitRequisition(...a) as unknown,
  confirmRequisition: (...a: unknown[]) => confirmRequisition(...a) as unknown,
  mergeRequisition: (...a: unknown[]) => mergeRequisition(...a) as unknown,
  listEnrolments: (...a: unknown[]) => listEnrolments(...a) as unknown,
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
}));

// Candidates in the folded opening. Every one normalises to the same title in
// an opening the backfill made — which is why split has to pick candidates.
const CANDIDATES = [
  { id: 'en-1', full_name: 'Ravi Kumar', target_job_title: 'Python Developer' },
  { id: 'en-2', full_name: 'Meena Iyer', target_job_title: 'python developer' },
  { id: 'en-3', full_name: 'Arjun Das', target_job_title: 'Python  Developer' },
];

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
  confirmRequisition.mockResolvedValue({ id: 'req-clean' });
  mergeRequisition.mockResolvedValue({
    into_requisition_id: 'req-clean',
    title: 'QA Analyst',
    moved: 9,
  });
  listEnrolments.mockResolvedValue(CANDIDATES);
  listRequisitions.mockResolvedValue([
    { id: 'req-folded', title: 'Python Developer', status: 'open' },
    { id: 'req-clean', title: 'QA Analyst', status: 'open' },
  ]);
});

describe('RequisitionReview — what the backfill guessed', () => {
  it('flags the opening that folded several spellings, and not the one that did not', async () => {
    renderPage();
    await screen.findByTestId('opening-req-folded');

    expect(within(openingCard('req-folded')).getByText(/2 spellings folded/)).toBeTruthy();
    expect(within(openingCard('req-clean')).getByText(/1 spelling/)).toBeTruthy();
  });

  it('offers a split on every opening, not only where spellings were folded', async () => {
    // A single-spelling opening can still hold two jobs; splitting by title
    // could never separate them, splitting by candidate can.
    renderPage();
    await screen.findByTestId('opening-req-folded');

    expect(within(openingCard('req-folded')).getByText('Split apart')).toBeTruthy();
    expect(within(openingCard('req-clean')).getByText('Split apart')).toBeTruthy();
  });

  it('confirms an opening with its own action, not by rewriting the title', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-clean');

    await user.click(within(openingCard('req-clean')).getByText('Looks right'));

    await waitFor(() => expect(confirmRequisition).toHaveBeenCalledWith('req-clean'));
    expect(updateRequisition).not.toHaveBeenCalled();
  });

  it('still renames when the title was edited', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-clean');

    const card = openingCard('req-clean');
    const input = within(card).getByLabelText('Opening title');
    await user.clear(input);
    await user.type(input, 'QA Engineer');
    await user.click(within(card).getByText('Rename & confirm'));

    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith('req-clean', { title: 'QA Engineer' }),
    );
    expect(confirmRequisition).not.toHaveBeenCalled();
  });
});

describe('RequisitionReview — splitting', () => {
  it('refuses to move every candidate out, because that is a rename', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Split apart'));
    for (const c of CANDIDATES) {
      await user.click(await within(card).findByLabelText(c.full_name));
    }
    await user.type(within(card).getByLabelText('New opening title'), 'Anything');

    expect(within(card).getByText(/moves every candidate out/)).toBeTruthy();
    expect(
      within(card).getByRole('button', { name: /Create opening & move/ }),
    ).toHaveProperty('disabled', true);
    expect(splitRequisition).not.toHaveBeenCalled();
  });

  it('sends exactly the candidates that were ticked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Split apart'));
    // Same normalised title as everyone else — by title, impossible to pick out.
    await user.click(await within(card).findByLabelText('Meena Iyer'));
    await user.type(within(card).getByLabelText('New opening title'), 'Data Engineer');
    await user.click(within(card).getByRole('button', { name: /Create opening & move/ }));

    await waitFor(() => expect(splitRequisition).toHaveBeenCalledTimes(1));
    expect(splitRequisition).toHaveBeenCalledWith('req-folded', {
      enrolment_ids: ['en-2'],
      new_title: 'Data Engineer',
    });
  });

  it('shows the spelling each candidate applied under', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Split apart'));
    expect(await within(card).findByText('applied as “python developer”')).toBeTruthy();
  });
});

describe('RequisitionReview — merging two openings', () => {
  it('does not merge on a single click, and names what will happen', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Merge into another opening…'));
    const select = await within(card).findByLabelText('Merge into');
    await waitFor(() => expect(within(select).getByText(/QA Analyst/)).toBeTruthy());
    await user.selectOptions(select, 'req-clean');
    await user.click(within(card).getByText('Merge…'));

    expect(mergeRequisition).not.toHaveBeenCalled();
    expect(within(card).getByText(/retire “Python Developer”/)).toBeTruthy();

    await user.click(within(card).getByText('Yes, merge'));
    await waitFor(() => expect(mergeRequisition).toHaveBeenCalledWith('req-folded', 'req-clean'));
  });

  it('never offers an opening as a merge target for itself', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Merge into another opening…'));
    const select = await within(card).findByLabelText('Merge into');
    await waitFor(() => expect(within(select).getByText(/QA Analyst/)).toBeTruthy());
    expect(within(select).queryByText(/Python Developer/)).toBeNull();
  });

  it('surfaces a refusal verbatim, because it names what to resolve', async () => {
    const user = userEvent.setup();
    mergeRequisition.mockRejectedValue(
      new Error('1 candidate(s) applied to both openings (Asha Rao)'),
    );
    renderPage();
    await screen.findByTestId('opening-req-folded');

    const card = openingCard('req-folded');
    await user.click(within(card).getByText('Merge into another opening…'));
    const select = await within(card).findByLabelText('Merge into');
    await waitFor(() => expect(within(select).getByText(/QA Analyst/)).toBeTruthy());
    await user.selectOptions(select, 'req-clean');
    await user.click(within(card).getByText('Merge…'));
    await user.click(within(card).getByText('Yes, merge'));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('1 candidate(s) applied to both openings (Asha Rao)'),
    );
  });
});

describe('RequisitionReview — applicants with no opening', () => {
  it('counts them rather than losing them from view', async () => {
    getBackfillReview.mockResolvedValue({ ...REVIEW, unfiled_applicants: 2 });
    renderPage();
    expect(await screen.findByText(/not filed under any opening/)).toBeTruthy();
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
