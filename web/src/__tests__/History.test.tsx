// Tests for History page
// Covers: loading skeletons, session list rendering, status badges,
// scorecard links, empty state, error state.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import History from '../pages/History';
import i18n from '../lib/i18n';
import type { SessionListResponse } from '../api/sessions';

// ---------------------------------------------------------------------------
// Mock sessions API
// ---------------------------------------------------------------------------

const MOCK_SESSIONS: SessionListResponse = {
  items: [
    {
      session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      job_title: 'Junior Java Developer',
      language: 'en',
      status: 'completed',
      started_at: '2026-05-29T10:00:00Z',
      completed_at: '2026-05-29T10:12:34Z',
      duration_seconds: 754,
      created_at: '2026-05-29T09:58:00Z',
      scorecard_id: '00000000-0000-0000-0000-000000000001',
    },
    {
      session_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
      job_title: 'Sales Associate',
      language: 'hi',
      status: 'abandoned',
      started_at: '2026-05-27T14:05:00Z',
      completed_at: null,
      duration_seconds: null,
      created_at: '2026-05-27T14:03:00Z',
      scorecard_id: null,
    },
  ],
  total: 2,
  page: 1,
  per_page: 10,
};

const mockListSessions = vi.fn().mockResolvedValue(MOCK_SESSIONS);

vi.mock('../api/sessions', () => ({
  listSessions: (...args: unknown[]) => mockListSessions(...args) as unknown,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <History />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('History page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListSessions.mockResolvedValue(MOCK_SESSIONS);
  });

  it('renders the page heading', async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /interview history/i })).toBeInTheDocument();
    });
  });

  it('shows loading skeletons initially', () => {
    // Keep mock pending so skeletons stay visible
    mockListSessions.mockImplementation(
      () => new Promise(() => undefined), // never resolves during this test
    );
    renderHistory();
    expect(screen.getByLabelText(/loading interview history/i)).toBeInTheDocument();
  });

  it('renders session rows after data loads', async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getAllByText('Junior Java Developer').length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText('Sales Associate').length).toBeGreaterThan(0);
  });

  it('shows status badges for each session', async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getAllByText('Completed').length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText('Abandoned').length).toBeGreaterThan(0);
  });

  it('renders a scorecard link for sessions that have scorecard_id', async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getAllByText('View scorecard').length).toBeGreaterThan(0);
    });
    const links = screen.getAllByRole('link', { name: /view scorecard/i });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveAttribute(
      'href',
      '/scorecard/00000000-0000-0000-0000-000000000001',
    );
  });

  it('shows empty state when no sessions exist', async () => {
    mockListSessions.mockResolvedValueOnce({ items: [], total: 0, page: 1, per_page: 10 });
    renderHistory();
    await waitFor(() => {
      expect(screen.getByTestId('history-empty-state')).toBeInTheDocument();
    });
    expect(screen.getByText(/no interviews yet/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /start your first interview/i })).toBeInTheDocument();
  });

  it('shows session count in the card header', async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getByText(/2 interviews total/i)).toBeInTheDocument();
    });
  });
  // Audit CA-M6: a failed load used to read as "start your first interview".
  it('shows a retryable error, not the empty state, when the list fails to load', async () => {
    mockListSessions.mockRejectedValueOnce(new Error('boom'));
    renderHistory();
    expect(await screen.findByText('Could not load your interview history.')).toBeInTheDocument();
    expect(screen.queryByText(/first interview/i)).not.toBeInTheDocument();

    mockListSessions.mockResolvedValueOnce(MOCK_SESSIONS);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect((await screen.findAllByText('Junior Java Developer')).length).toBeGreaterThan(0);
  });
  // Audit CA-M19: status, duration and date follow the UI language.
  it('shows status and duration in Hindi on a Hindi page', async () => {
    await i18n.changeLanguage('hi');
    try {
      renderHistory();
      expect((await screen.findAllByText('पूरा हुआ')).length).toBeGreaterThan(0);
      expect(screen.getAllByText('12 मि 34 से').length).toBeGreaterThan(0);
      expect(screen.queryByText('Completed')).not.toBeInTheDocument();
      expect(screen.queryByText('12m 34s')).not.toBeInTheDocument();
    } finally {
      await i18n.changeLanguage('en');
    }
  });
});
