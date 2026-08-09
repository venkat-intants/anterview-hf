// Tests for the staff-only read of another user's profile (FE-2).
//
// This page renders CANDIDATE-CONTROLLED strings (linkedin_url, github_url,
// bio, headline) into a console opened by hr_manager / super_admin /
// platform_owner. `safeExternalUrl` exists because React 18 does not block a
// `javascript:` href — it only warns in development — so a self-registering
// candidate could otherwise run script in the console's origin on one click.
// That guard has no test; it gets one here, along with the page's own smoke.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PublicProfile } from '../types/auth';

const CANDIDATE: PublicProfile = {
  user_id: 'u-9',
  full_name: 'Bhavya Nair',
  email: 'bhavya@example.com',
  roles: ['candidate'],
  headline: 'Backend engineer, 4 years',
  bio: 'Built payment rails at scale.',
  employment_status: 'employed',
  desired_roles: 'Backend Engineer, Platform Engineer',
  linkedin_url: 'https://linkedin.com/in/bhavya',
  github_url: 'https://github.com/bhavya',
  location: 'Hyderabad',
  phone: null,
  official_email: null,
  avatar_url: null,
  company_name: null,
  has_resume: true,
  created_at: '2026-07-01T10:00:00.000Z',
};

const getUserProfile = vi.fn();
vi.mock('../api/profile', () => ({
  getUserProfile: (...a: unknown[]) => getUserProfile(...a) as unknown,
}));

import ProfileView from '../pages/ProfileView';

function renderProfile() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/u/u-9']}>
        <Routes>
          <Route path="/u/:userId" element={<ProfileView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getUserProfile.mockResolvedValue(CANDIDATE);
});

describe('ProfileView', () => {
  it('renders the profile the route asked for', async () => {
    renderProfile();

    expect(await screen.findByRole('heading', { name: 'Bhavya Nair' })).toBeInTheDocument();
    expect(getUserProfile).toHaveBeenCalledWith('u-9');
    expect(screen.getByText('Backend engineer, 4 years')).toBeInTheDocument();
    expect(screen.getByText('Built payment rails at scale.')).toBeInTheDocument();
  });

  it('splits the desired-roles CSV into separate chips', async () => {
    renderProfile();

    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('Platform Engineer')).toBeInTheDocument();
  });

  it('states resume presence explicitly rather than by omission', async () => {
    getUserProfile.mockResolvedValue({ ...CANDIDATE, has_resume: false, desired_roles: 'QA' });
    renderProfile();

    expect(await screen.findByText(/no resume uploaded/i)).toBeInTheDocument();
  });

  it('distinguishes a forbidden profile from a missing one', async () => {
    getUserProfile.mockRejectedValue(new Error('403 Forbidden'));
    renderProfile();

    expect(await screen.findByText(/don't have access to this profile/i)).toBeInTheDocument();
  });

  it('reports a genuinely missing profile as not found', async () => {
    getUserProfile.mockRejectedValue(new Error('HTTP 404'));
    renderProfile();

    expect(await screen.findByText(/profile not found/i)).toBeInTheDocument();
  });
});

describe('ProfileView — candidate-controlled link safety', () => {
  it('renders an http(s) profile link as a real anchor', async () => {
    renderProfile();

    const linkedin = await screen.findByRole('link', { name: /linkedin/i });
    expect(linkedin).toHaveAttribute('href', 'https://linkedin.com/in/bhavya');
  });

  it.each([
    ['javascript:alert(document.cookie)'],
    ['data:text/html,<script>alert(1)</script>'],
    ['vbscript:msgbox(1)'],
    ['/relative/not/absolute'],
    ['not a url at all'],
  ])('never emits %s as an href', async (hostile) => {
    getUserProfile.mockResolvedValue({
      ...CANDIDATE,
      linkedin_url: hostile,
      github_url: null,
    });
    renderProfile();

    await screen.findByRole('heading', { name: 'Bhavya Nair' });
    for (const anchor of screen.queryAllByRole('link')) {
      expect(anchor.getAttribute('href')).not.toBe(hostile);
      expect(anchor.getAttribute('href') ?? '').toMatch(/^https?:/);
    }
  });
});
