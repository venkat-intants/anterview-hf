// generationError - the four generation routes answer 502 / 503 / 429 with a
// fixed sentence. The helper decides whether a retry can help; the component
// shows the sentence inline and never a status code.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApiError } from '../api/client';
import { isDailyGenerationLimit, toGenerationFailure } from '../lib/generationError';
import { GenerationError } from '../components/bank/GenerationError';

const DAILY = "Your company has reached today's limit for AI question generation. Try again tomorrow.";
const PAUSED =
  'Usage limits are temporarily unavailable, so this action is paused. Please try again in a minute.';

describe('toGenerationFailure', () => {
  it('detects the daily cap by status 429 plus the sentence', () => {
    expect(isDailyGenerationLimit(new ApiError(DAILY, 429))).toBe(true);
    expect(isDailyGenerationLimit(new ApiError('Your company has reached today’s limit', 429))).toBe(true);
  });

  it('does not take the per-minute 429, other statuses, or a plain Error for the daily cap', () => {
    expect(isDailyGenerationLimit(new ApiError('Too many requests. Try again in a minute.', 429))).toBe(false);
    expect(isDailyGenerationLimit(new ApiError(DAILY, 503))).toBe(false);
    expect(isDailyGenerationLimit(new Error(DAILY))).toBe(false);
  });

  it('keeps the server sentence and offers a retry for 502, 503 and the per-minute 429', () => {
    for (const e of [
      new ApiError('The AI service did not return usable questions.', 502),
      new ApiError(PAUSED, 503),
      new ApiError('Too many requests. Try again in a minute.', 429),
    ]) {
      expect(toGenerationFailure(e, 'fallback')).toEqual({ message: e.message, canRetry: true });
    }
  });

  it('shows the daily sentence with no retry', () => {
    expect(toGenerationFailure(new ApiError(DAILY, 429), 'fallback')).toEqual({
      message: DAILY,
      canRetry: false,
    });
  });

  it('never surfaces a bare status code', () => {
    expect(toGenerationFailure(new ApiError('HTTP 502', 502), 'Could not generate.').message).toBe(
      'Could not generate.',
    );
    expect(toGenerationFailure(new ApiError('', 503), 'Could not generate.').message).toBe(
      'Could not generate.',
    );
    expect(toGenerationFailure('weird', 'Could not generate.').message).toBe('Could not generate.');
  });
});

describe('GenerationError', () => {
  it('shows the sentence inline with Try again', async () => {
    const onRetry = vi.fn();
    render(
      <GenerationError failure={toGenerationFailure(new ApiError(PAUSED, 503), 'x')} onRetry={onRetry} />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(PAUSED);
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('shows no retry button for the daily limit', () => {
    render(
      <GenerationError failure={toGenerationFailure(new ApiError(DAILY, 429), 'x')} onRetry={vi.fn()} />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent("today's limit");
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});
