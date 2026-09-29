// AttemptProctoringSummary — camera-proctoring contract §7.
//
// The property this screen exists to get right: a low integrity score tells
// HR nothing about WHY it dropped, and gaze_away is deliberately the least
// reliable, lowest-weighted signal (contract §2) — folding it back into the
// same counted list a reader scans would let a reviewer reconstruct exactly
// the discriminatory "low score = cheated" read-through the weighting was
// designed to prevent. So: gaze is its own informational block, the score is
// last (never the headline), "camera not in use" is said in words rather
// than rendered as a clean-looking zero, and nothing on this screen implies
// a conclusion about the candidate.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AttemptProctoring } from '../api/exams';

const api = { getAttemptProctoring: vi.fn() };
vi.mock('../api/exams', async () => {
  const actual = await vi.importActual<typeof import('../api/exams')>('../api/exams');
  return {
    ...actual,
    getAttemptProctoring: (...a: unknown[]) => api.getAttemptProctoring(...a) as unknown,
  };
});

import AttemptProctoringSummary from '../components/hr/AttemptProctoringSummary';

const WITH_EVENTS: AttemptProctoring = {
  camera_in_use: true,
  integrity_score: 55,
  counts: { multiple_faces: 1, tab_blur: 2, gaze_away: 1 },
  events: [
    { event_type: 'tab_blur', started_at: '2026-09-01T10:00:00.000Z', ended_at: null, duration_seconds: null },
    { event_type: 'tab_blur', started_at: '2026-09-01T10:05:00.000Z', ended_at: null, duration_seconds: null },
    {
      event_type: 'multiple_faces',
      started_at: '2026-09-01T10:10:00.000Z',
      ended_at: '2026-09-01T10:16:15.000Z',
      duration_seconds: 375,
    },
    {
      event_type: 'gaze_away',
      started_at: '2026-09-01T10:20:00.000Z',
      ended_at: '2026-09-01T10:20:06.000Z',
      duration_seconds: 6,
    },
  ],
};

const NO_CAMERA: AttemptProctoring = {
  camera_in_use: false,
  integrity_score: 85,
  counts: { tab_blur: 1 },
  events: [
    { event_type: 'tab_blur', started_at: '2026-09-01T10:00:00.000Z', ended_at: null, duration_seconds: null },
  ],
};

const NO_CAMERA_NO_EVENTS: AttemptProctoring = {
  camera_in_use: false,
  integrity_score: null,
  counts: {},
  events: [],
};

function renderSummary(examId = 'e-1', attemptId = 'a-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AttemptProctoringSummary examId={examId} attemptId={attemptId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getAttemptProctoring.mockResolvedValue(WITH_EVENTS);
});

describe('AttemptProctoringSummary — loading and error', () => {
  it('shows a loading state before the data arrives', () => {
    api.getAttemptProctoring.mockReturnValue(new Promise(() => {})); // never resolves
    renderSummary();
    expect(screen.getByText(/loading proctoring timeline/i)).toBeInTheDocument();
  });

  it('explains an unloadable summary rather than rendering an empty shell', async () => {
    api.getAttemptProctoring.mockRejectedValue(new Error('boom'));
    renderSummary();
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});

describe('AttemptProctoringSummary — gaze_away is its own informational line', () => {
  it('labels the gaze block as informational and keeps it out of the counted-signals group', async () => {
    renderSummary();

    const gazeBlock = await screen.findByTestId('proctoring-gaze-line');
    expect(within(gazeBlock).getByText(/looked away from the screen — informational only/i)).toBeInTheDocument();
    expect(within(gazeBlock).getByText(/never ends an exam/i)).toBeInTheDocument();
    expect(within(gazeBlock).getByText(/innocent explanations/i)).toBeInTheDocument();

    // It must never show up inside the counted-signals group — under its
    // friendly wording OR its raw event_type, however it might be labelled.
    const groups = screen.getByTestId('proctoring-event-groups');
    expect(within(groups).queryByText(/informational/i)).not.toBeInTheDocument();
    expect(within(groups).queryByText(/looked away/i)).not.toBeInTheDocument();
    expect(within(groups).queryByText(/gaze/i)).not.toBeInTheDocument();
    // Exactly the two non-gaze signals from the fixture's counts, nothing more.
    expect(within(groups).getAllByText(/· \d+ times?$/)).toHaveLength(2);
  });

  it('shows a ranged event with its own duration, not just a tick mark', async () => {
    renderSummary();

    // The ranged camera signal (multiple_faces, 375s = 6m 15s) inside the
    // counted-signals group...
    expect(await screen.findByText(/lasted 6m 15s/)).toBeInTheDocument();
    // ...and the ranged gaze occurrence (6s), worded exactly as the spec's
    // own example ("looked away for 6s").
    expect(await screen.findByText(/looked away for 6s/)).toBeInTheDocument();
  });

  it('leads with what happened and puts the score last, not first', async () => {
    const { container } = renderSummary();
    await screen.findByTestId('proctoring-event-groups');

    const text = container.textContent ?? '';
    const eventsIdx = text.indexOf('Left fullscreen') !== -1 ? text.indexOf('Left fullscreen') : text.indexOf('Switched away from the exam tab');
    const scoreIdx = text.indexOf('Integrity score');
    expect(eventsIdx).toBeGreaterThan(-1);
    expect(scoreIdx).toBeGreaterThan(-1);
    expect(eventsIdx).toBeLessThan(scoreIdx);
  });
});

describe('AttemptProctoringSummary — camera not in use', () => {
  it('says plainly that the camera was not enabled, rather than rendering camera signals as zero', async () => {
    api.getAttemptProctoring.mockResolvedValue(NO_CAMERA);
    renderSummary();

    expect(
      await screen.findByText(/camera proctoring was not enabled for this attempt/i),
    ).toBeInTheDocument();
    // The camera-only signals never appear as a "0" count — they simply do
    // not exist as a countable row at all.
    expect(screen.queryByText(/no face visible/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/more than one face visible/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/× 0/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0 times/)).not.toBeInTheDocument();
    // Gaze was never measured either — no informational block to show.
    expect(screen.queryByTestId('proctoring-gaze-line')).not.toBeInTheDocument();

    // Non-camera browser events still show — the camera being off does not
    // hide the events that ARE real.
    expect(screen.getByText(/switched away from the exam tab/i)).toBeInTheDocument();
  });

  it('says so when there is nothing at all to report', async () => {
    api.getAttemptProctoring.mockResolvedValue(NO_CAMERA_NO_EVENTS);
    renderSummary();

    expect(
      await screen.findByText(/no other proctoring events were recorded either/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/no integrity score recorded for this attempt/i)).toBeInTheDocument();
  });
});

describe('AttemptProctoringSummary — no verdict language', () => {
  it('never states or implies a conclusion about the candidate', async () => {
    renderSummary();
    const panel = await screen.findByTestId('attempt-proctoring-summary');

    const forbidden = /suspicious|cheat(ed|ing)?|guilty|accus|verdict|conclusion|flagg?ed/i;
    expect(panel.textContent ?? '').not.toMatch(forbidden);
    expect(screen.queryByText(/^fail$/i)).not.toBeInTheDocument();
  });
});
