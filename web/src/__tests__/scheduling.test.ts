// scheduling.ts — every id interpolated into a URL goes through pathId (see
// pathId.ts), so a malformed id is refused before any request is built, and
// each endpoint's path matches the router in interview_scheduling.py exactly.

import { describe, it, expect, vi, beforeEach } from 'vitest';

const client = {
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  fetchBlobWithAuth: vi.fn(),
};
vi.mock('../api/client', () => ({
  apiGet: (...a: unknown[]) => client.apiGet(...a) as unknown,
  apiPost: (...a: unknown[]) => client.apiPost(...a) as unknown,
  apiPut: (...a: unknown[]) => client.apiPut(...a) as unknown,
  apiPatch: (...a: unknown[]) => client.apiPatch(...a) as unknown,
  apiDelete: (...a: unknown[]) => client.apiDelete(...a) as unknown,
  fetchBlobWithAuth: (...a: unknown[]) => client.fetchBlobWithAuth(...a) as unknown,
}));

import * as scheduling from '../api/scheduling';

const UUID_A = '11111111-1111-1111-1111-111111111111';
const UUID_B = '22222222-2222-2222-2222-222222222222';
const BAD_LINK = 'That link does not point at a valid record.';

beforeEach(() => {
  vi.clearAllMocks();
  client.apiGet.mockResolvedValue({ slots: [] });
  client.apiPost.mockResolvedValue({});
  client.apiPut.mockResolvedValue({});
  client.apiPatch.mockResolvedValue({});
  client.apiDelete.mockResolvedValue(undefined);
});

describe('scheduling.ts — HR loop/session URLs', () => {
  it('lists loops for an enrolment', async () => {
    client.apiGet.mockResolvedValue([]);
    await scheduling.listLoopsForEnrolment(UUID_A);
    expect(client.apiGet).toHaveBeenCalledWith(`/hr/enrolments/${UUID_A}/loops`);
  });

  it('refuses a malformed enrolment id before any network call', () => {
    expect(() => scheduling.listLoopsForEnrolment('not-a-uuid')).toThrow(BAD_LINK);
    expect(client.apiGet).not.toHaveBeenCalled();
  });

  it('adds a session against the loop id', async () => {
    await scheduling.addSession(UUID_A, {
      round_id: UUID_B,
      duration_minutes: 45,
      interviewer_user_ids: [UUID_B],
    });
    expect(client.apiPost).toHaveBeenCalledWith(
      `/hr/loops/${UUID_A}/sessions`,
      expect.objectContaining({ round_id: UUID_B }),
    );
  });

  it('reschedules against the session id', async () => {
    await scheduling.rescheduleSession(UUID_A, { starts_at: '2026-09-25T05:00:00.000Z' });
    expect(client.apiPatch).toHaveBeenCalledWith(`/hr/sessions/${UUID_A}`, expect.any(Object));
  });

  it('sends and cancels a loop by its own id', async () => {
    await scheduling.sendLoop(UUID_A);
    expect(client.apiPost).toHaveBeenCalledWith(`/hr/loops/${UUID_A}/send`, {});
    await scheduling.cancelLoop(UUID_A, 'no longer needed');
    expect(client.apiPost).toHaveBeenCalledWith(`/hr/loops/${UUID_A}/cancel`, {
      reason: 'no longer needed',
    });
  });

  it('records an outcome against the session id', async () => {
    await scheduling.setSessionOutcome(UUID_A, 'completed');
    expect(client.apiPost).toHaveBeenCalledWith(`/hr/sessions/${UUID_A}/outcome`, {
      outcome: 'completed',
      reason: null,
    });
  });

  it('fetches free slots for a session and unwraps the envelope', async () => {
    client.apiGet.mockResolvedValue({ slots: ['2026-09-25T05:00:00.000Z'] });
    const slots = await scheduling.getSessionSlots(UUID_A);
    expect(client.apiGet).toHaveBeenCalledWith(`/hr/sessions/${UUID_A}/slots`);
    expect(slots).toEqual(['2026-09-25T05:00:00.000Z']);
  });
});

describe('scheduling.ts — availability URLs (HR-on-anyone vs the interviewer’s own)', () => {
  it('reads and writes any interviewer’s availability by user id', async () => {
    await scheduling.getInterviewerAvailability(UUID_A);
    expect(client.apiGet).toHaveBeenCalledWith(`/hr/interviewers/${UUID_A}/availability`);
    await scheduling.addInterviewerAvailability(UUID_A, {
      starts_at: '2026-09-25T00:00:00.000Z',
      ends_at: '2026-09-25T08:00:00.000Z',
    });
    expect(client.apiPost).toHaveBeenCalledWith(
      `/hr/interviewers/${UUID_A}/availability`,
      expect.any(Object),
    );
  });

  it('removes a window by its own id — HR path and the interviewer’s own path differ', async () => {
    await scheduling.removeInterviewerAvailability(UUID_A);
    expect(client.apiDelete).toHaveBeenCalledWith(`/hr/availability/${UUID_A}`);
    await scheduling.removeMyAvailability(UUID_A);
    expect(client.apiDelete).toHaveBeenCalledWith(`/interviewer/availability/${UUID_A}`);
  });

  it('never puts an id in the caller’s own /interviewer routes', async () => {
    await scheduling.getMyAvailability();
    expect(client.apiGet).toHaveBeenCalledWith('/interviewer/availability');
    await scheduling.getMySessions();
    expect(client.apiGet).toHaveBeenCalledWith('/interviewer/sessions');
  });
});

describe('scheduling.ts — the candidate’s own URLs', () => {
  it('reads slots and books by loop and session id', async () => {
    await scheduling.getMySessionSlots(UUID_A, UUID_B);
    expect(client.apiGet).toHaveBeenCalledWith(
      `/users/me/interview-loops/${UUID_A}/sessions/${UUID_B}/slots`,
    );
    await scheduling.bookMySlot(UUID_A, {
      session_id: UUID_B,
      starts_at: '2026-09-25T05:00:00.000Z',
    });
    expect(client.apiPost).toHaveBeenCalledWith(
      `/users/me/interview-loops/${UUID_A}/book`,
      expect.objectContaining({ session_id: UUID_B }),
    );
  });

  it('lists the candidate’s own loops with no id to validate', async () => {
    client.apiGet.mockResolvedValue([]);
    await scheduling.listMyInterviewLoops();
    expect(client.apiGet).toHaveBeenCalledWith('/users/me/interview-loops');
  });
});

describe('scheduling.ts — panel workload and calibration', () => {
  it('builds the workload query string from the given range', async () => {
    client.apiGet.mockResolvedValue({ interviewers: [] });
    await scheduling.getWorkload('2026-09-01T00:00:00.000Z', '2026-09-08T00:00:00.000Z');
    expect(client.apiGet).toHaveBeenCalledWith(
      '/hr/panel/workload?start=2026-09-01T00%3A00%3A00.000Z&end=2026-09-08T00%3A00%3A00.000Z',
    );
  });

  it('sets capacity against the interviewer’s own id', async () => {
    await scheduling.setInterviewerCapacity(UUID_A, { max_sessions_per_day: 5 });
    expect(client.apiPut).toHaveBeenCalledWith(`/hr/interviewers/${UUID_A}/capacity`, {
      max_sessions_per_day: 5,
    });
  });

  it('refuses a malformed opening/round id in the calibration filter', () => {
    expect(() =>
      scheduling.getCalibration({
        start: '2026-01-01T00:00:00.000Z',
        end: '2026-04-01T00:00:00.000Z',
        requisitionId: 'not-a-uuid',
      }),
    ).toThrow(BAD_LINK);
    expect(client.apiGet).not.toHaveBeenCalled();
  });

  it('includes the opening/round filters only when chosen', async () => {
    client.apiGet.mockResolvedValue({ interviewers: [] });
    await scheduling.getCalibration({
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-04-01T00:00:00.000Z',
      requisitionId: UUID_A,
      roundId: UUID_B,
    });
    const url = client.apiGet.mock.calls[0]?.[0] as string;
    expect(url).toContain(`requisition_id=${UUID_A}`);
    expect(url).toContain(`round_id=${UUID_B}`);
  });
});
