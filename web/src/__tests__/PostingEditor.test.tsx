// The posting editor.
//
// The field worth most of this file is `salary_visible`. Recording a band for
// internal planning and advertising it are separate decisions, and the public
// endpoint omits the numbers entirely rather than sending nulls — so a
// candidate cannot tell a withheld salary from an unrecorded one. That only
// works if the recruiter understands which switch they are flipping, which is
// a UI problem, not a server one.
//
// The rest is about not letting a range be saved backwards. The database
// refuses it and the API refuses it; this refuses it early, next to the field,
// so the recruiter is not told after pressing save.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Requisition } from '../api/requisitions';

const updateRequisition = vi.fn();
vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>(
    '../api/requisitions',
  );
  return {
    ...actual,
    updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown,
  };
});

vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

import PostingEditor from '../components/workflow/PostingEditor';

function requisition(over: Partial<Requisition> = {}): Requisition {
  return {
    id: 'req-1',
    title: 'Backend Engineer',
    level: 'mid',
    status: 'open',
    jd_text: null,
    target_hires: null,
    closes_at: null,
    owner_user_id: null,
    from_backfill: false,
    public_apply_enabled: true,
    created_at: '2026-09-01T10:00:00Z',
    total_enrolments: 0,
    hired: 0,
    awaiting_decision: 0,
    funnel: [],
    department: null,
    location: null,
    employment_type: null,
    experience_min_years: null,
    experience_max_years: null,
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    salary_visible: false,
    responsibilities: [],
    required_skills: [],
    nice_to_have_skills: [],
    ...over,
  };
}

function renderEditor(req = requisition()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PostingEditor requisition={req} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  updateRequisition.mockResolvedValue(requisition());
});

describe('PostingEditor — the salary switch', () => {
  it('says the range is private while the switch is off', () => {
    renderEditor(requisition({ salary_min: 1_800_000, salary_max: 3_000_000 }));
    expect(screen.getByText(/never sent to the job board/)).toBeInTheDocument();
  });

  it('stops saying so once it is on', async () => {
    const user = userEvent.setup();
    renderEditor(requisition({ salary_min: 1_800_000 }));
    await user.click(screen.getByRole('checkbox', { name: /Show this range/ }));
    expect(screen.queryByText(/never sent to the job board/)).not.toBeInTheDocument();
  });

  it('sends the switch and the numbers separately', async () => {
    const user = userEvent.setup();
    renderEditor(requisition({ salary_min: 1_800_000, salary_max: 3_000_000 }));
    await user.click(screen.getByRole('checkbox', { name: /Show this range/ }));
    await user.click(screen.getByRole('button', { name: 'Save posting' }));

    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          salary_visible: true,
          salary_min: 1_800_000,
          salary_max: 3_000_000,
        }),
      ),
    );
  });
});

describe('PostingEditor — ranges', () => {
  it('refuses a backwards salary range before it is sent', async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.type(screen.getByLabelText('Minimum salary'), '900');
    await user.type(screen.getByLabelText('Maximum salary'), '100');

    expect(screen.getByText(/Minimum salary cannot be more/)).toBeInTheDocument();
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Save posting' }).disabled,
    ).toBe(true);
    expect(updateRequisition).not.toHaveBeenCalled();
  });

  it('refuses a backwards experience range too', async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.type(screen.getByLabelText('Minimum experience in years'), '8');
    await user.type(screen.getByLabelText('Maximum experience in years'), '3');

    expect(screen.getByText(/Minimum experience cannot be more/)).toBeInTheDocument();
  });

  it('accepts half a range, because job adverts say "3+ years"', async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.type(screen.getByLabelText('Minimum experience in years'), '3');

    expect(screen.queryByText(/cannot be more/)).not.toBeInTheDocument();
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Save posting' }).disabled,
    ).toBe(false);
  });
});

describe('PostingEditor — the lists', () => {
  it('adds an entry and sends it', async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.type(screen.getByLabelText('Required skills'), 'Python{Enter}');
    await user.click(screen.getByRole('button', { name: 'Save posting' }));

    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ required_skills: ['Python'] }),
      ),
    );
  });

  it('removes an entry', async () => {
    const user = userEvent.setup();
    renderEditor(requisition({ required_skills: ['Python', 'Go'] }));
    await user.click(screen.getByRole('button', { name: 'Remove Go' }));
    await user.click(screen.getByRole('button', { name: 'Save posting' }));

    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ required_skills: ['Python'] }),
      ),
    );
  });

  it('stops at the limit the server enforces, rather than letting it 422', () => {
    renderEditor(
      requisition({ responsibilities: Array.from({ length: 20 }, (_, i) => `Item ${i}`) }),
    );
    expect(screen.getByPlaceholderText('Twenty is the limit')).toBeDisabled();
  });
});

describe('PostingEditor — everything is optional', () => {
  it('saves an opening that fills in nothing', async () => {
    // Thousands of openings predate these fields, including backfilled ones
    // created from nothing but a title. A required field would make every one
    // of them unsaveable.
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByRole('button', { name: 'Save posting' }));
    await waitFor(() => expect(updateRequisition).toHaveBeenCalled());
  });

  it('offers "not specified" as an employment type', () => {
    renderEditor();
    expect(screen.getByRole('option', { name: 'Not specified' })).toBeInTheDocument();
  });
});
