// The opening's query keys.
//
// The bug this pins: three panels wrote `['requisition', id]` while the page
// read `['hr', 'requisition', id]`, so "Submit for approval" refreshed nothing
// and the approval pill stayed on "Not submitted". The probe below reads the
// key as the PAGE spells it (a literal, deliberately not the builder), so the
// test fails if the panel and the page ever disagree again.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import type { PublishSchedule, Requisition } from '../api/requisitions';
import { queryKeys } from '../lib/queryKeys';
import governanceSrc from '../components/workflow/GovernancePanel.tsx?raw';
import jdPanelSrc from '../components/workflow/JdVersionPanel.tsx?raw';
import postingSrc from '../components/workflow/PostingEditor.tsx?raw';
import builderSrc from '../pages/hr/WorkflowBuilder.tsx?raw';

const getPublishSchedule = vi.fn();
const submitRequisitionForApproval = vi.fn();
const updateRequisition = vi.fn();
const getRequisitionFn = vi.fn();

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    getPublishSchedule: (...a: unknown[]) => getPublishSchedule(...a) as unknown,
    submitRequisitionForApproval: (...a: unknown[]) => submitRequisitionForApproval(...a) as unknown,
    updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown,
  };
});
vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import GovernancePanel from '../components/workflow/GovernancePanel';

function requisition(over: Partial<Requisition> = {}): Requisition {
  return {
    id: 'req-1',
    title: 'Backend Engineer',
    level: 'mid',
    status: 'open',
    jd_text: null,
    target_hires: 3,
    closes_at: null,
    owner_user_id: null,
    from_backfill: false,
    public_apply_enabled: false,
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
    approval_status: 'draft',
    ...over,
  };
}

const schedule: PublishSchedule = {
  requisition_id: 'req-1',
  publish_at: null,
  published_at: null,
  public_apply_enabled: false,
  tolerance: 'about a minute',
};

// What WorkflowBuilder does: read the opening under its own key and show the
// approval status from it.
function PageProbe(): JSX.Element {
  const q = useQuery({
    queryKey: ['hr', 'requisition', 'req-1'],
    queryFn: () => getRequisitionFn() as Promise<Requisition>,
  });
  return <p data-testid="page-status">{q.data?.approval_status ?? 'loading'}</p>;
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 60_000 } },
  });
  render(
    <QueryClientProvider client={client}>
      <PageProbe />
      <GovernancePanel requisition={requisition()} />
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  vi.clearAllMocks();
  getPublishSchedule.mockResolvedValue(schedule);
  // reset, not clear: a Once value left over from an earlier test would be served first.
  getRequisitionFn.mockReset();
  getRequisitionFn
    .mockResolvedValueOnce(requisition({ approval_status: 'draft' }))
    .mockResolvedValue(requisition({ approval_status: 'pending_approval' }));
  submitRequisitionForApproval.mockResolvedValue(requisition({ approval_status: 'pending_approval' }));
});

describe('queryKeys.requisition', () => {
  it('is the key the page reads', () => {
    expect(queryKeys.requisition('req-1')).toEqual(['hr', 'requisition', 'req-1']);
  });

  it('is a prefix of the sub-resource keys, so one invalidation covers them', () => {
    const base = queryKeys.requisition('req-1');
    expect(queryKeys.requisitionDocumentRequirements('req-1').slice(0, 3)).toEqual(base);
    expect(queryKeys.requisitionReviewEnrolments('req-1').slice(0, 3)).toEqual(base);
  });
});

describe('submitting for approval in GovernancePanel', () => {
  it('refreshes the key the page reads, so the status pill updates', async () => {
    setup();
    await waitFor(() => expect(screen.getByTestId('page-status')).toHaveTextContent('draft'));

    await userEvent.click(await screen.findByRole('button', { name: /Submit for approval/ }));

    await waitFor(() => expect(screen.getByTestId('page-status')).toHaveTextContent('pending_approval'));
    expect(getRequisitionFn).toHaveBeenCalledTimes(2);
  });

  it('invalidates a prefix of the shared key, so the page and the openings list refresh (HR-L12)', async () => {
    const client = setup();
    const spy = vi.spyOn(client, 'invalidateQueries');
    await userEvent.click(await screen.findByRole('button', { name: /Submit for approval/ }));
    await waitFor(() => expect(submitRequisitionForApproval).toHaveBeenCalled());
    const target = queryKeys.requisition('req-1');
    await waitFor(() => {
      const keys = spy.mock.calls.map((c) => (c[0] as { queryKey?: readonly unknown[] })?.queryKey);
      expect(
        keys.some(
          (k) => Array.isArray(k) && k.length <= target.length && k.every((p, i) => p === target[i]),
        ),
      ).toBe(true);
    });
  });

  it('writes a saved budget under the key the page reads', async () => {
    updateRequisition.mockResolvedValue(requisition({ budget_amount: 500000 }));
    const client = setup();
    await userEvent.click(await screen.findByRole('button', { name: /Save budget/ }));
    await waitFor(() => expect(updateRequisition).toHaveBeenCalled());
    await waitFor(() =>
      expect(client.getQueryData<Requisition>(['hr', 'requisition', 'req-1'])?.budget_amount).toBe(500000),
    );
  });
});

describe('no file spells the short key any more', () => {
  it.each([
    ['GovernancePanel', governanceSrc],
    ['JdVersionPanel', jdPanelSrc],
    ['PostingEditor', postingSrc],
    ['WorkflowBuilder', builderSrc],
  ])('%s goes through queryKeys.requisition or the HR-wide invalidation', (_name, src) => {
    expect(src).not.toMatch(/\[\s*'requisition'\s*,/);
    expect(
      src.includes('queryKeys.requisition(') || src.includes('invalidateAfterHrMutation('),
    ).toBe(true);
  });
});
