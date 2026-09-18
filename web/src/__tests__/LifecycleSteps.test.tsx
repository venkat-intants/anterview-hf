// LifecycleSteps — each step's state is said in words, not by colour alone.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import LifecycleSteps from '../components/workflow/LifecycleSteps';

describe('LifecycleSteps', () => {
  it('names every state, and marks the current step', () => {
    render(
      <LifecycleSteps
        status="draft"
        reviewStatus="in_review"
        simulated
        publishable
        onRunDryRun={vi.fn()}
      />,
    );
    const list = screen.getByRole('list', { name: 'Workflow lifecycle' });
    expect(list).toHaveTextContent('1. Draft (done)');
    expect(list).toHaveTextContent('3. Review (current step)');
    expect(list).toHaveTextContent('4. Approved (not yet)');
    expect(screen.getByText(/3\. Review/).closest('li')).toHaveAttribute('aria-current', 'step');
  });

  it('says a blocked review is blocked', () => {
    render(
      <LifecycleSteps
        status="draft"
        reviewStatus="draft"
        simulated
        publishable={false}
        onRunDryRun={vi.fn()}
      />,
    );
    expect(screen.getByRole('list', { name: 'Workflow lifecycle' })).toHaveTextContent(
      '3. Review (blocked)',
    );
  });
});
