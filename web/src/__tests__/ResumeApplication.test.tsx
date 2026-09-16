// Resuming a saved application, and confirming what the CV said.
// PH3-B4c / PH3-B5.
//
// The two properties worth asserting in the UI rather than only on the server:
// a bad link says one thing and never says why (telling expired from submitted
// from never-existed would be a free oracle on a page anyone can reach), and
// the confirmation screen presents what the parser read as EDITABLE, never as
// a verdict.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ApplicationDraft } from '../api/publicApply';

const getDraft = vi.fn();
const saveDraft = vi.fn();
const confirmDraft = vi.fn();
const submitDraft = vi.fn();
const uploadDraftResume = vi.fn();

vi.mock('../api/publicApply', async () => {
  const actual = await vi.importActual<typeof import('../api/publicApply')>(
    '../api/publicApply',
  );
  return {
    ...actual,
    getDraft: (...a: unknown[]) => getDraft(...a) as unknown,
    saveDraft: (...a: unknown[]) => saveDraft(...a) as unknown,
    confirmDraft: (...a: unknown[]) => confirmDraft(...a) as unknown,
    submitDraft: (...a: unknown[]) => submitDraft(...a) as unknown,
    uploadDraftResume: (...a: unknown[]) => uploadDraftResume(...a) as unknown,
  };
});

import ResumeApplication from '../pages/ResumeApplication';

function draft(over: Partial<ApplicationDraft> = {}): ApplicationDraft {
  return {
    requisition_id: 'req-1',
    title: 'Platform Engineer',
    company_name: 'Acme',
    email: 'priya@example.com',
    full_name: null,
    phone: null,
    years_experience: null,
    current_company: null,
    current_title: null,
    linkedin_url: null,
    github_url: null,
    language: 'en',
    answers: {},
    resume_filename: null,
    has_resume: false,
    parsed: { full_name: null, email: null },
    confirmed: false,
    expires_at: '2099-01-01T00:00:00Z',
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/apply/draft/tok-123']}>
        <Routes>
          <Route path="/apply/draft/:token" element={<ResumeApplication />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getDraft.mockResolvedValue(draft());
  saveDraft.mockImplementation((_t: string, f: object) =>
    Promise.resolve(draft(f as Partial<ApplicationDraft>)),
  );
  confirmDraft.mockResolvedValue(draft({ confirmed: true, full_name: 'Priya S. Sharma' }));
  uploadDraftResume.mockResolvedValue(
    draft({ has_resume: true, resume_filename: 'cv.pdf' }),
  );
  submitDraft.mockResolvedValue({
    applicant_id: 'a-1',
    enrolment_id: 'e-1',
    full_name: 'Priya S. Sharma',
    already_applied: false,
    message: 'Thanks — your application is in.',
  });
});

// ===========================================================================
// A bad link says one thing
// ===========================================================================
describe('an invalid link', () => {
  it('gives one message without saying which failure it was', async () => {
    getDraft.mockRejectedValue(new Error('gone'));
    renderPage();
    expect(await screen.findByText(/This link no longer works/)).toBeInTheDocument();
    // Never names expired vs submitted vs never-existed.
    expect(screen.queryByText(/expired\b.*only/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/already submitted/i)).not.toBeInTheDocument();
  });

  it('tells them what to do instead', async () => {
    getDraft.mockRejectedValue(new Error('gone'));
    renderPage();
    expect(await screen.findByText(/start again from the job advert/)).toBeInTheDocument();
  });
});

// ===========================================================================
// PH3-B5 — what the parser read is editable, not a verdict
// ===========================================================================
describe('the confirmation step', () => {
  it('pre-fills the name the parser read', async () => {
    getDraft.mockResolvedValue(draft({ parsed: { full_name: 'Priya Sharma', email: null } }));
    renderPage();
    await waitFor(() =>
      expect(screen.getByLabelText(/Full name/)).toHaveValue('Priya Sharma'),
    );
  });

  it('lets the candidate change it', async () => {
    getDraft.mockResolvedValue(draft({ parsed: { full_name: 'Priya Sharma', email: null } }));
    renderPage();
    const name = await screen.findByLabelText(/Full name/);
    await userEvent.clear(name);
    await userEvent.type(name, 'Priya S. Sharma');
    expect(name).toHaveValue('Priya S. Sharma');
  });

  it('sends the correction, so the candidate wins over the parser', async () => {
    getDraft.mockResolvedValue(draft({ parsed: { full_name: 'Priya Sharma', email: null } }));
    renderPage();
    const name = await screen.findByLabelText(/Full name/);
    await userEvent.clear(name);
    await userEvent.type(name, 'Priya S. Sharma');
    await userEvent.click(screen.getByRole('button', { name: /These details are correct/ }));
    await waitFor(() =>
      expect(confirmDraft).toHaveBeenCalledWith(
        'tok-123',
        expect.objectContaining({ full_name: 'Priya S. Sharma' }),
      ),
    );
  });

  it('shows what the CV said when they have changed it', async () => {
    getDraft.mockResolvedValue(
      draft({ full_name: 'Priya S. Sharma', parsed: { full_name: 'Priya Sharma', email: null } }),
    );
    renderPage();
    expect(await screen.findByText(/Your CV says/)).toBeInTheDocument();
  });

  it('does not claim to have read anything when the parser found nothing', async () => {
    getDraft.mockResolvedValue(draft({ has_resume: true }));
    renderPage();
    expect(await screen.findByText(/Please fill these in/)).toBeInTheDocument();
    expect(screen.queryByText(/We read these from your CV/)).not.toBeInTheDocument();
  });

  it('will not confirm an empty name', async () => {
    renderPage();
    await screen.findByLabelText(/Full name/);
    expect(
      screen.getByRole('button', { name: /These details are correct/ }),
    ).toBeDisabled();
  });

  it('leaves every other field optional', async () => {
    renderPage();
    for (const label of [/Phone/, /Years of experience/, /Current employer/, /LinkedIn/]) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
    }
    // The label says so, so nobody thinks a blank box is a blocked application.
    expect(screen.getAllByText(/\(optional\)/).length).toBeGreaterThan(3);
  });
});

// ===========================================================================
// Submission is gated on what the server will actually accept
// ===========================================================================
describe('submitting', () => {
  it('is blocked, and says why, with no CV', async () => {
    renderPage();
    expect(await screen.findByText(/CV uploaded — still needed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit application/ })).toBeDisabled();
  });

  it('is blocked until the details are confirmed', async () => {
    getDraft.mockResolvedValue(draft({ has_resume: true, full_name: 'Priya' }));
    renderPage();
    expect(await screen.findByText(/Details confirmed — still needed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit application/ })).toBeDisabled();
  });

  it('is allowed once both are done', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya' }),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Submit application/ })).toBeEnabled(),
    );
  });

  it('confirms the application once it lands', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya' }),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Submit application/ })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole('button', { name: /Submit application/ }));
    expect(await screen.findByText(/your application is in/)).toBeInTheDocument();
  });

  it('surfaces a refusal rather than failing silently', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya' }),
    );
    submitDraft.mockRejectedValue(
      new Error('You applied for this role before and we are not able to consider a new application until 2026-12-05.'),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Submit application/ })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole('button', { name: /Submit application/ }));
    expect(await screen.findByText(/until 2026-12-05/)).toBeInTheDocument();
  });
});

// ===========================================================================
// Save & resume
// ===========================================================================
describe('saving', () => {
  it('says the progress is kept, and until when', async () => {
    renderPage();
    expect(await screen.findByText(/Your progress is saved/)).toBeInTheDocument();
  });

  it('saves without confirming', async () => {
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /Save and finish later/ }));
    await waitFor(() => expect(saveDraft).toHaveBeenCalled());
    expect(confirmDraft).not.toHaveBeenCalled();
  });

  it('warns that replacing the CV means checking the details again', async () => {
    getDraft.mockResolvedValue(draft({ has_resume: true, resume_filename: 'cv.pdf' }));
    renderPage();
    expect(await screen.findByText(/checking your details again/)).toBeInTheDocument();
  });
});
