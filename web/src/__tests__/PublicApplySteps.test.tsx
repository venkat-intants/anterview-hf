// The multi-step application flow.
//
// `PublicApply.test.tsx` covers what the form is FOR — consent, the file, the
// posting, the confirmation. This covers the stepping that was added around it,
// and the two things that stepping can quietly break:
//
//   • CONSENT MUST STAY LAST. Splitting a form is exactly how a "tick to agree"
//     drifts to step one, where it is asked before there is a CV to consent to.
//     Several tests here exist only to keep it where it is.
//   • AN OPTIONAL STEP MUST BE SKIPPABLE IN ONE CLICK. The middle step asks for
//     things a candidate may not want to answer; a required field there turns a
//     preference into a barrier, and the application is abandoned silently
//     because nothing is stored until the end.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Posting } from '../api/publicApply';

const POSTING: Posting = {
  requisition_id: 'req-1',
  title: 'Backend Engineer',
  level: 'mid',
  company_name: 'Acme',
  jd_text: 'Build and maintain the APIs behind our product.',
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
  // Most openings ask nothing; the questions step is not rendered at all.
  questions: [],
};

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

function pdf(name = 'cv.pdf'): File {
  const file = new File([new Uint8Array(1024)], name, { type: 'application/pdf' });
  Object.defineProperty(file, 'size', { value: 1024 });
  return file;
}

type User = ReturnType<typeof userEvent.setup>;
const cont = (user: User) => user.click(screen.getByRole('button', { name: 'Continue' }));
const contButton = () => screen.getByRole<HTMLButtonElement>('button', { name: 'Continue' });

async function identity(user: User): Promise<void> {
  await user.type(screen.getByLabelText('Your name'), 'Priya Sharma');
  await user.type(screen.getByLabelText('Email'), 'priya@example.com');
}

beforeEach(() => {
  vi.clearAllMocks();
  getPosting.mockResolvedValue(POSTING);
  submitApplication.mockResolvedValue({
    applicant_id: 'ap-1',
    enrolment_id: 'en-1',
    full_name: 'Priya Sharma',
    already_applied: false,
    message: 'Thanks — your application is in.',
  });
});

describe('PublicApply — stepping', () => {
  it('starts on the identity step', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(screen.getByLabelText('Your name')).toBeInTheDocument();
    expect(screen.queryByLabelText('Your CV (PDF)')).not.toBeInTheDocument();
  });

  it('will not advance without a name and an email', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(contButton().disabled).toBe(true);
  });

  it('advances once they are given', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    expect(contButton().disabled).toBe(false);
    await cont(user);
    expect(screen.getByLabelText('Current company')).toBeInTheDocument();
  });

  it('lets the optional step be skipped in one click', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    await cont(user);

    await user.click(screen.getByRole('button', { name: 'Skip' }));
    expect(screen.getByLabelText('Your CV (PDF)')).toBeInTheDocument();
  });

  it('never blocks on an optional answer', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    await cont(user);
    expect(contButton().disabled).toBe(false);
  });

  it('keeps what was typed when you go back', async () => {
    // Losing answers on Back is how a four-step form becomes worse than a
    // one-page one.
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    await cont(user);
    await user.type(screen.getByLabelText('Current company'), 'Globex');
    await user.click(screen.getByRole('button', { name: 'Back' }));

    expect(screen.getByLabelText('Your name')).toHaveValue('Priya Sharma');
    await cont(user);
    expect(screen.getByLabelText('Current company')).toHaveValue('Globex');
  });

  it('will not leave the CV step without a CV', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    await cont(user);
    await cont(user);
    expect(contButton().disabled).toBe(true);
  });
});

describe('PublicApply — consent stays last', () => {
  async function toReview(user: User): Promise<void> {
    await identity(user);
    await cont(user);
    await cont(user);
    await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf());
    await cont(user);
  }

  it('is absent from every step before the review', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    await identity(user);
    await cont(user);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    await cont(user);
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('appears on the review step, unticked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toReview(user);
    expect(screen.getByRole('checkbox')).toHaveProperty('checked', false);
  });

  it('still gates the submit button', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toReview(user);

    const send = screen.getByRole<HTMLButtonElement>('button', { name: 'Send application' });
    expect(send.disabled).toBe(true);
    await user.click(screen.getByRole('checkbox'));
    expect(send.disabled).toBe(false);
  });

  it('uploads nothing until consent is given, however far you walk', async () => {
    // The invariant the whole split had to preserve: reaching the last step is
    // not consenting, and nothing leaves the browser until the box is ticked.
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await toReview(user);
    expect(submitApplication).not.toHaveBeenCalled();
  });
});

describe('PublicApply — the review step', () => {
  async function walkWithDetails(user: User): Promise<void> {
    await identity(user);
    await cont(user);
    await user.type(screen.getByLabelText('Years of experience'), '6');
    await user.type(screen.getByLabelText('Current company'), 'Globex');
    await cont(user);
    await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf('priya_cv.pdf'));
    await cont(user);
  }

  it('shows back what was entered, including the file', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await walkWithDetails(user);

    expect(screen.getByText('Priya Sharma')).toBeInTheDocument();
    expect(screen.getByText('priya@example.com')).toBeInTheDocument();
    expect(screen.getByText('6 years')).toBeInTheDocument();
    expect(screen.getByText('Globex')).toBeInTheDocument();
    expect(screen.getByText('priya_cv.pdf')).toBeInTheDocument();
  });

  it('says which answers were left out rather than showing a blank', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    await cont(user);
    await cont(user);
    await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf());
    await cont(user);

    expect(screen.getAllByText('Not provided').length).toBeGreaterThan(0);
  });

  it('sends the optional answers that were given', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await walkWithDetails(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Send application' }));

    await waitFor(() =>
      expect(submitApplication).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          fullName: 'Priya Sharma',
          currentCompany: 'Globex',
          yearsExperience: 6,
          consentGranted: true,
        }),
      ),
    );
  });

  it('sends null for an experience the candidate skipped, not zero', async () => {
    // Zero years is a real answer — "I did not say" must not become it.
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await identity(user);
    await cont(user);
    await cont(user);
    await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf());
    await cont(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Send application' }));

    await waitFor(() =>
      expect(submitApplication).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ yearsExperience: null }),
      ),
    );
  });
});
