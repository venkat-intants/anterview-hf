// Tests for the self-service profile (FE-2): read mode, per-section editors and
// the avatar guards.
//
// One component serves candidate, hr_manager and admin, and the ROLE decides
// which fields are sent on save. Getting that wrong is not cosmetic: sending a
// candidate's `desired_roles` for an HR manager, or dropping an HR manager's
// `official_email`, writes the wrong shape to /auth/me/profile. These tests pin
// the per-role payload and the avatar guards.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { MeResponse } from '../types/auth';

const CANDIDATE: MeResponse = {
  user_id: 'u-1',
  full_name: 'Bhavya Nair',
  email: 'bhavya@example.com',
  roles: ['candidate'],
  has_resume: true,
  headline: 'Backend engineer',
  employment_status: 'employed',
  desired_roles: 'Backend Engineer',
  preferred_language: 'hi',
};

const HR: MeResponse = {
  user_id: 'u-2',
  full_name: 'Chetan Iyer',
  email: 'chetan@acme.edu',
  roles: ['hr_manager'],
  has_resume: false,
  company_name: 'Acme Skills University',
  official_email: 'chetan@acme.edu',
};

const getMe = vi.fn();
vi.mock('../api/auth', () => ({ getMe: (...a: unknown[]) => getMe(...a) as unknown }));

const updateProfile = vi.fn();
const imageFileToDataUrl = vi.fn();
vi.mock('../api/profile', () => ({
  updateProfile: (...a: unknown[]) => updateProfile(...a) as unknown,
  imageFileToDataUrl: (...a: unknown[]) => imageFileToDataUrl(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => mockUseAuth() as unknown }));

import Profile from '../pages/Profile';

type User = ReturnType<typeof userEvent.setup>;

function renderProfile() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Profile />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Render and wait for the SERVER PROFILE to have loaded. The page heading is
 * there before the query resolves, so the person's name (the header card's own
 * heading) is the synchronisation point for anything the profile supplies.
 */
async function renderHydrated(name = 'Bhavya Nair'): Promise<void> {
  renderProfile();
  await screen.findByRole('heading', { name });
}

/** Open a section editor and return the dialog it opens. */
async function openEditor(user: User, buttonName: RegExp): Promise<HTMLElement> {
  await user.click(screen.getByRole('button', { name: buttonName }));
  return screen.findByRole('dialog');
}

function clickSave(user: User): Promise<void> {
  return user.click(screen.getByRole('button', { name: /save changes/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getMe.mockResolvedValue(CANDIDATE);
  updateProfile.mockImplementation((body: Record<string, unknown>) =>
    Promise.resolve({ ...CANDIDATE, ...body }),
  );
  imageFileToDataUrl.mockResolvedValue('data:image/jpeg;base64,AAAA');
  mockUseAuth.mockReturnValue({
    isAuthenticated: true,
    isInitializing: false,
    user: {
      user_id: 'u-1',
      full_name: 'Bhavya Nair',
      email: 'bhavya@example.com',
      roles: ['candidate'],
    },
  });
});

describe('Profile - rendering', () => {
  it('shows the saved profile as text, not as an always-open form', async () => {
    await renderHydrated();

    expect(screen.getByRole('heading', { name: /your profile/i })).toBeInTheDocument();
    expect(screen.getByText('Backend engineer')).toBeInTheDocument();
    expect(screen.queryByLabelText(/full name/i)).not.toBeInTheDocument();
  });

  it('opens the editor hydrated from the server profile', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    const dialog = await openEditor(user, /edit profile/i);

    expect(within(dialog).getByLabelText(/full name/i)).toHaveValue('Bhavya Nair');
    expect(within(dialog).getByLabelText(/headline/i)).toHaveValue('Backend engineer');
  });

  it('labels the same field differently for an HR manager', async () => {
    // A recruiter has a job title, not a candidate "headline" — same input,
    // different meaning, and the label is the only thing that says so.
    getMe.mockResolvedValue(HR);
    const user = userEvent.setup();
    await renderHydrated('Chetan Iyer');
    const dialog = await openEditor(user, /edit profile/i);

    expect(within(dialog).getByLabelText(/title \/ role/i)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/^headline$/i)).not.toBeInTheDocument();
    // Shown in the header card and the Company row.
    expect(screen.getAllByText('Acme Skills University').length).toBeGreaterThan(0);
  });

  it('shows an inline empty state with an Add action for an empty section', async () => {
    getMe.mockResolvedValue({ ...CANDIDATE, bio: null });
    const user = userEvent.setup();
    await renderHydrated();

    const dialog = await openEditor(user, /add a summary/i);
    expect(within(dialog).getByLabelText(/tell recruiters/i)).toHaveValue('');
  });

  it('reports profile strength from existing fields and lists what is missing', async () => {
    await renderHydrated();

    // Candidate: headline, resume and desired roles are set (3 of 7); photo,
    // summary, location and links are not.
    expect(screen.getByRole('progressbar', { name: /profile strength/i })).toHaveAttribute(
      'aria-valuenow',
      '43',
    );
    expect(screen.getByRole('button', { name: /write a summary/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add a profile photo/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /upload your resume/i })).not.toBeInTheDocument();
  });

  it('opens the right editor from a strength item', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    const dialog = await openEditor(user, /link your linkedin or github/i);

    expect(within(dialog).getByLabelText(/linkedin/i)).toBeInTheDocument();
  });

  it('says so when the profile is complete', async () => {
    getMe.mockResolvedValue({
      ...CANDIDATE,
      avatar_url: 'data:image/jpeg;base64,AAAA',
      bio: 'Hello',
      location: 'Pune',
      linkedin_url: 'https://linkedin.com/in/b',
    });
    await renderHydrated();

    expect(screen.getByText(/your profile is complete/i)).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: /profile strength/i })).toHaveAttribute(
      'aria-valuenow',
      '100',
    );
  });

  it('shows a retryable error when the profile cannot be loaded', async () => {
    getMe.mockRejectedValueOnce(new Error('boom'));
    renderProfile();

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load your profile/i);
    await userEvent.setup().click(screen.getByRole('button', { name: /try again/i }));
    expect(await screen.findByRole('heading', { name: 'Bhavya Nair' })).toBeInTheDocument();
  });
});

describe('Profile - saving', () => {
  it('sends the candidate-only fields for a candidate', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    await openEditor(user, /edit career/i);
    await clickSave(user);

    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    const body = updateProfile.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body).toMatchObject({
      employment_status: 'employed',
      desired_roles: 'Backend Engineer',
      preferred_language: 'hi',
    });
    expect(body).not.toHaveProperty('official_email');
    expect(toastSuccess).toHaveBeenCalledWith('Profile saved.');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('sends a changed value in the payload and shows it once saved', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    const dialog = await openEditor(user, /edit profile/i);
    const headline = within(dialog).getByLabelText(/headline/i);
    await user.clear(headline);
    await user.type(headline, 'Staff engineer');
    await user.type(within(dialog).getByLabelText(/location/i), 'Pune');
    await clickSave(user);

    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    expect(updateProfile.mock.calls[0]?.[0]).toMatchObject({
      full_name: 'Bhavya Nair',
      headline: 'Staff engineer',
      location: 'Pune',
    });
    expect(await screen.findByText('Staff engineer')).toBeInTheDocument();
  });

  it('sends the HR-only field for an HR manager and no candidate fields', async () => {
    getMe.mockResolvedValue(HR);
    mockUseAuth.mockReturnValue({
      isAuthenticated: true,
      isInitializing: false,
      user: {
        user_id: 'u-2',
        full_name: 'Chetan Iyer',
        email: 'chetan@acme.edu',
        roles: ['hr_manager'],
      },
    });
    const user = userEvent.setup();
    await renderHydrated('Chetan Iyer');
    await openEditor(user, /edit company details/i);
    await clickSave(user);

    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    const body = updateProfile.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body).toHaveProperty('official_email', 'chetan@acme.edu');
    expect(body).not.toHaveProperty('desired_roles');
    expect(body).not.toHaveProperty('employment_status');
  });

  it.each([
    [['interviewer'], 'Interviewer'],
    [['platform_owner'], 'Platform owner'],
  ])('treats %j as staff, not a candidate (AD-F9)', async (roles, label) => {
    getMe.mockResolvedValue({ ...HR, roles, company_name: undefined, official_email: undefined });
    mockUseAuth.mockReturnValue({
      isAuthenticated: true,
      isInitializing: false,
      user: { user_id: 'u-2', full_name: 'Chetan Iyer', email: 'chetan@acme.edu', roles },
    });
    await renderHydrated('Chetan Iyer');

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText('Candidate')).not.toBeInTheDocument();
    // No candidate-only asks: no resume upload, no desired roles, no Career section.
    expect(screen.queryByText('Upload your resume')).not.toBeInTheDocument();
    expect(screen.queryByText('Add the roles you want')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit career/i })).not.toBeInTheDocument();
  });

  it('gives an HR manager no Career section', async () => {
    getMe.mockResolvedValue(HR);
    await renderHydrated('Chetan Iyer');

    expect(screen.queryByRole('button', { name: /edit career/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/preferred interview language/i)).not.toBeInTheDocument();
  });

  it('drops a name that is only whitespace rather than blanking the account', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    const dialog = await openEditor(user, /edit profile/i);

    const nameField = within(dialog).getByLabelText(/full name/i);
    await user.clear(nameField);
    await user.type(nameField, '   ');
    await clickSave(user);

    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    const body = updateProfile.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body.full_name).toBeUndefined();
  });

  it('surfaces a save failure instead of claiming success, and keeps the editor open', async () => {
    updateProfile.mockRejectedValue(new Error('LinkedIn URL must be http(s)'));
    const user = userEvent.setup();
    await renderHydrated();
    await openEditor(user, /edit profile/i);
    await clickSave(user);

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('LinkedIn URL must be http(s)'));
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('discards unsaved edits on Cancel', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    let dialog = await openEditor(user, /edit profile/i);
    const headline = within(dialog).getByLabelText(/headline/i);
    await user.clear(headline);
    await user.type(headline, 'Something else');
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(updateProfile).not.toHaveBeenCalled();
    expect(screen.getByText('Backend engineer')).toBeInTheDocument();

    dialog = await openEditor(user, /edit profile/i);
    expect(within(dialog).getByLabelText(/headline/i)).toHaveValue('Backend engineer');
  });
});

describe('Profile - avatar upload', () => {
  it('refuses a non-image file', async () => {
    // applyAccept:false reproduces the real hazard: `accept="image/*"` is a
    // filter hint, not a constraint — a user can switch the picker to "All
    // files", which is precisely why the JS type check has to exist.
    const user = userEvent.setup({ applyAccept: false });
    await renderHydrated();
    const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' }));

    expect(toastError).toHaveBeenCalledWith('Please choose an image file.');
    expect(imageFileToDataUrl).not.toHaveBeenCalled();
  });

  it('refuses an image over the 6 MB ceiling before doing any work', async () => {
    // The avatar is stored inline as a data URI in a TEXT column, so an
    // oversized file is a database problem, not just a slow upload.
    const user = userEvent.setup({ applyAccept: false });
    await renderHydrated();
    const big = new File([new Uint8Array(7 * 1024 * 1024)], 'huge.png', { type: 'image/png' });
    const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, big);

    expect(toastError).toHaveBeenCalledWith('Image is too large (max 6 MB).');
    expect(imageFileToDataUrl).not.toHaveBeenCalled();
  });

  it('downscales an accepted image to a 256px data URI and saves it', async () => {
    const user = userEvent.setup();
    await renderHydrated();
    const png = new File(['x'], 'me.png', { type: 'image/png' });
    const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, png);

    await waitFor(() => expect(imageFileToDataUrl).toHaveBeenCalledWith(png, 256));
    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith({ avatar_url: 'data:image/jpeg;base64,AAAA' }),
    );
  });

  it('reports an unreadable image with the translated message, not the helper text', async () => {
    imageFileToDataUrl.mockRejectedValueOnce(new Error('Canvas not supported.'));
    const user = userEvent.setup();
    await renderHydrated();
    const png = new File(['x'], 'me.png', { type: 'image/png' });
    const input = document.body.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, png);

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Could not read that image.'));
    expect(toastError).not.toHaveBeenCalledWith('Canvas not supported.');
    expect(updateProfile).not.toHaveBeenCalled();
  });
});
