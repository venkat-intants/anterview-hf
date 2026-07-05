// FileUploadZone — reusable, accessible PDF drag-and-drop upload widget.
// Supports progress reporting, client-side validation, and retry.

import { useCallback, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

export interface FileUploadZoneProps {
  /** Human-readable label, e.g. "Upload Resume" */
  label: string;
  /** MIME type accepted, e.g. "application/pdf" */
  accept: string;
  /** Maximum file size in bytes */
  maxBytes: number;
  /**
   * Called with the selected file and a progress callback.
   * Must resolve with an object containing `text_length`.
   */
  onUpload: (file: File, onProgress: (pct: number) => void) => Promise<{ text_length: number }>;
  /** If set, shows an "already on file" notice above the drop zone */
  existingFileLabel?: string;
}

type UploadState =
  | { status: 'idle' }
  | { status: 'uploading'; progress: number }
  | { status: 'success'; textLength: number }
  | { status: 'error'; message: string };

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileUploadZone({
  label,
  accept,
  maxBytes,
  onUpload,
  existingFileLabel,
}: FileUploadZoneProps) {
  const { t } = useTranslation();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploadState, setUploadState] = useState<UploadState>({ status: 'idle' });
  const [isDraggingOver, setIsDraggingOver] = useState(false);

  const validateAndUpload = useCallback(
    (file: File) => {
      // Client-side PDF check
      if (file.type !== 'application/pdf') {
        setUploadState({ status: 'error', message: t('fileUpload.onlyPdf') });
        return;
      }
      // Client-side size check
      if (file.size > maxBytes) {
        setUploadState({
          status: 'error',
          message: t('fileUpload.tooLarge', { size: formatBytes(maxBytes) }),
        });
        return;
      }

      setUploadState({ status: 'uploading', progress: 0 });

      onUpload(file, (pct) => {
        setUploadState({ status: 'uploading', progress: pct });
      })
        .then((result) => {
          setUploadState({ status: 'success', textLength: result.text_length });
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : t('fileUpload.uploadFailed');
          setUploadState({ status: 'error', message });
        });
    },
    [maxBytes, onUpload, t],
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) validateAndUpload(file);
      // Reset input so the same file can be re-selected after an error
      e.target.value = '';
    },
    [validateAndUpload],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDraggingOver(false);
      const file = e.dataTransfer.files[0];
      if (file) validateAndUpload(file);
    },
    [validateAndUpload],
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDraggingOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDraggingOver(false);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      inputRef.current?.click();
    }
  }, []);

  const handleRetry = useCallback(() => {
    setUploadState({ status: 'idle' });
    // Short timeout so the state update renders before the dialog opens
    setTimeout(() => inputRef.current?.click(), 0);
  }, []);

  const isUploading = uploadState.status === 'uploading';

  return (
    <div className="w-full">
      {/* Existing file notice */}
      {existingFileLabel && uploadState.status !== 'success' && (
        <p className="mb-3 text-body-sm text-emerald-400 flex items-center gap-1.5">
          <svg
            aria-hidden="true"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-4 w-4 shrink-0"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm3.857-9.809a.75.75 0 0 0-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 1 0-1.06 1.061l2.5 2.5a.75.75 0 0 0 1.137-.089l4-5.5Z"
              clipRule="evenodd"
            />
          </svg>
          {existingFileLabel}
        </p>
      )}

      {/* Success state */}
      {uploadState.status === 'success' && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/15 p-4 flex items-start gap-3">
          <svg
            aria-hidden="true"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-5 w-5 text-emerald-400 shrink-0 mt-0.5"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm3.857-9.809a.75.75 0 0 0-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 1 0-1.06 1.061l2.5 2.5a.75.75 0 0 0 1.137-.089l4-5.5Z"
              clipRule="evenodd"
            />
          </svg>
          <div className="flex-1">
            <p className="text-body-sm font-medium text-foreground">
              {t('fileUpload.processed', {
                label,
                chars: uploadState.textLength.toLocaleString(),
              })}
            </p>
            <button
              type="button"
              onClick={handleRetry}
              className="mt-1 text-caption text-emerald-400 underline underline-offset-2 hover:text-emerald-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              {t('fileUpload.uploadDifferent')}
            </button>
          </div>
        </div>
      )}

      {/* Error state */}
      {uploadState.status === 'error' && (
        <div
          role="alert"
          className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 flex items-start gap-3 mb-3"
        >
          <svg
            aria-hidden="true"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="h-5 w-5 text-destructive shrink-0 mt-0.5"
          >
            <path
              fillRule="evenodd"
              d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-8-5a.75.75 0 0 1 .75.75v4.5a.75.75 0 0 1-1.5 0v-4.5A.75.75 0 0 1 10 5Zm0 10a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
              clipRule="evenodd"
            />
          </svg>
          <div className="flex-1">
            <p className="text-body-sm text-destructive">{uploadState.message}</p>
            <button
              type="button"
              onClick={handleRetry}
              className="mt-1 text-caption text-destructive underline underline-offset-2 hover:text-destructive/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              {t('fileUpload.tryAgain')}
            </button>
          </div>
        </div>
      )}

      {/* Drop zone — hidden after success */}
      {uploadState.status !== 'success' && (
        <>
          {/* Hidden file input */}
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            accept={accept}
            aria-label={label}
            className="sr-only"
            onChange={handleFileChange}
            disabled={isUploading}
          />

          {/* Clickable / droppable area */}
          <div
            role="button"
            tabIndex={isUploading ? -1 : 0}
            aria-label={t('fileUpload.clickOrDrag', { label })}
            aria-disabled={isUploading}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onKeyDown={handleKeyDown}
            onClick={() => !isUploading && inputRef.current?.click()}
            className={[
              'group relative flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-secondary px-6 py-10 text-center transition-colors cursor-pointer',
              isDraggingOver
                ? 'border-primary/40 bg-accent'
                : 'border-border hover:border-primary/40 hover:bg-accent',
              isUploading ? 'cursor-not-allowed opacity-70' : '',
            ]
              .filter(Boolean)
              .join(' ')}
          >
            {/* Upload icon */}
            <svg
              aria-hidden="true"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              className="h-10 w-10 text-primary transition-transform duration-300 group-hover:-translate-y-0.5"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
              />
            </svg>

            <div>
              <p className="text-body-sm font-medium text-foreground">
                {isUploading ? t('fileUpload.uploading') : t('fileUpload.dragDrop')}
              </p>
              <p className="mt-1 text-caption text-muted-foreground">
                {t('fileUpload.pdfMax', { size: formatBytes(maxBytes) })}
              </p>
            </div>

            {/* Progress bar */}
            {isUploading && (
              <div className="w-full max-w-xs" aria-label={t('fileUpload.uploadProgress')}>
                <div className="h-2 w-full rounded-full bg-border overflow-hidden">
                  <div
                    role="progressbar"
                    aria-valuenow={uploadState.progress}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    className="h-2 rounded-full bg-primary transition-all duration-200"
                    style={{ width: `${uploadState.progress}%` }}
                  />
                </div>
                <p className="mt-1 text-caption text-primary text-center tabular-nums">
                  {uploadState.progress}%
                </p>
              </div>
            )}
          </div>

          {/* Visible label linked to the input for screen readers */}
          <label
            htmlFor={inputId}
            className="sr-only"
          >
            {label}
          </label>
        </>
      )}
    </div>
  );
}
