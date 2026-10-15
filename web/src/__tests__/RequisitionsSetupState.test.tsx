// Openings list: each row shows where the opening stands on its way live, from
// fields the list already carries. And GovernancePanel's two halves, which the
// hub places in different stages.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Requisition } from '../api/requisitions';

const m = vi.hoisted(() => ({
  listRequisitions: vi.fn(),
  getBackfillReview: vi.fn(),
  listTeam: vi.fn(),
  getPublishSchedule: vi.fn(),
}));

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    listRequisitions: m.listRequisitions,
    getBackfillReview: m.getBackfillReview,
    listTeam: m.listTeam,
    getPublishSchedule: m.getPublishSchedule,
  };
});
vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import Requisitions from '../pages/hr/Requisitions';
import GovernancePanel from '../components/workflow/GovernancePanel';

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

function wrap(ui: JSX.Element) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  m.getBackfillReview.mockResolvedValue({ backfilled_requisitions: [], merge_candidates: [] });
  m.listTeam.mockResolvedValue([]);
  m.getPublishSchedule.mockResolvedValue({
    requisition_id: 'r', publish_at: null, published_at: null, public_apply_enabled: false, tolerance: 'About a minute.',
  });
});

describe('the openings list shows real setup state', () => {
  it('names the approval and whether the opening is live, per row', async () => {
    m.listRequisitions.mockResolvedValue([
      opening({ id: 'a', title: 'Live Role', approval_status: 'approved', public_apply_enabled: true }),
      opening({ id: 'b', title: 'Waiting Role', approval_status: 'pending_approval' }),
      opening({ id: 'c', title: 'Sent Back Role', approval_status: 'rejected' }),
      opening({ id: 'd', title: 'Fresh Role', approval_status: 'draft' }),
      opening({ id: 'e', title: 'Paused Role', approval_status: 'approved', status: 'paused', public_apply_enabled: true }),
    ]);
    wrap(<Requisitions />);
    const rows = await screen.findAllByTestId('opening-row');
    const row = (title: string) => rows.find((r) => r.textContent?.includes(title)) as HTMLElement;

    const live = row('Live Role');
    expect(within(live).getByTestId('opening-approval').textContent).toBe('Approved');
    expect(within(live).getByText('accepting applications')).toBeTruthy();
    expect(within(live).queryByText('not live')).toBeNull();

    expect(within(row('Waiting Role')).getByTestId('opening-approval').textContent).toBe('Awaiting approval');
    expect(within(row('Waiting Role')).getByText('not live · not approved yet')).toBeTruthy();
    expect(within(row('Sent Back Role')).getByTestId('opening-approval').textContent).toBe('Changes requested');
    expect(within(row('Fresh Role')).getByTestId('opening-approval').textContent).toBe('Not submitted');

    // a paused opening is not "live", whatever its switch says, and is not
    // labelled "not live" either: its status tag already says why
    const paused = row('Paused Role');
    expect(within(paused).queryByText('accepting applications')).toBeNull();
    expect(within(paused).queryByText('not live')).toBeNull();
  });

  it('does not call an opening live once its closing date has passed, or with no live workflow', async () => {
    // QA ANTHIRE-002/003: the server's public gate refuses both; the list
    // used to say "accepting applications" for them.
    m.listRequisitions.mockResolvedValue([
      opening({
        id: 'x', title: 'Expired Role', approval_status: 'approved', public_apply_enabled: true,
        closes_at: '2020-01-01T00:00:00Z',
      }),
      opening({
        id: 'y', title: 'Draft Flow Role', approval_status: 'approved', public_apply_enabled: true,
        has_published_workflow: false,
      }),
    ]);
    wrap(<Requisitions />);
    const rows = await screen.findAllByTestId('opening-row');
    const row = (title: string) => rows.find((r) => r.textContent?.includes(title)) as HTMLElement;

    expect(within(row('Expired Role')).queryByText('accepting applications')).toBeNull();
    expect(within(row('Expired Role')).getByText('not live · closing date passed')).toBeTruthy();
    expect(within(row('Expired Role')).getByText(/closing date passed: extend it or close/i)).toBeTruthy();
    expect(within(row('Draft Flow Role')).queryByText('accepting applications')).toBeNull();
    expect(within(row('Draft Flow Role')).getByText('not live · no live workflow')).toBeTruthy();
  });

  it('has exactly one primary: New opening', async () => {
    m.listRequisitions.mockResolvedValue([opening({ id: 'a', title: 'Only Role' })]);
    wrap(<Requisitions />);
    await screen.findAllByTestId('opening-row');
    expect(screen.getAllByRole('button', { name: 'New opening' })).toHaveLength(1);
  });

  it('keeps the primary in the header when the list is empty', async () => {
    m.listRequisitions.mockResolvedValue([]);
    wrap(<Requisitions />);
    const buttons = await screen.findAllByRole('button', { name: 'New opening' });
    const primaries = buttons.filter((b) => !/border-input/.test(b.className));
    expect(primaries).toHaveLength(1);
    expect(primaries[0].getAttribute('data-testid')).toBe('new-opening');
  });
});

describe('GovernancePanel sections', () => {
  it('approval: budget and submit, and no request for the schedule', async () => {
    wrap(<GovernancePanel requisition={opening({ id: 'r' })} section="approval" />);
    expect(screen.getByRole('button', { name: 'Submit for approval' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save budget' })).toBeTruthy();
    expect(screen.queryByLabelText('Go live at')).toBeNull();
    await new Promise((r) => setTimeout(r, 20));
    expect(m.getPublishSchedule).not.toHaveBeenCalled();
  });

  it('schedule: only the schedule, with the reason beside a disabled control', async () => {
    wrap(<GovernancePanel requisition={opening({ id: 'r' })} section="schedule" />);
    expect(await screen.findByLabelText('Go live at')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Submit for approval' })).toBeNull();
    expect(screen.queryByLabelText('Budget amount')).toBeNull();
    const input = screen.getByLabelText('Go live at');
    expect(document.getElementById(input.getAttribute('aria-describedby') as string)?.textContent).toMatch(
      /needs budget approval/i,
    );
  });

  it('default: both, as before', async () => {
    wrap(<GovernancePanel requisition={opening({ id: 'r', approval_status: 'approved' })} />);
    expect(screen.getByLabelText('Budget amount')).toBeTruthy();
    expect(await screen.findByLabelText('Go live at')).toBeTruthy();
    expect(screen.getByLabelText('Go live at').getAttribute('aria-describedby')).toBeNull();
  });
});
