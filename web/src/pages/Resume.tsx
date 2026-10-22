// Resume — Resume manager page. Renders inside AppShell (no shell here).
// Shows: upload zone, current-resume card, version history with set-current/delete.
// "Extracted skills" (design-only, no API) is intentionally omitted.

import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  FileText,
  Download,
  Trash2,
  Upload,
  AlertCircle,
  FileCheck2,
} from '@/design/components/icons';
import {
  listResumes,
  getCurrentResume,
  setCurrentResume,
  deleteResume,
  uploadResume,
} from '@/api/resume';
import type { ResumeVersionItem } from '@/api/resume';
import FileUploadZone from '@/components/FileUploadZone';
import { toast } from '@/lib/toast';
import { LoadErrorPanel } from '@/components/candidate/LoadErrorPanel';
import { formatDate } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { downloadUrl } from '@/lib/safeUrl';
import { StatusTag } from '@/design/components/primitives';
import { Page, PageHeader, SectionCard } from '@/design/components/page';
import { Button, buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

// ── Constants ──────────────────────────────────────────────────────────────────

const RESUME_MAX_BYTES = 5 * 1024 * 1024; // 5 MB

// ── Helpers ───────────────────────────────────────────────────────────────────

// ── Sub-components ─────────────────────────────────────────────────────────────

function LoadingSkeletons() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4" aria-busy="true" aria-label={t('resume.loadingAria')}>
      <Skeleton className="h-28 w-full rounded-card" />
      <Skeleton className="h-40 w-full rounded-card" />
      <Skeleton className="h-20 w-full rounded-card" />
    </div>
  );
}

function EmptyState({ onUploadSuccess }: { onUploadSuccess: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const handleUpload = useCallback(
    (file: File, onProgress: (pct: number) => void) => {
      return uploadResume(file, undefined, onProgress).then((result) => {
        void queryClient.invalidateQueries({ queryKey: ['resumes'] });
        void queryClient.invalidateQueries({ queryKey: ['resume', 'current'] });
        toast.success(t('resume.uploadedSuccess'));
        onUploadSuccess();
        return { text_length: result.text_length };
      });
    },
    [queryClient, onUploadSuccess, t],
  );

  return (
    <div data-testid="resume-empty-state">
      <div className="flex flex-col items-center gap-5 rounded-card border border-border bg-card px-6 py-12 text-center shadow-card">
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-[var(--ui-highlight-wash)] text-foreground">
          <FileText className="h-7 w-7" aria-hidden="true" />
        </span>
        <div>
          <p className="text-[17px] font-bold text-foreground">{t('resume.noResumeTitle')}</p>
          <p className="mx-auto mt-1 max-w-[420px] text-[14px] text-muted-foreground">
            {t('resume.noResumeDesc')}
          </p>
        </div>
        <div className="w-full max-w-md">
          <FileUploadZone
            label="Resume"
            accept="application/pdf"
            maxBytes={RESUME_MAX_BYTES}
            onUpload={handleUpload}
          />
        </div>
      </div>
    </div>
  );
}

// Delete confirm dialog
interface DeleteDialogProps {
  open: boolean;
  filename: string;
  isPending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function DeleteDialog({ open, filename, isPending, onConfirm, onCancel }: DeleteDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="w-[calc(100%-2rem)] rounded-card">
        <DialogHeader>
          <DialogTitle>{t('resume.deleteTitle')}</DialogTitle>
          <DialogDescription>{t('resume.deleteDesc', { filename })}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={onCancel} disabled={isPending}>
            {t('resume.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={isPending}
            aria-busy={isPending}
          >
            {isPending ? t('resume.deleting') : t('resume.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Single resume version row
interface ResumeVersionRowProps {
  item: ResumeVersionItem;
  onSetCurrent: (id: string) => void;
  onDelete: (item: ResumeVersionItem) => void;
  isSettingCurrent: boolean;
  isDeleting: boolean;
}

function ResumeVersionRow({
  item,
  onSetCurrent,
  onDelete,
  isSettingCurrent,
  isDeleting,
}: ResumeVersionRowProps) {
  const { t } = useTranslation();
  return (
    <li
      className={cn(
        'flex flex-col gap-3 rounded-[16px] border p-4 transition-colors sm:flex-row sm:items-center',
        item.is_current
          ? 'border-[var(--ui-ok)]/30 bg-[var(--ui-ok-wash)]'
          : 'border-border bg-card hover:bg-[var(--ui-inset-soft)]',
      )}
      data-testid={`resume-version-${item.resume_id}`}
    >
      {/* File icon + info */}
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span
          className={cn(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px]',
            item.is_current ? 'bg-card text-[var(--ui-ok)]' : 'bg-[var(--ui-inset)] text-muted-foreground',
          )}
        >
          {item.is_current ? (
            <FileCheck2 className="h-5 w-5" aria-hidden="true" />
          ) : (
            <FileText className="h-5 w-5" aria-hidden="true" />
          )}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p
              className="truncate text-[14px] font-semibold text-foreground"
              title={item.filename}
            >
              {item.filename}
            </p>
            {item.is_current && (
              <StatusTag tone="forest" dot>
                {t('resume.currentBadge')}
              </StatusTag>
            )}
          </div>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {t('resume.uploaded')} {formatDate(item.uploaded_at)} ·{' '}
            {t('resume.charactersExtracted', { count: item.text_length })}
          </p>
        </div>
      </div>

      {/* Actions */}
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {item.download_url && downloadUrl(item.download_url) && (
          <a
            href={downloadUrl(item.download_url) ?? undefined}
            download={item.filename}
            aria-label={t('resume.downloadAria', { filename: item.filename })}
            className={buttonVariants({ variant: 'outline' })}
          >
            <Download aria-hidden="true" />
            {t('resume.download')}
          </a>
        )}
        {!item.is_current && (
          <Button
            variant="outline"
            onClick={() => onSetCurrent(item.resume_id)}
            disabled={isSettingCurrent}
            aria-busy={isSettingCurrent}
            aria-label={t('resume.setCurrentAria', { filename: item.filename })}
          >
            {isSettingCurrent ? t('resume.setting') : t('resume.setAsCurrent')}
          </Button>
        )}
        <Button
          variant="outline"
          size="icon"
          className="text-[var(--ui-danger)] hover:text-[var(--ui-danger)]"
          onClick={() => onDelete(item)}
          disabled={isDeleting}
          aria-label={t('resume.deleteAria', { filename: item.filename })}
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
    </li>
  );
}

// ── Resume page ────────────────────────────────────────────────────────────────

export default function Resume() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<ResumeVersionItem | null>(null);

  // Queries — keys preserved exactly: ['resumes'] and ['resume','current']
  const {
    data: resumes,
    isLoading: resumesLoading,
    isError: resumesError,
    refetch: refetchResumes,
    isFetching: resumesFetching,
  } = useQuery({
    queryKey: ['resumes'],
    queryFn: listResumes,
    staleTime: 2 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const {
    data: currentResume,
    isLoading: currentLoading,
    isError: currentError,
  } = useQuery({
    queryKey: ['resume', 'current'],
    queryFn: getCurrentResume,
    staleTime: 2 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const isLoading = resumesLoading || currentLoading;

  // Set-current mutation — 3-key invalidation preserved
  const setCurrentMutation = useMutation({
    mutationFn: (id: string) => setCurrentResume(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['resumes'] });
      void queryClient.invalidateQueries({ queryKey: ['resume', 'current'] });
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      toast.success(t('resume.setCurrentSuccess'));
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : t('resume.updateError'));
    },
  });

  // Delete mutation — 3-key invalidation preserved
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteResume(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['resumes'] });
      void queryClient.invalidateQueries({ queryKey: ['resume', 'current'] });
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      toast.success(t('resume.deletedSuccess'));
      setDeleteTarget(null);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : t('resume.deleteError'));
      setDeleteTarget(null);
    },
  });

  // Upload handler — 3-key invalidation preserved
  const handleUpload = useCallback(
    (file: File, onProgress: (pct: number) => void) => {
      return uploadResume(file, undefined, onProgress).then((result) => {
        void queryClient.invalidateQueries({ queryKey: ['resumes'] });
        void queryClient.invalidateQueries({ queryKey: ['resume', 'current'] });
        void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
        toast.success(t('resume.uploadedSuccess'));
        return { text_length: result.text_length };
      });
    },
    [queryClient, t],
  );

  // The list failing has its own panel with a retry; only the current-resume
  // card failing on its own is worth a toast.
  useEffect(() => {
    if (currentError && !resumesError) {
      toast.error(t('resume.currentLoadError'));
    }
  }, [resumesError, currentError, t]);

  const resumeList = resumes ?? [];
  const hasResumes = resumeList.length > 0;

  return (
    <Page width="form">
      <PageHeader
        title={<span id="resume-heading">{t('resume.pageTitle')}</span>}
        description={t('resume.pageDesc')}
      />

      {isLoading ? (
        <LoadingSkeletons />
      ) : resumesError && !resumes ? (
        // A failed load is not "no resume on file" (audit CA-M7).
        <LoadErrorPanel
          title={t('resume.loadError')}
          onRetry={() => void refetchResumes()}
          retrying={resumesFetching}
        />
      ) : !hasResumes ? (
        <EmptyState
          onUploadSuccess={() =>
            void queryClient.invalidateQueries({ queryKey: ['resumes'] })
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
          {/* Left column: current-resume card + upload zone */}
          <div className="space-y-5 lg:col-span-2">
            {/* Current resume highlight card */}
            {currentResume && (
              <div className="rounded-card border border-border bg-card p-5 shadow-card">
                <div className="flex items-center gap-3">
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[12px] bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]">
                    <FileCheck2 size={22} aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p
                      className="truncate text-[15px] font-semibold text-foreground"
                      title={currentResume.filename}
                    >
                      {currentResume.filename}
                    </p>
                    <p className="text-[13px] text-muted-foreground">
                      {t('resume.charactersExtracted', { count: currentResume.text_length })} ·{' '}
                      {t('resume.uploaded')} {formatDate(currentResume.uploaded_at)}
                    </p>
                  </div>
                </div>

                <div className="mt-4 flex items-center gap-2">
                  {currentResume.download_url && downloadUrl(currentResume.download_url) ? (
                    <a
                      href={downloadUrl(currentResume.download_url) ?? undefined}
                      download={currentResume.filename}
                      aria-label={t('resume.downloadAria', { filename: currentResume.filename })}
                      className={cn(buttonVariants({ variant: 'outline' }), 'flex-1')}
                    >
                      <Download aria-hidden="true" />
                      {t('resume.download')}
                    </a>
                  ) : (
                    <Button variant="outline" className="flex-1" disabled>
                      <Download aria-hidden="true" />
                      {t('resume.download')}
                    </Button>
                  )}
                </div>

                <StatusTag tone="forest" dot className="mt-4">
                  {t('resume.activeResumeTitle')}
                </StatusTag>
              </div>
            )}

            {/* Inline currentError alert — role="alert" preserved */}
            {currentError && (
              <div
                role="alert"
                className="flex items-center gap-2 rounded-[16px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] px-4 py-3 text-[13.5px] font-medium text-[var(--ui-danger)]"
              >
                <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
                {t('resume.currentLoadError')}
              </div>
            )}

            {/* Upload new version */}
            <SectionCard
              title={t('resume.uploadTitle')}
              icon={Upload}
              description={t('resume.uploadDesc')}
            >
              <FileUploadZone
                label="Resume"
                accept="application/pdf"
                maxBytes={RESUME_MAX_BYTES}
                onUpload={handleUpload}
              />
            </SectionCard>
          </div>

          {/* Right column: version history list */}
          <div className="lg:col-span-3">
            <SectionCard title={t('resume.versionHistory', { count: resumeList.length })}>
              <ul className="space-y-3" aria-label={t('resume.listAria')}>
                {resumeList.map((item) => (
                  <ResumeVersionRow
                    key={item.resume_id}
                    item={item}
                    onSetCurrent={(id) => setCurrentMutation.mutate(id)}
                    onDelete={(it) => setDeleteTarget(it)}
                    isSettingCurrent={
                      setCurrentMutation.isPending &&
                      setCurrentMutation.variables === item.resume_id
                    }
                    isDeleting={
                      deleteMutation.isPending &&
                      deleteTarget?.resume_id === item.resume_id
                    }
                  />
                ))}
              </ul>
            </SectionCard>
          </div>
        </div>
      )}

      {/* Delete confirm dialog */}
      <DeleteDialog
        open={deleteTarget !== null}
        filename={deleteTarget?.filename ?? ''}
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.resume_id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </Page>
  );
}
