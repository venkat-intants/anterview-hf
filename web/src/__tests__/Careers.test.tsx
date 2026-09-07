// Tests for the public job board.
//
// The board is the first thing a stranger sees, so the cases that matter are
// the degraded ones:
//
//   • most openings predate the posting fields, so a company can have no
//     departments and no locations at all. Every dropdown must vanish rather
//     than render empty — an empty <select> reads as broken.
//   • a withheld salary must not appear, and the page cannot tell "withheld"
//     from "not recorded" because the server sends null for both.
//   • an empty board is a board. "No roles match those filters" and "nothing
//     open right now" are different sentences and must not be swapped.
//   • filters live in the URL, so a filtered board is shareable — and paging
//     must not reset itself, which is a bug this suite exists to have caught.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CareersBoard, JobCard } from '../api/careers';

const getCareersBoard = vi.fn();
vi.mock('../api/careers', () => ({
  getCareersBoard: (...a: unknown[]) => getCareersBoard(...a) as unknown,
}));

import { ApiError } from '../api/client';
import Careers from '../pages/Careers';

function job(over: Partial<JobCard> = {}): JobCard {
  return {
    requisition_id: 'req-1',
    title: 'Platform Engineer',
    level: 'senior',
    department: 'Engineering',
    location: 'Hyderabad',
    employment_type: 'full_time',
    experience_min_years: 4,
    experience_max_years: 8,
    skills: ['Kubernetes', 'Terraform'],
    posted_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    ...over,
  };
}

function board(over: Partial<CareersBoard> = {}): CareersBoard {
  return {
    company_name: 'Acme Test Co',
    company_slug: 'acme-test',
    total: 1,
    page: 1,
    per_page: 20,
    filters: {
      departments: ['Design', 'Engineering'],
      locations: ['Hyderabad', 'Mumbai'],
      employment_types: ['full_time', 'contract'],
    },
    items: [job()],
    ...over,
  };
}

function renderBoard(url = '/careers/acme-test') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/careers/:companySlug" element={<Careers />} />
          <Route path="/apply/:id" element={<div>apply page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getCareersBoard.mockResolvedValue(board());
});

describe('Careers board', () => {
  it('names the company and counts the roles', async () => {
    renderBoard();
    expect(await screen.findByText('Acme Test Co')).toBeInTheDocument();
    expect(screen.getByText('1 open position')).toBeInTheDocument();
  });

  it('pluralises the count', async () => {
    getCareersBoard.mockResolvedValue(board({ total: 4, items: [job(), job({ requisition_id: 'r2' })] }));
    renderBoard();
    expect(await screen.findByText('4 open positions')).toBeInTheDocument();
  });

  it('shows what a card needs and links to the application', async () => {
    renderBoard();
    expect(await screen.findByText('Platform Engineer')).toBeInTheDocument();
    expect(screen.getByText('Hyderabad · Full-time · Engineering')).toBeInTheDocument();
    expect(screen.getByText('Kubernetes')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Platform Engineer/ })).toHaveAttribute(
      'href',
      '/apply/req-1',
    );
  });

  it('reads recency rather than a date', async () => {
    renderBoard();
    expect(await screen.findByText(/Posted 2 days ago/)).toBeInTheDocument();
  });

  it('does not put the job description on a card', async () => {
    // A board is for deciding whether to open a role, not for reading it.
    const { container } = renderBoard();
    await screen.findByText('Platform Engineer');
    expect(container.textContent).not.toMatch(/About the role/);
  });

  it('shows a published salary, grouped for the reader', async () => {
    // Formatted with toLocaleString, so the grouping follows the VIEWER —
    // 18,00,000 for an Indian candidate, 1,800,000 elsewhere. That is the
    // behaviour we want on a candidate-facing page, so the expectation is
    // computed the same way rather than pinning one locale into the test.
    getCareersBoard.mockResolvedValue(
      board({
        items: [job({ salary_min: 1_800_000, salary_max: 3_000_000, salary_currency: 'INR' })],
      }),
    );
    renderBoard();
    const expected = `INR ${(1_800_000).toLocaleString()} – INR ${(3_000_000).toLocaleString()}`;
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it('shows no salary block at all when there is none to show', async () => {
    // Null covers both a withheld band and an unrecorded one, so there is
    // deliberately no "salary not specified" copy to tell them apart.
    //
    // Scoped to the CARD rather than the whole page: the board's own filter
    // chrome legitimately says "salary" ("Any salary"), and asserting over the
    // container would make this test fail for a reason that has nothing to do
    // with what a candidate is told about this role.
    renderBoard();
    const title = await screen.findByText('Platform Engineer');
    const card = title.closest('a');
    expect(card).not.toBeNull();
    expect(card?.textContent).not.toMatch(/salary/i);
  });

  it('offers only the filters this company actually has', async () => {
    renderBoard();
    await screen.findByText('Platform Engineer');
    expect(screen.getByLabelText('Department')).toBeInTheDocument();
    expect(screen.getByLabelText('Location')).toBeInTheDocument();
    expect(screen.getByLabelText('Employment type')).toBeInTheDocument();
  });

  it('always offers the salary filter, unlike the facet dropdowns', async () => {
    // Not a facet: the rungs are fixed, so there is nothing to be empty of and
    // no reason to hide it on a board whose openings predate the salary fields.
    renderBoard();
    await screen.findByText('Platform Engineer');
    expect(screen.getByLabelText('Minimum salary')).toBeInTheDocument();
  });

  it('offers relevance sorting only once there is a search term to rank by', async () => {
    // Without a query it would have nothing to rank and would quietly behave
    // as newest while claiming otherwise.
    renderBoard();
    await screen.findByText('Platform Engineer');
    expect(screen.queryByLabelText('Sort by')).not.toBeInTheDocument();
  });

  it('shows the sort control when the URL carries a query', async () => {
    renderBoard('/careers/acme-test?q=engineer');
    await screen.findByText('Platform Engineer');
    expect(screen.getByLabelText('Sort by')).toBeInTheDocument();
  });

  it('hides a dropdown entirely when there is nothing to put in it', async () => {
    // The common case: openings that predate the posting fields. An empty
    // select reads as broken; an absent one reads as a board that does not
    // sort by that.
    getCareersBoard.mockResolvedValue(
      board({ filters: { departments: [], locations: [], employment_types: [] } }),
    );
    renderBoard();
    await screen.findByText('Platform Engineer');
    expect(screen.queryByLabelText('Department')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Location')).not.toBeInTheDocument();
  });

  it('puts a chosen filter in the URL so the board can be shared', async () => {
    const user = userEvent.setup();
    renderBoard();
    await screen.findByText('Platform Engineer');
    await user.selectOptions(screen.getByLabelText('Department'), 'Design');

    await waitFor(() =>
      expect(getCareersBoard).toHaveBeenLastCalledWith(
        'acme-test',
        expect.objectContaining({ department: 'Design' }),
      ),
    );
  });

  it('reads filters back out of the URL on load', async () => {
    renderBoard('/careers/acme-test?department=Design&q=kubernetes');
    await waitFor(() =>
      expect(getCareersBoard).toHaveBeenCalledWith(
        'acme-test',
        expect.objectContaining({ department: 'Design', q: 'kubernetes' }),
      ),
    );
  });

  it('returns to page one when a filter narrows the results', async () => {
    // Otherwise you sit on page 4 of a one-page result, looking at an empty
    // board with matches behind it.
    const user = userEvent.setup();
    getCareersBoard.mockResolvedValue(board({ total: 80, page: 3 }));
    renderBoard('/careers/acme-test?page=3');
    await screen.findByText('Platform Engineer');
    await user.selectOptions(screen.getByLabelText('Department'), 'Design');

    await waitFor(() =>
      expect(getCareersBoard).toHaveBeenLastCalledWith(
        'acme-test',
        expect.objectContaining({ department: 'Design', page: 1 }),
      ),
    );
  });

  it('pages forward without resetting itself', async () => {
    // The bug this test was written to catch: the handler cleared `page` on
    // every change, so Next set page 2 and then deleted it — a dead button.
    const user = userEvent.setup();
    getCareersBoard.mockResolvedValue(board({ total: 80 }));
    renderBoard();
    await screen.findByText('Platform Engineer');
    await user.click(screen.getByRole('button', { name: 'Next' }));

    await waitFor(() =>
      expect(getCareersBoard).toHaveBeenLastCalledWith(
        'acme-test',
        expect.objectContaining({ page: 2 }),
      ),
    );
  });

  it('hides pagination when everything fits on one page', async () => {
    renderBoard();
    await screen.findByText('Platform Engineer');
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });

  it('distinguishes "nothing matched" from "nothing open"', async () => {
    getCareersBoard.mockResolvedValue(board({ total: 0, items: [] }));
    renderBoard('/careers/acme-test?department=Design');
    expect(await screen.findByText('No roles match those filters')).toBeInTheDocument();
  });

  it('says a company is simply not hiring when it has no filters applied', async () => {
    getCareersBoard.mockResolvedValue(board({ total: 0, items: [] }));
    renderBoard();
    expect(await screen.findByText('No open roles right now')).toBeInTheDocument();
    expect(screen.getByText(/not advertising anything/)).toBeInTheDocument();
  });

  it('treats an unknown company as a missing page, not a server error', async () => {
    getCareersBoard.mockRejectedValue(new ApiError('nope', 404));
    renderBoard('/careers/ghost-co');
    expect(await screen.findByText('No careers page here')).toBeInTheDocument();
  });

  it('says something different when the server actually failed', async () => {
    getCareersBoard.mockRejectedValue(new ApiError('boom', 500));
    renderBoard();
    expect(await screen.findByText('Could not load these roles')).toBeInTheDocument();
  });

  it('renders an opening that has no advert at all', async () => {
    // Every opening created before the posting fields existed.
    getCareersBoard.mockResolvedValue(
      board({
        filters: { departments: [], locations: [], employment_types: [] },
        items: [
          job({
            department: null,
            location: null,
            employment_type: null,
            experience_min_years: null,
            experience_max_years: null,
            skills: [],
          }),
        ],
      }),
    );
    renderBoard();
    expect(await screen.findByText('Platform Engineer')).toBeInTheDocument();
  });
});
