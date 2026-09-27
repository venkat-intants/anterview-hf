// "Save and finish later" on the public application form (PH3-B4).
//
// Saving records who the person is and that they agreed — the draft holds an
// email and a CV, so consent is taken at the FIRST save rather than at submit
// (PH3-B4c). What these pin is the part that used to be missing: the answers
// they had already typed travel with the draft. Without that, "continue where
// you left off" opened an empty form, and the person retyped everything.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const POSTING = {
  id: 'req-1',
  title: 'Backend Engineer',
  company_name: 'Acme Test Co',
  level: 'mid',
  jd_text: 'Build things.',
  department: null,
  location: null,
  employment_type: null,
  experience_min_years: null,
  experience_max_years: null,
  must_have_skills: [],
  nice_to_have_skills: [],
  salary_min: null,
  salary_max: null,
  salary_currency: null,
  questions: [],
  source: 'direct',
  source_detail: null,
};

const getPosting = vi.fn();
const submitApplication = vi.fn();
const startDraft = vi.fn();
const saveDraft = vi.fn();
const uploadDraftResume = vi.fn();
vi.mock('../api/publicApply', () => ({
  getPosting: (...a: unknown[]) => getPosting(...a) as unknown,
  submitApplication: (...a: unknown[]) => submitApplication(...a) as unknown,
  startDraft: (...a: unknown[]) => startDraft(...a) as unknown,
  saveDraft: (...a: unknown[]) => saveDraft(...a) as unknown,
  uploadDraftResume: (...a: unknown[]) => uploadDraftResume(...a) as unknown,
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

function pdf(): File {
  return new File(['%PDF-1.4 cv'], 'cv.pdf', { type: 'application/pdf' });
}

/** Fill the form as far as the consent box, the way a person does. */
async function fillToConsent(user: ReturnType<typeof userEvent.setup>) {
  renderPage();
  await screen.findByText('Backend Engineer');
  await user.type(screen.getByLabelText('Your name'), 'Sunita Rao');
  await user.type(screen.getByLabelText('Email'), 'sunita@example.com');
  await user.click(screen.getByRole('button', { name: 'Continue' }));
  await user.click(await screen.findByRole('button', { name: 'Skip' }));
  await user.upload(screen.getByLabelText('Your CV (PDF)'), pdf());
  await user.click(screen.getByRole('button', { name: 'Continue' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getPosting.mockResolvedValue(POSTING);
  startDraft.mockResolvedValue({ resume_token: 'draft_tok_123456', email: 'sunita@example.com' });
  saveDraft.mockResolvedValue({});
  uploadDraftResume.mockResolvedValue({});
});

describe('saving an application for later', () => {
  it('cannot be saved before consent, because saving stores their email', async () => {
    const user = userEvent.setup();
    await fillToConsent(user);

    const save = await screen.findByRole('button', { name: 'Save and finish later' });
    // Visible but inert: the reason it cannot be pressed is the checkbox right
    // above it. A hidden control would just look broken.
    expect(save).toBeVisible();
    expect(save).toBeDisabled();
    expect(startDraft).not.toHaveBeenCalled();
  });

  it('carries the details they already typed into the draft', async () => {
    const user = userEvent.setup();
    await fillToConsent(user);
    await user.click(
      screen.getByRole('checkbox', { name: /may store my name, email and CV/i }),
    );
    await user.click(screen.getByRole('button', { name: 'Save and finish later' }));

    await waitFor(() => expect(startDraft).toHaveBeenCalled());
    // Starting a draft records only who they are and that they agreed, so the
    // typing has to follow it — otherwise they come back to an empty form.
    await waitFor(() => expect(saveDraft).toHaveBeenCalled());
    expect(saveDraft.mock.calls[0][0]).toBe('draft_tok_123456');
    expect(saveDraft.mock.calls[0][1]).toMatchObject({ full_name: 'Sunita Rao' });
  });

  it('carries the CV too, as bytes and not just a filename', async () => {
    const user = userEvent.setup();
    await fillToConsent(user);
    await user.click(
      screen.getByRole('checkbox', { name: /may store my name, email and CV/i }),
    );
    await user.click(screen.getByRole('button', { name: 'Save and finish later' }));

    await waitFor(() => expect(uploadDraftResume).toHaveBeenCalled());
    expect(uploadDraftResume.mock.calls[0][0]).toBe('draft_tok_123456');
    expect((uploadDraftResume.mock.calls[0][1] as File).name).toBe('cv.pdf');
  });

  it('still shows the link when carrying the details fails', async () => {
    // The draft exists by then, and its link is the only way back to it.
    // Losing a typed name is a papercut; losing the draft is the application.
    saveDraft.mockRejectedValue(new Error('network'));
    uploadDraftResume.mockRejectedValue(new Error('network'));
    const user = userEvent.setup();
    await fillToConsent(user);
    await user.click(
      screen.getByRole('checkbox', { name: /may store my name, email and CV/i }),
    );
    await user.click(screen.getByRole('button', { name: 'Save and finish later' }));

    expect(
      await screen.findByText('Saved. Keep this link to carry on later.'),
    ).toBeInTheDocument();
    const link = screen.getByLabelText<HTMLInputElement>('Your resume link');
    expect(link.value).toContain('draft_tok_123456');
  });
});
