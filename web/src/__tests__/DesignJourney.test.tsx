// JourneyRail and ApprovalTrack. The contract under test is accessibility:
// state is conveyed by text as well as an icon, the active stage is marked, the
// returned note is tied to its step, and tab order is stage order.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ApprovalTrack, type ApprovalStep } from '../design/components/approval-track';
import { JourneyRail, type JourneyStage, type StageState } from '../design/components/journey-rail';

const STATE_LABELS: Record<StageState, string> = {
  todo: 'Not started',
  active: 'In progress',
  blocked: 'Blocked',
  awaiting: 'Waiting',
  done: 'Done',
  optional: 'Optional',
};

const STAGES: JourneyStage[] = [
  { key: 'role', title: 'Role and posting', state: 'done', fact: 'JD v2 published', actor: 'Priya', at: '3 Oct' },
  {
    key: 'process',
    title: 'Hiring process',
    state: 'active',
    fact: '2 of 3 rounds have an exam',
    substeps: [
      { key: 'rounds', label: 'Rounds', state: 'done' },
      { key: 'exams', label: 'Exams attached', state: 'active' },
      { key: 'dry', label: 'Dry run', state: 'todo' },
    ],
  },
  { key: 'budget', title: 'Budget and approval', state: 'awaiting', actor: 'Asha (company admin)' },
  {
    key: 'live',
    title: 'Go live',
    state: 'blocked',
    reason: 'Waiting for the hiring process and approval',
  },
  { key: 'docs', title: 'Preboarding documents', state: 'optional' },
];

function mockViewport(desktop: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: desktop,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
    onchange: null,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function Harness({ onSelect }: { onSelect?: (k: string) => void }): JSX.Element {
  const [active, setActive] = useState('process');
  return (
    <JourneyRail
      stages={STAGES}
      activeKey={active}
      label="Opening setup"
      stateLabels={STATE_LABELS}
      onSelect={(k) => {
        onSelect?.(k);
        setActive(k);
      }}
    >
      <p data-testid="panel">Panel for {active}</p>
    </JourneyRail>
  );
}

describe('JourneyRail (desktop)', () => {
  it('lists the stages in order as buttons in an ordered list', () => {
    mockViewport(true);
    render(<Harness />);
    const nav = screen.getByRole('navigation', { name: 'Opening setup' });
    const items = within(nav).getAllByRole('listitem').filter((li) => li.querySelector(':scope > button'));
    expect(items).toHaveLength(5);
    const buttons = within(nav).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual([
      expect.stringContaining('Role and posting'),
      expect.stringContaining('Hiring process'),
      expect.stringContaining('Budget and approval'),
      expect.stringContaining('Go live'),
      expect.stringContaining('Preboarding documents'),
    ]);
  });

  it('marks only the active stage with aria-current and aria-expanded', () => {
    mockViewport(true);
    render(<Harness />);
    const buttons = screen.getAllByRole('button');
    const current = buttons.filter((b) => b.getAttribute('aria-current') === 'step');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent('Hiring process');
    expect(buttons.map((b) => b.getAttribute('aria-expanded'))).toEqual(['false', 'true', 'false', 'false', 'false']);
    expect(current[0].getAttribute('aria-controls')).toBeTruthy();
    // Every aria-controls that is present points at an element that exists, and
    // the inactive stages (whose panels are not rendered) carry none.
    for (const b of buttons) {
      const target = b.getAttribute('aria-controls');
      if (b === current[0]) expect(target).toBeTruthy();
      else expect(target).toBeNull();
      if (target) expect(document.getElementById(target)).not.toBeNull();
    }
  });

  it('puts the panel beside the rail, once, labelled by the active stage', () => {
    mockViewport(true);
    render(<Harness />);
    expect(screen.getAllByTestId('panel')).toHaveLength(1);
    const region = screen.getByRole('region', { name: /Hiring process/ });
    expect(region).toContainElement(screen.getByTestId('panel'));
    const active = screen.getByRole('button', { name: /Hiring process/ });
    expect(active.getAttribute('aria-controls')).toBe(region.id);
  });

  it('states every state in words, not by icon or colour alone', () => {
    mockViewport(true);
    render(<Harness />);
    for (const [key, word] of [
      ['Role and posting', 'Done'],
      ['Hiring process', 'In progress'],
      ['Budget and approval', 'Waiting'],
      ['Go live', 'Blocked'],
      ['Preboarding documents', 'Optional'],
    ] as const) {
      expect(screen.getByRole('button', { name: new RegExp(key) })).toHaveTextContent(word);
    }
  });

  it('shows the reason for a blocked stage, the actor for awaiting, and the fact for done', () => {
    mockViewport(true);
    render(<Harness />);
    expect(screen.getByRole('button', { name: /Go live/ })).toHaveTextContent('Waiting for the hiring process and approval');
    expect(screen.getByRole('button', { name: /Budget and approval/ })).toHaveTextContent('Asha (company admin)');
    expect(screen.getByRole('button', { name: /Role and posting/ })).toHaveTextContent('JD v2 published');
    expect(screen.getByRole('button', { name: /Role and posting/ })).toHaveTextContent('Priya · 3 Oct');
  });

  it('never ticks an optional stage', () => {
    mockViewport(true);
    render(<Harness />);
    const optional = screen.getByRole('button', { name: /Preboarding documents/ });
    expect(optional.querySelector('svg')).toBeNull();
  });

  it('lists substeps with their state in text for assistive tech', () => {
    mockViewport(true);
    render(<Harness />);
    const sub = screen.getByText('Exams attached').closest('li');
    expect(sub).toHaveTextContent('(In progress)');
    expect(screen.getByText('Rounds').closest('li')).toHaveTextContent('(Done)');
  });

  it('calls onSelect with the stage key on click', async () => {
    mockViewport(true);
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    await userEvent.click(screen.getByRole('button', { name: /Budget and approval/ }));
    expect(onSelect).toHaveBeenCalledWith('budget');
    expect(screen.getByTestId('panel')).toHaveTextContent('Panel for budget');
  });

  it('keeps tab order equal to stage order and activates from the keyboard', async () => {
    mockViewport(true);
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSelect={onSelect} />);
    await user.tab();
    expect(screen.getByRole('button', { name: /Role and posting/ })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: /Hiring process/ })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: /Budget and approval/ })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith('budget');
    await user.tab();
    expect(screen.getByRole('button', { name: /Go live/ })).toHaveFocus();
    await user.keyboard(' ');
    expect(onSelect).toHaveBeenCalledWith('live');
  });
});

describe('JourneyRail (below lg)', () => {
  it('renders an accordion with the panel inside the open stage, once', () => {
    mockViewport(false);
    render(<Harness />);
    expect(screen.getAllByTestId('panel')).toHaveLength(1);
    const region = screen.getByRole('region', { name: /Hiring process/ });
    expect(region).toContainElement(screen.getByTestId('panel'));
    const item = region.closest('li');
    expect(item).toContainElement(screen.getByRole('button', { name: /Hiring process/ }));
    expect(screen.getByRole('button', { name: /Hiring process/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: /Go live/ })).toHaveAttribute('aria-expanded', 'false');
  });

  it('sets aria-controls only on the stage whose panel is in the DOM', () => {
    mockViewport(false);
    render(<Harness />);
    for (const b of screen.getAllByRole('button')) {
      const target = b.getAttribute('aria-controls');
      if (b.getAttribute('aria-expanded') === 'true') {
        expect(target).toBeTruthy();
        expect(document.getElementById(target as string)).not.toBeNull();
      } else {
        expect(target).toBeNull();
      }
    }
  });

  it('moves the panel to the stage that is selected', async () => {
    mockViewport(false);
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: /Go live/ }));
    const region = screen.getByRole('region', { name: /Go live/ });
    expect(region).toContainElement(screen.getByTestId('panel'));
    expect(screen.getAllByTestId('panel')).toHaveLength(1);
  });
});

const TRACK_LABELS = { done: 'Approved', current: 'Needs action', todo: 'Not yet', returned: 'Sent back' } as const;

function track(steps: ApprovalStep[], extra: Partial<React.ComponentProps<typeof ApprovalTrack>> = {}): JSX.Element {
  return <ApprovalTrack steps={steps} label="Approval progress" stateLabels={TRACK_LABELS} {...extra} />;
}

describe('ApprovalTrack', () => {
  const steps: ApprovalStep[] = [
    { key: 'draft', label: 'Drafted', state: 'done', actor: 'Priya', at: '2 Oct' },
    { key: 'review', label: 'Company admin review', state: 'current', actor: 'Asha' },
    { key: 'publish', label: 'Published', state: 'todo' },
  ];

  it('is an ordered list, with aria-current on the current step only', () => {
    render(track(steps, { 'data-testid': 'tr' }));
    const list = screen.getByRole('list', { name: 'Approval progress' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items.map((li) => li.getAttribute('aria-current'))).toEqual([null, 'step', null]);
    expect(screen.getByTestId('tr')).toContainElement(list);
  });

  it('says each state in words and names who acted or must act', () => {
    render(track(steps));
    const [done, current, todo] = screen.getAllByRole('listitem');
    expect(done).toHaveTextContent('Approved');
    expect(done).toHaveTextContent('Priya · 2 Oct');
    expect(current).toHaveTextContent('Needs action');
    expect(current).toHaveTextContent('Asha');
    expect(todo).toHaveTextContent('Not yet');
  });

  it('shows a returned note inline and ties it to the step with aria-describedby', () => {
    render(
      track([
        { key: 'draft', label: 'Drafted', state: 'done' },
        { key: 'review', label: 'Review', state: 'returned', actor: 'Asha', note: 'Budget is above the band' },
      ]),
    );
    const returned = screen.getAllByRole('listitem')[1];
    const id = returned.getAttribute('aria-describedby');
    expect(id).toBeTruthy();
    const note = document.getElementById(id ?? '');
    expect(note).toHaveTextContent('Budget is above the band');
    expect(returned).toContainElement(note);
    expect(returned).toHaveTextContent('Sent back');
  });

  it('adds no describedby when nothing was returned', () => {
    render(track(steps));
    for (const li of screen.getAllByRole('listitem')) expect(li).not.toHaveAttribute('aria-describedby');
  });

  it('renders the primary and secondary actions, primary last', () => {
    render(
      track(steps, {
        primary: <button type="button">Approve</button>,
        secondary: <button type="button">Send back</button>,
      }),
    );
    const buttons = screen.getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Send back', 'Approve']);
  });

  it('renders no action row without actions', () => {
    render(track(steps));
    expect(screen.queryByRole('button')).toBeNull();
  });
});
