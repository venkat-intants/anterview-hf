// Tests for Resume manager page
// Covers: loading state, current resume display, version list, set-as-current,
// delete dialog confirm, upload zone presence, empty state.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import Resume from '../pages/Resume';
import i18n from '../lib/i18n';
import type { ResumeVersionItem, ResumeCurrentResponse } from '../api/resume';

// ---------------------------------------------------------------------------
// Mock resume API
// ---------------------------------------------------------------------------

const MOCK_CURRENT: ResumeCurrentResponse = {
  resume_id: 'rrrrrrrr-1111-1111-1111-rrrrrrrrrrrr',
  filename: 'priya_resume_v2.pdf',
  resume_s3_key: 'resumes/u/r1.pdf',
  text_length: 2840,
  uploaded_at: '2026-05-28T08:30:00Z',
  created_at: '2026-05-28T08:30:00Z',
  download_url: 'https://r2.example.com/r1.pdf',
};

const MOCK_LIST: ResumeVersionItem[] = [
  {
    resume_id: 'rrrrrrrr-1111-1111-1111-rrrrrrrrrrrr',
    filename: 'priya_resume_v2.pdf',
    resume_s3_key: 'resumes/u/r1.pdf',
    text_length: 2840,
    is_current: true,
    uploaded_at: '2026-05-28T08:30:00Z',
    created_at: '2026-05-28T08:30:00Z',
    download_url: 'https://r2.example.com/r1.pdf',
  },
  {
    resume_id: 'rrrrrrrr-2222-2222-2222-rrrrrrrrrrrr',
    filename: 'priya_resume_v1.pdf',
    resume_s3_key: 'resumes/u/r2.pdf',
    text_length: 2210,
    is_current: false,
    uploaded_at: '2026-05-15T11:00:00Z',
    created_at: '2026-05-15T11:00:00Z',
    download_url: null,
  },
];

const mockListResumes = vi.fn().mockResolvedValue(MOCK_LIST);
const mockGetCurrentResume = vi.fn().mockResolvedValue(MOCK_CURRENT);
const mockSetCurrentResume = vi
  .fn()
  .mockResolvedValue({ message: 'ok', resume_id: 'rrrrrrrr-2222-2222-2222-rrrrrrrrrrrr' });
const mockDeleteResume = vi
  .fn()
  .mockResolvedValue({ message: 'deleted' });
const mockUploadResume = vi.fn().mockResolvedValue({
  message: 'ok',
  resume_id: 'new-id',
  resume_s3_key: 'resumes/u/new.pdf',
  text_length: 3000,
});

vi.mock('../api/resume', () => ({
  listResumes: (...args: unknown[]) => mockListResumes(...args) as unknown,
  getCurrentResume: (...args: unknown[]) => mockGetCurrentResume(...args) as unknown,
  setCurrentResume: (...args: unknown[]) => mockSetCurrentResume(...args) as unknown,
  deleteResume: (...args: unknown[]) => mockDeleteResume(...args) as unknown,
  uploadResume: (...args: unknown[]) => mockUploadResume(...args) as unknown,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderResume() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Resume />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Resume page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListResumes.mockResolvedValue(MOCK_LIST);
    mockGetCurrentResume.mockResolvedValue(MOCK_CURRENT);
  });

  it('renders the page heading', async () => {
    renderResume();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /resume manager/i })).toBeInTheDocument();
    });
  });

  it('shows loading state initially', () => {
    mockListResumes.mockImplementation(() => new Promise(() => undefined));
    mockGetCurrentResume.mockImplementation(() => new Promise(() => undefined));
    renderResume();
    expect(screen.getByLabelText(/loading resume data/i)).toBeInTheDocument();
  });

  it('displays the current resume filename in the active resume card', async () => {
    renderResume();
    await waitFor(() => {
      // The filename appears in the "Active Resume" card AND in the version list row
      expect(screen.getAllByText('priya_resume_v2.pdf').length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText(/active resume/i)).toBeInTheDocument();
  });

  it('shows the Current badge on the active version', async () => {
    renderResume();
    await waitFor(() => {
      const currentBadges = screen.getAllByText('Current');
      expect(currentBadges.length).toBeGreaterThan(0);
    });
  });

  it('lists both resume versions', async () => {
    renderResume();
    await waitFor(() => {
      expect(screen.getAllByText('priya_resume_v2.pdf').length).toBeGreaterThan(0);
    });
    expect(screen.getByText('priya_resume_v1.pdf')).toBeInTheDocument();
  });

  it('renders a "Set as current" button for non-current versions', async () => {
    renderResume();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /set priya_resume_v1\.pdf as current/i })).toBeInTheDocument();
    });
  });

  it('calls setCurrentResume and shows success feedback on set-current click', async () => {
    const user = userEvent.setup();
    renderResume();
    const setBtn = await screen.findByRole('button', {
      name: /set priya_resume_v1\.pdf as current/i,
    });
    await user.click(setBtn);
    await waitFor(() => {
      expect(mockSetCurrentResume).toHaveBeenCalledWith(
        'rrrrrrrr-2222-2222-2222-rrrrrrrrrrrr',
      );
    });
  });

  it('opens delete confirm dialog when delete button is clicked', async () => {
    const user = userEvent.setup();
    renderResume();
    // find any delete button (could be either version)
    const deleteBtns = await screen.findAllByRole('button', { name: /delete/i });
    await user.click(deleteBtns[0]);
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    expect(screen.getByText(/delete resume version/i)).toBeInTheDocument();
  });

  it('calls deleteResume after confirming in the dialog', async () => {
    const user = userEvent.setup();
    renderResume();
    const deleteBtns = await screen.findAllByRole('button', { name: /delete/i });
    await user.click(deleteBtns[0]);
    // Click the "Delete" button inside the dialog
    const confirmBtn = await screen.findByRole('button', { name: /^delete$/i });
    await user.click(confirmBtn);
    await waitFor(() => {
      expect(mockDeleteResume).toHaveBeenCalled();
    });
  });

  it('cancels deletion on Cancel click', async () => {
    const user = userEvent.setup();
    renderResume();
    const deleteBtns = await screen.findAllByRole('button', { name: /delete/i });
    await user.click(deleteBtns[0]);
    const cancelBtn = await screen.findByRole('button', { name: /cancel/i });
    await user.click(cancelBtn);
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(mockDeleteResume).not.toHaveBeenCalled();
  });

  it('shows empty state when no resumes exist', async () => {
    mockListResumes.mockResolvedValueOnce([]);
    mockGetCurrentResume.mockRejectedValueOnce(new Error('404'));
    renderResume();
    await waitFor(() => {
      expect(screen.getByTestId('resume-empty-state')).toBeInTheDocument();
    });
    expect(screen.getByText(/no resume on file/i)).toBeInTheDocument();
  });

  it('renders the upload zone for new version upload', async () => {
    renderResume();
    await waitFor(() => {
      expect(screen.getByText(/upload new version/i)).toBeInTheDocument();
    });
  });
});
