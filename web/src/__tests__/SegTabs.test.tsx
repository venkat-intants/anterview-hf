// IV-L14 (re-audit): SegTabs rendered role=tab buttons with no roving
// tabindex, no arrow keys and no way to name the panel each tab controls.

import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SegTabs } from '../design/components/primitives';
import { tabIds } from '../lib/tabKeys';

const TABS = [
  { key: 'workload', label: 'Workload' },
  { key: 'availability', label: 'Availability' },
  { key: 'calibration', label: 'Calibration' },
];

function Harness({ idBase }: { idBase?: string }) {
  const [tab, setTab] = useState('workload');
  return (
    <>
      <SegTabs tabs={TABS} active={tab} onChange={setTab} idBase={idBase} label="Sections" />
      <p>showing {tab}</p>
    </>
  );
}

describe('SegTabs keyboard', () => {
  it('keeps only the selected tab in the Tab order', () => {
    render(<Harness />);
    expect(screen.getByRole('tablist', { name: 'Sections' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Workload' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: 'Availability' })).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tab', { name: 'Calibration' })).toHaveAttribute('tabindex', '-1');
  });

  it('moves and selects with the arrow keys, Home and End', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.tab();
    expect(screen.getByRole('tab', { name: 'Workload' })).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    const availability = screen.getByRole('tab', { name: 'Availability' });
    expect(availability).toHaveFocus();
    expect(availability).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('showing availability')).toBeInTheDocument();

    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Calibration' })).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Workload' })).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'Calibration' })).toHaveFocus();
    await user.keyboard('{Home}');
    expect(screen.getByRole('tab', { name: 'Workload' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('SegTabs panel wiring', () => {
  it('names the panel each tab controls when given an idBase', () => {
    render(<Harness idBase="panel" />);
    const tab = screen.getByRole('tab', { name: 'Availability' });
    expect(tab).toHaveAttribute('id', tabIds('panel', 'availability').tab);
    expect(tab).toHaveAttribute('aria-controls', tabIds('panel', 'availability').panel);
  });

  it('points at no panel when used as a filter (no idBase)', () => {
    render(<Harness />);
    expect(screen.getByRole('tab', { name: 'Availability' })).not.toHaveAttribute('aria-controls');
  });
});
