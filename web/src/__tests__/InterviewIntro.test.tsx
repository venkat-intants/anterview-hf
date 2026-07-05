// Tests for InterviewIntro component.
//
// Covers:
//   1. Renders the correct language-matched video src.
//   2. "Begin interview" calls video.play() and hides the begin button.
//   3. onEnded event calls onDone.
//   4. "Skip" button calls onDone immediately.
//   5. onError on the video element calls onDone (graceful fallback).
//   6. Double-trigger (error + ended) calls onDone exactly once (guard ref).
//   7. Accessibility: aria-labels on buttons and video.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import InterviewIntro from '../components/InterviewIntro';

// jsdom does not implement HTMLMediaElement.play — stub it globally.
const mockPlay = vi.fn().mockResolvedValue(undefined);
Object.defineProperty(HTMLMediaElement.prototype, 'play', {
  configurable: true,
  writable: true,
  value: mockPlay,
});

describe('InterviewIntro', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Video src ─────────────────────────────────────────────────────────────

  it('renders the English intro clip when language is "en"', () => {
    render(<InterviewIntro language="en" onDone={() => undefined} />);
    const video = screen.getByTestId('intro-video');
    expect((video as HTMLVideoElement).src).toContain('/intro/intro_en.mp4');
  });

  it('renders the Hindi intro clip when language is "hi"', () => {
    render(<InterviewIntro language="hi" onDone={() => undefined} />);
    const video = screen.getByTestId('intro-video');
    expect((video as HTMLVideoElement).src).toContain('/intro/intro_hi.mp4');
  });

  it('renders the Telugu intro clip when language is "te"', () => {
    render(<InterviewIntro language="te" onDone={() => undefined} />);
    const video = screen.getByTestId('intro-video');
    expect((video as HTMLVideoElement).src).toContain('/intro/intro_te.mp4');
  });

  // ── Begin button → play → onEnded → onDone ────────────────────────────────

  it('"Begin interview" calls video.play() and hides the begin button', async () => {
    render(<InterviewIntro language="en" onDone={() => undefined} />);

    const btn = screen.getByTestId('begin-button');
    await userEvent.click(btn);

    expect(mockPlay).toHaveBeenCalledOnce();
    // Button is gone after clicking begin
    expect(screen.queryByTestId('begin-button')).toBeNull();
    // Skip is still visible
    expect(screen.getByTestId('skip-button')).toBeInTheDocument();
  });

  it('onEnded event calls onDone', () => {
    const onDone = vi.fn();
    render(<InterviewIntro language="en" onDone={onDone} />);

    const video = screen.getByTestId('intro-video');
    act(() => {
      fireEvent.ended(video);
    });

    expect(onDone).toHaveBeenCalledOnce();
  });

  it('"Begin" then onEnded calls onDone exactly once', async () => {
    const onDone = vi.fn();
    render(<InterviewIntro language="en" onDone={onDone} />);

    await userEvent.click(screen.getByTestId('begin-button'));
    const video = screen.getByTestId('intro-video');
    act(() => {
      fireEvent.ended(video);
    });

    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── Skip button ───────────────────────────────────────────────────────────

  it('"Skip" calls onDone immediately without playing the video', async () => {
    const onDone = vi.fn();
    render(<InterviewIntro language="en" onDone={onDone} />);

    await userEvent.click(screen.getByTestId('skip-button'));

    expect(onDone).toHaveBeenCalledOnce();
    expect(mockPlay).not.toHaveBeenCalled();
  });

  // ── onError graceful fallback ─────────────────────────────────────────────

  it('video onError event calls onDone so a broken clip never blocks the interview', () => {
    const onDone = vi.fn();
    render(<InterviewIntro language="en" onDone={onDone} />);

    const video = screen.getByTestId('intro-video');
    act(() => {
      fireEvent.error(video);
    });

    expect(onDone).toHaveBeenCalledOnce();
  });

  it('onError then onEnded calls onDone exactly once (guard ref prevents double call)', () => {
    const onDone = vi.fn();
    render(<InterviewIntro language="en" onDone={onDone} />);

    const video = screen.getByTestId('intro-video');
    act(() => {
      fireEvent.error(video);
      fireEvent.ended(video);
    });

    // safeDone guard should prevent the second call.
    expect(onDone).toHaveBeenCalledOnce();
  });

  it('"Skip" then onEnded calls onDone exactly once', async () => {
    const onDone = vi.fn();
    render(<InterviewIntro language="en" onDone={onDone} />);

    await userEvent.click(screen.getByTestId('skip-button'));
    // Parent would normally unmount the intro at this point; simulate a stray
    // ended event to confirm the guard ref works.
    const video = screen.getByTestId('intro-video');
    act(() => {
      fireEvent.ended(video);
    });

    expect(onDone).toHaveBeenCalledOnce();
  });

  // ── Accessibility ─────────────────────────────────────────────────────────

  it('begin button has an accessible aria-label', () => {
    render(<InterviewIntro language="en" onDone={() => undefined} />);
    expect(
      screen.getByRole('button', { name: /begin interview/i }),
    ).toBeInTheDocument();
  });

  it('skip button has an accessible aria-label', () => {
    render(<InterviewIntro language="en" onDone={() => undefined} />);
    expect(
      screen.getByRole('button', { name: /skip introduction/i }),
    ).toBeInTheDocument();
  });

  it('video element has aria-label for screen readers', () => {
    render(<InterviewIntro language="en" onDone={() => undefined} />);
    expect(
      screen.getByLabelText(/ai interviewer introduction video/i),
    ).toBeInTheDocument();
  });
});
