// The JD version panel — PH3-B6.
//
// The property worth testing is the one a recruiter has to believe: saving a
// draft does not change what candidates see. That is enforced on the server
// (a draft never writes to job_requisitions), so what this file checks is that
// the UI says so plainly, and that the two buttons are genuinely different
// actions rather than two labels on one.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { JdHistory, JdVersion, Requisition } from '../api/requisitions';

const getJdHistory = vi.fn();
const saveJdDraft = vi.fn();
const publishJdVersion = vi.fn();
const discardJdDraft = vi.fn();

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>(
    '../api/requisitions',
  );
  return {
    ...actual,
    getJdHistory: (...a: unknown[]) => getJdHistory(...a) as unknown,
    saveJdDraft: (...a: unknown[]) => saveJdDraft(...a) as unknown,
    publishJdVersion: (...a: unknown[]) => publishJdVersion(...a) as unknown,
    discardJdDraft: (...a: unknown[]) => discardJdDraft(...a) as unknown,
  };
});

vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import JdVersionPanel from '../components/workflow/JdVersionPanel';

function version(over: Partial<JdVersion> = {}): JdVersion {
  return {
    id: 'v-1',
    version: 1,
    status: 'published',
    jd_text: 'The original advert.',
    responsibilities: [],
    required_skills: [],
    nice_to_have_skills: [],
    change_note: null,
    created_by_user_id: 'u-1',
    created_by_name: 'Priya',
    created_at: '2026-09-01T10:00:00Z',
    published_at: '2026-09-01T10:00:00Z',
    superseded_at: null,
    ...over,
  };
}

function requisition(over: Partial<Requisition> = {}): Requisition {
  return {
    id: 'req-1',
    title: 'Backend Engineer',
    level: 'mid',
    status: 'open',
    jd_text: 'The original advert.',
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
    approval_status: 'approved',
    ...over,
  };
}

function history(versions: JdVersion[]): JdHistory {
  return {
    requisition_id: 'req-1',
    published_version_id: versions.find((v) => v.status === 'published')?.id ?? null,
    versions,
  };
}

function renderPanel(req = requisition()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <JdVersionPanel requisition={req} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getJdHistory.mockResolvedValue(history([version()]));
  saveJdDraft.mockResolvedValue(version({ id: 'v-2', version: 2, status: 'draft' }));
  publishJdVersion.mockResolvedValue(version({ id: 'v-2', version: 2 }));
  discardJdDraft.mockResolvedValue(undefined);
});

describe('a draft is not what candidates see', () => {
  it('says so before anything is saved', async () => {
    renderPanel();
    expect(
      await screen.findByText(/Candidates keep seeing the published version/),
    ).toBeInTheDocument();
  });

  it('warns while a draft exists', async () => {
    getJdHistory.mockResolvedValue(
      history([version(), version({ id: 'v-2', version: 2, status: 'draft', published_at: null })]),
    );
    renderPanel();
    expect(
      await screen.findByText(/not visible to candidates/),
    ).toBeInTheDocument();
  });

  it('offers publishing as a separate act from saving', async () => {
    getJdHistory.mockResolvedValue(
      history([version(), version({ id: 'v-2', version: 2, status: 'draft', published_at: null })]),
    );
    renderPanel();
    // findBy for both: "Save draft" renders immediately but "Publish v2"
    // depends on the history query, so a sync getBy here races the fetch.
    expect(await screen.findByRole('button', { name: /Save draft/ })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Publish v2/ })).toBeInTheDocument();
  });

  it('does not offer publishing when there is no draft', async () => {
    renderPanel();
    await screen.findByRole('button', { name: /Save draft/ });
    expect(screen.queryByRole('button', { name: /^Publish/ })).not.toBeInTheDocument();
  });
});

describe('the editor starts from something real', () => {
  it('uses the draft when there is one, not the live advert', async () => {
    getJdHistory.mockResolvedValue(
      history([
        version(),
        version({
          id: 'v-2',
          version: 2,
          status: 'draft',
          jd_text: 'A revision in progress.',
          published_at: null,
        }),
      ]),
    );
    renderPanel();
    await waitFor(() =>
      expect(screen.getByLabelText(/Draft \(v2\)/)).toHaveValue('A revision in progress.'),
    );
  });

  it('falls back to the live advert so editing does not start from blank', async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByLabelText(/New draft/)).toHaveValue('The original advert.'),
    );
  });
});

describe('history', () => {
  it('shows when each version was the live one', async () => {
    getJdHistory.mockResolvedValue(
      history([
        version({
          id: 'v-1',
          version: 1,
          status: 'archived',
          published_at: '2026-09-01T10:00:00Z',
          superseded_at: '2026-09-10T10:00:00Z',
        }),
      ]),
    );
    renderPanel();
    // The window a wording was live for is the question PH3-B6 Task 5 asks.
    expect(await screen.findByText(/Live .*–.*/)).toBeInTheDocument();
  });

  it('attributes a version to whoever wrote it', async () => {
    // The date, the author and the live window are separate text nodes inside
    // one line, so this asserts on the rendered line rather than on a node.
    const { container } = renderPanel();
    await screen.findByText(/Version history/);
    await waitFor(() => expect(container.textContent).toContain('Priya'));
  });

  it('offers restore on a previous version, and says it does not rewrite history', async () => {
    getJdHistory.mockResolvedValue(
      history([
        version({ id: 'v-2', version: 2 }),
        version({
          id: 'v-1',
          version: 1,
          status: 'archived',
          superseded_at: '2026-09-10T10:00:00Z',
        }),
      ]),
    );
    renderPanel();
    expect(await screen.findByRole('button', { name: /Restore/ })).toBeInTheDocument();
    expect(screen.getByText(/Nothing in the history is overwritten/)).toBeInTheDocument();
  });

  it('restoring publishes that version rather than editing the past', async () => {
    getJdHistory.mockResolvedValue(
      history([
        version({ id: 'v-2', version: 2 }),
        version({
          id: 'v-1',
          version: 1,
          status: 'archived',
          superseded_at: '2026-09-10T10:00:00Z',
        }),
      ]),
    );
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Restore/ }));
    await waitFor(() => expect(publishJdVersion).toHaveBeenCalledWith('req-1', 'v-1'));
  });

  it('explains an empty history rather than showing nothing', async () => {
    getJdHistory.mockResolvedValue(history([]));
    renderPanel();
    expect(await screen.findByText(/next edit will create version 1/)).toBeInTheDocument();
  });
});

describe('saving', () => {
  it('sends the edited text and the note', async () => {
    renderPanel();
    const box = await screen.findByLabelText(/New draft/);
    await userEvent.clear(box);
    await userEvent.type(box, 'Rewritten.');
    await userEvent.type(screen.getByLabelText(/What changed/), 'Tightened it');
    await userEvent.click(screen.getByRole('button', { name: /Save draft/ }));
    await waitFor(() =>
      expect(saveJdDraft).toHaveBeenCalledWith('req-1', {
        jd_text: 'Rewritten.',
        change_note: 'Tightened it',
      }),
    );
  });

  it('sends no note when none was written, rather than an empty string', async () => {
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Save draft/ }));
    await waitFor(() =>
      expect(saveJdDraft).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ change_note: null }),
      ),
    );
  });
});
