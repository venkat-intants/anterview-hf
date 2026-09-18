// DocumentRequirementsSection — PH4-A4. What an opening asks candidates for
// during preboarding: create, toggle mandatory/optional, and remove.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DocumentRequirement } from '../api/offers';

const listDocumentRequirements = vi.fn();
const addDocumentRequirement = vi.fn();
const updateDocumentRequirement = vi.fn();
const deleteDocumentRequirement = vi.fn();
vi.mock('../api/offers', () => ({
  listDocumentRequirements: (...a: unknown[]) => listDocumentRequirements(...a) as unknown,
  addDocumentRequirement: (...a: unknown[]) => addDocumentRequirement(...a) as unknown,
  updateDocumentRequirement: (...a: unknown[]) => updateDocumentRequirement(...a) as unknown,
  deleteDocumentRequirement: (...a: unknown[]) => deleteDocumentRequirement(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import DocumentRequirementsSection from '../components/DocumentRequirementsSection';

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DocumentRequirementsSection requisitionId="req-1" />
    </QueryClientProvider>,
  );
}

const REQUIREMENT: DocumentRequirement = {
  id: 'dr-1',
  name: 'PAN card',
  doc_type: 'tax',
  description: null,
  mandatory: true,
  requires_expiry: false,
  position: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  listDocumentRequirements.mockResolvedValue([REQUIREMENT]);
});

describe('DocumentRequirementsSection', () => {
  it('lists a requirement with its mandatory tag', async () => {
    renderSection();
    await screen.findByText('PAN card');
    expect(screen.getByText('Mandatory')).toBeInTheDocument();
  });

  it('adds a requirement', async () => {
    const user = userEvent.setup();
    addDocumentRequirement.mockResolvedValue({ ...REQUIREMENT, id: 'dr-2', name: 'Aadhaar card', doc_type: 'identity' });
    renderSection();
    await screen.findByText('PAN card');

    await user.click(screen.getByRole('button', { name: 'Add requirement' }));
    await user.type(screen.getByLabelText('Name'), 'Aadhaar card');
    await user.selectOptions(screen.getByLabelText('Kind'), 'identity');
    await user.click(screen.getByRole('button', { name: 'Add requirement' }));

    await waitFor(() =>
      expect(addDocumentRequirement).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ name: 'Aadhaar card', doc_type: 'identity', mandatory: true }),
      ),
    );
  });

  it('toggles mandatory to optional', async () => {
    const user = userEvent.setup();
    updateDocumentRequirement.mockResolvedValue({ ...REQUIREMENT, mandatory: false });
    renderSection();
    await screen.findByText('PAN card');

    await user.click(screen.getByRole('button', { name: 'Make optional' }));
    await waitFor(() =>
      expect(updateDocumentRequirement).toHaveBeenCalledWith('dr-1', { mandatory: false }),
    );
  });

  it('removes a requirement with confirmation', async () => {
    const user = userEvent.setup();
    deleteDocumentRequirement.mockResolvedValue(undefined);
    renderSection();
    await screen.findByText('PAN card');

    await user.click(screen.getByRole('button', { name: 'Remove PAN card' }));
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(deleteDocumentRequirement).toHaveBeenCalledWith('dr-1'));
  });

  it('shows an empty state', async () => {
    listDocumentRequirements.mockResolvedValue([]);
    renderSection();
    expect(
      await screen.findByText(/No documents required yet/),
    ).toBeInTheDocument();
  });
});
