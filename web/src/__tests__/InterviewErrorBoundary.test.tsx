// Tests for the interview-scoped error boundary (FE-2, CWE-755).
//
// The behaviour under test is containment + recoverability: a render crash
// inside the live panel must leave the rest of the app mounted, and must offer
// a rejoin that re-mounts the panel (the LiveKit room and the server-side
// session outlive a client render crash) rather than only a page reload.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import InterviewErrorBoundary from '../features/interview/InterviewErrorBoundary';
import ErrorBoundary from '../components/ErrorBoundary';

// The boundary toasts from componentDidCatch; sonner needs no real toaster here.
vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

const APP_CHROME = 'app chrome outside the boundary';
const PANEL = 'live interview panel';

// Module-level switch so a rejoin can re-mount the SAME component into a
// healthy state — which is what a real transient render crash looks like.
let panelShouldCrash = true;

function Panel() {
  if (panelShouldCrash) throw new Error('malformed transcript item');
  return <div>{PANEL}</div>;
}

function renderScoped() {
  return render(
    <div>
      <div>{APP_CHROME}</div>
      <InterviewErrorBoundary>
        <Panel />
      </InterviewErrorBoundary>
    </div>,
  );
}

beforeEach(() => {
  panelShouldCrash = true;
  // React logs the caught error to console.error; the assertions below are the
  // real signal, so keep the run readable.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('InterviewErrorBoundary', () => {
  it('renders its children untouched when nothing throws', () => {
    panelShouldCrash = false;
    renderScoped();

    expect(screen.getByText(PANEL)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('contains a render crash — the surrounding app stays mounted', () => {
    renderScoped();

    expect(screen.getByText(APP_CHROME)).toBeInTheDocument();
    expect(screen.queryByText(PANEL)).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('offers a rejoin action, not only a page reload', () => {
    renderScoped();

    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reload page/i })).toBeInTheDocument();
  });

  it('rejoin re-mounts the interview panel', async () => {
    const user = userEvent.setup();
    renderScoped();

    expect(screen.queryByText(PANEL)).not.toBeInTheDocument();

    // The rejoin only helps if the crash was transient — simulate the panel
    // coming back healthy, which is the whole point of re-mounting rather than
    // reloading the document.
    panelShouldCrash = false;
    await user.click(screen.getByRole('button', { name: /try again/i }));

    expect(screen.getByText(PANEL)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('re-shows the fallback if the panel crashes again after a rejoin', async () => {
    const user = userEvent.setup();
    renderScoped();

    await user.click(screen.getByRole('button', { name: /try again/i }));

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(APP_CHROME)).toBeInTheDocument();
  });
});

// The new `fallback` prop must be purely additive — App.tsx mounts ErrorBoundary
// with children only, and that path has to keep rendering the reload card.
describe('ErrorBoundary — default fallback (unchanged app-root behaviour)', () => {
  it('renders the reload card when no custom fallback is supplied', () => {
    render(
      <ErrorBoundary>
        <Panel />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('button', { name: /reload page/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument();
  });
});
