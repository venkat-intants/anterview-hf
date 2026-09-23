// MetricCell — a suppressed median/mean, PH5 wave-1 audit fix.
//
// A suppressed RATE already showed its withheld figure on hover (the server
// sends it for every non-check-in metric; only a check-in OUTCOME comes back
// null). MetricCell's median/mean branch did not: it showed "too few to
// compare" with nothing behind it even when the server had sent a real
// value — time to hire and human interviewer scorecards, specifically, never
// suppress to null. This file pins the fix: the value + n now surface on
// hover exactly like a rate's, and a genuinely null (check-in) value still
// shows nothing beyond the text.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import MetricCell from '../components/hr/analytics/MetricCell';
import type { MedianMeanMetricResult } from '../api/metrics';

function renderCell(result: MedianMeanMetricResult) {
  return render(
    <MetricCell
      name="time_to_hire_days"
      result={result}
      definition={undefined}
      onInfo={vi.fn()}
      onDrillDown={vi.fn()}
    />,
  );
}

describe('MetricCell — suppressed median/mean', () => {
  it('shows the real value and n on hover for a suppressed NON-check-in metric (time to hire, interviewer score)', () => {
    renderCell({
      metric: 'time_to_hire_days',
      version: 1,
      kind: 'median',
      value: 21.4,
      n: 3,
      suppressed: true,
    });

    const label = screen.getByText('too few to compare');
    expect(label.parentElement).toHaveAttribute(
      'title',
      '21.4 (n=3), too few to compare reliably',
    );
    // The sample size is always visible too, suppressed or not.
    expect(screen.getByText('(n=3)')).toBeInTheDocument();
  });

  it('shows the real value for a suppressed MEAN the same way', () => {
    renderCell({
      metric: 'hire_interviewer_score',
      version: 1,
      kind: 'mean',
      value: 7.8,
      n: 4,
      suppressed: true,
    });

    const label = screen.getByText('too few to compare');
    expect(label.parentElement).toHaveAttribute('title', '7.8 (n=4), too few to compare reliably');
  });

  it('shows nothing beyond the text for a suppressed check-in outcome (null value)', () => {
    renderCell({
      metric: 'time_to_hire_days',
      version: 1,
      kind: 'median',
      value: null,
      n: 5,
      suppressed: true,
    });

    const label = screen.getByText('too few to compare');
    // No tooltip at all — there is nothing to reveal, and a null value is
    // never surfaced anywhere, hover included.
    expect(label.parentElement).not.toHaveAttribute('title');
    expect(screen.getByText('(n=5)')).toBeInTheDocument();
  });

  it('renders the value plainly, with n, when not suppressed', () => {
    renderCell({
      metric: 'time_to_hire_days',
      version: 1,
      kind: 'median',
      value: 12,
      n: 40,
      suppressed: false,
    });

    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('(n=40)')).toBeInTheDocument();
    expect(screen.queryByText('too few to compare')).not.toBeInTheDocument();
  });
});
