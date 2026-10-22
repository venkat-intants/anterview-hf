// Resuming a saved application, and confirming what the CV said.
// PH3-B4c / PH3-B5.
//
// The two properties worth asserting in the UI rather than only on the server:
// a bad link says one thing and never says why (telling expired from submitted
// from never-existed would be a free oracle on a page anyone can reach), and
// the confirmation screen presents what the parser read as EDITABLE, never as
// a verdict.

import { readFileSync } from 'node:fs';

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ApplicationDraft } from '../api/publicApply';
import i18n from '../lib/i18n';

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

// The finish states read the session (to decide whether to offer a sign-in link).
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: false, isInitializing: false, user: null }),
}));

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
  // The page keeps its token in this tab's storage between reloads (CA-H3).
  sessionStorage.clear();
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
    message: 'Thanks - your application is in.',
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
    // The field renders empty and is seeded from the parsed CV by an effect.
    // Wait for the seed before editing: on a loaded runner the effect could
    // otherwise land between clear() and type() and overwrite what was typed —
    // the cause of this test's intermittent CI failure.
    await waitFor(() => expect(name).toHaveValue('Priya Sharma'));
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
    // Same seeding race as "lets the candidate change it": change the value
    // only once the parsed name has been seeded, or the seed can overwrite it.
    await waitFor(() => expect(name).toHaveValue('Priya Sharma'));
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
    expect(await screen.findByText(/CV uploaded - still needed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit application/ })).toBeDisabled();
  });

  it('is blocked until the details are confirmed', async () => {
    getDraft.mockResolvedValue(draft({ has_resume: true, full_name: 'Priya' }));
    renderPage();
    expect(await screen.findByText(/Details confirmed - still needed/)).toBeInTheDocument();
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
    // The finish state says what happens next and who acts, and offers the sign-in route.
    const next = screen.getByRole('heading', { name: 'What happens next' }).closest("section") as HTMLElement;
    expect(next).toHaveTextContent('will read your application');
    expect(within(next).getByRole("link", { name: 'Track this in My applications' })).toHaveAttribute('href', '/login');
  });

  it('surfaces a refusal rather than failing silently', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya' }),
    );
    submitDraft.mockRejectedValue(
      new Error('You applied for this role before and we are not able to consider a new application until 05 Dec 2026.'),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Submit application/ })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole('button', { name: /Submit application/ }));
    expect(await screen.findByText(/until 05 Dec 2026/)).toBeInTheDocument();
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

  // Audit CA-M22: a save used to say nothing either way.
  it('says it saved', async () => {
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /Save and finish later/ }));
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
  });

  it('says when a save failed', async () => {
    saveDraft.mockRejectedValue(new Error('We could not save your progress just now.'));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /Save and finish later/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not save/i);
  });
});

// ===========================================================================
// Audit CA-M22: a confirmation vouches for particular values
// ===========================================================================
describe('editing after confirming', () => {
  it('asks for the details to be confirmed again before submitting', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya' }),
    );
    renderPage();
    const submit = await screen.findByRole('button', { name: /Submit application/ });
    await waitFor(() => expect(submit).toBeEnabled());

    const name = screen.getByLabelText(/Full name/);
    await userEvent.type(name, ' Sharma');
    expect(screen.getByText(/changed these details since you confirmed them/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit application/ })).toBeDisabled();
    expect(screen.getByText(/Details confirmed - still needed/)).toBeInTheDocument();
  });
});

// ===========================================================================
// Audit CA-H1: the opening's own questions are asked here too
// ===========================================================================
describe('screening questions', () => {
  const QUESTION = {
    id: 'q-1',
    prompt: 'Can you work night shifts?',
    kind: 'yes_no' as const,
    help_text: null,
    required: true,
    options: [],
  };

  it('asks them, and holds submission until a required one is answered', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya', questions: [QUESTION] }),
    );
    renderPage();
    expect(await screen.findByText('Can you work night shifts?')).toBeInTheDocument();
    expect(screen.getByText(/Required questions answered - still needed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit application/ })).toBeDisabled();

    await userEvent.click(screen.getByRole('radio', { name: 'Yes' }));
    expect(screen.getByRole('button', { name: /Submit application/ })).toBeEnabled();
  });

  it('saves the answers before submitting, so what is sent is what is on screen', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya', questions: [QUESTION] }),
    );
    renderPage();
    await userEvent.click(await screen.findByRole('radio', { name: 'Yes' }));
    await userEvent.click(screen.getByRole('button', { name: /Submit application/ }));

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    expect(saveDraft).toHaveBeenCalledWith('tok-123', { answers: { 'q-1': true } });
    expect(saveDraft.mock.invocationCallOrder[0]).toBeLessThan(
      submitDraft.mock.invocationCallOrder[0],
    );
  });

  it('saves answers with "Save and finish later"', async () => {
    getDraft.mockResolvedValue(draft({ questions: [QUESTION] }));
    renderPage();
    await userEvent.click(await screen.findByRole('radio', { name: 'No' }));
    await userEvent.click(screen.getByRole('button', { name: /Save and finish later/ }));
    await waitFor(() =>
      expect(saveDraft).toHaveBeenCalledWith(
        'tok-123',
        expect.objectContaining({ answers: { 'q-1': false } }),
      ),
    );
  });
});

// ===========================================================================
// Audit CA-H3: the page tells them this link is the only way back in
// ===========================================================================
describe('a reload', () => {
  it('keeps the draft open after the address bar was scrubbed', async () => {
    window.location.hash = '#tok-reload';
    const first = renderPage();
    await waitFor(() => expect(getDraft).toHaveBeenCalledWith('tok-reload'));
    expect(window.location.hash).toBe('');
    first.unmount();
    getDraft.mockClear();

    // A reload: same tab, no fragment any more. renderPage only sets a hash
    // when none is present, so set the path explicitly without one.
    window.history.replaceState(null, '', '/apply/draft');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/apply/draft']}>
          <ResumeApplication />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(getDraft).toHaveBeenCalledWith('tok-reload'));
  });

  it('forgets the token once the application is sent', async () => {
    getDraft.mockResolvedValue(
      draft({ has_resume: true, confirmed: true, full_name: 'Priya' }),
    );
    renderPage();
    const submit = await screen.findByRole('button', { name: /Submit application/ });
    await waitFor(() => expect(submit).toBeEnabled());
    await userEvent.click(submit);
    await screen.findByText(/your application is in/);
    expect(sessionStorage.getItem('anthire.draft.token')).toBeNull();
  });

  it('does not tell them to ask for a new link nobody can issue', async () => {
    getDraft.mockRejectedValue(new Error('Not found'));
    renderPage();
    await screen.findByText('This link no longer works');
    expect(screen.queryByText(/ask the person who invited you/i)).toBeNull();
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

  it('takes two clicks - it destroys their work', async () => {
    renderPage();
    await userEvent.click(
      await screen.findByRole('button', { name: /Delete my saved application/ }),
    );
    expect(deleteDraft).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole('button', { name: /Confirm - delete everything/ }),
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
      screen.getByRole('button', { name: /Confirm - delete everything/ }),
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

// ===========================================================================
// PH3-B5 criterion 11 — candidate-facing text follows existing localization
//
// The confirmation screen was English-only: its copy never reached the i18n
// bundles, so a candidate who had chosen हिंदी or తెలుగు everywhere else hit
// English at the one screen that decides what gets stored about them.
//
// Asserting the STRINGS render, not merely that keys exist: a key present in
// `en` and missing in `hi` silently falls back to English, which is exactly
// the bug, and would pass any test that only checked for a key's presence.
// ===========================================================================
describe('ResumeApplication - localisation', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it.each([
    ['hi', 'अपने विवरण जाँचें', 'आपका CV'],
    ['te', 'మీ వివరాలను సరిచూడండి', 'మీ CV'],
  ])('renders the confirmation screen in %s', async (lng, checkTitle, cvTitle) => {
    getDraft.mockResolvedValue(draft({ has_resume: true }));
    await i18n.changeLanguage(lng);
    renderPage();

    expect(await screen.findByText(checkTitle)).toBeInTheDocument();
    expect(screen.getByText(cvTitle)).toBeInTheDocument();
    // And the English it replaced is genuinely gone — a fallback would leave it.
    expect(screen.queryByText('Check your details')).not.toBeInTheDocument();
  });

  it('translates the delete control, which is the DPDP right to act', async () => {
    getDraft.mockResolvedValue(draft({ has_resume: true }));
    await i18n.changeLanguage('hi');
    renderPage();

    expect(
      await screen.findByRole('button', { name: 'मेरा सहेजा गया आवेदन हटाएँ' }),
    ).toBeInTheDocument();
  });

  it('keeps every bundle in step, key for key', () => {
    // The real failure mode is not a bad translation — it is the NEXT person
    // adding an English key and forgetting the other two, which falls back to
    // English silently. This makes that a red test rather than a live defect.
    // getResourceBundle is typed `any`, so the cast happens BEFORE the member
    // access — reaching through an `any` trips no-unsafe-member-access, which
    // the web lint runs with --max-warnings 0.
    const bundles = ['en', 'hi', 'te'].map((l) => {
      const bundle = i18n.getResourceBundle(l, 'translation') as {
        resumeApply: Record<string, string>;
      };
      return bundle.resumeApply;
    });
    const [en, hi, te] = bundles;
    expect(Object.keys(en).length).toBeGreaterThan(40);
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort());
    expect(Object.keys(te).sort()).toEqual(Object.keys(en).sort());

    // Every value actually differs from English, except the proper nouns that
    // are deliberately left alone across all locales.
    const PROPER_NOUNS = new Set(['linkedin', 'github']);
    for (const key of Object.keys(en)) {
      if (PROPER_NOUNS.has(key)) continue;
      expect(hi[key], `hi.${key} was left in English`).not.toBe(en[key]);
      expect(te[key], `te.${key} was left in English`).not.toBe(en[key]);
    }
  });
});

// ===========================================================================
// The seed is idempotent
//
// Written while chasing a flaky failure of "lets the candidate change it" — the
// same commit passed one CI run and failed the next. I believed the cause was
// `refetchOnWindowFocus: true` (set app-wide in main.tsx) re-running the
// seeding effect and overwriting typed values.
//
// THAT WAS WRONG, and this test is what showed it: the first version passed
// with the guard REMOVED. React Query's structural sharing returns the same
// object reference for deeply-equal data, and DraftOut has no per-fetch
// changing field, so the effect never re-fires on an unchanged refetch. A
// candidate tabbing away does not lose their edits.
//
// Kept because the property is still worth holding: a refetch must not reset
// the form, whatever churns the object identity. The flake itself remains
// unexplained — it is not reproducible locally (repeated clean runs) and this
// guard is not claimed to fix it.
//
// UPDATE (PH4 Wave 2): the flake was the test typing before the seeding
// effect had run — the field renders empty first, and an effect landing
// between clear() and type() overwrote the typed text. The test now waits for
// the seeded value before editing.
// ===========================================================================
describe('ResumeApplication - the seed is idempotent', () => {
  it('a refetch of unchanged data leaves typed values alone', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    getDraft.mockResolvedValue(draft({ parsed: { full_name: 'Priya Sharma', email: null } }));
    if (!window.location.hash) window.location.hash = '#tok-123';

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/apply/draft']}>
          <ResumeApplication />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const name = await screen.findByLabelText(/Full name/);
    // Same seeding race as "lets the candidate change it": change the value
    // only once the parsed name has been seeded, or the seed can overwrite it.
    await waitFor(() => expect(name).toHaveValue('Priya Sharma'));
    fireEvent.change(name, { target: { value: 'Priya S. Sharma' } });
    getDraft.mockResolvedValue(draft({ parsed: { full_name: 'Priya Sharma', email: null } }));
    await client.refetchQueries({ queryKey: ['apply', 'draft'] });

    await waitFor(() =>
      expect(
        name,
        'a refetch overwrote the candidate’s correction',
      ).toHaveValue('Priya S. Sharma'),
    );
  });

  it('still takes the server’s reading when it genuinely changes', () => {
    // The guard must not freeze the form: after a CV upload re-parses, or a
    // save normalises a value, the new server reading still has to land. Every
    // seeded field must appear in the signature or a change to it is ignored.
    const source = readFileSync('src/pages/ResumeApplication.tsx', 'utf-8');
    const effect = source.slice(source.indexOf('const seeded = useRef'));
    expect(effect).toContain('signature === seeded.current');
    for (const f of [
      'full_name',
      'phone',
      'years_experience',
      'current_company',
      'current_title',
      'linkedin_url',
      'github_url',
    ]) {
      expect(
        effect.slice(0, effect.indexOf('setForm')),
        `${f} is seeded but not in the signature, so a server change to it is ignored`,
      ).toContain(f);
    }
  });
});
