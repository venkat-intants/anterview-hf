// Tests for FileUploadZone component
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import FileUploadZone from '../components/FileUploadZone';

function makePdf(name = 'cv.pdf'): File {
  return new File(['%PDF-1.4'], name, { type: 'application/pdf' });
}

function makeNonPdf(name = 'doc.docx'): File {
  return new File(['PK...'], name, {
    type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  });
}

function makeOversizedPdf(name = 'huge.pdf', bytes = 6 * 1024 * 1024): File {
  const buf = new Uint8Array(bytes);
  return new File([buf], name, { type: 'application/pdf' });
}

const MAX_BYTES = 5 * 1024 * 1024; // 5 MB

/** Helper: simulate a file being selected via the hidden input */
function uploadFile(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  // fireEvent.change properly sets the files list in jsdom
  Object.defineProperty(input, 'files', {
    value: [file],
    configurable: true,
  });
  fireEvent.change(input);
}

describe('FileUploadZone', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the drop zone with the provided label', () => {
    const onUpload = vi.fn();
    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );
    expect(screen.getByRole('button', { name: /resume.*click or drag/i })).toBeInTheDocument();
  });

  it('shows the existing file label when provided', () => {
    const onUpload = vi.fn();
    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
        existingFileLabel="Resume on file — upload a new one to replace it"
      />,
    );
    expect(screen.getByText(/resume on file/i)).toBeInTheDocument();
  });

  it('rejects a non-PDF file with an inline error', async () => {
    const onUpload = vi.fn();
    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );

    uploadFile(makeNonPdf());

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/only pdf files are accepted/i)).toBeInTheDocument();
    });

    expect(onUpload).not.toHaveBeenCalled();
  });

  it('rejects an oversized PDF with an inline error', async () => {
    const onUpload = vi.fn();
    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );

    uploadFile(makeOversizedPdf());

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/too large/i)).toBeInTheDocument();
    });

    expect(onUpload).not.toHaveBeenCalled();
  });

  it('calls onUpload and renders the character count on success', async () => {
    const onUpload = vi.fn().mockResolvedValue({ text_length: 3214 });

    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );

    uploadFile(makePdf());

    await waitFor(() => {
      expect(onUpload).toHaveBeenCalledOnce();
      expect(screen.getByText(/3,214 characters extracted/i)).toBeInTheDocument();
    });
  });

  it('renders a server error message when onUpload rejects', async () => {
    const onUpload = vi.fn().mockRejectedValue(new Error('File type not supported by server'));

    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );

    uploadFile(makePdf());

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/file type not supported by server/i)).toBeInTheDocument();
    });
  });

  it('shows a retry affordance after an error', async () => {
    const onUpload = vi.fn().mockRejectedValue(new Error('Upload failed'));

    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );

    uploadFile(makePdf());

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
    });
  });

  it('drop zone is keyboard accessible — has tabIndex 0 and can receive focus', () => {
    const onUpload = vi.fn();
    render(
      <FileUploadZone
        label="Resume"
        accept="application/pdf"
        maxBytes={MAX_BYTES}
        onUpload={onUpload}
      />,
    );
    const zone = screen.getByRole('button', { name: /resume.*click or drag/i });
    // The zone must have tabIndex=0 so it is reachable by keyboard
    expect(zone).toHaveAttribute('tabindex', '0');
    // Direct focus works (simulates what a keyboard Tab would do)
    act(() => zone.focus());
    expect(zone).toHaveFocus();
  });
});
