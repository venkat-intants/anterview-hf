// Tests for the HR exam list + inline create form (FE-2).
//
// This is where a written round is born. The two things worth pinning are the
// draft/published distinction — a published exam is one a candidate can already
// be sitting, so its card must not read like a draft — and that the create form
// sends the operator's actual settings rather than the component's defaults.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ExamSummary } from '../api/exams';

const BASE = {
  description: null,
  target_job_title: null,
  time_limit_seconds: null,
  allow_retake: false,
  auto_advance_on_pass: false,
  created_at: '2026-07-01T10:00:00.000Z',
} as const;

const PUBLISHED: ExamSummary = {
  ...BASE,
  id: 'e-live',
  title: 'Python Fundamentals Screening',
  pass_threshold: 60,
  status: 'published',
  kind: 'mcq',
  question_count: 20,
  attempt_count: 34,
};

const DRAFT: ExamSummary = {
  ...BASE,
  id: 'e-draft',
  title: 'React Deep Dive',
  pass_threshold: 70,
  status: 'draft',
  kind: 'coding',
  question_count: 3,
  attempt_count: 0,
};

const listExams = vi.fn();
const createExam = vi.fn();
vi.mock('../api/exams', () => ({
  listExams: (...a: unknown[]) => listExams(...a) as unknown,
  createExam: (...a: unknown[]) => createExam(...a) as unknown,
}));

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

import Exams from '../pages/hr/Exams';

function renderExams() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Exams />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listExams.mockResolvedValue([PUBLISHED, DRAFT]);
  createExam.mockResolvedValue({ ...DRAFT, id: 'e-new' });
});

describe('Exams — list', () => {
  it('distinguishes a live exam from a draft', async () => {
    renderExams();

    await screen.findByText('Python Fundamentals Screening');
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
    // Attempt counts are meaningless before publish and must not be shown.
    expect(screen.getByText('34 attempts')).toBeInTheDocument();
    expect(screen.getByText(/not published yet/i)).toBeInTheDocument();
  });

  it('offers Results only for a published exam', async () => {
    renderExams();

    await screen.findByText('React Deep Dive');
    expect(
      screen.getByRole('button', { name: /view results for exam python fundamentals/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /view results for exam react deep dive/i }),
    ).not.toBeInTheDocument();
  });

  it('shows each exam question count and pass threshold', async () => {
    renderExams();

    await screen.findByText('React Deep Dive');
    expect(screen.getByText('20 Qs')).toBeInTheDocument();
    expect(screen.getByText(/pass ≥ 70%/)).toBeInTheDocument();
  });

  it('invites a first exam when the list is empty', async () => {
    listExams.mockResolvedValue([]);
    renderExams();

    // The hint only appears once the query settles — before that the grid shows
    // skeletons, so awaiting it is what proves the EMPTY result was rendered
    // rather than the still-loading state.
    expect(await screen.findByText(/no exams yet/i)).toBeInTheDocument();
    expect(screen.getByText(/create your first exam/i)).toBeInTheDocument();
  });

  it('opens the editor for the exam whose Edit was pressed', async () => {
    const user = userEvent.setup();
    renderExams();

    await user.click(await screen.findByRole('button', { name: /edit exam react deep dive/i }));
    expect(navigate).toHaveBeenCalledWith('/hr/exams/e-draft');
  });
});

describe('Exams — create form', () => {
  it('stays hidden until the create tile is pressed', async () => {
    const user = userEvent.setup();
    renderExams();

    await screen.findByText('React Deep Dive');
    expect(screen.queryByRole('heading', { name: /new exam/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create new exam/i }));
    expect(screen.getByRole('heading', { name: /new exam/i })).toBeInTheDocument();
  });

  it('sends the operator settings, converting the time limit to seconds', async () => {
    const user = userEvent.setup();
    renderExams();

    await screen.findByText('React Deep Dive');
    await user.click(screen.getByRole('button', { name: /create new exam/i }));

    await user.type(screen.getByLabelText(/exam title/i), 'SQL Screening');
    await user.clear(screen.getByLabelText(/pass threshold percent/i));
    await user.type(screen.getByLabelText(/pass threshold percent/i), '75');
    await user.type(screen.getByLabelText(/time limit in minutes/i), '45');
    await user.click(screen.getByRole('switch', { name: /allow retake/i }));
    await user.click(screen.getByRole('button', { name: /coding round/i }));
    await user.click(screen.getByRole('button', { name: /^create exam$/i }));

    await waitFor(() =>
      expect(createExam).toHaveBeenCalledWith({
        title: 'SQL Screening',
        pass_threshold: 75,
        time_limit_seconds: 45 * 60,
        allow_retake: true,
        auto_advance_on_pass: false,
        kind: 'coding',
      }),
    );
  });

  it('treats a blank time limit as no limit, not as zero', async () => {
    const user = userEvent.setup();
    renderExams();

    await screen.findByText('React Deep Dive');
    await user.click(screen.getByRole('button', { name: /create new exam/i }));
    await user.type(screen.getByLabelText(/exam title/i), 'Untimed');
    await user.click(screen.getByRole('button', { name: /^create exam$/i }));

    await waitFor(() => expect(createExam).toHaveBeenCalled());
    const input = createExam.mock.calls[0]?.[0] as { time_limit_seconds: number | null };
    expect(input.time_limit_seconds).toBeNull();
  });

  it('refuses a whitespace-only title that slips past the required attribute', async () => {
    // `required` stops a genuinely empty field, but "   " satisfies it and would
    // create an exam with a blank name in every list and every candidate email.
    const user = userEvent.setup();
    renderExams();

    await screen.findByText('React Deep Dive');
    await user.click(screen.getByRole('button', { name: /create new exam/i }));
    await user.type(screen.getByLabelText(/exam title/i), '   ');
    await user.click(screen.getByRole('button', { name: /^create exam$/i }));

    expect(createExam).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Give the exam a title.');
  });

  it('routes straight into the new exam so questions can be added', async () => {
    const user = userEvent.setup();
    renderExams();

    await screen.findByText('React Deep Dive');
    await user.click(screen.getByRole('button', { name: /create new exam/i }));
    await user.type(screen.getByLabelText(/exam title/i), 'SQL Screening');
    await user.click(screen.getByRole('button', { name: /^create exam$/i }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/hr/exams/e-new'));
  });

  it('surfaces a create failure instead of leaving a dead button', async () => {
    createExam.mockRejectedValue(new Error('Exam limit reached'));
    const user = userEvent.setup();
    renderExams();

    await screen.findByText('React Deep Dive');
    await user.click(screen.getByRole('button', { name: /create new exam/i }));
    await user.type(screen.getByLabelText(/exam title/i), 'SQL Screening');
    await user.click(screen.getByRole('button', { name: /^create exam$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Exam limit reached'));
    // Form stays open with the typed title so the work is not lost.
    expect(within(screen.getByRole('heading', { name: /new exam/i }).parentElement!).getByLabelText(/exam title/i)).toHaveValue('SQL Screening');
  });
});
