// Onboarding wizard — the three answers that used to go nowhere.
//
// Each test here locks down a promise the wizard makes on screen:
//   1. "Your AI interviewer will speak and listen in this language" — the
//      language answer has to reach the key every start-interview page reads,
//      overwriting the 'en' AppShell seeds on first paint, and the UI itself.
//   2. "What should we call you?" — the name they type must survive a slow
//      GET /users/me/onboarding landing mid-keystroke, and must reach the
//      session user so the dashboard does not greet them by the old name.
//
// Rendered without AppShell, like Dashboard.test.tsx, to keep the scope tight.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { I18nextProvider } from 'react-i18next';

import { AuthProvider, useAuth } from '../context/AuthContext';
import Onboarding from '../pages/Onboarding';
import i18n from '../lib/i18n';
import {
  getOnboardingStatus,
  getPracticePlan,
  previewPracticePlan,
  submitOnboarding,
  type OnboardingStatus,
} from '../api/onboarding';

const INTERVIEW_LANGUAGE_KEY = 'intants:interview-language';

vi.mock('../api/onboarding', async () => {
  const actual =
    await vi.importActual<typeof import('../api/onboarding')>('../api/onboarding');
  return {
    ...actual,
    getOnboardingStatus: vi.fn(),
    getPracticePlan: vi.fn(),
    previewPracticePlan: vi.fn(),
    submitOnboarding: vi.fn(),
    skipOnboarding: vi.fn(),
  };
});

const FRESH_STATUS: OnboardingStatus = {
  applicable: true,
  seen: false,
  completed: false,
  skipped: false,
  full_name: 'Venkatapathi Idukuda',
  goal: null,
  target_role: null,
  target_level: null,
  preferred_language: 'en',
};

const EMPTY_PLAN = {
  ready: false,
  target_role: null,
  target_level: null,
  domain_family: null,
  domain_label: null,
  what_this_person_does: null,
  competencies: [],
  readiness: null,
  interviews_completed: 0,
  last_interview_at: null,
  focus_competency_id: null,
  focus_competency_name: null,
  never_probed: [],
};

/** Mirrors the session the user arrives with — set once, like a real login. */
function TokenSetter({ children }: { children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('mock-access-token', {
      user_id: '11111111-1111-1111-1111-111111111111',
      full_name: 'Venkatapathi Idukuda',
      email: 'venkat@intants.com',
      roles: ['candidate'],
    });
  }, [setAuth]);
  return <>{children}</>;
}

/** Surfaces the AuthContext copy of the name the shell greets people by. */
function SessionNameProbe() {
  const { user } = useAuth();
  return <span data-testid="session-name">{user?.full_name ?? 'none'}</span>;
}

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/onboarding']}>
          <AuthProvider>
            <TokenSetter>
              <SessionNameProbe />
              <Routes>
                <Route path="/onboarding" element={<Onboarding />} />
                <Route path="/dashboard" element={<h1>Dashboard</h1>} />
              </Routes>
            </TokenSetter>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

/** Walks steps 1→4 with a role typed, leaving the language step on screen. */
async function advanceToLanguageStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /continue/i }));
  await user.click(await screen.findByRole('button', { name: /continue/i }));
  const roleInput = await screen.findByRole('textbox');
  await user.type(roleInput, 'CNC Machine Operator');
  await user.click(screen.getByRole('button', { name: /continue/i }));
  await screen.findByRole('radiogroup', { name: /which language/i });
}

describe('Onboarding wizard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(getOnboardingStatus).mockResolvedValue(FRESH_STATUS);
    vi.mocked(getPracticePlan).mockResolvedValue(EMPTY_PLAN);
    vi.mocked(previewPracticePlan).mockResolvedValue(EMPTY_PLAN);
    vi.mocked(submitOnboarding).mockImplementation((body) =>
      Promise.resolve({
        ...FRESH_STATUS,
        seen: true,
        completed: true,
        full_name: body.full_name ?? null,
        goal: body.goal ?? null,
        target_role: body.target_role,
        target_level: body.target_level,
        preferred_language: body.preferred_language,
      }),
    );
  });

  afterEach(async () => {
    // changeLanguage mutates the shared i18n instance for the rest of the file.
    await i18n.changeLanguage('en');
  });

  it('sends the chosen language to the interview start path and the UI', async () => {
    const user = userEvent.setup();
    // AppShell writes this on first paint for anyone who reaches the shell, so
    // the wizard's write has to overwrite rather than fall back.
    localStorage.setItem(INTERVIEW_LANGUAGE_KEY, 'en');

    renderWizard();
    await advanceToLanguageStep(user);

    await user.click(screen.getByRole('radio', { name: 'తెలుగు' }));
    await user.click(screen.getByRole('button', { name: /build my practice plan/i }));

    await waitFor(() => {
      expect(vi.mocked(submitOnboarding)).toHaveBeenCalledWith(
        expect.objectContaining({ preferred_language: 'te' }),
      );
    });
    await waitFor(() => {
      expect(localStorage.getItem(INTERVIEW_LANGUAGE_KEY)).toBe('te');
    });
    expect(i18n.language).toBe('te');
  });

  it('pushes the name they typed into the session so the shell stops using the old one', async () => {
    const user = userEvent.setup();
    renderWizard();

    const nameInput = await screen.findByRole('textbox');
    await user.clear(nameInput);
    await user.type(nameInput, 'Venkat');
    await advanceToLanguageStep(user);
    await user.click(screen.getByRole('button', { name: /build my practice plan/i }));

    await waitFor(() => {
      expect(screen.getByTestId('session-name')).toHaveTextContent('Venkat');
    });
  });

  it('does not overwrite a name being typed when the status request lands late', async () => {
    const user = userEvent.setup();
    let resolveStatus: (s: OnboardingStatus) => void = () => undefined;
    vi.mocked(getOnboardingStatus).mockReturnValue(
      new Promise<OnboardingStatus>((resolve) => {
        resolveStatus = resolve;
      }),
    );

    renderWizard();

    const nameInput = await screen.findByRole('textbox');
    await user.type(nameInput, 'Venkat');

    // The cold-container case: the response arrives after they started typing.
    resolveStatus(FRESH_STATUS);

    await waitFor(() => {
      expect(vi.mocked(getOnboardingStatus)).toHaveBeenCalled();
    });
    expect(nameInput).toHaveValue('Venkat');
  });

  it('exposes selection state on the option groups, not just a colour', async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.click(await screen.findByRole('button', { name: /continue/i }));

    const goals = await screen.findByRole('radiogroup', { name: /what brings you here/i });
    const firstJob = await screen.findByRole('radio', { name: /looking for my first job/i });
    expect(goals).toBeInTheDocument();
    expect(firstJob).toHaveAttribute('aria-checked', 'false');

    await user.click(firstJob);
    expect(firstJob).toHaveAttribute('aria-checked', 'true');
  });
});
