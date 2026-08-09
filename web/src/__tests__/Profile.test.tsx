// Tests for the self-service profile editor (FE-2).
//
// One component serves candidate, hr_manager and admin, and the ROLE decides
// which fields are sent on save. Getting that wrong is not cosmetic: sending a
// candidate's `desired_roles` for an HR manager, or dropping an HR manager's
// `official_email`, writes the wrong shape to /auth/me/profile. These tests pin
// the per-role payload and the avatar guards.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

/**
 * The page renders "Save changes" twice — once in the header and once at the
 * foot of a long form. Both drive the same handler; clicking the first is the
 * header one. getAllBy is deliberate: collapsing to getBy would break the day
 * someone removes one of them for the wrong reason.
 */
function clickSave(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  return user.click(screen.getAllByRole('button', { name: /save changes/i })[0]);
}

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
    user: { user_id: 'u-1', full_name: 'Bhavya Nair', email: 'bhavya@example.com', roles: ['candidate'] },
  });
});

describe('Profile — rendering', () => {
  it('hydrates the form from the server profile', async () => {
    renderProfile();

    expect(await screen.findByRole('heading', { name: /your profile/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/full name/i)).toHaveValue('Bhavya Nair');
    expect(screen.getByLabelText(/headline/i)).toHaveValue('Backend engineer');
  });

  it('labels the same field differently for an HR manager', async () => {
    // A recruiter has a job title, not a candidate "headline" — same input,
    // different meaning, and the label is the only thing that says so.
    getMe.mockResolvedValue(HR);
    renderProfile();

    expect(await screen.findByLabelText(/title \/ role/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^headline$/i)).not.toBeInTheDocument();
    // Shown twice: the identity chip and the read-only Company field.
    expect(screen.getAllByText('Acme Skills University').length).toBeGreaterThan(0);
  });
});

describe('Profile — saving', () => {
  it('sends the candidate-only fields for a candidate', async () => {
    const user = userEvent.setup();
    renderProfile();

    await screen.findByRole('heading', { name: /your profile/i });
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
  });

  it('sends the HR-only field for an HR manager and no candidate fields', async () => {
    getMe.mockResolvedValue(HR);
    mockUseAuth.mockReturnValue({
      isAuthenticated: true,
      isInitializing: false,
      user: { user_id: 'u-2', full_name: 'Chetan Iyer', email: 'chetan@acme.edu', roles: ['hr_manager'] },
    });
    const user = userEvent.setup();
    renderProfile();

    await screen.findByLabelText(/title \/ role/i);
    await clickSave(user);

    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    const body = updateProfile.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body).toHaveProperty('official_email', 'chetan@acme.edu');
    expect(body).not.toHaveProperty('desired_roles');
    expect(body).not.toHaveProperty('employment_status');
  });

  it('drops a name that is only whitespace rather than blanking the account', async () => {
    const user = userEvent.setup();
    renderProfile();

    const nameField = await screen.findByLabelText(/full name/i);
    await user.clear(nameField);
    await user.type(nameField, '   ');
    await clickSave(user);

    await waitFor(() => expect(updateProfile).toHaveBeenCalled());
    const body = updateProfile.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body.full_name).toBeUndefined();
  });

  it('surfaces a save failure instead of claiming success', async () => {
    updateProfile.mockRejectedValue(new Error('LinkedIn URL must be http(s)'));
    const user = userEvent.setup();
    renderProfile();

    await screen.findByRole('heading', { name: /your profile/i });
    await clickSave(user);

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('LinkedIn URL must be http(s)'));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe('Profile — avatar upload', () => {
  it('refuses a non-image file', async () => {
    // applyAccept:false reproduces the real hazard: `accept="image/*"` is a
    // filter hint, not a constraint — a user can switch the picker to "All
    // files", which is precisely why the JS type check has to exist.
    const user = userEvent.setup({ applyAccept: false });
    const { container } = renderProfile();

    await screen.findByRole('heading', { name: /your profile/i });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' }));

    expect(toastError).toHaveBeenCalledWith('Please choose an image file.');
    expect(imageFileToDataUrl).not.toHaveBeenCalled();
  });

  it('refuses an image over the 6 MB ceiling before doing any work', async () => {
    // The avatar is stored inline as a data URI in a TEXT column, so an
    // oversized file is a database problem, not just a slow upload.
    const user = userEvent.setup({ applyAccept: false });
    const { container } = renderProfile();

    await screen.findByRole('heading', { name: /your profile/i });
    const big = new File([new Uint8Array(7 * 1024 * 1024)], 'huge.png', { type: 'image/png' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, big);

    expect(toastError).toHaveBeenCalledWith('Image is too large (max 6 MB).');
    expect(imageFileToDataUrl).not.toHaveBeenCalled();
  });

  it('downscales an accepted image to a 256px data URI', async () => {
    const user = userEvent.setup();
    const { container } = renderProfile();

    await screen.findByRole('heading', { name: /your profile/i });
    const png = new File(['x'], 'me.png', { type: 'image/png' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, png);

    await waitFor(() => expect(imageFileToDataUrl).toHaveBeenCalledWith(png, 256));
  });
});
