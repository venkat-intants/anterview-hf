// Recruiter-defined questions on the apply form.
//
// The step is conditional, which is the first thing worth pinning: most
// openings ask nothing, and a "Questions" step with nothing on it makes the
// form look longer than it is.
//
// After that it is mostly about the required ones. The server refuses an
// application missing a required answer, so a form that lets you reach the
// submit button without one turns a validation rule into a rejected
// application — after the CV has been chosen and the consent given, which is
// the worst possible moment to be told.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Posting, PostingQuestion } from '../api/publicApply';

const getPosting = vi.fn();
const submitApplication = vi.fn();
vi.mock('../api/publicApply', () => ({
  getPosting: (...a: unknown[]) => getPosting(...a) as unknown,
  submitApplication: (...a: unknown[]) => submitApplication(...a) as unknown,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ requisitionId: 'req-1' }) };
});

import PublicApply from '../pages/PublicApply';

function question(over: Partial<PostingQuestion> = {}): PostingQuestion {
  return {
    id: 'q-1',
    prompt: 'Why are you interested in this role?',
    kind: 'long_text',
    help_text: null,
    required: false,
    options: [],
    ...over,
  };
}

function posting(questions: PostingQuestion[]): Posting {
  return {
    requisition_id: 'req-1',
    title: 'Backend Engineer',
    level: 'mid',
    company_name: 'Acme',
    jd_text: 'Build things.',
    closes_at: null,
    department: null,
    location: null,
    employment_type: null,
    experience_min_years: null,
    experience_max_years: null,
    responsibilities: [],
    required_skills: [],
    nice_to_have_skills: [],
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    questions,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PublicApply />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function pdf(): File {
  const file = new File([new Uint8Array(1024)], 'cv.pdf', { type: 'application/pdf' });
  Object.defineProperty(file, 'size', { value: 1024 });
  return file;
}

type User = ReturnType<typeof userEvent.setup>;
const cont = (user: User) => user.click(screen.getByRole('button', { name: 'Continue' }));
const contButton = () => screen.getByRole<HTMLButtonElement>('button', { name: 'Continue' });

/** Walk to the questions step. */
async function toQuestions(user: User): Promise<void> {
  await user.type(screen.getByLabelText('Your name'), 'Priya Sharma');
  await user.type(screen.getByLabelText('Email'), 'priya@example.com');
  await cont(user);
  await cont(user);
  await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf());
  await cont(user);
}

beforeEach(() => {
  vi.clearAllMocks();
  submitApplication.mockResolvedValue({
    applicant_id: 'ap-1',
    enrolment_id: 'en-1',
    full_name: 'Priya Sharma',
    already_applied: false,
    message: 'Thanks.',
  });
});

describe('the questions step exists only when there are questions', () => {
  it('is absent when the opening asks nothing', async () => {
    getPosting.mockResolvedValue(posting([]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    // Straight to review — the CV name is on it.
    expect(screen.getByText('cv.pdf')).toBeInTheDocument();
    expect(screen.queryByText('Questions')).not.toBeInTheDocument();
  });

  it('appears when it asks something', async () => {
    getPosting.mockResolvedValue(posting([question()]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    expect(
      screen.getByLabelText(/Why are you interested in this role\?/),
    ).toBeInTheDocument();
  });
});

describe('required questions gate the form', () => {
  it('will not advance past an unanswered required question', async () => {
    getPosting.mockResolvedValue(posting([question({ required: true })]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    expect(contButton().disabled).toBe(true);
  });

  it('advances once it is answered', async () => {
    getPosting.mockResolvedValue(posting([question({ required: true })]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    await user.type(screen.getByLabelText(/Why are you interested/), 'Interesting work');
    expect(contButton().disabled).toBe(false);
  });

  it('treats whitespace as unanswered, like the server does', async () => {
    getPosting.mockResolvedValue(posting([question({ required: true })]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    await user.type(screen.getByLabelText(/Why are you interested/), '   ');
    expect(contButton().disabled).toBe(true);
  });

  it('does not block on an optional question', async () => {
    getPosting.mockResolvedValue(posting([question({ required: false })]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    expect(contButton().disabled).toBe(false);
  });

  it('marks which questions are optional', async () => {
    getPosting.mockResolvedValue(posting([question({ required: false })]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    expect(screen.getByText('(optional)')).toBeInTheDocument();
  });
});

describe('each kind renders as something you can actually use', () => {
  async function open(q: PostingQuestion): Promise<User> {
    getPosting.mockResolvedValue(posting([q]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);
    return user;
  }

  it('a number question takes a number', async () => {
    const user = await open(question({ kind: 'number', prompt: 'Years of Python?' }));
    const field = screen.getByLabelText(/Years of Python\?/);
    expect(field).toHaveAttribute('type', 'number');
    await user.type(field, '6');
    expect(field).toHaveValue(6);
  });

  it('a yes/no question offers exactly two answers', async () => {
    await open(question({ kind: 'yes_no', prompt: 'Willing to relocate?' }));
    expect(screen.getByText('Yes')).toBeInTheDocument();
    expect(screen.getByText('No')).toBeInTheDocument();
  });

  it('a single choice offers what the recruiter listed, and nothing else', async () => {
    await open(
      question({ kind: 'single_choice', prompt: 'Notice period?', options: ['Immediate', '30 days'] }),
    );
    const select = screen.getByLabelText(/Notice period\?/);
    expect(select).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Immediate' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '30 days' })).toBeInTheDocument();
  });

  it('a multi choice lets several be picked', async () => {
    const user = await open(
      question({ kind: 'multi_choice', prompt: 'Which do you know?', options: ['Python', 'Go'] }),
    );
    await user.click(screen.getByText('Python'));
    await user.click(screen.getByText('Go'));
    const boxes = screen.getAllByRole<HTMLInputElement>('checkbox');
    expect(boxes.filter((b) => b.checked)).toHaveLength(2);
  });

  it('shows the recruiter help text when there is any', async () => {
    await open(question({ help_text: 'A couple of sentences is plenty.' }));
    expect(screen.getByText('A couple of sentences is plenty.')).toBeInTheDocument();
  });
});

describe('the answers reach the server', () => {
  it('sends them keyed by question id, in the shapes the server validates', async () => {
    getPosting.mockResolvedValue(
      posting([
        question({ id: 'q-why', prompt: 'Why?', kind: 'long_text', required: true }),
        question({ id: 'q-yrs', prompt: 'Years?', kind: 'number' }),
        question({ id: 'q-rel', prompt: 'Relocate?', kind: 'yes_no' }),
        question({
          id: 'q-lang',
          prompt: 'Which?',
          kind: 'multi_choice',
          options: ['Python', 'Go'],
        }),
      ]),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);

    await user.type(screen.getByLabelText(/Why\?/), 'Interesting problems');
    await user.type(screen.getByLabelText(/Years\?/), '6');
    await user.click(screen.getByText('Yes'));
    await user.click(screen.getByText('Python'));
    await cont(user);
    // The consent box, which is the only checkbox on the review step — the
    // multi-choice ones belong to the questions step and are gone by now.
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Send application' }));

    await waitFor(() =>
      expect(submitApplication).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          answers: {
            'q-why': 'Interesting problems',
            'q-yrs': '6',
            'q-rel': true,
            'q-lang': ['Python'],
          },
        }),
      ),
    );
  });

  it('shows the answers on the review step', async () => {
    // Reviewing an application that hides half of what it will send is not a
    // review.
    getPosting.mockResolvedValue(
      posting([question({ id: 'q-why', prompt: 'Why?', kind: 'long_text' })]),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toQuestions(user);
    await user.type(screen.getByLabelText(/Why\?/), 'Interesting problems');
    await cont(user);

    expect(screen.getByText('Interesting problems')).toBeInTheDocument();
  });
});
