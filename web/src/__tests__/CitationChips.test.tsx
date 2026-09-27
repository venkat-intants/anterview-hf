// Tests for the shared citation renderer (PH5-E1).
//
// This is the one component every console's source strip and inline marker
// goes through, so the properties worth pinning are the ones the design
// spells out explicitly:
//
//   • an href makes a real react-router Link, not a full-reloading <a>;
//   • no href makes a non-interactive chip, with the locator as its tooltip —
//     never a live `#` link that goes nowhere;
//   • a kind is legible without hovering (icon + word), so an applicant chip
//     and a document chip cannot be confused;
//   • the strip caps at N and folds the rest into "+N more";
//   • an inline `[S1]` marker becomes a chip linking to the SAME target its
//     strip entry does, because it is the same citation object; an
//     unmatched marker is left exactly as written.

import type { ReactElement } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CitationChips, { CitedText, type CitationLike } from '../components/agent/CitationChips';

function citation(over: Partial<CitationLike> = {}): CitationLike {
  return {
    kind: 'applicant',
    id: 'ap-1',
    label: 'Asha Rao',
    href: '/hr/applicants/ap-1',
    ...over,
  };
}

function withRouter(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('CitationChips — strip', () => {
  it('renders a real Link for a citation with an href', () => {
    withRouter(<CitationChips citations={[citation()]} />);
    const link = screen.getByRole('link', { name: /Asha Rao/ });
    expect(link).toHaveAttribute('href', '/hr/applicants/ap-1');
  });

  it('renders a non-interactive chip for a citation with no href, using the locator as its tooltip', () => {
    withRouter(
      <CitationChips
        citations={[
          citation({
            kind: 'role_profile',
            id: 'rp-1',
            label: 'Backend Engineer',
            href: null,
            locator: 'weighted competency model',
          }),
        ]}
      />,
    );
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByTitle('weighted competency model')).toBeInTheDocument();
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
  });

  it('shows a per-kind word so a reader need not hover to tell kinds apart', () => {
    withRouter(
      <CitationChips
        citations={[
          citation({ kind: 'applicant', id: 'a1', label: 'Asha Rao' }),
          citation({ kind: 'document', id: 'd1', label: 'Employee handbook', href: '/hr/documents/d1' }),
        ]}
      />,
    );
    expect(screen.getByText('Applicant')).toBeInTheDocument();
    expect(screen.getByText('Document')).toBeInTheDocument();
  });

  it('caps at 4 by default and folds the rest into "+N more"', () => {
    const many = Array.from({ length: 6 }, (_, i) =>
      citation({ id: `ap-${i}`, label: `Candidate ${i}`, href: null }),
    );
    withRouter(<CitationChips citations={many} />);
    expect(screen.getByText('Candidate 0')).toBeInTheDocument();
    expect(screen.getByText('Candidate 3')).toBeInTheDocument();
    expect(screen.queryByText('Candidate 4')).not.toBeInTheDocument();
    expect(screen.getByText('+2 more')).toBeInTheDocument();
  });

  it('honours a custom cap', () => {
    const many = Array.from({ length: 3 }, (_, i) =>
      citation({ id: `ap-${i}`, label: `Candidate ${i}`, href: null }),
    );
    withRouter(<CitationChips citations={many} cap={1} />);
    expect(screen.getByText('Candidate 0')).toBeInTheDocument();
    expect(screen.queryByText('Candidate 1')).not.toBeInTheDocument();
    expect(screen.getByText('+2 more')).toBeInTheDocument();
  });

  it('renders nothing for an empty list, rather than an empty strip', () => {
    const { container } = withRouter(<CitationChips citations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('CitedText — inline [S…] markers', () => {
  it('renders a matched marker as a chip linking to the same target as the strip entry', () => {
    const source = citation({
      kind: 'document',
      id: 'doc-1',
      label: 'Employee handbook',
      href: '/hr/documents/doc-1',
      ref: 'S1',
      locator: 'v3 · page 4',
    });

    render(
      <MemoryRouter>
        <div>
          <p>
            <CitedText text="The notice period is 30 days[S1]." citations={[source]} />
          </p>
          <CitationChips citations={[source]} variant="strip" />
        </div>
      </MemoryRouter>,
    );

    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(2); // one inline marker, one strip entry
    for (const link of links) {
      expect(link).toHaveAttribute('href', '/hr/documents/doc-1');
    }
    expect(screen.getByText(/The notice period is 30 days/)).toBeInTheDocument();
  });

  it('leaves an unmatched marker exactly as written, rather than dropping or linking it', () => {
    const source = citation({ ref: 'S1', href: '/hr/applicants/ap-1' });
    render(
      <MemoryRouter>
        <CitedText text="Strong on Python[S1], unclear on the rest[S9]." citations={[source]} />
      </MemoryRouter>,
    );

    // S1 resolved to a chip; S9 has no matching citation and survives as text.
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(screen.getByText(/unclear on the rest\[S9\]\./)).toBeInTheDocument();
  });

  it('returns the text unchanged when no citation carries a ref at all', () => {
    render(
      <MemoryRouter>
        <CitedText
          text="Plain reasoning, no record read."
          citations={[citation({ ref: undefined })]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText('Plain reasoning, no record read.')).toBeInTheDocument();
  });
});
