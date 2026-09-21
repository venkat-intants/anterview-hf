// SubmissionPanel — PH4-D4. The interviewer's read of a job
// simulation / portfolio submission (InterviewerScorecard's "Submission" tab).
//
// The property this file exists to pin: a candidate's link is untrusted
// content the server never fetches (AR-7) — this interstitial is the only
// safeguard, so it must show the HOSTNAME THE BROWSER WILL RESOLVE (parsed
// with `new URL(...)`, never the candidate's own label for the link) and the
// anchor must carry rel="noopener noreferrer nofollow" and open in a new tab.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ScorecardSubmission } from '../api/jobTasks';

const getScorecardSubmission = vi.fn();
const downloadScorecardArtifact = vi.fn();
const downloadScorecardMaterial = vi.fn();
vi.mock('../api/jobTasks', () => ({
  getScorecardSubmission: (...a: unknown[]) => getScorecardSubmission(...a) as unknown,
  downloadScorecardArtifact: (...a: unknown[]) => downloadScorecardArtifact(...a) as unknown,
  downloadScorecardMaterial: (...a: unknown[]) => downloadScorecardMaterial(...a) as unknown,
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
  brief: null,
  items: [],
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

// Security review, PH4 wave 5: checking only that `new URL(href).hostname`
// is non-empty let `javascript://github.com/x` through with a hostname of
// `github.com` — this interstitial would have labelled it "resolves to
// github.com" and rendered it as the anchor's own href. `vbscript:`, `http:`
// and a protocol-relative `//host` all had the same gap. The fix requires
// `url.protocol === 'https:'`.
describe('SubmissionPanel — the interstitial refuses a non-https scheme', () => {
  const realLocation = window.location;

  beforeEach(() => {
    // This app is always served over https in production (Vercel) — force
    // that here so the assertions below test the SCHEME check itself, not
    // `downloadUrl`'s separate allowance for a machine that is itself
    // running insecurely (jsdom's default test URL is http://localhost).
    Object.defineProperty(window, 'location', {
      value: { ...realLocation, protocol: 'https:' },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      value: realLocation,
      writable: true,
      configurable: true,
    });
  });

  function renderWithLink(link_url: string) {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      responses: [
        {
          id: 'r-1',
          item_key: null,
          response_type: 'link',
          text_value: null,
          link_url,
          link_kind: 'other',
          title: 'Suspicious link',
          description: null,
          original_name: null,
          content_type: null,
          size_bytes: null,
        },
      ],
    });
    return renderPanel();
  }

  it.each([
    ['a javascript: URL with an allowed-looking host', 'javascript://github.com/x'],
    ['a plain http: URL', 'http://github.com/x'],
    ['a data: URL', 'data:text/html,<p>x</p>'],
    ['a protocol-relative URL', '//github.com/x'],
  ])('refuses %s and never offers to open it', async (_label, link_url) => {
    renderWithLink(link_url);

    await screen.findByText('Suspicious link');
    expect(screen.getByText('This link could not be opened.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /open link/i })).not.toBeInTheDocument();
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

// Gap 2/3 fixes: `submission_for_reviewer` now returns the round's brief and
// item prompts, and a dedicated materials-download route — previously a
// reviewer saw only a raw item key and an un-clickable material title.
describe('SubmissionPanel — the round in context (gap 2/3 fixes)', () => {
  it("shows the round's brief and an item's actual prompt, not its key", async () => {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      kind: 'job_simulation',
      brief: 'Design a small feature end to end.',
      items: [
        {
          key: 'design_doc',
          prompt: 'Describe your approach to the assignment.',
          response_type: 'text',
          required: true,
          max_chars: 2000,
        },
      ],
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

    expect(await screen.findByText('Design a small feature end to end.')).toBeInTheDocument();
    expect(
      screen.getByText('Describe your approach to the assignment.'),
    ).toBeInTheDocument();
    // The raw key must never leak through once a matching prompt exists.
    expect(screen.queryByText('Design doc')).not.toBeInTheDocument();
  });

  it("downloads a reference material through the scorecard's own route", async () => {
    const user = userEvent.setup();
    downloadScorecardMaterial.mockResolvedValue({
      url: 'https://files.example.com/spec.pdf',
      expires_in: 300,
    });
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      materials: [
        {
          id: 'mat-1',
          title: 'Reference spec',
          original_name: 'spec.pdf',
          content_type: 'application/pdf',
          size_bytes: 1024,
          position: 0,
        },
      ],
    });
    renderPanel();

    const button = await screen.findByRole('button', { name: /reference spec/i });
    await user.click(button);

    await waitFor(() =>
      expect(downloadScorecardMaterial).toHaveBeenCalledWith('sc-1', 'mat-1'),
    );
  });
});

// Security review, PH4 wave 5: the candidate is told submitting is the
// moment work goes to the hiring team, and can "Remove" anything beforehand.
// This screen used to render every response whatever `data.status` was, with
// only the header line saying "Not yet submitted" — so a reviewer could read
// work the candidate had not yet sent. The backend gates this too
// (`submission_for_reviewer` only returns a `submitted` row); this is the
// belt to that brace.
describe('SubmissionPanel — nothing renders before the candidate submits', () => {
  it('shows only a not-yet-submitted message for an in-progress submission, never its responses', async () => {
    getScorecardSubmission.mockResolvedValue({
      ...BASE,
      status: 'in_progress',
      submitted_at: null,
      responses: [
        {
          id: 'r-1',
          item_key: 'design_doc',
          response_type: 'text',
          text_value: 'Draft — do not read this yet.',
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

    expect(await screen.findByText(/not yet submitted/i)).toBeInTheDocument();
    expect(screen.queryByText('Draft — do not read this yet.')).not.toBeInTheDocument();
    expect(screen.queryByText(/evidence, not a verdict/i)).not.toBeInTheDocument();
  });
});
