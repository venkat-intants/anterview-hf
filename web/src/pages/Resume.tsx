// Resume — Resume manager page. Renders inside AppShell (no shell here).
// Design skin applied; all logic + mutations preserved verbatim.
// Shows: upload zone, current-resume card, version history with set-current/delete.
// "Extracted skills" (design-only, no API) is intentionally omitted.

import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { motion, type Variants } from 'framer-motion';
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
import { formatDate } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { GlassCard, StatusTag, Pill } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { Button } from '@/components/ui/button';
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── Animation variants ─────────────────────────────────────────────────────────

const stagger: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
};

const fadeUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: [0.22, 1, 0.36, 1] } },
};

// ── Inline skeleton (avoids @/components/ui/skeleton — shadcn forbidden on this page) ──

function SkeletonBlock({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-[24px] bg-white/[0.06]', className)} />;
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function LoadingSkeletons() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading resume data">
      <SkeletonBlock className="h-28 w-full" />
      <SkeletonBlock className="h-40 w-full" />
      <SkeletonBlock className="h-20 w-full" />
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
    <GlassCard
      className="flex flex-col items-center justify-center gap-4 py-16 text-center"
    >
      <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.1)] ring-1 ring-[rgba(var(--accent-rgb),0.2)]">
        <FileText className="h-7 w-7 text-[#60a5fa]" aria-hidden="true" />
      </div>
      <div>
        <p className="text-[15px] font-semibold text-white">{t('resume.noResumeTitle')}</p>
        <p className="mt-1 text-[13px] text-[#888b91]">{t('resume.noResumeDesc')}</p>
      </div>
      <div className="w-full max-w-sm px-4">
        <FileUploadZone
          label="Resume"
          accept="application/pdf"
          maxBytes={RESUME_MAX_BYTES}
          onUpload={handleUpload}
        />
      </div>
    </GlassCard>
    </div>
  );
}

// Delete confirm dialog — preserved verbatim from live logic
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('resume.deleteTitle')}</DialogTitle>
          <DialogDescription>{t('resume.deleteDesc', { filename })}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
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

// Single resume version row — reskinned with design language
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
    <motion.div
      variants={fadeUp}
      className={cn(
        'flex flex-col gap-3 rounded-[20px] border p-4 transition-colors sm:flex-row sm:items-center',
        item.is_current
          ? 'border-[rgba(var(--accent-rgb),0.25)] bg-[linear-gradient(160deg,rgba(0,27,51,0.6),rgba(3,7,25,0.6))]'
          : 'border-white/[0.08] bg-[#0f0f10] hover:bg-white/[0.02]',
      )}
      data-testid={`resume-version-${item.resume_id}`}
    >
      {/* File icon + info */}
      <div className="flex flex-1 min-w-0 items-start gap-3">
        <span
          className={cn(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px]',
            item.is_current
              ? 'bg-[rgba(39,201,63,0.14)]'
              : 'bg-white/[0.06]',
          )}
        >
          {item.is_current ? (
            <FileCheck2
              className="h-5 w-5 text-[#27c93f]"
              aria-hidden="true"
            />
          ) : (
            <FileText
              className="h-5 w-5 text-[#888b91]"
              aria-hidden="true"
            />
          )}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p
              className="truncate text-[13.5px] font-medium text-white"
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
          <p className="mt-0.5 text-[12px] text-[#888b91]">
            {t('resume.uploaded')} {formatDate(item.uploaded_at)} ·{' '}
            {formatBytes(item.text_length)} {t('resume.extracted')}
          </p>
        </div>
      </div>

      {/* Actions */}
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {item.download_url && (
          <a
            href={item.download_url}
            download={item.filename}
            aria-label={`Download ${item.filename}`}
            className="inline-flex items-center gap-1.5 rounded-[9999px] border border-white/10 bg-white/[0.06] px-3.5 py-1.5 text-[12.5px] font-semibold text-white transition-colors hover:bg-white/[0.1] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            {t('resume.download')}
          </a>
        )}
        {!item.is_current && (
          <Pill
            variant="ghost"
            className="py-1.5 px-3.5 text-[12.5px]"
            onClick={() => onSetCurrent(item.resume_id)}
            disabled={isSettingCurrent}
            aria-busy={isSettingCurrent}
            aria-label={`Set ${item.filename} as current resume`}
          >
            {isSettingCurrent ? t('resume.setting') : t('resume.setAsCurrent')}
          </Pill>
        )}
        <Pill
          variant="danger"
          className="py-1.5 px-3 text-[12.5px]"
          onClick={() => onDelete(item)}
          disabled={isDeleting}
          aria-label={`Delete ${item.filename}`}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
        </Pill>
      </div>
    </motion.div>
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

  // Error toast — fires once per distinct error transition
  useEffect(() => {
    if (resumesError || currentError) {
      toast.error(t('resume.loadError'));
    }
  }, [resumesError, currentError, t]);

  const resumeList = resumes ?? [];
  const hasResumes = resumeList.length > 0;

  return (
    <motion.section
      aria-labelledby="resume-heading"
      initial="hidden"
      animate="visible"
      variants={stagger}
      className="mx-auto max-w-[1000px] space-y-6 px-6 py-8 lg:px-8"
    >
      {/* Page heading */}
      <motion.div variants={fadeUp}>
        <Reveal>
          <h1
            id="resume-heading"
            className="text-[28px] font-semibold tracking-[-1px] text-white"
          >
            {t('resume.pageTitle')}
          </h1>
          <p className="mt-1 text-[14px] text-[#888b91]">{t('resume.pageDesc')}</p>
        </Reveal>
      </motion.div>

      {isLoading ? (
        <LoadingSkeletons />
      ) : !hasResumes ? (
        <motion.div variants={fadeUp}>
          <EmptyState
            onUploadSuccess={() =>
              void queryClient.invalidateQueries({ queryKey: ['resumes'] })
            }
          />
        </motion.div>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
          {/* Left column: current-resume card + upload zone */}
          <div className="lg:col-span-2 space-y-5">
            <Reveal dir="left">
              {/* Current resume highlight card */}
              {currentResume && (
                <GlassCard className="p-5">
                  <div className="flex items-center gap-3">
                    <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[12px] bg-[rgba(39,201,63,0.14)]">
                      <FileCheck2 size={22} className="text-[#27c93f]" aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <p
                        className="truncate text-[14px] font-medium text-white"
                        title={currentResume.filename}
                      >
                        {currentResume.filename}
                      </p>
                      <p className="text-[12px] text-[#888b91]">
                        {formatBytes(currentResume.text_length)} ·{' '}
                        {t('resume.uploaded')} {formatDate(currentResume.uploaded_at)}
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 flex items-center gap-2">
                    {currentResume.download_url ? (
                      <a
                        href={currentResume.download_url}
                        download={currentResume.filename}
                        aria-label={`Download ${currentResume.filename}`}
                        className="inline-flex flex-1 items-center justify-center gap-2 rounded-[9999px] border border-white/10 bg-white/[0.06] px-5 py-2.5 text-[14px] font-semibold text-white transition-colors hover:bg-white/[0.1]"
                      >
                        <Download size={15} aria-hidden="true" />
                        {t('resume.download')}
                      </a>
                    ) : (
                      <Pill variant="ghost" className="flex-1 py-2.5" disabled>
                        <Download size={15} aria-hidden="true" />
                        {t('resume.download')}
                      </Pill>
                    )}
                  </div>

                  <StatusTag tone="forest" dot className="mt-4">
                    {t('resume.activeResumeTitle')}
                  </StatusTag>
                </GlassCard>
              )}

              {/* Inline currentError alert — role="alert" preserved */}
              {currentError && (
                <div
                  role="alert"
                  className="flex items-center gap-2 rounded-[16px] border border-[rgba(230,113,79,0.3)] bg-[rgba(230,113,79,0.1)] px-4 py-3 text-[13px] text-[#e6714f]"
                >
                  <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
                  {t('resume.currentLoadError')}
                </div>
              )}

              {/* Upload new version */}
              <GlassCard className="p-5">
                <div className="mb-3 flex items-center gap-2">
                  <Upload className="h-4 w-4 text-[#60a5fa]" aria-hidden="true" />
                  <h2 className="text-[15px] font-semibold text-white">
                    {t('resume.uploadTitle')}
                  </h2>
                </div>
                <p className="mb-4 text-[12.5px] text-[#888b91]">{t('resume.uploadDesc')}</p>
                <FileUploadZone
                  label="Resume"
                  accept="application/pdf"
                  maxBytes={RESUME_MAX_BYTES}
                  onUpload={handleUpload}
                />
              </GlassCard>
            </Reveal>
          </div>

          {/* Right column: version history list */}
          <div className="lg:col-span-3">
            <Reveal dir="right">
              <GlassCard className="p-5">
                <h2 className="mb-4 text-[15px] font-semibold text-white">
                  {t('resume.versionHistory', { count: resumeList.length })}
                </h2>
                <div
                  className="space-y-3"
                  aria-label="Resume version list"
                >
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
                </div>
              </GlassCard>
            </Reveal>
          </div>
        </div>
      )}

      {/* Delete confirm dialog — preserved verbatim */}
      <DeleteDialog
        open={deleteTarget !== null}
        filename={deleteTarget?.filename ?? ''}
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.resume_id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </motion.section>
  );
}
