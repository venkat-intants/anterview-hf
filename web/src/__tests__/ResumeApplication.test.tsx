// Resuming a saved application, and confirming what the CV said.
// PH3-B4c / PH3-B5.
//
// The two properties worth asserting in the UI rather than only on the server:
// a bad link says one thing and never says why (telling expired from submitted
// from never-existed would be a free oracle on a page anyone can reach), and
// the confirmation screen presents what the parser read as EDITABLE, never as
// a verdict.

import { readFileSync } from 'node:fs';

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ApplicationDraft } from '../api/publicApply';

const getDraft = vi.fn();
const saveDraft = vi.fn();
const confirmDraft = vi.fn();
const submitDraft = vi.fn();
const uploadDraftResume = vi.fn();
const deleteDraft = vi.fn();

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
    deleteDraft: (...a: unknown[]) => deleteDraft(...a) as unknown,
  };
});

import ResumeApplication from '../pages/ResumeApplication';

// Relative to the web/ root, which is vitest's cwd.
const appSource = readFileSync('src/App.tsx', 'utf-8');

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
  // The token lives in the URL fragment now, not the path — jsdom's location
  // is what the page reads, so set it rather than routing a param. A test that
  // wants a different token sets the hash itself before calling this.
  if (!window.location.hash) window.location.hash = '#tok-123';
  const client = new QueryClient({
    // refetchOnWindowFocus off: a jsdom focus event mid-interaction makes React
    // Query hand the component a new draft object, which re-runs the seeding
    // effect and discards what was just typed. Nothing here tests refetching.
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/apply/draft']}>
        <ResumeApplication />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = '';
  getDraft.mockResolvedValue(draft());
  saveDraft.mockImplementation((_t: string, f: object) =>
    Promise.resolve(draft(f as Partial<ApplicationDraft>)),
  );
  confirmDraft.mockResolvedValue(draft({ confirmed: true, full_name: 'Priya S. Sharma' }));
  uploadDraftResume.mockResolvedValue(
    draft({ has_resume: true, resume_filename: 'cv.pdf' }),
  );
  deleteDraft.mockResolvedValue(undefined);
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
    // fireEvent.change rather than clear-then-type, deliberately.
    //
    // The confirm button is disabled while the name is empty, and clear() puts
    // the field through exactly that state. Locally the typed value always
    // flushed before the click; on a loaded CI runner it did not, the click
    // landed on a disabled button, and confirmDraft was never called — a
    // failure that says "expected spy to be called" and looks like a logic bug
    // in the component rather than a race in the test.
    //
    // What this test is actually about is the PAYLOAD: that the candidate's
    // correction is what gets sent, not the parser's guess. Setting the value
    // in one step asserts exactly that and has no intermediate state to race.
    // The typing interaction itself is covered by the test above.
    getDraft.mockResolvedValue(draft({ parsed: { full_name: 'Priya Sharma', email: null } }));
    renderPage();
    const name = await screen.findByLabelText(/Full name/);
    fireEvent.change(name, { target: { value: 'Priya S. Sharma' } });
    await userEvent.click(
      await screen.findByRole('button', { name: /These details are correct/ }),
    );
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


// ===========================================================================
// DPDP — the data principal can act, not just wait
// ===========================================================================
describe('deleting a saved application', () => {
  it('is offered, and says it cannot be undone', async () => {
    renderPage();
    expect(
      await screen.findByRole('button', { name: /Delete my saved application/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();
  });

  it('takes two clicks — it destroys their work', async () => {
    renderPage();
    await userEvent.click(
      await screen.findByRole('button', { name: /Delete my saved application/ }),
    );
    expect(deleteDraft).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole('button', { name: /Confirm — delete everything/ }),
    );
    await waitFor(() => expect(deleteDraft).toHaveBeenCalledWith('tok-123'));
  });

  it('can be backed out of', async () => {
    renderPage();
    await userEvent.click(
      await screen.findByRole('button', { name: /Delete my saved application/ }),
    );
    await userEvent.click(screen.getByRole('button', { name: /Keep it/ }));
    expect(
      screen.getByRole('button', { name: /Delete my saved application/ }),
    ).toBeInTheDocument();
    expect(deleteDraft).not.toHaveBeenCalled();
  });

  it('confirms what was removed afterwards', async () => {
    renderPage();
    await userEvent.click(
      await screen.findByRole('button', { name: /Delete my saved application/ }),
    );
    await userEvent.click(
      screen.getByRole('button', { name: /Confirm — delete everything/ }),
    );
    expect(await screen.findByText(/has been deleted/)).toBeInTheDocument();
    expect(screen.getByText(/CV you uploaded/)).toBeInTheDocument();
  });
});

// ===========================================================================
// The token never reaches a server log
// ===========================================================================
describe('the resume token', () => {
  it('is taken from the URL fragment', async () => {
    // Behavioural, not a source grep: the page is rendered at a path carrying
    // NO token, and the only place `tok-123` exists is window.location.hash.
    // If the page ever went back to reading a route param, this call would be
    // made with the wrong value or not at all.
    window.location.hash = '#tok-from-fragment';
    renderPage();
    await waitFor(() => expect(getDraft).toHaveBeenCalled());
    expect(getDraft).toHaveBeenCalledWith('tok-from-fragment');
  });

  it('is never put in a path the browser would log or send as a Referer', () => {
    // The whole point of the fragment: it does not leave the client. Assert the
    // route the app registers carries no token segment.
    const routes = appSource.match(/path="\/apply\/draft[^"]*"/g) ?? [];
    expect(routes.length).toBeGreaterThan(0);
    for (const r of routes) expect(r).not.toContain(':token');
  });
});
