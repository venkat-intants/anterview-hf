// Round 3 changed page.tsx and added a Card, and 200+ call sites use GlassCard,
// SectionCard and EmptyState. This file pins what those render TODAY, so an
// untouched page cannot change by accident: if a class here has to move, that
// is a deliberate restyle and this test is updated in the same change.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Inbox } from 'lucide-react';
import { GlassCard } from '../design/components/primitives';
import { EmptyState, PageHeader, SectionCard, Stepper } from '../design/components/page';

function classesOf(el: Element | null): string {
  return el?.getAttribute('class') ?? '';
}

describe('GlassCard output is unchanged', () => {
  it('default', () => {
    render(<GlassCard data-testid="g">x</GlassCard>);
    expect(classesOf(screen.getByTestId('g'))).toBe('rounded-card border p-6 shadow-card border-border bg-card');
  });

  it('feature', () => {
    render(<GlassCard data-testid="g" feature>x</GlassCard>);
    expect(classesOf(screen.getByTestId('g'))).toBe(
      'rounded-card border p-6 shadow-card border-[rgba(var(--accent-rgb),0.2)] bg-[image:var(--ui-grad-feature)]',
    );
  });

  it('hover and a caller override', () => {
    render(<GlassCard data-testid="g" hover className="p-4">x</GlassCard>);
    expect(classesOf(screen.getByTestId('g'))).toBe(
      'rounded-card border shadow-card border-border bg-card transition-[transform,box-shadow,border-color] duration-300 will-change-transform hover:-translate-y-0.5 hover:border-[var(--ui-line-strong)] hover:shadow-card-hover p-4',
    );
  });
});

describe('SectionCard output is unchanged', () => {
  it('root, header and title', () => {
    render(
      <SectionCard title="Profile" description="About you" action={<button type="button">Edit</button>} data-testid="s">
        body
      </SectionCard>,
    );
    const root = screen.getByTestId('s');
    expect(root.tagName).toBe('SECTION');
    expect(classesOf(root)).toBe('rounded-card border border-border bg-card p-6 shadow-card');
    expect(classesOf(root.firstElementChild)).toBe('mb-4 flex items-start justify-between gap-4');
    expect(classesOf(screen.getByRole('heading', { name: 'Profile' }))).toBe(
      'flex items-center gap-2 text-[17px] font-bold tracking-[-0.2px] text-foreground',
    );
  });
});

describe('EmptyState defaults are unchanged', () => {
  const FRAMED = 'flex flex-col items-center justify-center rounded-[16px] border border-dashed border-[var(--ui-line-strong)] text-center';

  it('inline (the default)', () => {
    const { container } = render(<EmptyState title="Nothing yet" icon={Inbox} />);
    expect(classesOf(container.firstElementChild)).toBe(`${FRAMED} px-5 py-9`);
  });

  it('page', () => {
    const { container } = render(<EmptyState title="Nothing yet" size="page" />);
    expect(classesOf(container.firstElementChild)).toBe(`${FRAMED} px-6 py-16`);
  });

  it('keeps the icon circle, title and description classes', () => {
    const { container } = render(<EmptyState title="T" description="D" icon={Inbox} action={<a href="/x">Go</a>} />);
    expect(classesOf(container.querySelector('span'))).toBe(
      'mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-[var(--ui-highlight-wash)] text-foreground',
    );
    expect(classesOf(screen.getByText('T'))).toBe('text-[15px] font-semibold text-foreground');
    expect(classesOf(screen.getByText('D'))).toBe('mt-1 max-w-[420px] text-[13.5px] leading-relaxed text-muted-foreground');
  });

  it('bordered={false} drops only the frame', () => {
    const { container } = render(<EmptyState title="T" bordered={false} />);
    expect(classesOf(container.firstElementChild)).toBe('flex flex-col items-center justify-center text-center px-5 py-9');
  });

  it('row is one frameless line without an icon', () => {
    const { container } = render(<EmptyState title="No offers" size="row" icon={Inbox} action={<a href="/x">Browse</a>} />);
    const root = container.firstElementChild;
    expect(classesOf(root)).toContain('min-h-[44px]');
    expect(classesOf(root)).not.toContain('border');
    expect(container.querySelector('svg')).toBeNull();
    expect(screen.getByRole('link', { name: 'Browse' })).toBeInTheDocument();
  });
});

describe('PageHeader and Stepper keep their output', () => {
  it('PageHeader without meta is the same markup', () => {
    render(
      <PageHeader title="Jobs" description="Open roles" eyebrow="Hiring" titleTestId="t" />,
    );
    const h1 = screen.getByTestId('t');
    expect(h1.tagName).toBe('H1');
    const header = h1.closest('header');
    expect(classesOf(header)).toBe('mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-4');
    // title block holds eyebrow, h1, description only
    expect(header?.firstElementChild?.children).toHaveLength(3);
  });

  it('Stepper keeps "Step n of N" below sm when no compactLabel is given', () => {
    render(
      <Stepper
        label="Progress"
        current="b"
        steps={[{ key: 'a', label: 'One' }, { key: 'b', label: 'Two' }]}
      />,
    );
    expect(screen.getByText(/Step 2 of 2/)).toBeInTheDocument();
  });
});

describe('Stepper compactLabel', () => {
  it('replaces the below-sm line and leaves the full stepper alone', () => {
    render(
      <MemoryRouter>
        <Stepper
          label="Progress"
          current="b"
          compactLabel="Step 2 of 3 · Round 2 of 4"
          steps={[{ key: 'a', label: 'One' }, { key: 'b', label: 'Two' }, { key: 'c', label: 'Three' }]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText('Step 2 of 3 · Round 2 of 4')).toBeInTheDocument();
    expect(screen.queryByText(/Step 2 of 3$/)).toBeNull();
    // the sm+ list still marks the current step
    expect(screen.getAllByRole('listitem').some((li) => li.getAttribute('aria-current') === 'step')).toBe(true);
  });
});
