// PublicOffer — the candidate's own offer, reached with no login (PH4-A3/A4).
//
// This page was the subject of a security review, and these are the
// properties that review turned on:
//   • the offer token comes from the URL #fragment and is stripped with
//     history.replaceState on arrival — it must never linger in the address
//     bar or reach a server as anything but the X-Offer-Token header;
//   • the documents session token is never written to localStorage or
//     sessionStorage — it lives in memory only (the offer token itself is kept
//     in this tab's sessionStorage so a reload works, audit CA-H3);
//   • a 423 (too many wrong codes) locks the whole flow with the server's own
//     wording, and a 401 on the documents session sends the candidate back to
//     asking for a new code rather than failing silently;
//   • accept/decline controls, and the documents step, are offered only in
//     the states the state machine actually allows them from.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { PublicDocumentsChecklist, PublicOffer as PublicOfferShape } from '../api/publicOffer';

const viewOffer = vi.fn();
const requestOfferCode = vi.fn();
const acceptOffer = vi.fn();
const declineOffer = vi.fn();
const requestDocumentsCode = vi.fn();
const openDocumentsSession = vi.fn();
const getMyDocuments = vi.fn();
const uploadMyDocument = vi.fn();
const withdrawDocumentsConsent = vi.fn();
vi.mock('../api/publicOffer', () => ({
  viewOffer: (...a: unknown[]) => viewOffer(...a) as unknown,
  requestOfferCode: (...a: unknown[]) => requestOfferCode(...a) as unknown,
  acceptOffer: (...a: unknown[]) => acceptOffer(...a) as unknown,
  declineOffer: (...a: unknown[]) => declineOffer(...a) as unknown,
  requestDocumentsCode: (...a: unknown[]) => requestDocumentsCode(...a) as unknown,
  openDocumentsSession: (...a: unknown[]) => openDocumentsSession(...a) as unknown,
  getMyDocuments: (...a: unknown[]) => getMyDocuments(...a) as unknown,
  uploadMyDocument: (...a: unknown[]) => uploadMyDocument(...a) as unknown,
  withdrawDocumentsConsent: (...a: unknown[]) => withdrawDocumentsConsent(...a) as unknown,
}));

import PublicOffer from '../pages/PublicOffer';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PublicOffer />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SENT: PublicOfferShape = {
  status: 'sent',
  company: 'Acme Corp',
  job_title: 'Backend Engineer',
  employment_type: 'full_time',
  start_date: '2026-10-01',
  location: 'Bengaluru',
  base_salary: '1200000.00',
  currency: 'INR',
  pay_period: 'annual',
  bonus: null,
  equity: null,
  benefits: null,
  terms: null,
  probation_months: 3,
  notice_period_days: 30,
  expires_at: '2026-10-10T00:00:00.000Z',
  responded_at: null,
  preboarding_completed_at: null,
};

const ACCEPTED: PublicOfferShape = {
  ...SENT,
  status: 'accepted',
  responded_at: '2026-09-18T00:00:00.000Z',
};

const CHECKLIST: PublicDocumentsChecklist = {
  offer_status: 'accepted',
  preboarding_completed_at: null,
  complete_ready: false,
  outstanding: ['PAN card'],
  items: [
    {
      requirement_id: 'req-1',
      name: 'PAN card',
      doc_type: 'tax',
      description: null,
      mandatory: true,
      requires_expiry: false,
      state: 'outstanding',
      document: null,
    },
    {
      requirement_id: 'req-2',
      name: 'Aadhaar card',
      doc_type: 'identity',
      description: null,
      mandatory: true,
      requires_expiry: false,
      state: 'outstanding',
      document: null,
    },
  ],
};

const replaceState = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = '#offer_tok_123456';
  window.history.replaceState = replaceState;
  localStorage.clear();
  sessionStorage.clear();
  viewOffer.mockResolvedValue(SENT);
});

afterEach(() => {
  window.location.hash = '';
});

describe('PublicOffer - the link itself', () => {
  it('reads the token from the URL fragment and strips it from the address bar', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');

    expect(viewOffer).toHaveBeenCalledWith('offer_tok_123456');
    // Path and router state kept; only the fragment goes.
    expect(replaceState).toHaveBeenCalledWith(window.history.state, '', '/');
  });

  // Audit CA-H3: a reload used to land on "this link isn't valid".
  it('still opens the offer after a reload, once the fragment is gone', async () => {
    const first = renderPage();
    await screen.findByText('Backend Engineer');
    first.unmount();
    window.location.hash = '';
    viewOffer.mockClear();

    renderPage();
    await screen.findByText('Backend Engineer');
    expect(viewOffer).toHaveBeenCalledWith('offer_tok_123456');
  });

  it('forgets the link once the offer can do nothing more', async () => {
    viewOffer.mockResolvedValue({ ...SENT, status: 'withdrawn' });
    renderPage();
    await waitFor(() => expect(sessionStorage.getItem('anthire.offer.token')).toBeNull());
  });

  it('tells them to reopen the emailed link before asking for a new one', async () => {
    viewOffer.mockRejectedValue(new ApiError('Not found', 404));
    renderPage();
    expect(await screen.findByText(/Open the link in that email again/)).toBeInTheDocument();
  });

  it('never calls the API and shows an invalid link when there is no token', async () => {
    window.location.hash = '';
    renderPage();

    expect(await screen.findByText(/isn't valid/i)).toBeInTheDocument();
    expect(viewOffer).not.toHaveBeenCalled();
  });

  it('calls a rejected link invalid', async () => {
    viewOffer.mockRejectedValue(new ApiError("This offer link isn't available.", 404));
    renderPage();
    expect(await screen.findByText(/isn't valid/i)).toBeInTheDocument();
  });

  it('offers a retry, not "invalid", for a transient failure', async () => {
    viewOffer.mockRejectedValue(new ApiError('Bad gateway', 503));
    renderPage();

    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 8000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/isn't valid/i)).not.toBeInTheDocument();
  });
});

describe('PublicOffer - a sent offer', () => {
  it('shows the job, company and compensation', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    expect(screen.getByText(/12,00,000|1,200,000/)).toBeInTheDocument();
  });

  it('offers Accept and Decline, and nothing from the documents step', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(screen.getByRole('button', { name: 'Accept offer' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline offer' })).toBeInTheDocument();
    expect(screen.queryByText('Your documents')).not.toBeInTheDocument();
  });

  it('accepts with an emailed code and a typed full name', async () => {
    const user = userEvent.setup();
    requestOfferCode.mockResolvedValue({ sent: true, minutes: 15 });
    acceptOffer.mockResolvedValue({ ...ACCEPTED });
    viewOffer.mockResolvedValueOnce(SENT).mockResolvedValue(ACCEPTED);
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Accept offer' }));
    await user.click(screen.getByRole('button', { name: 'Send me a code' }));
    await screen.findByText(/Code sent/);
    expect(requestOfferCode).toHaveBeenCalledWith('offer_tok_123456', 'accept');

    await user.type(screen.getByLabelText(/enter the code/i), '123456');
    await user.type(screen.getByLabelText(/full name/i), 'Kiran Rao');
    await user.click(screen.getByRole('button', { name: 'Confirm acceptance' }));

    await waitFor(() =>
      expect(acceptOffer).toHaveBeenCalledWith('offer_tok_123456', '123456', 'Kiran Rao', 'en'),
    );
    expect(await screen.findByText('Offer accepted')).toBeInTheDocument();
  });

  it('says what accepting agrees to, before the candidate confirms (DPDP)', async () => {
    const user = userEvent.setup();
    requestOfferCode.mockResolvedValue({ sent: true, minutes: 15 });
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Accept offer' }));
    await user.click(screen.getByRole('button', { name: 'Send me a code' }));
    await screen.findByText(/Code sent/);
    expect(screen.getByText(/you agree to share the documents/i)).toBeInTheDocument();
    // Security review, PH4 wave 5: names the actual places rather than
    // hedging with "may be outside India" (see docs/DATA-FLOW.md).
    expect(screen.getByText(/Singapore, United States/)).toBeInTheDocument();
    expect(screen.queryByText(/may be outside India/i)).not.toBeInTheDocument();
    expect(screen.getByText(/withdraw this consent/i)).toBeInTheDocument();
  });

  it('declines with an emailed code and an optional reason', async () => {
    const user = userEvent.setup();
    requestOfferCode.mockResolvedValue({ sent: true, minutes: 15 });
    declineOffer.mockResolvedValue({ ...SENT, status: 'declined' });
    viewOffer.mockResolvedValueOnce(SENT).mockResolvedValue({ ...SENT, status: 'declined' });
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Decline offer' }));
    await user.click(screen.getByRole('button', { name: 'Send me a code' }));
    await screen.findByText(/Code sent/);

    await user.type(screen.getByLabelText(/enter the code/i), '654321');
    await user.type(screen.getByLabelText(/reason/i), 'Accepted elsewhere');
    await user.click(screen.getByRole('button', { name: 'Confirm decline' }));

    await waitFor(() =>
      expect(declineOffer).toHaveBeenCalledWith('offer_tok_123456', '654321', 'Accepted elsewhere'),
    );
    expect(await screen.findByText('You declined this offer')).toBeInTheDocument();
  });

  it('locks with the server’s own wording after too many wrong codes', async () => {
    const user = userEvent.setup();
    requestOfferCode.mockRejectedValue(
      new ApiError(
        'Too many wrong codes. For your security this offer is locked - please ask the hiring team to send it to you again.',
        423,
      ),
    );
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Accept offer' }));
    await user.click(screen.getByRole('button', { name: 'Send me a code' }));

    expect(await screen.findByText('Too many wrong codes')).toBeInTheDocument();
    expect(
      screen.getByText(/please ask the hiring team to send it to you again/i),
    ).toBeInTheDocument();
    // The whole page is locked — no way back into the answer flow.
    expect(screen.queryByRole('button', { name: 'Accept offer' })).not.toBeInTheDocument();
  });
});

describe('PublicOffer - terminal states', () => {
  it('shows an expired offer as expired, not as an invalid link', async () => {
    viewOffer.mockResolvedValue({ ...SENT, status: 'expired' });
    renderPage();
    expect(await screen.findByText('This offer has expired')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept offer' })).not.toBeInTheDocument();
  });

  it('shows a withdrawn offer as withdrawn', async () => {
    viewOffer.mockResolvedValue({ ...SENT, status: 'withdrawn' });
    renderPage();
    expect(await screen.findByText('This offer has been withdrawn')).toBeInTheDocument();
  });

  it('shows an already-declined offer with no further action', async () => {
    viewOffer.mockResolvedValue({ ...SENT, status: 'declined' });
    renderPage();
    expect(await screen.findByText('You declined this offer')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /accept|decline/i })).not.toBeInTheDocument();
  });
});

describe('PublicOffer - documents, once accepted', () => {
  beforeEach(() => {
    viewOffer.mockResolvedValue(ACCEPTED);
  });

  it('starts the documents step only with a second emailed code', async () => {
    const user = userEvent.setup();
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    renderPage();

    await screen.findByText('Your documents');
    expect(getMyDocuments).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    expect(requestDocumentsCode).toHaveBeenCalledWith('offer_tok_123456');
    expect(await screen.findByText(/Code sent/)).toBeInTheDocument();
  });

  it('never persists the session token to any storage', async () => {
    const user = userEvent.setup();
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_super_secret_token',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue(CHECKLIST);
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    await waitFor(() =>
      expect(getMyDocuments).toHaveBeenCalledWith('offer_tok_123456', 'sess_super_secret_token'),
    );
    await screen.findByText('PAN card');

    // The token this whole step exists to protect must never land in
    // persistent storage — only ever held in the component's own memory. The
    // OFFER token is kept for this tab (CA-H3), and is the only thing stored.
    expect(localStorage.length).toBe(0);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('sess_super_secret_token');
    expect(Object.keys(sessionStorage)).toEqual(['anthire.offer.token']);
  });

  it('says so when the hiring team has asked for no documents', async () => {
    const user = userEvent.setup();
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue({ ...CHECKLIST, outstanding: [], items: [] });
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText(/has not asked for any documents/)).toBeInTheDocument();
  });

  it('explains a verified document past its date, and lets it be replaced', async () => {
    const user = userEvent.setup();
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue({
      ...CHECKLIST,
      items: [{ ...CHECKLIST.items[0], state: 'expired' }],
    });
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText(/passed its expiry date/)).toBeInTheDocument();
    const row = screen.getByText('PAN card').closest('li') as HTMLElement;
    expect(row.querySelector('input[type="file"]')).not.toBeNull();
  });

  it('lets the candidate withdraw the consent the offer page promised they could', async () => {
    const user = userEvent.setup();
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue(CHECKLIST);
    withdrawDocumentsConsent.mockResolvedValue(undefined);
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByText('PAN card');

    // Asked before it happens, never on the first click.
    await user.click(screen.getByRole('button', { name: /Withdraw my consent/i }));
    expect(withdrawDocumentsConsent).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: /Yes, withdraw consent/i }));
    await waitFor(() =>
      expect(withdrawDocumentsConsent).toHaveBeenCalledWith('offer_tok_123456', 'sess_tok'),
    );
    expect(await screen.findByText(/consent is withdrawn/i)).toBeInTheDocument();
  });

  it('shows Aadhaar-specific masking guidance for an identity document', async () => {
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue(CHECKLIST);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    await screen.findByText('Aadhaar card');
    expect(screen.getByText(/MASKED Aadhaar/)).toBeInTheDocument();
    // The PAN row must not be told to mask anything.
    const panRow = screen.getByText('PAN card').closest('li') as HTMLElement;
    expect(panRow).not.toHaveTextContent(/MASKED Aadhaar/);
  });

  it('sends the candidate back to asking for a new code when the session has expired (401)', async () => {
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockRejectedValue(
      new ApiError('Enter the code we emailed you to open your documents.', 401),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByText(/session has ended/i)).toBeInTheDocument();
    // Back to square one — no leftover session, no leftover checklist.
    expect(screen.getByRole('button', { name: 'Get a code' })).toBeInTheDocument();
    expect(screen.queryByText('PAN card')).not.toBeInTheDocument();
  });

  it('uploads a document once a session is open', async () => {
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue(CHECKLIST);
    uploadMyDocument.mockResolvedValue({ document_id: 'doc-1', version: 1, state: 'submitted' });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByText('PAN card');

    const panRow = screen.getByText('PAN card').closest('li') as HTMLElement;
    const file = new File(['%PDF-1.4 test'], 'pan.pdf', { type: 'application/pdf' });
    const input = panRow.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    await waitFor(() =>
      expect(uploadMyDocument).toHaveBeenCalledWith(
        'offer_tok_123456',
        'sess_tok',
        'req-1',
        file,
        undefined,
      ),
    );
  });

  // Characterisation tests, not a fix — and the distinction matters enough to
  // write down, because the comment here used to claim the opposite.
  //
  // A full pipeline test hit a 500 on this upload, and the conclusion drawn at
  // the time was that the page had swallowed it: the row still read "Not
  // uploaded yet" with no message, so a candidate would believe their PAN card
  // had gone through. That was wrong. The 500 was real and is fixed server-side
  // in document_storage.py; the component was already correct and is unchanged
  // by that work — errText() falls through to e.message, isUnauthorized is 401
  // only, and the fallback key exists in all three bundles. These two cases
  // pass on the unfixed tree as well, which is exactly why they must not be
  // cited as evidence that anything here was repaired.
  //
  // They are still worth keeping. "A failed upload is said out loud" is a
  // property a candidate depends on, it had no test, and the next refactor of
  // this mutation could quietly lose it.
  it.each([
    ['the server fails', new Error('Internal server error.'), /Internal server error/],
    ['the request never arrives', new TypeError('Failed to fetch'), /Failed to fetch/],
  ])('tells the candidate when an upload fails because %s', async (_why, failure, shown) => {
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue(CHECKLIST);
    uploadMyDocument.mockRejectedValue(failure);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByText('PAN card');

    const panRow = screen.getByText('PAN card').closest('li') as HTMLElement;
    const input = panRow.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF-1.4 test'], 'pan.pdf', { type: 'application/pdf' }));

    expect(await within(panRow).findByText(shown)).toBeInTheDocument();
  });

  it('shows the completed state once preboarding is done, with no code step', async () => {
    viewOffer.mockResolvedValue({
      ...ACCEPTED,
      preboarding_completed_at: '2026-09-18T00:00:00.000Z',
    });
    renderPage();

    await screen.findByText('Your documents');
    expect(await screen.findByText(/documents are complete/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Get a code' })).not.toBeInTheDocument();
  });
});

// UX round 3 — the offer's three steps, driven by what the server holds.
describe('PublicOffer - the three steps', () => {
  const current = () => {
    const nav = screen.getByRole('navigation', { name: 'Offer progress' });
    const item = nav.querySelector('li[aria-current="step"]');
    return item?.textContent ?? '';
  };

  it('is on Review for a sent offer, and on Accept once the accept panel is open', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(current()).toMatch(/Review/);

    await user.click(screen.getByRole('button', { name: 'Accept offer' }));
    expect(current()).toMatch(/Accept/);
  });

  it('is on Joining documents once accepted, with the offer collapsed to one row', async () => {
    viewOffer.mockResolvedValue(ACCEPTED);
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Offer accepted' })).toBeInTheDocument();
    expect(current()).toMatch(/Joining documents/);
    // The offer is still readable, behind "View offer"; documents lead.
    expect(screen.getByText('View offer')).toBeInTheDocument();
    expect(screen.getByText('Your documents')).toBeInTheDocument();
    expect(screen.getByText(/checks each document/i)).toBeInTheDocument();
  });

  it.each([
    ['expired', 'This offer has expired'],
    ['withdrawn', 'This offer has been withdrawn'],
    ['declined', 'You declined this offer'],
  ] as const)('draws no steps and offers no Accept for a %s offer', async (status, title) => {
    viewOffer.mockResolvedValue({ ...SENT, status });
    renderPage();
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Offer progress' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /accept/i })).not.toBeInTheDocument();
  });

  it('explains why a second code is asked for, before asking', async () => {
    viewOffer.mockResolvedValue(ACCEPTED);
    renderPage();
    await screen.findByText('Your documents');
    expect(screen.getByText(/their own one-time code, separate from the one you used/i)).toBeInTheDocument();
  });
});

describe('PublicOffer - joining documents in words', () => {
  async function openChecklist(checklist: PublicDocumentsChecklist) {
    viewOffer.mockResolvedValue(ACCEPTED);
    requestDocumentsCode.mockResolvedValue({ sent: true, minutes: 60 });
    openDocumentsSession.mockResolvedValue({
      session_token: 'sess_tok',
      expires_at: '2026-09-18T02:00:00.000Z',
    });
    getMyDocuments.mockResolvedValue(checklist);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Your documents');
    await user.click(screen.getByRole('button', { name: 'Get a code' }));
    await screen.findByText(/Code sent/);
    await user.type(screen.getByLabelText(/enter the code/i), '111111');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
  }

  it('shows "n of N still needed" from outstanding and items', async () => {
    await openChecklist({
      ...CHECKLIST,
      outstanding: ['PAN card', 'Aadhaar card'],
    });
    expect(await screen.findByTestId('docs-remaining')).toHaveTextContent('2 of 2 still needed');
  });

  it('counts only what is not yet verified, and says which are already with the hiring team', async () => {
    await openChecklist({
      ...CHECKLIST,
      outstanding: ['Aadhaar card'],
      items: [
        { ...CHECKLIST.items[0], state: 'verified' },
        {
          ...CHECKLIST.items[1],
          state: 'submitted',
          document: {
            id: 'd1', version: 1, file_name: 'a.pdf', content_type: 'application/pdf', size_bytes: 10,
            expires_on: null, uploaded_at: null, reviewed_at: null, review_note: null,
          },
        },
      ],
    });
    expect(await screen.findByTestId('docs-remaining')).toHaveTextContent('1 of 2 still needed');
    expect(screen.getByText('1 is sent and waiting for review.')).toBeInTheDocument();
  });

  it('names each document state in words, with the reason for "Send again"', async () => {
    await openChecklist({
      ...CHECKLIST,
      outstanding: ['PAN card', 'Aadhaar card'],
      items: [
        { ...CHECKLIST.items[0] },
        {
          ...CHECKLIST.items[1],
          state: 'rejected',
          document: {
            id: 'd2', version: 1, file_name: 'a.pdf', content_type: 'application/pdf', size_bytes: 10,
            expires_on: null, uploaded_at: null, reviewed_at: null, review_note: 'photo is blurred',
          },
        },
      ],
    });
    const pan = (await screen.findByText('PAN card')).closest('li') as HTMLElement;
    const aadhaar = screen.getByText('Aadhaar card').closest('li') as HTMLElement;
    expect(within(pan).getByText('Needed')).toBeInTheDocument();
    expect(within(aadhaar).getByText('Send again')).toBeInTheDocument();
    expect(within(aadhaar).getByText(/photo is blurred/)).toBeInTheDocument();
  });
});
