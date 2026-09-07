// Real openings inside a candidate's account.
//
// This sits directly above the practice catalogue on the same page, and the
// two mean opposite things: one applies you to a job at a company, the other
// starts a mock interview with nobody on the other end. Most of this file is
// about keeping them apart, because getting it wrong is worse than not
// building the section — a candidate who thinks they applied, and did not,
// finds out by never hearing back.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { OpenRole } from '../api/applications';

const listOpenRoles = vi.fn();
vi.mock('../api/applications', () => ({
  listOpenRoles: (...a: unknown[]) => listOpenRoles(...a) as unknown,
}));

import OpenRoles from '../components/OpenRoles';

function role(over: Partial<OpenRole> = {}): OpenRole {
  return {
    requisition_id: 'req-1',
    title: 'Senior Python Engineer',
    company_name: 'Acme Test Co',
    company_slug: 'acme-test',
    level: 'senior',
    department: 'Engineering',
    location: 'Bengaluru',
    employment_type: 'full_time',
    experience_min_years: 4,
    experience_max_years: 9,
    skills: ['Python', 'FastAPI'],
    posted_at: new Date().toISOString(),
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    already_applied: false,
    ...over,
  };
}

function renderRoles() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OpenRoles />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listOpenRoles.mockResolvedValue([role()]);
});

describe('OpenRoles — real, not practice', () => {
  it('says these reach a hiring team', async () => {
    // The load-bearing sentence: below this section are mock interviews.
    renderRoles();
    await screen.findByText('Senior Python Engineer');
    expect(screen.getByText(/applying here reaches their hiring team/i)).toBeInTheDocument();
  });

  it('warns that what is below is for rehearsing', async () => {
    renderRoles();
    await screen.findByText('Senior Python Engineer');
    expect(screen.getByText(/for rehearsing, not applying/i)).toBeInTheDocument();
  });

  it('names the company on every card', async () => {
    // A practice role has no company. This one always does.
    renderRoles();
    expect(await screen.findByText(/Acme Test Co/)).toBeInTheDocument();
  });

  it('sends you to the application, not to an interview', async () => {
    renderRoles();
    const link = await screen.findByRole('link', { name: /View & apply/ });
    expect(link).toHaveAttribute('href', '/apply/req-1');
  });

  it('never offers to start an interview', async () => {
    renderRoles();
    await screen.findByText('Senior Python Engineer');
    expect(screen.queryByText(/start interview/i)).not.toBeInTheDocument();
  });
});

describe('OpenRoles — one you already applied to', () => {
  it('says so instead of inviting a second application', async () => {
    listOpenRoles.mockResolvedValue([role({ already_applied: true })]);
    renderRoles();
    expect(await screen.findByText('Applied')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /View & apply/ })).not.toBeInTheDocument();
  });

  it('offers their own record instead', async () => {
    listOpenRoles.mockResolvedValue([role({ already_applied: true })]);
    renderRoles();
    const link = await screen.findByRole('link', { name: 'Track it' });
    expect(link).toHaveAttribute('href', '/applications');
  });
});

describe('OpenRoles — what a card shows', () => {
  it('shows where and what kind', async () => {
    renderRoles();
    expect(await screen.findByText(/Bengaluru · Full-time · Engineering/)).toBeInTheDocument();
  });

  it('shows a published salary', async () => {
    listOpenRoles.mockResolvedValue([
      role({ salary_min: 2_400_000, salary_max: 3_600_000, salary_currency: 'INR' }),
    ]);
    renderRoles();
    const expected = `INR ${(2_400_000).toLocaleString()} – INR ${(3_600_000).toLocaleString()}`;
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it('shows no salary block when there is none', async () => {
    // Null covers a withheld band and an unrecorded one alike, so there is
    // deliberately nothing to distinguish them.
    const { container } = renderRoles();
    await screen.findByText('Senior Python Engineer');
    expect(container.textContent).not.toMatch(/salary/i);
  });

  it('renders an opening with no advert filled in', async () => {
    listOpenRoles.mockResolvedValue([
      role({
        department: null,
        location: null,
        employment_type: null,
        experience_min_years: null,
        experience_max_years: null,
        skills: [],
      }),
    ]);
    renderRoles();
    expect(await screen.findByText('Senior Python Engineer')).toBeInTheDocument();
  });
});

describe('OpenRoles — nothing to show', () => {
  it('disappears entirely when nobody is hiring', async () => {
    // An empty box on a page that also lists practice roles adds confusion
    // rather than information.
    listOpenRoles.mockResolvedValue([]);
    const { container } = renderRoles();
    await vi.waitFor(() => expect(listOpenRoles).toHaveBeenCalled());
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('says so when the list could not be loaded', async () => {
    listOpenRoles.mockRejectedValue(new Error('boom'));
    renderRoles();
    expect(await screen.findByText(/Could not load open roles/)).toBeInTheDocument();
  });
});
