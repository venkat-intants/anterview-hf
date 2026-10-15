// Page, PageBody, Card, Section, Well and RowList: the frame and the containers.

import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Page, PageBody, PageHeader } from '../design/components/page';
import { Card } from '../design/components/card';
import { Row, RowList, Section, Well } from '../design/components/rows';
import { Briefcase } from '../design/components/icons';

function renderIn(ui: JSX.Element) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function classesOf(el: Element | null): string {
  return el?.getAttribute('class') ?? '';
}

describe('Page', () => {
  it.each([
    ['form', 'max-w-[880px]'],
    ['default', 'max-w-[1200px]'],
    ['workspace', 'max-w-none'],
    ['reading', 'max-w-[720px]'],
  ] as const)('width %s uses %s', (width, cls) => {
    render(<Page width={width} data-testid="p">x</Page>);
    expect(classesOf(screen.getByTestId('p'))).toContain(cls);
  });

  it('defaults to 1200 and stacks regions on a 24px gap', () => {
    render(<Page data-testid="p">x</Page>);
    const c = classesOf(screen.getByTestId('p'));
    expect(c).toContain('max-w-[1200px]');
    expect(c).toContain('flex-col');
    expect(c).toContain('gap-6');
  });

  it('is left-anchored and adds no padding, except reading which centres', () => {
    render(
      <>
        <Page width="workspace" data-testid="a">x</Page>
        <Page width="reading" data-testid="b">x</Page>
      </>,
    );
    const a = classesOf(screen.getByTestId('a'));
    expect(a).not.toMatch(/\bmx-auto\b/);
    expect(a).not.toMatch(/(^|\s)p[xy]?-\d/);
    expect(classesOf(screen.getByTestId('b'))).toContain('mx-auto');
  });

  it('cancels the PageHeader bottom margin so the gap is not doubled', () => {
    render(
      <Page data-testid="p">
        <PageHeader title="Jobs" titleTestId="t" />
      </Page>,
    );
    expect(classesOf(screen.getByTestId('p'))).toContain('[&>header]:mb-0');
    // the header itself is untouched: it still carries mb-6 outside a Page
    expect(classesOf(screen.getByTestId('t').closest('header'))).toContain('mb-6');
  });
});

describe('PageHeader meta', () => {
  it('renders meta under the description, and keeps the title test id and breadcrumbs', () => {
    render(
      <PageHeader
        title="Opening"
        description="d"
        titleTestId="title"
        breadcrumbs={<nav aria-label="crumbs">crumbs</nav>}
        meta={<span>Approved</span>}
      />,
    );
    expect(screen.getByTestId('title')).toHaveTextContent('Opening');
    expect(screen.getByRole('navigation', { name: 'crumbs' })).toBeInTheDocument();
    expect(screen.getByText('Approved')).toBeInTheDocument();
  });
});

describe('PageBody', () => {
  it('is a single column without an aside', () => {
    const { container } = render(<PageBody>main</PageBody>);
    expect(classesOf(container.firstElementChild)).not.toContain('lg:grid-cols');
    expect(screen.queryByRole('complementary')).toBeNull();
  });

  it('adds a 320px aside column at lg+ and names the landmark', () => {
    const { container } = render(
      <PageBody aside={<p>facts</p>} asideLabel="About this opening">
        main
      </PageBody>,
    );
    expect(classesOf(container.firstElementChild)).toContain('lg:grid-cols-[minmax(0,1fr)_320px]');
    expect(screen.getByRole('complementary', { name: 'About this opening' })).toHaveTextContent('facts');
    expect(screen.getByText('main')).toBeInTheDocument();
  });
});

describe('Card', () => {
  it('is plain by default: hairline, no shadow, no hover', () => {
    render(<Card data-testid="c" title="Details">body</Card>);
    const c = classesOf(screen.getByTestId('c'));
    expect(c).toContain('rounded-card');
    expect(c).toContain('border-border');
    expect(c).not.toContain('shadow-card');
    expect(c).not.toContain('hover:');
    expect(screen.getByRole('heading', { level: 2, name: 'Details' })).toBeInTheDocument();
  });

  it('raised adds the shadow; feature uses the accent surface', () => {
    render(
      <>
        <Card data-testid="r" emphasis="raised">x</Card>
        <Card data-testid="f" emphasis="feature">x</Card>
      </>,
    );
    expect(classesOf(screen.getByTestId('r'))).toContain('shadow-card');
    expect(classesOf(screen.getByTestId('f'))).toContain('bg-[image:var(--ui-grad-feature)]');
  });

  it.each([
    ['compact', 'p-4'],
    ['default', 'p-5 sm:p-6'],
    ['roomy', 'p-6 sm:p-8'],
  ] as const)('density %s pads %s', (density, pad) => {
    render(<Card data-testid="c" density={density}>x</Card>);
    expect(classesOf(screen.getByTestId('c'))).toContain(pad);
  });

  it('renders every slot in order and honours titleAs', () => {
    render(
      <Card
        data-testid="c"
        eyebrow="Offer"
        title="Backend Engineer"
        titleAs="h3"
        description="Mumbai"
        icon={Briefcase}
        actions={<button type="button">Edit</button>}
        footer={<span>Updated today</span>}
        divided
      >
        <p>body</p>
      </Card>,
    );
    const text = screen.getByTestId('c').textContent ?? '';
    expect(text.indexOf('Offer')).toBeLessThan(text.indexOf('Backend Engineer'));
    expect(text.indexOf('Backend Engineer')).toBeLessThan(text.indexOf('Mumbai'));
    expect(text.indexOf('Mumbai')).toBeLessThan(text.indexOf('body'));
    expect(text.indexOf('body')).toBeLessThan(text.indexOf('Updated today'));
    expect(screen.getByRole('heading', { level: 3, name: 'Backend Engineer' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(classesOf(screen.getByText('Offer').parentElement?.parentElement ?? null)).toContain('border-b');
  });

  it('flush removes padding and clips', () => {
    render(<Card data-testid="c" flush>x</Card>);
    const c = classesOf(screen.getByTestId('c'));
    expect(c).toContain('overflow-hidden');
    expect(c).not.toMatch(/(^|\s)p-\d/);
  });

  it('passes data-testid and aria through, and a section names itself from its title', () => {
    render(<Card as="section" title="Budget" data-testid="c" aria-describedby="x">y</Card>);
    const el = screen.getByTestId('c');
    expect(el).toHaveAttribute('aria-describedby', 'x');
    expect(screen.getByRole('region', { name: 'Budget' })).toBe(el);
  });

  it('with `to` and no actions the whole card is one raised link', () => {
    renderIn(<Card to="/jobs/1" title="Backend Engineer" description="Mumbai" data-testid="c">body</Card>);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/jobs/1');
    expect(link).toBe(screen.getByTestId('c'));
    expect(link).toHaveTextContent('Backend Engineer');
    const c = classesOf(link);
    expect(c).toContain('shadow-card');
    expect(c).toContain('hover:shadow-lift');
    expect(c).toContain('motion-reduce:transition-none');
    expect(c).toContain('focus-visible:ring-2');
  });

  it('with `to` and actions the title is the only link, and the button is not nested in it', async () => {
    const user = userEvent.setup();
    let clicked = 0;
    renderIn(
      <Card
        to="/offers/9"
        title="Offer 9"
        actions={<button type="button" onClick={() => { clicked += 1; }}>Withdraw</button>}
        data-testid="c"
      >
        body
      </Card>,
    );
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', '/offers/9');
    expect(links[0]).toHaveTextContent('Offer 9');
    expect(classesOf(links[0])).toContain('after:absolute');
    const btn = screen.getByRole('button', { name: 'Withdraw' });
    expect(links[0].contains(btn)).toBe(false);
    expect(screen.getByTestId('c').tagName).toBe('DIV');
    await user.click(btn);
    expect(clicked).toBe(1);
  });
});

describe('Section, Well, RowList', () => {
  it('Section is an unboxed region with a hairline and a named heading', () => {
    render(
      <Section title="Timing" description="When" actions={<button type="button">Edit</button>} data-testid="s">
        content
      </Section>,
    );
    const s = screen.getByRole('region', { name: 'Timing' });
    expect(s).toBe(screen.getByTestId('s'));
    expect(classesOf(s)).toContain('border-t');
    expect(classesOf(s)).not.toContain('rounded');
    expect(classesOf(s)).not.toContain('bg-card');
    expect(within(s).getByRole('heading', { level: 2 })).toHaveTextContent('Timing');
    expect(within(s).getByRole('button', { name: 'Edit' })).toBeInTheDocument();
  });

  it('Well is an inset fill with no border and passes attributes through', () => {
    render(<Well data-testid="w" aria-label="Note">hello</Well>);
    const w = screen.getByTestId('w');
    expect(classesOf(w)).toContain('bg-[var(--ui-inset-soft)]');
    expect(classesOf(w)).not.toContain('border');
    expect(w).toHaveAttribute('aria-label', 'Note');
  });

  it('RowList renders divided rows of at least 56px, some as links', () => {
    renderIn(
      <RowList aria-label="Interviews" data-testid="list">
        <Row title="Round 1" meta="Tue" trailing={<span>Done</span>} data-testid="r1" />
        <Row title="Round 2" meta="Thu" to="/r/2" leading={<span>•</span>} />
      </RowList>,
    );
    const list = screen.getByRole('list', { name: 'Interviews' });
    expect(classesOf(list)).toContain('divide-y');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(classesOf(screen.getByTestId('r1').firstElementChild)).toContain('min-h-[56px]');
    const link = screen.getByRole('link', { name: /Round 2/ });
    expect(link).toHaveAttribute('href', '/r/2');
    expect(classesOf(link)).toContain('min-h-[56px]');
  });
});
