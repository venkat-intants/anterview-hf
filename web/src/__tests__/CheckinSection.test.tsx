// CheckinSection — the 90-day hire check-in (PH5 wave-1 follow-up A). What
// matters here:
//   • Employment/Reason/Performance are each a closed choice — no free text
//     anywhere (the security review removed `correction_reason` too);
//   • Employment ⇒ iff Reason (left) or Performance (employed), never both;
//   • a correction is the SAME form again, pre-filled, with no reason field —
//     just the "kept as a previous version" line;
//   • a 409/422 is shown inline (role="alert"), using the server's own text;
//   • the check-in WINDOW gates the form: closed ⇒ no form at all; open but
//     before day 80 ⇒ "Still employed" is disabled with a date hint.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CheckinOut, EnrolmentCheckins } from '../api/checkins';

const checkinsApi = {
  getEnrolmentCheckins: vi.fn(),
  createCheckin: vi.fn(),
  correctCheckin: vi.fn(),
};
vi.mock('../api/checkins', () => ({
  getEnrolmentCheckins: (...a: unknown[]) => checkinsApi.getEnrolmentCheckins(...a) as unknown,
  createCheckin: (...a: unknown[]) => checkinsApi.createCheckin(...a) as unknown,
  correctCheckin: (...a: unknown[]) => checkinsApi.correctCheckin(...a) as unknown,
}));

import CheckinSection from '../components/hr/CheckinSection';
import { ApiError } from '../api/client';

const OPEN_WINDOW = {
  start: '2026-06-01',
  employed_from: '2026-08-20',
  closes_at: '2026-11-28',
  open: true,
};

function liveCheckin(over: Partial<CheckinOut> = {}): CheckinOut {
  return {
    checkin_id: 'ci-1',
    enrolment_id: 'en-1',
    kind: '90_day',
    employment: 'employed',
    left_reason: null,
    performance: 'meets',
    recorded_by_user_id: 'u-hr-1',
    recorded_by_name: 'Ishaan Kapoor',
    recorded_at: '2026-09-10T00:00:00.000Z',
    supersedes_id: null,
    superseded: false,
    ...over,
  };
}

function checkinsResponse(over: Partial<EnrolmentCheckins> = {}): EnrolmentCheckins {
  return {
    checkins: [],
    notice: 'This is recorded by HR and used only in aggregate.',
    window: OPEN_WINDOW,
    ...over,
  };
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <CheckinSection enrolmentId="en-1" />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  checkinsApi.getEnrolmentCheckins.mockResolvedValue(checkinsResponse());
});

describe('CheckinSection — notice and empty state', () => {
  it('shows the server notice verbatim, and the create form with nothing recorded yet', async () => {
    renderSection();

    expect(
      await screen.findByText('This is recorded by HR and used only in aggregate.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Employment')).toBeInTheDocument();
  });
});

describe('CheckinSection — iff fields', () => {
  it('shows Reason only when Left is chosen', async () => {
    renderSection();
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('Employment'), 'left');

    expect(screen.getByLabelText('Reason')).toBeInTheDocument();
    expect(screen.queryByLabelText('Performance')).not.toBeInTheDocument();
  });

  it('shows Performance only when Still employed is chosen', async () => {
    renderSection();
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText('Employment'), 'employed');

    expect(screen.getByLabelText('Performance')).toBeInTheDocument();
    expect(screen.queryByLabelText('Reason')).not.toBeInTheDocument();
  });
});

describe('CheckinSection — create', () => {
  it('is disabled until a full combination is chosen, then saves it', async () => {
    checkinsApi.createCheckin.mockResolvedValue(liveCheckin());
    renderSection();
    const user = userEvent.setup();

    const save = () => screen.getByRole('button', { name: 'Save check-in' });
    await screen.findByLabelText('Employment');
    expect(save()).toBeDisabled();

    await user.selectOptions(screen.getByLabelText('Employment'), 'left');
    expect(save()).toBeDisabled(); // Reason not chosen yet

    await user.selectOptions(screen.getByLabelText('Reason'), 'voluntary');
    expect(save()).toBeEnabled();

    await user.click(save());

    await waitFor(() =>
      expect(checkinsApi.createCheckin).toHaveBeenCalledWith('en-1', {
        employment: 'left',
        left_reason: 'voluntary',
      }),
    );
  });

  it('invalidates the analytics funnel and the due list too, not just its own query', async () => {
    checkinsApi.createCheckin.mockResolvedValue(liveCheckin());
    const { client } = renderSection();
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText('Employment'), 'employed');
    await user.selectOptions(screen.getByLabelText('Performance'), 'meets');
    await user.click(screen.getByRole('button', { name: 'Save check-in' }));

    await waitFor(() => expect(checkinsApi.createCheckin).toHaveBeenCalled());
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['hr', 'enrolment', 'en-1', 'checkins'],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['hr', 'analytics', 'funnel'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['hr', 'checkins', 'due'] });
  });

  it('shows a 409 inline, using the server detail, rather than a toast', async () => {
    checkinsApi.createCheckin.mockRejectedValue(
      new ApiError('A live check-in already exists for this hire.', 409),
    );
    renderSection();
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText('Employment'), 'employed');
    await user.selectOptions(screen.getByLabelText('Performance'), 'meets');
    await user.click(screen.getByRole('button', { name: 'Save check-in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A live check-in already exists for this hire.',
    );
  });
});

describe('CheckinSection — correction (no reason field)', () => {
  it('shows the live check-in, then a pre-filled form with no reason textarea', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({ checkins: [liveCheckin()] }),
    );
    renderSection();
    const user = userEvent.setup();

    expect(await screen.findByText('Still employed — Meets expectations')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Correct' }));

    expect(
      screen.getByText('The earlier record is kept as a previous version.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Why are you correcting this?')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Employment')).toHaveValue('employed');
    expect(screen.getByLabelText('Performance')).toHaveValue('meets');
  });

  it('submits the same shape as a create — no correction_reason', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({ checkins: [liveCheckin()] }),
    );
    checkinsApi.correctCheckin.mockResolvedValue(liveCheckin({ performance: 'exceeds' }));
    renderSection();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Correct' }));
    await user.selectOptions(screen.getByLabelText('Performance'), 'exceeds');
    await user.click(screen.getByRole('button', { name: 'Save correction' }));

    await waitFor(() =>
      expect(checkinsApi.correctCheckin).toHaveBeenCalledWith('ci-1', {
        employment: 'employed',
        performance: 'exceeds',
      }),
    );
  });
});

describe('CheckinSection — who recorded it', () => {
  it('shows the recorder by name', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({ checkins: [liveCheckin({ recorded_by_name: 'Ishaan Kapoor' })] }),
    );
    renderSection();

    expect(await screen.findByText(/Recorded by Ishaan Kapoor on/)).toBeInTheDocument();
  });

  it('falls back to "a former team member" — never the raw id — when the name is null', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({ checkins: [liveCheckin({ recorded_by_name: null })] }),
    );
    renderSection();

    expect(await screen.findByText(/Recorded by a former team member on/)).toBeInTheDocument();
    expect(screen.queryByText(/u-hr-1/)).not.toBeInTheDocument();
  });
});

describe('CheckinSection — the check-in window', () => {
  it('shows no form once the window has closed, and says so when nothing was ever recorded', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({
        checkins: [],
        window: {
          start: '2026-01-01',
          employed_from: '2026-03-22',
          closes_at: '2026-06-30',
          open: false,
        },
      }),
    );
    renderSection();

    expect(
      await screen.findByText(/The check-in window for this hire closed on/),
    ).toBeInTheDocument();
    expect(screen.getByText(/No check-in was ever recorded\./)).toBeInTheDocument();
    expect(screen.queryByLabelText('Employment')).not.toBeInTheDocument();
  });

  it('does not offer Correct once the window has closed, even with a live check-in', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({
        checkins: [liveCheckin()],
        window: {
          start: '2026-01-01',
          employed_from: '2026-03-22',
          closes_at: '2026-06-30',
          open: false,
        },
      }),
    );
    renderSection();

    expect(await screen.findByText('Still employed — Meets expectations')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Correct' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Employment')).not.toBeInTheDocument();
  });

  it('disables "Still employed" before day 80, with a date hint', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({
        window: {
          start: '2026-09-01',
          employed_from: '2099-01-01',
          closes_at: '2099-06-30',
          open: true,
        },
      }),
    );
    renderSection();

    const employedOption = await screen.findByRole('option', { name: 'Still employed' });
    expect(employedOption).toBeDisabled();
    expect(screen.getByText(/Available from/)).toBeInTheDocument();
  });
});

describe('CheckinSection — earlier versions', () => {
  it('collapses superseded rows under "Earlier versions"', async () => {
    checkinsApi.getEnrolmentCheckins.mockResolvedValue(
      checkinsResponse({
        checkins: [
          liveCheckin({ checkin_id: 'ci-2', performance: 'exceeds' }),
          liveCheckin({
            checkin_id: 'ci-1',
            superseded: true,
            performance: 'below',
            recorded_at: '2026-09-01T00:00:00.000Z',
          }),
        ],
      }),
    );
    renderSection();

    expect(await screen.findByText('Earlier versions (1)')).toBeInTheDocument();
    expect(
      screen.getByText('Still employed — Below expectations', { exact: false }),
    ).toBeInTheDocument();
  });
});
