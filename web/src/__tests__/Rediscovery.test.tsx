// Rediscovery (/hr/rediscovery) — PH5-E3.
//
// `api/rediscovery.ts` and `api/pools.ts` are mocked via `importActual` so the
// real `rediscoveryErrorMessage`, `poolErrorMessage`, `parseBracketHighlights`
// and `matchReasonFromResult` run — only the network calls are doubles (the
// CorpusDocuments.test.tsx precedent).
//
// THREE LINES PINNED HERE VERBATIM, because design §10.3 treats softening any
// of them as a defect, not a style choice:
//   1. the empty-universe state
//   2. the always-on "Searching N of M" counter
//   3. "Semantic search is unavailable — matching keywords only"

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { RediscoveryResult, RediscoverySearchResponse } from '../api/rediscovery';

const rediscoveryApi = {
  searchRediscovery: vi.fn(),
  getRediscoveryUniverse: vi.fn(),
};
vi.mock('../api/rediscovery', async () => {
  const actual = await vi.importActual<typeof import('../api/rediscovery')>('../api/rediscovery');
  return {
    ...actual,
    searchRediscovery: (...a: unknown[]) => rediscoveryApi.searchRediscovery(...a) as unknown,
    getRediscoveryUniverse: (...a: unknown[]) => rediscoveryApi.getRediscoveryUniverse(...a) as unknown,
  };
});

const poolsApi = {
  listPools: vi.fn(),
  createPool: vi.fn(),
  addPoolMembers: vi.fn(),
};
vi.mock('../api/pools', async () => {
  const actual = await vi.importActual<typeof import('../api/pools')>('../api/pools');
  return {
    ...actual,
    listPools: (...a: unknown[]) => poolsApi.listPools(...a) as unknown,
    createPool: (...a: unknown[]) => poolsApi.createPool(...a) as unknown,
    addPoolMembers: (...a: unknown[]) => poolsApi.addPoolMembers(...a) as unknown,
  };
});

const listRequisitions = vi.fn();
vi.mock('../api/requisitions', () => ({
  listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
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

import Rediscovery from '../pages/hr/Rediscovery';

function result(over: Partial<RediscoveryResult> = {}): RediscoveryResult {
  return {
    applicant_id: 'app-1',
    full_name: 'Asha K',
    current_title: 'Fitter',
    current_company: 'Acme',
    years_experience: 6,
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
    why: [],
    evidence_freshness: 'ageing',
    requires_review: true,
    review_reasons: ['evidence_ageing'],
    eligibility: { opted_in_at: '2026-08-01T00:00:00Z', expires_at: '2027-08-01T00:00:00Z' },
    ...over,
  };
}

function searchResponse(over: Partial<RediscoverySearchResponse> = {}): RediscoverySearchResponse {
  return {
    semantic: true,
    universe: { eligible: 14, total: 2317 },
    matched: 1,
    returned: 1,
    weights: { semantic: 0.7, lexical: 0.3, evidence_max: 0.15 },
    results: [result()],
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Rediscovery />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  rediscoveryApi.getRediscoveryUniverse.mockResolvedValue({
    universe: { eligible: 14, total: 2317 },
    consent_months: 12,
  });
  listRequisitions.mockResolvedValue([{ id: 'req-1', title: 'Maintenance Fitter' }]);
  poolsApi.listPools.mockResolvedValue({
    pools: [{ id: 'pool-1', name: 'Fitters', member_count: 0, created_at: '2026-01-01', updated_at: '2026-01-01' }],
    limits: { max_per_company: 100, max_members: 2000 },
  });
});

async function runSearch(user: ReturnType<typeof userEvent.setup>, query = 'hydraulics') {
  await user.type(screen.getByLabelText('Search'), query);
  await user.click(screen.getByRole('button', { name: 'Search' }));
}

describe('Rediscovery — the empty universe', () => {
  it('names the number of opted-in candidates, verbatim, when nobody has opted in', async () => {
    rediscoveryApi.getRediscoveryUniverse.mockResolvedValue({
      universe: { eligible: 0, total: 2317 },
      consent_months: 12,
    });
    renderPage();

    expect(
      await screen.findByText(
        'No candidates have opted in yet. Rediscovery searches only candidates who chose to be kept for future openings. New applicants can opt in on your application form from today; candidates who applied earlier appear here only if they opt in themselves.',
      ),
    ).toBeInTheDocument();
    // The search box has nothing to search — it is not offered.
    expect(screen.queryByLabelText('Search')).not.toBeInTheDocument();
  });

  it('shows the always-on counter even before any search has run', async () => {
    renderPage();
    expect(await screen.findByText(/Searching 14 of/)).toBeInTheDocument();
    expect(screen.getByText(/the rest have not opted in/)).toBeInTheDocument();
    expect(rediscoveryApi.searchRediscovery).not.toHaveBeenCalled();
  });
});

describe('Rediscovery — searching', () => {
  it('runs a search and shows a result', async () => {
    const user = userEvent.setup();
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse());
    renderPage();
    await screen.findByText(/Searching 14 of/);

    await runSearch(user);

    await waitFor(() =>
      expect(rediscoveryApi.searchRediscovery).toHaveBeenCalledWith({
        query: 'hydraulics',
        requisitionId: null,
      }),
    );
    expect(await screen.findByText('Asha K')).toBeInTheDocument();
  });

  it('sends the chosen opening as the target', async () => {
    const user = userEvent.setup();
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse());
    renderPage();
    await screen.findByText(/Searching 14 of/);

    await user.selectOptions(await screen.findByLabelText('For this opening (optional)'), 'req-1');
    await runSearch(user);

    await waitFor(() =>
      expect(rediscoveryApi.searchRediscovery).toHaveBeenCalledWith({
        query: 'hydraulics',
        requisitionId: 'req-1',
      }),
    );
  });

  it('shows the keyword-only banner when semantic search is unavailable', async () => {
    const user = userEvent.setup();
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse({ semantic: false }));
    renderPage();
    await screen.findByText(/Searching 14 of/);

    await runSearch(user);

    expect(
      await screen.findByText('Semantic search is unavailable — matching keywords only'),
    ).toBeInTheDocument();
  });

  it('does not show the keyword-only banner when semantic search is up', async () => {
    const user = userEvent.setup();
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse({ semantic: true }));
    renderPage();
    await screen.findByText(/Searching 14 of/);

    await runSearch(user);

    await screen.findByText('Asha K');
    expect(
      screen.queryByText('Semantic search is unavailable — matching keywords only'),
    ).not.toBeInTheDocument();
  });
});

describe('Rediscovery — the unexplained section is separate', () => {
  it('keeps an unexplained row out of the explained list, under its own heading, with no one-click invite', async () => {
    const user = userEvent.setup();
    const explainedRow = result({ applicant_id: 'app-1', full_name: 'Asha K', explained: true });
    const unexplainedRow = result({
      applicant_id: 'app-2',
      full_name: 'Ravi P',
      explained: false,
      unexplained_note: 'matched on similarity only — nothing here says why',
    });
    rediscoveryApi.searchRediscovery.mockResolvedValue(
      searchResponse({ results: [explainedRow, unexplainedRow], matched: 2, returned: 2 }),
    );
    renderPage();
    await screen.findByText(/Searching 14 of/);
    await runSearch(user);

    await screen.findByText('Asha K');
    expect(
      screen.getByText('Matched on similarity only — nothing here says why'),
    ).toBeInTheDocument();

    // MUTATION CHECK performed by hand: temporarily rendered `results` as one
    // flat list (dropping the explained/unexplained split) — the
    // `unexplained-results` testid disappeared and this assertion went red.
    // Reverted; green again.
    const unexplainedSection = screen.getByTestId('unexplained-results');
    expect(within(unexplainedSection).getByText('Ravi P')).toBeInTheDocument();
    expect(within(screen.getByTestId('explained-results')).getByText('Asha K')).toBeInTheDocument();
    expect(within(screen.getByTestId('explained-results')).queryByText('Ravi P')).not.toBeInTheDocument();

    // No one-click "Invite" anywhere on this screen — invite only ever
    // happens from inside a pool, never straight off a search result.
    expect(screen.queryByRole('button', { name: /^invite/i })).not.toBeInTheDocument();
    expect(within(unexplainedSection).getByText(unexplainedRow.unexplained_note!)).toBeInTheDocument();
  });
});

describe('Rediscovery — the why panel', () => {
  it('renders a resume_terms snippet\'s [[…]] markers as a highlight, never raw HTML', async () => {
    const user = userEvent.setup();
    const row = result({
      why: [
        {
          signal: 'resume_terms',
          contribution: 4,
          explainable: true,
          terms_matched: ['hydraulics'],
          snippet: 'Performed [[preventive maintenance]] of hydraulic presses.',
        },
      ],
    });
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse({ results: [row] }));
    renderPage();
    await screen.findByText(/Searching 14 of/);
    await runSearch(user);

    await user.click(await screen.findByRole('button', { name: /why this match/i }));

    const mark = await screen.findByText('preventive maintenance');
    expect(mark.tagName.toLowerCase()).toBe('mark');
    // The bracket markers themselves must never reach the screen as text.
    expect(screen.queryByText(/\[\[/)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain('<b>preventive maintenance</b>');
  });

  it('shows the fixed similarity sentence, never a model-written explanation, for resume_similarity', async () => {
    const user = userEvent.setup();
    const row = result({
      why: [
        {
          signal: 'resume_similarity',
          contribution: 48,
          explainable: false,
          citation: { kind: 'applicant', id: 'app-1', label: 'CV' },
        },
      ],
    });
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse({ results: [row] }));
    renderPage();
    await screen.findByText(/Searching 14 of/);
    await runSearch(user);

    await user.click(await screen.findByRole('button', { name: /why this match/i }));
    expect(
      await screen.findByText(
        /Their CV reads as similar to what you searched for\. That is a similarity score, not a stated fact/,
      ),
    ).toBeInTheDocument();
  });
});

describe('Rediscovery — add to pool', () => {
  it('adds a single result to an existing pool with its frozen match reason', async () => {
    const user = userEvent.setup();
    rediscoveryApi.searchRediscovery.mockResolvedValue(searchResponse());
    poolsApi.addPoolMembers.mockResolvedValue({ added: ['mem-1'], skipped: [] });
    renderPage();
    await screen.findByText(/Searching 14 of/);
    await runSearch(user);
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('button', { name: 'Add to pool' }));
    await user.selectOptions(await screen.findByLabelText('Pool'), 'pool-1');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(poolsApi.addPoolMembers).toHaveBeenCalledTimes(1));
    const [poolIdArg, input] = poolsApi.addPoolMembers.mock.calls[0] as [
      string,
      { applicantIds: string[]; source: string; matchReason?: { score: number; explained: boolean } },
    ];
    expect(poolIdArg).toBe('pool-1');
    expect(input.applicantIds).toEqual(['app-1']);
    expect(input.source).toBe('rediscovery');
    // The frozen snapshot carries the live result's own score and explained
    // flag — never recomputed, never a fresh call to the server.
    expect(input.matchReason?.score).toBe(69);
    expect(input.matchReason?.explained).toBe(true);
  });

  it('adds several selected results in bulk, one call per applicant', async () => {
    const user = userEvent.setup();
    const rowA = result({ applicant_id: 'app-1', full_name: 'Asha K' });
    const rowB = result({ applicant_id: 'app-2', full_name: 'Ravi P' });
    rediscoveryApi.searchRediscovery.mockResolvedValue(
      searchResponse({ results: [rowA, rowB], matched: 2, returned: 2 }),
    );
    poolsApi.addPoolMembers.mockResolvedValue({ added: ['m'], skipped: [] });
    renderPage();
    await screen.findByText(/Searching 14 of/);
    await runSearch(user);
    await screen.findByText('Asha K');

    await user.click(screen.getByRole('checkbox', { name: 'Select Asha K' }));
    await user.click(screen.getByRole('checkbox', { name: 'Select Ravi P' }));
    await user.click(screen.getByRole('button', { name: 'Add 2 to pool' }));
    await user.selectOptions(await screen.findByLabelText('Pool'), 'pool-1');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(poolsApi.addPoolMembers).toHaveBeenCalledTimes(2));
    expect(poolsApi.addPoolMembers).toHaveBeenCalledWith(
      'pool-1',
      expect.objectContaining({ applicantIds: ['app-1'] }),
    );
    expect(poolsApi.addPoolMembers).toHaveBeenCalledWith(
      'pool-1',
      expect.objectContaining({ applicantIds: ['app-2'] }),
    );
  });
});
