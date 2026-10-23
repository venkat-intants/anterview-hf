// "View resume" beside the score: the CV opens in a new tab from an authenticated
// fetch, the application's own CV when one is named, and a failure says why.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastError = vi.fn<(m: string) => void>();
vi.mock('../lib/toast', () => ({
  toast: { error: (m: string) => toastError(m), success: vi.fn(), info: vi.fn() },
}));

const fetchBlob = vi.fn();
vi.mock('../api/client', async (orig) => ({
  ...(await orig<typeof import('../api/client')>()),
  fetchBlobWithAuth: (url: string) => fetchBlob(url) as Promise<Response>,
}));

import ViewCvButton from '../components/hr/ViewCvButton';
import { openApplicantCv } from '../api/applicants';

const revoke = vi.fn<(url: string) => void>();

const AP = '11111111-2222-4333-8444-555555555555';
const EN = '66666666-7777-4888-9999-aaaaaaaaaaaa';

function pdfResponse(): Response {
  return {
    ok: true,
    status: 200,
    blob: () => Promise.resolve(new Blob(['%PDF-1.7'])),
  } as unknown as Response;
}

describe('openApplicantCv', () => {
  let tab: { opener: unknown; location: { href: string }; close: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    tab = { opener: 'x', location: { href: '' }, close: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window);
    URL.createObjectURL = vi.fn(() => 'blob:cv');
    revoke.mockReset();
    URL.revokeObjectURL = revoke;
    fetchBlob.mockReset();
    toastError.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it('opens the tab inside the click, then shows the fetched PDF in it', async () => {
    fetchBlob.mockResolvedValue(pdfResponse());
    await openApplicantCv(AP);
    expect(window.open).toHaveBeenCalledWith('', '_blank');
    expect(String(fetchBlob.mock.calls[0][0]).endsWith(`/hr/applicants/${AP}/resume`)).toBe(true);
    expect(tab.location.href).toBe('blob:cv');
    expect(tab.opener).toBeNull();
  });

  it('opens the tab BEFORE the fetch, so the browser treats it as part of the click', async () => {
    fetchBlob.mockResolvedValue(pdfResponse());
    await openApplicantCv(AP);
    const opened = vi.mocked(window.open).mock.invocationCallOrder[0];
    expect(opened).toBeLessThan(fetchBlob.mock.invocationCallOrder[0]);
  });

  it('a blocked pop-up asks for pop-ups and never fetches (nothing is audited as seen)', async () => {
    vi.mocked(window.open).mockReturnValue(null);
    await expect(openApplicantCv(AP)).rejects.toThrow(/allow pop-ups/i);
    expect(fetchBlob).not.toHaveBeenCalled();
  });

  it('frees the blob URL a minute later', async () => {
    vi.useFakeTimers();
    try {
      fetchBlob.mockResolvedValue(pdfResponse());
      await openApplicantCv(AP);
      expect(revoke).not.toHaveBeenCalled();
      vi.advanceTimersByTime(60_000);
      expect(revoke).toHaveBeenCalledWith('blob:cv');
    } finally {
      vi.useRealTimers();
    }
  });

  it('asks for the CV a specific application was sent with', async () => {
    fetchBlob.mockResolvedValue(pdfResponse());
    await openApplicantCv(AP, EN);
    expect(String(fetchBlob.mock.calls[0][0]).endsWith(`/resume?enrolment_id=${EN}`)).toBe(true);
  });

  it('closes the tab and surfaces the server sentence when there is no CV', async () => {
    fetchBlob.mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: 'No CV file is on record for this applicant.' }),
    } as unknown as Response);
    await expect(openApplicantCv(AP)).rejects.toThrow('No CV file is on record');
    expect(tab.close).toHaveBeenCalled();
  });

  it('the button shows that failure as a toast', async () => {
    fetchBlob.mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: 'The CV file could not be found in storage.' }),
    } as unknown as Response);
    const user = userEvent.setup();
    render(<ViewCvButton applicantId={AP} />);
    await user.click(screen.getByRole('button', { name: 'View resume' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('The CV file could not be found in storage.'),
    );
    expect(screen.getByRole('button', { name: 'View resume' })).toBeEnabled();
  });

  it('says what failed in plain words on a server error with no sentence (audit C1)', async () => {
    fetchBlob.mockResolvedValue({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error('not json')),
    } as unknown as Response);
    const user = userEvent.setup();
    render(<ViewCvButton applicantId={AP} />);
    await user.click(screen.getByRole('button', { name: 'View resume' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Could not open the resume. Try again in a moment.'),
    );
  });
});
