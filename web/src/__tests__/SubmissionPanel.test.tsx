// SubmissionPanel — PH4-D4. The interviewer's read of a job
// simulation / portfolio submission (InterviewerScorecard's "Submission" tab).
//
// The property this file exists to pin: a candidate's link is untrusted
// content the server never fetches (AR-7) — this interstitial is the only
// safeguard, so it must show the HOSTNAME THE BROWSER WILL RESOLVE (parsed
// with `new URL(...)`, never the candidate's own label for the link) and the
// anchor must carry rel="noopener noreferrer nofollow" and open in a new tab.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ScorecardSubmission } from '../api/jobTasks';

const getScorecardSubmission = vi.fn();
const downloadScorecardArtifact = vi.fn();
vi.mock('../api/jobTasks', () => ({
  getScorecardSubmission: (...a: unknown[]) => getScorecardSubmission(...a) as unknown,
  downloadScorecardArtifact: (...a: unknown[]) => downloadScorecardArtifact(...a) as unknown,
}));

vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import SubmissionPanel from '../components/interviewer/SubmissionPanel';

function renderPanel(scorecardId = 'sc-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SubmissionPanel scorecardId={scorecardId} />
    </QueryClientProvider>,
  );
}

const BASE: ScorecardSubmission = {
  submission_id: 'sub-1',
  status: 'submitted',
  kind: 'portfolio',
  submitted_at: '2026-09-19T00:00:00.000Z',
  materials: [],
  responses: [],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SubmissionPanel — the link interstitial', () => {
  it('does not open a link before a reviewer confirms', async () => {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      responses: [
        {
          id: 'r-1',
          item_key: null,
          response_type: 'link',
          text_value: null,
          link_url: 'https://github.com/example/repo',
          link_kind: 'repository',
          title: 'My repo',
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    renderPanel();

    await screen.findByText('My repo');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open link/i })).toBeInTheDocument();
  });

  it("shows the hostname the BROWSER will resolve, not the candidate's own label", async () => {
    // A label chosen to read as one host while the URL itself resolves to
    // another — exactly the mismatch the interstitial exists to catch.
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      responses: [
        {
          id: 'r-1',
          item_key: null,
          response_type: 'link',
          text_value: null,
          link_url: 'https://attacker.example/x',
          link_kind: 'other',
          title: 'github.com — my project',
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText('github.com — my project');
    await user.click(screen.getByRole('button', { name: /open link/i }));

    // The resolved host is its own element (a <strong>), so this is an exact
    // match — not a substring check that the untrusted title text (which
    // legitimately contains the literal string "github.com") could satisfy
    // by accident.
    expect(await screen.findByText('attacker.example')).toBeInTheDocument();
    expect(screen.queryByText('github.com')).not.toBeInTheDocument();
  });

  it('opens with rel="noopener noreferrer nofollow" and a new tab, only after confirming', async () => {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      responses: [
        {
          id: 'r-1',
          item_key: null,
          response_type: 'link',
          text_value: null,
          link_url: 'https://github.com/example/repo',
          link_kind: 'repository',
          title: 'My repo',
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText('My repo');
    await user.click(screen.getByRole('button', { name: /open link/i }));

    const link = await screen.findByRole('link', { name: /open github.com/i });
    expect(link).toHaveAttribute('href', 'https://github.com/example/repo');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer nofollow');
  });

  it('lets cancelling close the interstitial without opening anything', async () => {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      responses: [
        {
          id: 'r-1',
          item_key: null,
          response_type: 'link',
          text_value: null,
          link_url: 'https://github.com/example/repo',
          link_kind: 'repository',
          title: 'My repo',
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText('My repo');
    await user.click(screen.getByRole('button', { name: /open link/i }));
    const cancelButton = await screen.findByRole('button', { name: /cancel/i });
    await user.click(cancelButton);

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open link/i })).toBeInTheDocument();
  });
});

describe('SubmissionPanel — evidence, not a verdict', () => {
  it('renders a text answer as plain text', async () => {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      kind: 'job_simulation',
      responses: [
        {
          id: 'r-1',
          item_key: 'design_doc',
          response_type: 'text',
          text_value: 'My approach is...',
          link_url: null,
          link_kind: null,
          title: null,
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    renderPanel();
    expect(await screen.findByText('My approach is...')).toBeInTheDocument();
  });

  it('says this is evidence, never a score', async () => {
    getScorecardSubmission.mockResolvedValue(BASE);
    renderPanel();
    expect(await screen.findByText(/evidence, not a verdict/i)).toBeInTheDocument();
  });
});
