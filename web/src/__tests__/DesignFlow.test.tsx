// StatGroup, NextStep, ActionRow, TurnBadge, StepContext, WhatNext.

import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Stat, StatGroup } from '../design/components/stat-group';
import {
  ActionRow, NextStep, StepContext, TurnBadge, WhatNext,
} from '../design/components/next-step';
import { Calendar } from '../design/components/icons';

function renderIn(ui: JSX.Element) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function classesOf(el: Element | null): string {
  return el?.getAttribute('class') ?? '';
}

describe('StatGroup', () => {
  it('renders one surface of cells with label and value', () => {
    renderIn(
      <StatGroup aria-label="Pipeline" data-testid="g">
        <Stat label="Applied" value={42} />
        <Stat label="Held" value={4} unit="people" />
        <Stat label="Score" value="71" unit="/100" />
      </StatGroup>,
    );
    const list = screen.getByRole('list', { name: 'Pipeline' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    expect(screen.getByText('Applied')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('/100')).toBeInTheDocument();
    // two-up on a phone, three across from sm
    expect(classesOf(list)).toContain('grid-cols-2');
    expect(classesOf(list)).toContain('sm:grid-cols-3');
  });

  it('formats numbers en-IN and uses tabular figures', () => {
    renderIn(
      <StatGroup>
        <Stat label="Applicants" value={123456} />
      </StatGroup>,
    );
    const value = screen.getByText('1,23,456');
    expect(classesOf(value)).toContain('tabular-nums');
  });

  it('renders nothing when every value is null or undefined', () => {
    const { container } = renderIn(
      <StatGroup data-testid="g">
        <Stat label="A" value={null} />
        <Stat label="B" value={undefined} />
      </StatGroup>,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('g')).toBeNull();
  });

  it('shows 0 as a real value, so a group of zeros still renders', () => {
    renderIn(
      <StatGroup>
        <Stat label="Held" value={0} />
      </StatGroup>,
    );
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('shows an em dash with an aria-label for a single missing value', () => {
    renderIn(
      <StatGroup missingLabel="No data yet">
        <Stat label="Applied" value={9} />
        <Stat label="Score" value={null} />
        <Stat label="Time" value={null} missingLabel="Not measured" />
      </StatGroup>,
    );
    expect(screen.getByRole('img', { name: 'No data yet' })).toHaveTextContent('—');
    expect(screen.getByRole('img', { name: 'Not measured' })).toHaveTextContent('—');
  });

  it('a cell with `to` is a link, and the cell target is at least 40px tall by padding', () => {
    renderIn(
      <StatGroup>
        <Stat label="Held" value={4} to="/hr/held" />
        <Stat label="Applied" value={9} />
      </StatGroup>,
    );
    const link = screen.getByRole('link', { name: /Held/ });
    expect(link).toHaveAttribute('href', '/hr/held');
    expect(classesOf(link)).toContain('p-4');
    expect(classesOf(link)).toContain('focus-visible:ring-2');
  });
});

describe('NextStep', () => {
  it('is a section named by its title, with one action', () => {
    renderIn(
      <NextStep
        eyebrow="Your next step"
        title="Take your assessment"
        context="Backend Engineer at Acme"
        dateLine="Due 14 Oct"
        action={<a href="/exam/1">Start</a>}
      />,
    );
    const region = screen.getByRole('region', { name: 'Take your assessment' });
    expect(within(region).getByText('Your next step')).toBeInTheDocument();
    expect(within(region).getByText('Backend Engineer at Acme')).toBeInTheDocument();
    expect(within(region).getByText('Due 14 Oct')).toBeInTheDocument();
    expect(within(region).getAllByRole('link')).toHaveLength(1);
  });

  it('gives the action area a full-width 48px target on a phone', () => {
    renderIn(<NextStep title="T" action={<button type="button">Go</button>} />);
    const area = screen.getByRole('button', { name: 'Go' }).parentElement;
    const c = classesOf(area);
    expect(c).toContain('w-full');
    expect(c).toContain('[&>*]:min-h-[48px]');
    expect(c).toContain('[&>*]:w-full');
  });

  it('renders without an action as a plain statement', () => {
    renderIn(<NextStep title="With the hiring team" tone="info" />);
    const region = screen.getByRole('region', { name: 'With the hiring team' });
    expect(within(region).queryByRole('button')).toBeNull();
    expect(within(region).queryByRole('link')).toBeNull();
  });

  it('shows "more" only with a count and a translated label; link only with moreTo', () => {
    const { rerender } = renderIn(
      <NextStep title="T" moreCount={2} moreLabel="2 more waiting" moreTo="/applications" />,
    );
    expect(screen.getByRole('link', { name: '2 more waiting' })).toHaveAttribute('href', '/applications');
    rerender(
      <MemoryRouter>
        <NextStep title="T" moreCount={2} moreLabel="2 more waiting" />
      </MemoryRouter>,
    );
    expect(screen.getByText('2 more waiting')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
    rerender(
      <MemoryRouter>
        <NextStep title="T" moreCount={0} moreLabel="0 more waiting" />
      </MemoryRouter>,
    );
    expect(screen.queryByText('0 more waiting')).toBeNull();
    // a count with no label prints nothing: the component has no words of its own
    rerender(
      <MemoryRouter>
        <NextStep title="T" moreCount={3} />
      </MemoryRouter>,
    );
    expect(screen.queryByText(/more/)).toBeNull();
  });

  it.each(['default', 'info', 'ok', 'warn', 'danger'] as const)('tone %s renders with its title', (tone) => {
    renderIn(<NextStep title={`Tone ${tone}`} tone={tone} />);
    expect(screen.getByRole('region', { name: `Tone ${tone}` })).toBeInTheDocument();
  });

  it('honours titleAs', () => {
    renderIn(<NextStep title="Sub" titleAs="h3" />);
    expect(screen.getByRole('heading', { level: 3, name: 'Sub' })).toBeInTheDocument();
  });
});

describe('ActionRow', () => {
  it('shows title, date line and the action, and announces an error', () => {
    render(
      <ActionRow
        icon={Calendar}
        title="Choose an interview time"
        dateLine="By 14 Oct"
        action={<button type="button">Choose</button>}
        error="Could not load slots"
      />,
    );
    expect(screen.getByText('Choose an interview time')).toBeInTheDocument();
    expect(screen.getByText('By 14 Oct')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load slots');
  });

  it('has no alert when there is no error', () => {
    render(<ActionRow icon={Calendar} title="t" action={<button type="button">Go</button>} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('TurnBadge', () => {
  it.each(['you', 'them', 'closed'] as const)('%s shows its label as text with an icon', (turn) => {
    const { container } = render(<TurnBadge turn={turn} label={`label-${turn}`} />);
    expect(screen.getByText(`label-${turn}`)).toBeInTheDocument();
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelector(`[data-turn="${turn}"]`)).not.toBeNull();
  });
});

describe('StepContext and WhatNext', () => {
  it('StepContext joins step, role and company, skipping what is absent', () => {
    const { rerender } = render(<StepContext step="Step 2 of 4" role="Backend Engineer" company="Acme" />);
    expect(screen.getByText('Step 2 of 4')).toBeInTheDocument();
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument();
    rerender(<StepContext step="Step 1 of 4" />);
    expect(screen.queryByText('Acme')).toBeNull();
    expect(screen.queryByText('·')).toBeNull();
  });

  it('WhatNext is a named region holding its children', () => {
    render(
      <WhatNext title="What happens next">
        <p>The team reviews your answers.</p>
      </WhatNext>,
    );
    const region = screen.getByRole('region', { name: 'What happens next' });
    expect(region).toHaveTextContent('The team reviews your answers.');
  });
});
