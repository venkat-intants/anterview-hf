// Audit X1 + X16: no screen should ever show "HTTP 422", "HTTP 500" or the
// browser's "Failed to fetch". Two layers are pinned here:
//   - api/client.ts turns every error body (string, FastAPI array, object, none)
//     into an ApiError whose message is a sentence;
//   - lib/errorMessage.errorMessage() picks the sentence to show for any error.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiErrorFromBody, apiGet, apiPost } from '../api/client';
import {
  NETWORK_ERROR_MESSAGE,
  SESSION_EXPIRED_MESSAGE,
  errorMessage,
  humaniseField,
  isReadableMessage,
  statusMessage,
  validationMessage,
} from '../lib/errorMessage';
import { toGenerationFailure } from '../lib/generationError';

function stubFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: vi.fn().mockResolvedValue(body),
    } as unknown as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api client error messages', () => {
  it('prefers the server sentence for a FastAPI validation array', async () => {
    const detail = [{ type: 'greater_than_equal', loc: ['body', 'max_candidates'], msg: 'x' }];
    stubFetch(422, { detail, message: 'Max candidates must be 1 or more.' });

    const err = await apiPost('/hr/x', {}).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('Max candidates must be 1 or more.');
    expect((err as ApiError).status).toBe(422);
    // The array is kept for callers that map errors onto fields.
    expect((err as ApiError).detail).toEqual(detail);
    expect((err as ApiError).hasServerMessage).toBe(true);
  });

  it('describes the first array entry when the server sent no sentence', async () => {
    stubFetch(422, {
      detail: [
        { type: 'missing', loc: ['body', 'start_date'], msg: 'Field required' },
        { type: 'missing', loc: ['body', 'title'], msg: 'Field required' },
      ],
    });

    const err = (await apiGet('/hr/x').catch((e: unknown) => e)) as ApiError;

    expect(err.message).toBe('Start date is required.');
    expect(err.message).not.toMatch(/HTTP/);
  });

  it('never falls back to "HTTP nnn" when the body has no detail', async () => {
    stubFetch(503, {});
    const err = (await apiGet('/hr/x').catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe('The server had a problem. Try again in a moment.');
    expect(err.hasServerMessage).toBe(false);
  });

  it('keeps a string detail and an object detail message as they were', () => {
    expect(apiErrorFromBody({ detail: 'This opening is closed.' }, 409).message).toBe(
      'This opening is closed.',
    );
    const obj = { message: '3 candidates are still open.', unresolved: 3 };
    const err = apiErrorFromBody({ detail: obj }, 409);
    expect(err.message).toBe('3 candidates are still open.');
    expect(err.detail).toEqual(obj);
  });

  it('uses status copy for an object detail without a message, not "[object Object]"', () => {
    const err = apiErrorFromBody({ detail: { unresolved: 3 } }, 409);
    expect(err.message).toBe(statusMessage(409));
    expect(err.detail).toEqual({ unresolved: 3 });
  });

  it('handles a body that is not JSON at all', () => {
    expect(apiErrorFromBody(undefined, 502).message).toBe(statusMessage(502));
  });
});

describe('errorMessage', () => {
  const FALLBACK = 'Could not save the round.';

  it('maps a fetch TypeError to the connection sentence', () => {
    expect(errorMessage(new TypeError('Failed to fetch'), FALLBACK)).toBe(NETWORK_ERROR_MESSAGE);
    expect(errorMessage(new TypeError('Load failed'), FALLBACK)).toBe(NETWORK_ERROR_MESSAGE);
    expect(errorMessage(new Error('Network error - could not reach server'), FALLBACK)).toBe(
      NETWORK_ERROR_MESSAGE,
    );
  });

  it('shows a server sentence as written', () => {
    expect(errorMessage(new ApiError('This opening is closed.', 409), FALLBACK)).toBe(
      'This opening is closed.',
    );
  });

  it('prefers the caller fallback over client status copy, with a retry hint on 5xx', () => {
    const generic = new ApiError(statusMessage(500), 500, undefined, false);
    expect(errorMessage(generic, FALLBACK)).toBe(
      'Could not save the round. Try again in a moment.',
    );
    expect(errorMessage(new ApiError('Internal server error.', 500), FALLBACK)).toBe(
      'Could not save the round. Try again in a moment.',
    );
  });

  it('uses status copy where it says more than the fallback', () => {
    const forbidden = new ApiError(statusMessage(403), 403, undefined, false);
    expect(errorMessage(forbidden, FALLBACK)).toBe('You do not have permission to do that.');
    const throttled = new ApiError('HTTP 429', 429);
    expect(errorMessage(throttled, FALLBACK)).toBe(
      'Too many attempts. Wait a moment and try again.',
    );
  });

  it('never returns a bare status or transport text', () => {
    for (const err of [
      new ApiError('HTTP 422', 422),
      new ApiError('', 400),
      new Error('HTTP 404'),
      new Error('Failed to fetch'),
      new RangeError('Invalid time value'),
      'boom',
      null,
    ]) {
      const text = errorMessage(err, FALLBACK);
      expect(text).not.toMatch(/HTTP \d{3}|Failed to fetch|Invalid time value/);
    }
  });

  it('maps the client session-expired error to plain copy', () => {
    expect(errorMessage(new Error('Session expired'), FALLBACK)).toBe(SESSION_EXPIRED_MESSAGE);
  });

  it('shows a readable plain Error our own code threw', () => {
    const timeout = new Error('Upload timed out - check your connection and try again.');
    expect(errorMessage(timeout, FALLBACK)).toBe(timeout.message);
  });
});

describe('validationMessage and humaniseField', () => {
  it.each([
    [
      { type: 'string_too_long', loc: ['body', 'title'], ctx: { max_length: 120 } },
      'Title must be at most 120 characters.',
    ],
    [
      { type: 'string_too_short', loc: ['body', 'title'], ctx: { min_length: 1 } },
      'Title cannot be empty.',
    ],
    [
      { type: 'less_than_equal', loc: ['body', 'pass_mark'], ctx: { le: 100 } },
      'Pass mark must be 100 or less.',
    ],
    [{ type: 'int_parsing', loc: ['query', 'page'] }, 'Page must be a whole number.'],
    [
      { type: 'literal_error', loc: ['body', 'kind'], ctx: { expected: "'exam' or 'interview'" } },
      "Kind must be one of 'exam' or 'interview'.",
    ],
    [
      { type: 'value_error', loc: ['body', 'email'], msg: 'value is not a valid email address: x' },
      'Email must be a valid email address.',
    ],
    [
      { type: 'value_error', loc: ['body', 'ends_at'], msg: 'Value error, ends before it starts' },
      'Ends at: ends before it starts.',
    ],
    [
      { type: 'missing', loc: ['body'], msg: 'Field required' },
      'Some required information is missing.',
    ],
  ])('%o reads as a sentence', (entry, expected) => {
    expect(validationMessage([entry])).toBe(expected);
  });

  it('returns null for nothing usable', () => {
    expect(validationMessage([])).toBeNull();
    expect(validationMessage(['x'])).toBeNull();
  });

  it.each([
    [['body', 'jd_url'], 'JD URL'],
    [['body', 'items', 0, 'unitPrice'], 'Unit price'],
    [['body', 'email', 'str'], 'Email'],
    [['body'], null],
  ])('humaniseField(%o) is %s', (loc, expected) => {
    expect(humaniseField(loc)).toBe(expected);
  });

  it('isReadableMessage rejects codes, stubs and transport text', () => {
    expect(isReadableMessage('HTTP 500')).toBe(false);
    expect(isReadableMessage('502')).toBe(false);
    expect(isReadableMessage('[object Object]')).toBe(false);
    expect(isReadableMessage('NetworkError when attempting to fetch resource.')).toBe(false);
    expect(isReadableMessage('This opening is closed.')).toBe(true);
  });
});

describe('generation failures honour the client fallback flag', () => {
  it('shows the generator fallback, not the generic status copy', () => {
    const generic = new ApiError(statusMessage(502), 502, undefined, false);
    expect(toGenerationFailure(generic, 'Could not generate questions.').message).toBe(
      'Could not generate questions.',
    );
  });
});
