// BankProvenanceChip — PH4-D1. The "From bank · vN" chip on an exam's own
// question list. It never rendered in production: the API did not send the
// provenance fields (fixed in d8d714c; the D1 smoke now checks the API side).
// This pins the rendering side, on both lists that use it.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import BankProvenanceChip from '../components/bank/BankProvenanceChip';

describe('BankProvenanceChip', () => {
  it('names the bank version a reused question was copied from', () => {
    render(<BankProvenanceChip rootId="root-1" version={3} />);
    expect(screen.getByText('From bank · v3')).toBeInTheDocument();
  });

  it('renders nothing for a question written directly in the exam', () => {
    const { container } = render(<BankProvenanceChip rootId={null} version={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('still says "From bank" if the version is somehow missing, never "vnull"', () => {
    render(<BankProvenanceChip rootId="root-1" version={null} />);
    expect(screen.getByText('From bank')).toBeInTheDocument();
    expect(screen.queryByText(/vnull|vundefined/)).not.toBeInTheDocument();
  });
});
