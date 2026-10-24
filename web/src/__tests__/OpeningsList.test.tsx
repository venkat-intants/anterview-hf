// The openings list: a dense row per opening with five dots of setup progress,
// the approval, whether it is live, who is in it, and one next action.
//
// The dots are the part that can lie, so most of this file is about them: a dot
// is "done" only when the LIST payload proves it. The list does not carry
// whether a job description is published or a hiring process is live, so those
// two read "open to check" for every opening, however far along it is.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Requisition } from '../api/requisitions';
import {
  attentionRank,
  matchesQuery,
  nextAction,
  provenCount,
  setupDots,
  sortOpenings,
  type DotState,
} from '../lib/openingList';

const m = vi.hoisted(() => ({
  listRequisitions: vi.fn(),
  getBackfillReview: vi.fn(),
  listTeam: vi.fn(),
}));

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    listRequisitions: m.listRequisitions,
    getBackfillReview: m.getBackfillReview,
    listTeam: m.listTeam,
  };
});
vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }));

import Requisitions from '../pages/hr/Requisitions';

function opening(over: Partial<Requisition>): Requisition {
  return {
    id: 'r', title: 'Role', level: 'mid', status: 'open', jd_text: null, target_hires: null,
    closes_at: null, owner_user_id: null, from_backfill: false, public_apply_enabled: false,
    created_at: '2026-09-01T10:00:00Z', total_enrolments: 0, hired: 0, awaiting_decision: 0,
    funnel: [], approval_status: 'draft', department: null, location: null, employment_type: null,
    experience_min_years: null, experience_max_years: null, salary_min: null, salary_max: null,
    salary_currency: null, salary_visible: false, responsibilities: [], required_skills: [],
    nice_to_have_skills: [],
    ...over,
  };
}

const states = (r: Requisition) => setupDots(r).map((d) => d.state);

describe('setup dots are only as sure as the list payload', () => {
  it('a fresh draft: approval and go-live are known to be open, the rest cannot be told', () => {
    expect(states(opening({}))).toEqual(['unknown', 'unknown', 'open', 'open', 'optional']);
  });

  it('waiting for approval is shown as waiting on someone else, not as done or open', () => {
    const dots = setupDots(opening({ approval_status: 'pending_approval' }));
    expect(dots[2].state).toBe('waiting');
    expect(dots[2].text).toBe('Budget and approval: waiting for approval');
  });

  it('a sent-back opening is open, and says why in words', () => {
    const dots = setupDots(opening({ approval_status: 'rejected' }));
    expect(dots[2].state).toBe('open');
    expect(dots[2].text).toMatch(/changes requested/);
  });

  it('approved but switched off: approval is done, go live is not', () => {
    expect(states(opening({ approval_status: 'approved' }))).toEqual([
      'unknown', 'unknown', 'done', 'open', 'optional',
    ]);
  });

  it('approved and accepting applications: still does not claim the JD or the process', () => {
    const r = opening({ approval_status: 'approved', public_apply_enabled: true });
    expect(states(r)).toEqual(['unknown', 'unknown', 'done', 'done', 'optional']);
    expect(setupDots(r)[0].text).toBe('Opening and job description: open to check');
    expect(setupDots(r)[1].text).toBe('Hiring process: open to check');
  });

  it('a paused opening is not accepting applications whatever its switch says', () => {
    const r = opening({ approval_status: 'approved', public_apply_enabled: true, status: 'paused' });
    expect(setupDots(r)[3].state).toBe('open');
  });

  it('documents are never ticked: zero requirements is a legitimate choice', () => {
    for (const a of ['draft', 'pending_approval', 'approved', 'rejected'] as const) {
      expect(setupDots(opening({ approval_status: a }))[4].state).toBe('optional');
    }
  });

  it('counts only what the list proves', () => {
    expect(provenCount(setupDots(opening({ approval_status: 'approved', public_apply_enabled: true })))).toBe(2);
    expect(provenCount(setupDots(opening({})))).toBe(0);
  });
});

describe('has_published_jd / has_published_workflow, when the server sends them', () => {
  type Row = {
    name: string;
    over: Partial<Requisition>;
    role: DotState;
    process: DotState;
    roleText: string;
    processText: string;
  };
  const rows: Row[] = [
    {
      name: 'both present and true',
      over: { has_published_jd: true, has_published_workflow: true },
      role: 'done',
      process: 'done',
      roleText: 'Opening and job description: job description published',
      processText: 'Hiring process: workflow published',
    },
    {
      name: 'both present and false',
      over: { has_published_jd: false, has_published_workflow: false },
      role: 'open',
      process: 'open',
      roleText: 'Opening and job description: no published job description',
      processText: 'Hiring process: no published workflow',
    },
    {
      name: 'mixed (JD yes, process no)',
      over: { has_published_jd: true, has_published_workflow: false },
      role: 'done',
      process: 'open',
      roleText: 'Opening and job description: job description published',
      processText: 'Hiring process: no published workflow',
    },
    {
      name: 'both absent (older backend)',
      over: {},
      role: 'unknown',
      process: 'unknown',
      roleText: 'Opening and job description: open to check',
      processText: 'Hiring process: open to check',
    },
    {
      name: 'only one absent',
      over: { has_published_jd: true },
      role: 'done',
      process: 'unknown',
      roleText: 'Opening and job description: job description published',
      processText: 'Hiring process: open to check',
    },
  ];

  it.each(rows)('dots: $name', ({ over, role, process, roleText, processText }) => {
    const dots = setupDots(opening(over));
    expect(dots[0].state).toBe(role);
    expect(dots[0].text).toBe(roleText);
    expect(dots[1].state).toBe(process);
    expect(dots[1].text).toBe(processText);
  });

  it('proven count includes the server-confirmed stages', () => {
    const r = opening({ has_published_jd: true, has_published_workflow: true, approval_status: 'approved' });
    expect(provenCount(setupDots(r))).toBe(3);
  });

  type Next = { name: string; over: Partial<Requisition>; text: string };
  const nexts: Next[] = [
    { name: 'JD missing comes first', over: { has_published_jd: false, has_published_workflow: false }, text: 'Next: publish the job description' },
    { name: 'then the hiring process', over: { has_published_jd: true, has_published_workflow: false }, text: 'Next: set up the hiring process' },
    { name: 'then approval', over: { has_published_jd: true, has_published_workflow: true }, text: 'Next: submit for approval' },
    {
      name: 'approval already pending: setup gap named, parallelism said',
      over: { has_published_jd: false, approval_status: 'pending_approval' },
      text: 'Next: publish the job description (approval is in progress)',
    },
    {
      name: 'sent back keeps its own instruction',
      over: { has_published_jd: false, approval_status: 'rejected' },
      text: 'Next: revise and resubmit',
    },
    {
      name: 'all proven, approved, not live: go live',
      over: { has_published_jd: true, has_published_workflow: true, approval_status: 'approved' },
      text: 'Next: go live',
    },
    {
      name: 'absent fields never read as missing',
      over: { approval_status: 'approved' },
      text: 'Next: finish setup',
    },
    {
      name: 'absent fields on a draft: today\'s behaviour',
      over: {},
      text: 'Next: submit for approval',
    },
  ];
  it.each(nexts)('next action: $name', ({ over, text }) => {
    expect(nextAction(opening({ id: 'x', ...over }))?.text).toBe(text);
  });
});

describe('the one next action', () => {
  const to =(r: Requisition) => nextAction(r)?.to;

  it('a draft is sent to submit for approval, on the Setup tab', () => {
    const a = nextAction(opening({ id: 'x' }));
    expect(a?.text).toBe('Next: submit for approval');
    expect(a?.to).toBe('/hr/requisitions/x?tab=setup');
  });

  it('a sent-back opening is sent to revise', () => {
    expect(nextAction(opening({ id: 'x', approval_status: 'rejected' }))?.text).toBe(
      'Next: revise and resubmit',
    );
  });

  it('waiting on approval is not an action for you', () => {
    const a = nextAction(opening({ id: 'x', approval_status: 'pending_approval' }));
    expect(a?.text).toBe('Waiting for approval');
    expect(a?.passive).toBe(true);
  });

  it('approved and not live: finish setup', () => {
    expect(nextAction(opening({ id: 'x', approval_status: 'approved' }))?.text).toBe('Next: finish setup');
  });

  it('people waiting on you come before any setup step, and go to the queue', () => {
    const r = opening({ id: 'x', awaiting_decision: 6, approval_status: 'draft' });
    expect(nextAction(r)?.text).toBe('Next: decide on 6');
    expect(to(r)).toBe('/hr/requisitions/x/decisions');
  });

  it('a running opening opens the pipeline; a closed empty one has nothing to do', () => {
    expect(
      to(opening({ id: 'x', approval_status: 'approved', public_apply_enabled: true })),
    ).toBe('/hr/requisitions/x?tab=pipeline');
    expect(nextAction(opening({ status: 'closed' }))).toBeNull();
  });

  it('a paused opening past its closing date asks for a new date, not a reopen (HR-M8)', () => {
    const r = opening({
      id: 'x', status: 'paused', approval_status: 'approved', closes_at: '2020-01-01T00:00:00Z',
    });
    expect(nextAction(r)?.text).toBe('Paused: set a new closing date to reopen, or close it');
    expect(to(r)).toBe('/hr/requisitions/x?tab=setup');
    expect(nextAction(opening({ status: 'paused', approval_status: 'approved' }))?.text).toBe(
      'Paused: reopen or close',
    );
  });

  it('a closed opening with people still waiting still points at the queue', () => {
    expect(nextAction(opening({ id: 'x', status: 'closed', awaiting_decision: 2 }))?.text).toBe(
      'Next: decide on 2',
    );
  });
});

describe('sorting and searching', () => {
  const rows = [
    opening({ id: 'live', title: 'Live', approval_status: 'approved', public_apply_enabled: true, created_at: '2026-09-05T00:00:00Z' }),
    opening({ id: 'closed', title: 'Closed', status: 'closed', created_at: '2026-09-09T00:00:00Z' }),
    opening({ id: 'draft', title: 'Draft', created_at: '2026-09-02T00:00:00Z' }),
    opening({ id: 'wait', title: 'Waiting on you', awaiting_decision: 3, approval_status: 'approved', public_apply_enabled: true }),
    opening({ id: 'sent', title: 'Sent back', approval_status: 'rejected' }),
    opening({ id: 'pend', title: 'Pending', approval_status: 'pending_approval' }),
    opening({ id: 'paused', title: 'Paused', status: 'paused', approval_status: 'approved' }),
  ];

  it('needs-you first: people waiting, then sent back, draft, approved-not-live, pending, running, paused, closed', () => {
    expect(sortOpenings(rows, 'needs-you').map((r) => r.id)).toEqual([
      'wait', 'sent', 'draft', 'pend', 'live', 'paused', 'closed',
    ]);
  });

  it('ranks, lowest first', () => {
    expect(attentionRank(rows[3])).toBe(0);
    expect(attentionRank(rows[1])).toBe(7);
  });

  it('newest first and title order', () => {
    expect(sortOpenings(rows, 'newest')[0].id).toBe('closed');
    expect(sortOpenings(rows, 'title').map((r) => r.title)[0]).toBe('Closed');
  });

  it('matches title, department and location, ignoring case', () => {
    const r = opening({ title: 'Backend Engineer', department: 'Platform', location: 'Hyderabad' });
    expect(matchesQuery(r, 'backend')).toBe(true);
    expect(matchesQuery(r, 'PLATFORM')).toBe(true);
    expect(matchesQuery(r, 'hyder')).toBe(true);
    expect(matchesQuery(r, 'nurse')).toBe(false);
    expect(matchesQuery(r, '  ')).toBe(true);
  });
});

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Requisitions />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  m.getBackfillReview.mockResolvedValue({ backfilled_requisitions: [], merge_candidates: [] });
  m.listTeam.mockResolvedValue([]);
});

describe('the list on screen', () => {
  const ROWS = [
    opening({ id: 'a', title: 'Live Role', approval_status: 'approved', public_apply_enabled: true, total_enrolments: 40, hired: 2, awaiting_decision: 6, department: 'Support', location: 'Hyderabad' }),
    opening({ id: 'b', title: 'Fresh Role', approval_status: 'draft', department: 'Finance' }),
    opening({ id: 'c', title: 'Sent Back Role', approval_status: 'rejected' }),
  ];

  it('is a table with a header row and one row per opening', async () => {
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    const table = await screen.findByRole('table', { name: 'Openings' });
    for (const h of ['Opening', 'Setup', 'Approval', 'Status', 'Candidates', 'Next']) {
      expect(within(table).getByRole('columnheader', { name: h })).toBeTruthy();
    }
    expect(screen.getAllByTestId('opening-row')).toHaveLength(3);
  });

  it('puts the opening with people waiting first, and says how many', async () => {
    m.listRequisitions.mockResolvedValue([ROWS[1], ROWS[2], ROWS[0]]);
    renderList();
    const rows = await screen.findAllByTestId('opening-row');
    expect(rows[0].textContent).toMatch(/Live Role/);
    expect(within(rows[0]).getByText('6 awaiting you')).toBeTruthy();
  });

  it('shows five dots per row, each with its state in words', async () => {
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    const rows = await screen.findAllByTestId('opening-row');
    const live = rows.find((r) => r.textContent?.includes('Live Role')) as HTMLElement;
    const dots = within(within(live).getByTestId('setup-dots')).getAllByRole('listitem');
    expect(dots).toHaveLength(5);
    expect(dots.map((d) => d.getAttribute('data-state'))).toEqual([
      'unknown', 'unknown', 'done', 'done', 'optional',
    ]);
    // never colour alone: every dot carries its words
    expect(dots[0].textContent).toMatch(/Opening and job description: open to check/);
    expect(dots[2].textContent).toMatch(/Budget and approval: approved/);
    expect(within(live).getByTestId('setup-dots').getAttribute('aria-label')).toMatch(/2 of 4/);
  });

  it('links each row to the opening, and the next action to the right place', async () => {
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    const rows = await screen.findAllByTestId('opening-row');
    const fresh = rows.find((r) => r.textContent?.includes('Fresh Role')) as HTMLElement;
    expect(within(fresh).getByRole('link', { name: 'Fresh Role' }).getAttribute('href')).toBe('/hr/requisitions/b');
    const next = within(fresh).getByRole('link', { name: /Next: submit for approval/ });
    expect(next.getAttribute('href')).toBe('/hr/requisitions/b?tab=setup');

    const live = rows.find((r) => r.textContent?.includes('Live Role')) as HTMLElement;
    expect(within(live).getByRole('link', { name: /Next: decide on 6/ }).getAttribute('href')).toBe(
      '/hr/requisitions/a/decisions',
    );
  });

  it('searches by title, department and location', async () => {
    const user = userEvent.setup();
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    await screen.findAllByTestId('opening-row');
    await user.type(screen.getByRole('searchbox', { name: 'Search openings' }), 'finance');
    const rows = screen.getAllByTestId('opening-row');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toMatch(/Fresh Role/);

    await user.clear(screen.getByRole('searchbox', { name: 'Search openings' }));
    await user.type(screen.getByRole('searchbox', { name: 'Search openings' }), 'zzz');
    expect(screen.queryAllByTestId('opening-row')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Clear the search' }));
    expect(screen.getAllByTestId('opening-row')).toHaveLength(3);
  });

  it('re-sorts from the sort control', async () => {
    const user = userEvent.setup();
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    await screen.findAllByTestId('opening-row');
    await user.selectOptions(screen.getByLabelText('Sort'), 'title');
    const titles = screen.getAllByTestId('opening-row').map((r) => r.textContent ?? '');
    expect(titles[0]).toMatch(/Fresh Role/);
    expect(titles[2]).toMatch(/Sent Back Role/);
  });

  it('asks the server for the status the tab names', async () => {
    const user = userEvent.setup();
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    await screen.findAllByTestId('opening-row');
    expect(m.listRequisitions).toHaveBeenLastCalledWith({ status: 'open', limit: 200 });
    await user.click(screen.getByRole('tab', { name: 'Paused' }));
    await screen.findAllByTestId('opening-row');
    expect(m.listRequisitions).toHaveBeenLastCalledWith({ status: 'paused', limit: 200 });
    await user.click(screen.getByRole('tab', { name: 'All' }));
    await screen.findAllByTestId('opening-row');
    expect(m.listRequisitions).toHaveBeenLastCalledWith({ limit: 200 });
  });

  it('sums the openings in view, and explains the dots', async () => {
    m.listRequisitions.mockResolvedValue(ROWS);
    renderList();
    const rollup = await screen.findByTestId('openings-rollup');
    expect(rollup.textContent).toMatch(/6 awaiting you · 40 candidates · 2 hired · 1 accepting applications/);
    expect(screen.getByText(/open to check - the list cannot confirm it/)).toBeTruthy();
  });

  it('is quiet while loading and plain when it fails', async () => {
    m.listRequisitions.mockReturnValue(new Promise(() => undefined));
    const { unmount } = renderList();
    expect(await screen.findByText('Loading openings…')).toBeTruthy();
    unmount();

    m.listRequisitions.mockRejectedValue(new Error('boom'));
    renderList();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load openings.');
    expect(alert).toHaveTextContent('boom');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('the list is honest about what it loaded', () => {
  it('names an empty tab in words (HR-L2)', async () => {
    const user = userEvent.setup();
    m.listRequisitions.mockResolvedValue([]);
    renderList();
    expect(await screen.findByText('No open roles')).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: 'All' }));
    expect(await screen.findByText('No openings yet')).toBeTruthy();
    expect(screen.queryByText(/Nothing all/)).toBeNull();
  });

  it('says when it is showing only the first page (HR-M7)', async () => {
    m.listRequisitions.mockResolvedValue(
      Array.from({ length: 200 }, (_, i) => opening({ id: `r${i}`, title: `Role ${i}` })),
    );
    renderList();
    expect(await screen.findByTestId('openings-truncated')).toBeTruthy();
    expect(screen.getByText('200+ openings')).toBeTruthy();
  });

  it('says nothing extra when everything fitted', async () => {
    m.listRequisitions.mockResolvedValue([opening({ id: 'a' })]);
    renderList();
    await screen.findAllByTestId('opening-row');
    expect(screen.queryByTestId('openings-truncated')).toBeNull();
  });
});
