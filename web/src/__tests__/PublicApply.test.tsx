// Tests for the public application form — Group E, E4.
//
// The only page in this product a stranger reaches, and the only one that
// collects a person's PII with no account behind it. What matters:
//
//   • CONSENT GATES SUBMISSION. The box is unticked, the button is dead until
//     it is ticked, and nothing is uploaded before then. It is the lawful basis
//     for holding the CV, so a form that could be submitted without it would
//     make the ledger entry the server writes a record of something that did
//     not happen.
//   • an opening that is not taking applications says so plainly and never
//     hints at why — closed, paused and never-published must be
//     indistinguishable, or the URL becomes a way to enumerate private roles.
//   • a repeat application reassures rather than alarms.
//   • the page never claims a decision was made.

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
  // The advert, empty by default — most openings predate these fields, so the
  // bare posting is the case worth defaulting to.
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

function pdf(name = 'cv.pdf', bytes = 1024): File {
  const file = new File([new Uint8Array(bytes)], name, { type: 'application/pdf' });
  // jsdom honours the constructor size, but pin it so the size guard is tested
  // against the number the test intends rather than a jsdom detail.
  Object.defineProperty(file, 'size', { value: bytes });
  return file;
}

const nextStep = async (user: ReturnType<typeof userEvent.setup>): Promise<void> => {
  await user.click(screen.getByRole('button', { name: 'Continue' }));
};

/**
 * Walk to the CV step and upload, without touching consent.
 *
 * The form is four steps now, so filling it is navigation rather than typing
 * into one page. Consent still lives on the last step with the submit button,
 * which is what the suite below is really about.
 */
async function fillForm(user: ReturnType<typeof userEvent.setup>, file = pdf()): Promise<void> {
  await user.type(screen.getByLabelText('Your name'), 'Priya Sharma');
  await user.type(screen.getByLabelText('Email'), 'priya@example.com');
  await nextStep(user); // -> Experience
  await nextStep(user); // -> Your CV (everything on Experience is optional)
  await user.upload(screen.getByLabelText('Your CV (PDF)'), file);
}

/** Walk all the way to the review step, still without consenting. */
async function fillToReview(
  user: ReturnType<typeof userEvent.setup>,
  file = pdf(),
): Promise<void> {
  await fillForm(user, file);
  await nextStep(user); // -> Review
}

/** Reach the CV step, for tests about the file itself. */
async function goToCvStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText('Your name'), 'Priya Sharma');
  await user.type(screen.getByLabelText('Email'), 'priya@example.com');
  await nextStep(user);
  await nextStep(user);
}

const submitButton = (): HTMLButtonElement =>
  screen.getByRole<HTMLButtonElement>('button', { name: 'Send application' });

beforeEach(() => {
  vi.clearAllMocks();
  getPosting.mockResolvedValue(POSTING);
  submitApplication.mockResolvedValue({
    applicant_id: 'ap-1',
    enrolment_id: 'en-1',
    full_name: 'Priya Sharma',
    already_applied: false,
    message: 'Thanks — your application is in. We will be in touch by email.',
  });
});

describe('PublicApply — the posting', () => {
  it('shows the role and the company that is hiring', async () => {
    renderPage();
    expect(await screen.findByText('Backend Engineer')).toBeTruthy();
    expect(screen.getByText('Acme')).toBeTruthy();
    expect(screen.getByText(/Build and maintain the APIs/)).toBeTruthy();
  });

  it('reads identically whatever the reason was', async () => {
    // The property, stated as it actually matters: closed, paused,
    // never-published and not-a-real-id must be indistinguishable, or the URL
    // becomes a way to enumerate a company's private roles. The page does
    // offer a general "may have been filled or closed" line — speculative
    // helper text for the applicant, not a claim about THIS opening — so the
    // test compares the rendered output across causes rather than banning
    // words.
    const renders: string[] = [];
    for (const err of [new Error('HTTP 404'), new Error('Not found'), new Error('')]) {
      getPosting.mockRejectedValue(err);
      const { unmount } = renderPage();
      const heading = await screen.findByText('This opening is not accepting applications');
      renders.push(heading.parentElement?.textContent ?? '');
      unmount();
    }
    expect(new Set(renders).size).toBe(1);
  });
});

describe('PublicApply — consent gates everything', () => {
  it('starts unticked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    expect(screen.getByRole('checkbox')).toHaveProperty('checked', false);
  });

  it('is not asked for before there is anything to consent to', async () => {
    // Consent is about storing a CV. Asking on step one, before one has been
    // chosen, would be asking about nothing — and it must be the last thing
    // that happens before the upload, not the first.
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('will not submit a complete form until consent is given', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    expect(submitButton().disabled).toBe(true);

    await user.click(screen.getByRole('checkbox'));
    expect(submitButton().disabled).toBe(false);
  });

  it('uploads nothing while consent is missing', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    await user.click(submitButton());

    expect(submitApplication).not.toHaveBeenCalled();
  });

  it('sends consent as the true value it was given, not as a constant', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());

    await waitFor(() => expect(submitApplication).toHaveBeenCalledTimes(1));
    expect(submitApplication).toHaveBeenCalledWith(
      'req-1',
      expect.objectContaining({
        fullName: 'Priya Sharma',
        email: 'priya@example.com',
        consentGranted: true,
      }),
    );
  });

  it('names the company in the consent text, because that is who holds the data', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    expect(screen.getByText(/I agree that Acme may store my name, email and CV/)).toBeTruthy();
  });
});

describe('PublicApply — the file', () => {
  it('rejects a file over 5 MB before it is uploaded', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await goToCvStep(user);
    await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf('big.pdf', 6 * 1024 * 1024));

    expect(screen.getByText(/over 5 MB/)).toBeTruthy();
    // Used to assert the submit button stayed disabled; submit is two steps
    // away now. The equivalent guarantee, and a slightly stronger one: a
    // rejected file does not let you leave the CV step at all, so there is no
    // path from here to a submit.
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Continue' }).disabled,
    ).toBe(true);
  });

  it('asks for a text-based PDF rather than letting a scan fail silently', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await goToCvStep(user);
    expect(screen.getByText(/not a scan/)).toBeTruthy();
  });
});

describe('PublicApply — afterwards', () => {
  it('confirms receipt without claiming any decision was made', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());

    expect(await screen.findByText('Application received')).toBeTruthy();
    expect(screen.getByText(/nothing is decided automatically/i)).toBeTruthy();
  });

  it('treats a repeat application as reassurance, not an error', async () => {
    const user = userEvent.setup();
    submitApplication.mockResolvedValue({
      applicant_id: 'ap-1',
      enrolment_id: 'en-1',
      full_name: 'Priya Sharma',
      already_applied: true,
      message: 'You have already applied for this role. We have your application.',
    });
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());

    expect(await screen.findByText('You have already applied')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows a failure in words the applicant can act on', async () => {
    const user = userEvent.setup();
    submitApplication.mockRejectedValue(
      new Error('We could not read that PDF. If it is a scan, please upload a text-based version.'),
    );
    renderPage();
    await screen.findByText('Backend Engineer');

    await fillToReview(user);
    await user.click(screen.getByRole('checkbox'));
    await user.click(submitButton());

    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      expect.stringContaining('could not read that PDF'),
    );
  });
});
