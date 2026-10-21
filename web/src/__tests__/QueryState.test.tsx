// QueryState keeps loading, failed, empty and loaded apart (audit X3). The page
// tests (Exams, ExamResults) pin the reference migrations; this pins the
// component's own contract for the pages that adopt it next.

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryErrorPanel, QueryState, type QueryLike } from '../components/QueryState';
import { ApiError } from '../api/client';

function q<T>(over: Partial<QueryLike<T>>): QueryLike<T> {
  return {
    data: undefined,
    isError: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  };
}

const renderList = (query: QueryLike<string[]>) =>
  render(
    <QueryState
      query={query}
      errorTitle="Could not load applicants."
      isEmpty={(rows) => rows.length === 0}
      empty={<p>No applicants yet</p>}
    >
      {(rows) => (
        <ul>
          {rows.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </QueryState>,
  );

describe('QueryState', () => {
  it('shows busy skeletons while loading', () => {
    const { container } = renderList(q({ isFetching: true }));
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.queryByText('No applicants yet')).not.toBeInTheDocument();
  });

  it('shows an error panel, not the empty state, when the load failed', async () => {
    const refetch = vi.fn();
    const user = userEvent.setup();
    renderList(q({ isError: true, error: new ApiError('HTTP 500', 500), refetch }));

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Could not load applicants.');
    expect(screen.queryByText('No applicants yet')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('shows the empty state for loaded, empty data', () => {
    renderList(q({ data: [] }));
    expect(screen.getByText('No applicants yet')).toBeInTheDocument();
  });

  it('renders the data', () => {
    renderList(q({ data: ['Asha', 'Ravi'] }));
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('keeps data on screen when a background refetch fails', () => {
    renderList(q({ data: ['Asha'], isError: true, error: new Error('Server down') }));
    expect(screen.getByText('Asha')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('QueryErrorPanel', () => {
  it('says why when the reason is known', () => {
    render(
      <QueryErrorPanel title="Could not load offers." error={new TypeError('Failed to fetch')} />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not reach the server. Check your connection and try again.',
    );
  });

  it('does not repeat the title or show a raw status', () => {
    render(
      <QueryErrorPanel title="Could not load offers." error={new ApiError('HTTP 502', 502)} />,
    );
    const alert = screen.getByRole('alert');
    expect(alert.textContent).not.toMatch(/HTTP/);
    expect(alert.textContent?.match(/Could not load offers\./g)).toHaveLength(1);
  });

  it('marks the retry busy while it runs', () => {
    render(<QueryErrorPanel title="Could not load offers." onRetry={vi.fn()} retrying />);
    expect(screen.getByRole('button', { name: 'Trying again…' })).toBeDisabled();
  });
});
