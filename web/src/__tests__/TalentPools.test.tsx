// TalentPools (/hr/pools, /hr/pools/:poolId) — PH5-E3.
//
// `api/pools.ts` is mocked via `importActual` so the real `poolErrorMessage`
// and `isStaleEvidenceUnreviewed` run (the CorpusDocuments.test.tsx
// precedent) — only the network calls themselves are doubles.
//
// THE THREE PROPERTIES THIS FILE MUST PIN, because a test that cannot fail
// is worse than no test (see the mutation-check notes on each):
//   1. An INELIGIBLE member offers ONLY Remove.
//   2. Deleting a pool names how many members go with it.
//   3. Inviting a member whose evidence needs review is a two-step
//      server-driven gate (422 stale_evidence_unreviewed → acknowledge →
//      retry), never a client-side guess.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { PoolMemberOut, PoolOut } from '../api/pools';
import type { Applicant } from '../api/applicants';

const api = {
  listPools: vi.fn(),
  createPool: vi.fn(),
  getPool: vi.fn(),
  updatePool: vi.fn(),
  deletePool: vi.fn(),
  addPoolMembers: vi.fn(),
  removePoolMember: vi.fn(),
  markEvidenceReviewed: vi.fn(),
  inviteMember: vi.fn(),
};
vi.mock('../api/pools', async () => {
  const actual = await vi.importActual<typeof import('../api/pools')>('../api/pools');
  return {
    ...actual,
    listPools: (...a: unknown[]) => api.listPools(...a) as unknown,
    createPool: (...a: unknown[]) => api.createPool(...a) as unknown,
    getPool: (...a: unknown[]) => api.getPool(...a) as unknown,
    updatePool: (...a: unknown[]) => api.updatePool(...a) as unknown,
    deletePool: (...a: unknown[]) => api.deletePool(...a) as unknown,
    addPoolMembers: (...a: unknown[]) => api.addPoolMembers(...a) as unknown,
    removePoolMember: (...a: unknown[]) => api.removePoolMember(...a) as unknown,
    markEvidenceReviewed: (...a: unknown[]) => api.markEvidenceReviewed(...a) as unknown,
    inviteMember: (...a: unknown[]) => api.inviteMember(...a) as unknown,
  };
});

const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
}));

const listApplicants = vi.fn();
vi.mock('../api/applicants', () => ({
  listApplicants: (...a: unknown[]) => listApplicants(...a) as unknown,
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

import TalentPools from '../pages/hr/TalentPools';

function pool(over: Partial<PoolOut> = {}): PoolOut {
  return {
    id: 'pool-1',
    name: 'Fitters, Vizag',
    member_count: 2,
    created_by_name: 'Priya Nair',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-10T00:00:00Z',
    ...over,
  };
}

function member(over: Partial<PoolMemberOut> = {}): PoolMemberOut {
  return {
    member_id: 'mem-1',
    applicant_id: 'app-1',
    full_name: 'Asha K',
    current_title: 'Fitter',
    current_company: 'Acme',
    source: 'manual',
    added_at: '2026-09-05T00:00:00Z',
    added_by_name: 'Priya Nair',
    eligible: true,
    ...over,
  };
}

function applicant(over: Partial<Applicant> = {}): Applicant {
  return {
    id: 'app-9',
    full_name: 'Ravi Kumar',
    email: 'ravi@example.com',
    target_job_title: 'Welder',
    target_level: 'mid',
    status: 'new',
    ats_overall: null,
    ats_breakdown: null,
    ats_strengths: null,
    ats_concerns: null,
    ats_recommendation: null,
    ats_summary: null,
    created_at: '2026-09-01T00:00:00Z',
    ...over,
  };
}

function renderPage(path = '/hr/pools') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/hr/pools" element={<TalentPools />} />
          <Route path="/hr/pools/:poolId" element={<TalentPools />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listPools.mockResolvedValue({ pools: [], limits: { max_per_company: 100, max_members: 2000 } });
  listRequisitions.mockResolvedValue([{ id: 'req-1', title: 'Maintenance Fitter' }]);
  listApplicants.mockResolvedValue([]);
});

// ---------------------------------------------------------------------------
// List: empty state, create, rename, archive, delete
// ---------------------------------------------------------------------------

describe('TalentPools — list', () => {
  it('shows an empty state when there are no pools', async () => {
    renderPage();
    expect(await screen.findByText('No pools yet')).toBeInTheDocument();
  });

  it('lists a pool with its member count, creator and updated date', async () => {
    api.listPools.mockResolvedValue({
      pools: [pool()],
      limits: { max_per_company: 100, max_members: 2000 },
    });
    renderPage();
    expect(await screen.findByText('Fitters, Vizag')).toBeInTheDocument();
    const row = screen.getByRole('table');
    expect(within(row).getByText('2')).toBeInTheDocument();
    expect(within(row).getByText('Priya Nair')).toBeInTheDocument();
  });

  it('creates a pool with the trimmed name and description', async () => {
    const user = userEvent.setup();
    api.createPool.mockResolvedValue(pool({ id: 'pool-2', name: 'New pool' }));
    renderPage();
    await screen.findByText('No pools yet');

    await user.click(screen.getByRole('button', { name: 'New pool' }));
    await user.type(screen.getByLabelText('Name'), '  New pool  ');
    await user.click(screen.getByRole('button', { name: 'Create pool' }));

    await waitFor(() =>
      expect(api.createPool).toHaveBeenCalledWith({ name: 'New pool', description: null }),
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('Pool created'));
  });

  it('renames a pool', async () => {
    const user = userEvent.setup();
    api.listPools.mockResolvedValue({
      pools: [pool()],
      limits: { max_per_company: 100, max_members: 2000 },
    });
    api.updatePool.mockResolvedValue(pool({ name: 'Fitters, Renamed' }));
    renderPage();
    await screen.findByText('Fitters, Vizag');

    await user.click(screen.getByRole('button', { name: 'Rename' }));
    const input = screen.getByLabelText('Rename Fitters, Vizag');
    await user.clear(input);
    await user.type(input, 'Fitters, Renamed');
    await user.keyboard('{Enter}');

    await waitFor(() =>
      expect(api.updatePool).toHaveBeenCalledWith('pool-1', { name: 'Fitters, Renamed' }),
    );
  });

  it('archives a pool, and the same control restores it', async () => {
    const user = userEvent.setup();
    api.listPools.mockResolvedValue({
      pools: [pool()],
      limits: { max_per_company: 100, max_members: 2000 },
    });
    api.updatePool.mockResolvedValue(pool({ archived_at: '2026-09-20T00:00:00Z' }));
    renderPage();
    await screen.findByText('Fitters, Vizag');

    await user.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() =>
      expect(api.updatePool).toHaveBeenCalledWith('pool-1', { archived: true }),
    );
  });

  it('names how many members go with the pool before deleting it', async () => {
    const user = userEvent.setup();
    api.listPools.mockResolvedValue({
      pools: [pool({ member_count: 7 })],
      limits: { max_per_company: 100, max_members: 2000 },
    });
    renderPage();
    await screen.findByText('Fitters, Vizag');

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    // MUTATION CHECK: this line is the one this test exists to pin. Deleting
    // the `p.member_count` interpolation from TalentPools.tsx's confirm text
    // (replacing it with a generic "Are you sure?") turns this query red.
    // Verified by hand: broken, watched fail, restored, watched pass again.
    expect(screen.getByText(/Delete this pool and its 7 members\?/)).toBeInTheDocument();

    api.deletePool.mockResolvedValue({ deleted: true, members_removed: 7 });
    const group = screen.getByRole('group', { name: /confirm deleting/i });
    await user.click(within(group).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(api.deletePool).toHaveBeenCalledWith('pool-1'));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Pool deleted, along with 7 members'),
    );
  });
});

// ---------------------------------------------------------------------------
// Detail — members
// ---------------------------------------------------------------------------

describe('TalentPools — pool detail', () => {
  it('shows an empty state when the pool has no members', async () => {
    api.getPool.mockResolvedValue({ pool: pool(), members: [] });
    renderPage('/hr/pools/pool-1');
    expect(await screen.findByText(/No members yet/)).toBeInTheDocument();
  });

  it('shows a Manual source chip for a manually added member', async () => {
    api.getPool.mockResolvedValue({ pool: pool(), members: [member({ source: 'manual' })] });
    renderPage('/hr/pools/pool-1');
    expect(await screen.findByText('Asha K')).toBeInTheDocument();
    expect(screen.getByText('Manual')).toBeInTheDocument();
  });

  it('shows a "From rediscovery" source chip for a rediscovery-sourced member', async () => {
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [member({ source: 'rediscovery' })],
    });
    renderPage('/hr/pools/pool-1');
    expect(await screen.findByText('From rediscovery')).toBeInTheDocument();
  });

  it('shows "Evidence not re-checked" until someone marks it reviewed', async () => {
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [member({ evidence_freshness: 'ageing' })],
    });
    renderPage('/hr/pools/pool-1');
    expect(await screen.findByText('Evidence not re-checked')).toBeInTheDocument();
    expect(screen.getByText('Ageing')).toBeInTheDocument();
  });

  it('shows who re-checked the evidence and when, once it has been', async () => {
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [
        member({
          evidence_freshness: 'ageing',
          evidence_reviewed_at: '2026-09-24T00:00:00Z',
          evidence_reviewed_by_name: 'Priya Nair',
        }),
      ],
    });
    renderPage('/hr/pools/pool-1');
    expect(await screen.findByText(/Evidence re-checked by Priya Nair on/)).toBeInTheDocument();
    expect(screen.queryByText('Evidence not re-checked')).not.toBeInTheDocument();
  });

  it('marks evidence reviewed on request', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [member({ evidence_freshness: 'stale' })],
    });
    api.markEvidenceReviewed.mockResolvedValue(
      member({ evidence_freshness: 'stale', evidence_reviewed_at: '2026-09-26T00:00:00Z' }),
    );
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: 'Mark evidence reviewed' }));
    await waitFor(() => expect(api.markEvidenceReviewed).toHaveBeenCalledWith('pool-1', 'mem-1'));
  });

  // -------------------------------------------------------------------------
  // The load-bearing rule: an ineligible member offers ONLY Remove.
  //
  // MUTATION CHECK performed by hand: temporarily changed the `m.eligible`
  // guard in TalentPools.tsx's action cell to `true` unconditionally (so an
  // ineligible row also rendered Review evidence / Mark evidence reviewed /
  // Invite). This test went red (found the extra buttons). Reverted; green
  // again. Recorded here rather than left to be taken on faith.
  // -------------------------------------------------------------------------
  it('offers an ineligible member ONLY Remove — no evidence, no invite, no applicant link', async () => {
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [
        member({
          eligible: false,
          ineligible_reason: 'consent_expired',
          enrolment_id: 'enr-1',
          match_reason: {
            frozen_at: '2026-09-01T00:00:00Z',
            score: 60,
            explained: true,
            breakdown: {
              semantic: 0.5,
              lexical: 0.2,
              evidence_boost: 0,
              coverage: 0,
              freshness_factor: 0,
              covered_competencies: [],
              semantic_available: true,
              weights: { semantic: 0.7, lexical: 0.3, evidence_max: 0.15 },
            },
            evidence_freshness: 'stale',
            requires_review: true,
            review_reasons: ['evidence_stale'],
            why: [],
          },
        }),
      ],
    });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    expect(screen.getByText('Consent expired')).toBeInTheDocument();
    // Name is plain text, not a link, for an ineligible row.
    expect(screen.queryByRole('link', { name: 'Asha K' })).not.toBeInTheDocument();

    const row = screen.getByText('Asha K').closest('tr') as HTMLElement;
    expect(within(row).getByRole('button', { name: /remove/i })).toBeInTheDocument();
    expect(within(row).queryByRole('link', { name: /review evidence/i })).not.toBeInTheDocument();
    expect(
      within(row).queryByRole('button', { name: /mark evidence reviewed/i }),
    ).not.toBeInTheDocument();
    expect(
      within(row).queryByRole('button', { name: /invite to an opening/i }),
    ).not.toBeInTheDocument();
  });

  it('removes a member after a confirm, with an optional reason', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [member()] });
    api.removePoolMember.mockResolvedValue({ removed: true });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: /remove/i }));
    const group = screen.getByRole('group', { name: /confirm removing/i });
    await user.type(within(group).getByLabelText(/reason for removing/i), 'Duplicate');
    await user.click(within(group).getByRole('button', { name: 'Remove' }));

    await waitFor(() =>
      expect(api.removePoolMember).toHaveBeenCalledWith('pool-1', 'mem-1', 'Duplicate'),
    );
  });

  it('cancels a remove without calling the API', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [member()] });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: /remove/i }));
    const group = screen.getByRole('group', { name: /confirm removing/i });
    await user.click(within(group).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('group', { name: /confirm removing/i })).not.toBeInTheDocument();
    expect(api.removePoolMember).not.toHaveBeenCalled();
  });

  it('opens the frozen match-reason snapshot, labelled as one, never re-computed', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [
        member({
          source: 'rediscovery',
          match_reason: {
            frozen_at: '2026-09-24T00:00:00Z',
            score: 69,
            explained: true,
            breakdown: {
              semantic: 0.68,
              lexical: 0.4,
              evidence_boost: 0.09,
              coverage: 1,
              freshness_factor: 0.6,
              covered_competencies: ['problem_solving'],
              semantic_available: true,
              weights: { semantic: 0.7, lexical: 0.3, evidence_max: 0.15 },
            },
            evidence_freshness: 'ageing',
            requires_review: true,
            review_reasons: ['evidence_ageing'],
            why: [
              {
                signal: 'interviewer_scorecard',
                contribution: 4,
                explainable: true,
                competency_id: 'problem_solving',
                competency: 'Fault Diagnosis',
                score: 4,
                of: 5,
                freshness: 'ageing',
                citation: { kind: 'interviewer_scorecard', id: 'sc-1', label: 'Round 2' },
              },
            ],
          },
        }),
      ],
    });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: /why this match/i }));
    expect(
      screen.getByText(/Added from a rediscovery search on/),
    ).toBeInTheDocument();
    // Facts, not a link — the frozen citation carries no href (design §6.4).
    expect(screen.getByText('Round 2')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Round 2' })).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // A manually-added member has no computed match, so the frozen-snapshot
  // panel must be ABSENT for it, not an empty shell.
  //
  // MUTATION CHECK performed by hand: temporarily changed `MemberRow`'s
  // `hasSnapshot` guard from `Boolean(m.match_reason)` to `true`
  // unconditionally. This test went red (found a "Why this match?" toggle
  // with nothing behind it). Reverted; green again.
  // -------------------------------------------------------------------------
  it('renders no frozen-snapshot panel for a manually added member (no match_reason)', async () => {
    api.getPool.mockResolvedValue({
      pool: pool(),
      members: [member({ source: 'manual', match_reason: undefined })],
    });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    expect(screen.queryByRole('button', { name: /why this match/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Added from a rediscovery search on/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Invite — the criterion-14 gate reacts to the server's 422, never guesses it
// ---------------------------------------------------------------------------

describe('TalentPools — invite to an opening', () => {
  it('invites directly when the server does not ask for an acknowledgement', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [member()] });
    api.inviteMember.mockResolvedValue({
      enrolment_id: 'enr-1',
      requisition_id: 'req-1',
      already_enrolled: false,
    });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: 'Invite to an opening' }));
    await user.selectOptions(await screen.findByLabelText('Opening'), 'req-1');
    await user.click(screen.getByRole('button', { name: 'Invite' }));

    await waitFor(() =>
      expect(api.inviteMember).toHaveBeenCalledWith('pool-1', 'mem-1', {
        requisitionId: 'req-1',
        acknowledgedStale: false,
      }),
    );
  });

  it('asks for an acknowledgement on a 422, then retries with it — never pre-guessed client-side', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [member()] });
    api.inviteMember
      .mockRejectedValueOnce(
        new ApiError('This evidence has not been reviewed recently.', 422, {
          failure_code: 'stale_evidence_unreviewed',
        }),
      )
      .mockResolvedValueOnce({ enrolment_id: 'enr-1', requisition_id: 'req-1', already_enrolled: false });
    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: 'Invite to an opening' }));
    await user.selectOptions(await screen.findByLabelText('Opening'), 'req-1');
    await user.click(screen.getByRole('button', { name: 'Invite' }));

    expect(
      await screen.findByText(/This evidence has not been reviewed recently/),
    ).toBeInTheDocument();
    // MUTATION CHECK performed by hand: temporarily hard-coded `needsAck` to
    // `false` after the catch (i.e. swallowed the 422 silently) — this
    // assertion went red (no acknowledgement text ever appeared). Reverted.
    expect(api.inviteMember).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Invite anyway' }));
    await waitFor(() =>
      expect(api.inviteMember).toHaveBeenLastCalledWith('pool-1', 'mem-1', {
        requisitionId: 'req-1',
        acknowledgedStale: true,
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// Add candidates manually — criterion 2 ("candidates can be added to a pool
// manually"). Before this control existed, `addPoolMembers`'s only call site
// anywhere in the app was Rediscovery.tsx, hard-coded to `source:
// 'rediscovery'` — so this is the only place source: 'manual' is ever sent.
// ---------------------------------------------------------------------------

describe('TalentPools — add candidates manually', () => {
  it('searches this company\'s applicants, selects one, and adds with source manual and NO match_reason', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [] });
    listApplicants.mockResolvedValue([applicant({ id: 'app-9', full_name: 'Ravi Kumar' })]);
    api.addPoolMembers.mockResolvedValue({ added: ['app-9'], skipped: [] });

    renderPage('/hr/pools/pool-1');
    await screen.findByText(/No members yet/);

    await user.click(screen.getByRole('button', { name: 'Add candidates' }));
    const dialog = await screen.findByRole('dialog', { name: /Add candidates to Fitters, Vizag/i });

    await user.type(within(dialog).getByLabelText('Search your applicants'), 'Ravi');
    await waitFor(() => expect(listApplicants).toHaveBeenLastCalledWith({ q: 'Ravi' }));

    await user.click(await within(dialog).findByRole('checkbox', { name: /Ravi Kumar/i }));
    await user.click(within(dialog).getByRole('button', { name: /Add 1 candidate/i }));

    // MUTATION CHECK performed by hand: added a fabricated `matchReason:
    // matchReasonFromResult(...)`-shaped object to this call. This assertion
    // went red (an unexpected `matchReason` key appeared). Reverted; green
    // again — a manual add must never carry a computed match.
    await waitFor(() =>
      expect(api.addPoolMembers).toHaveBeenCalledWith('pool-1', {
        applicantIds: ['app-9'],
        source: 'manual',
        note: null,
      }),
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('Added 1 candidate to the pool'));
  });

  it('sends the optional note, trimmed', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [] });
    listApplicants.mockResolvedValue([applicant({ id: 'app-9', full_name: 'Ravi Kumar' })]);
    api.addPoolMembers.mockResolvedValue({ added: ['app-9'], skipped: [] });

    renderPage('/hr/pools/pool-1');
    await screen.findByText(/No members yet/);
    await user.click(screen.getByRole('button', { name: 'Add candidates' }));

    await user.click(await screen.findByRole('checkbox', { name: /Ravi Kumar/i }));
    await user.type(screen.getByLabelText('Note (optional)'), '  Met at a job fair  ');
    await user.click(screen.getByRole('button', { name: /Add 1 candidate/i }));

    await waitFor(() =>
      expect(api.addPoolMembers).toHaveBeenCalledWith('pool-1', {
        applicantIds: ['app-9'],
        source: 'manual',
        note: 'Met at a job fair',
      }),
    );
  });

  // -------------------------------------------------------------------------
  // A `skipped` response must be surfaced, not swallowed by a generic
  // success toast.
  //
  // MUTATION CHECK performed by hand: temporarily removed the
  // `setSkipped(...)` call in `AddCandidatesDialog`'s `onSuccess` (kept only
  // the `toast.error` summary). This assertion went red (no per-candidate
  // reason ever rendered). Reverted; green again.
  // -------------------------------------------------------------------------
  it('surfaces a skipped candidate with its reason, not just a generic toast', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [] });
    listApplicants.mockResolvedValue([
      applicant({ id: 'app-1', full_name: 'Meena Rao' }),
      applicant({ id: 'app-2', full_name: 'Zara Khan' }),
    ]);
    api.addPoolMembers.mockResolvedValue({
      added: ['app-1'],
      skipped: [{ applicant_id: 'app-2', reason: 'already_a_member' }],
    });

    renderPage('/hr/pools/pool-1');
    await screen.findByText(/No members yet/);
    await user.click(screen.getByRole('button', { name: 'Add candidates' }));

    await user.click(await screen.findByRole('checkbox', { name: /Meena Rao/i }));
    await user.click(screen.getByRole('checkbox', { name: /Zara Khan/i }));
    await user.click(screen.getByRole('button', { name: /Add 2 candidates/i }));

    // Scoped to the "not added" panel: Zara Khan still also appears as a
    // (now unchecked) row in the picker list above it.
    const notAdded = await screen.findByRole('status');
    expect(within(notAdded).getByText(/Zara Khan/)).toBeInTheDocument();
    expect(within(notAdded).getByText(/Already a member of this pool/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Added 1 candidate to the pool'),
    );
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('1 candidate could not be added'),
    );
    // The dialog stays open on a partial skip — Cancel is still there.
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('does not offer applicants already in this pool as a fresh choice', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [member({ applicant_id: 'app-1' })] });
    listApplicants.mockResolvedValue([
      applicant({ id: 'app-1', full_name: 'Asha K' }),
      applicant({ id: 'app-9', full_name: 'Ravi Kumar' }),
    ]);

    renderPage('/hr/pools/pool-1');
    await screen.findByText('Asha K');
    await user.click(screen.getByRole('button', { name: 'Add candidates' }));

    await screen.findByRole('checkbox', { name: /Ravi Kumar/i });
    expect(screen.queryByRole('checkbox', { name: /Asha K/i })).not.toBeInTheDocument();
  });

  it('closes without calling the API on Cancel', async () => {
    const user = userEvent.setup();
    api.getPool.mockResolvedValue({ pool: pool(), members: [] });
    renderPage('/hr/pools/pool-1');
    await screen.findByText(/No members yet/);

    await user.click(screen.getByRole('button', { name: 'Add candidates' }));
    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.addPoolMembers).not.toHaveBeenCalled();
  });
});
