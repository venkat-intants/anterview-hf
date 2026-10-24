// RoundPanel: one round card (settings, publish state, sections, assign).
// Moved out of pages/hr/ExamEditor.tsx.
// English-only by design (CLAUDE.md - staff consoles are not translated).

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  Send,
  Loader2,
  X,
  Pencil,
  ChevronDown,
  ChevronRight,
} from '@/design/components/icons';
import { updateRound, duplicateRound, deleteSection, type Round } from '@/api/exams';
import { canUnpublishRound, roundLockReason } from '@/lib/examLocks';
import {
  examEditorMutationKey,
  INVALID_MINUTES_MESSAGE,
  parseMinutes,
  parseThreshold,
} from '@/lib/examEditor';
import { errorMessage } from '@/lib/errorMessage';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { statusLabel } from '@/lib/statusLabel';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { LockBanner } from '@/components/bank/LockBanner';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { Button } from '@/components/ui/button';
import { FormField } from '@/components/ui/form-field';
import { Input } from '@/components/ui/input';
import { roundStatusTone } from '@/components/hr/exam/examEditorUi';
import { SectionPanel } from '@/components/hr/exam/SectionPanel';
import { AddSectionForm } from '@/components/hr/exam/AddSectionForm';
import { RoundAssignPanel } from '@/components/hr/exam/RoundAssignPanel';

// ── Round panel ───────────────────────────────────────────────────────────────

interface RoundPanelProps {
  examId: string;
  round: Round;
  totalRounds: number;
  isFirst: boolean;
  isLast: boolean;
  onDelete: (roundId: string) => void;
  deleting: boolean;
  onMove: (roundId: string, direction: -1 | 1) => void;
  reordering: boolean;
}

export function RoundPanel({
  examId,
  round,
  totalRounds,
  isFirst,
  isLast,
  onDelete,
  deleting,
  onMove,
  reordering,
}: RoundPanelProps) {
  const qc = useQueryClient();
  const editorKey = examEditorMutationKey(examId);
  const [expanded, setExpanded] = useState(true);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(round.title);
  const [thresholdDraft, setThresholdDraft] = useState(String(round.pass_threshold));
  const roundMinutes = round.time_limit_seconds
    ? String(Math.round(round.time_limit_seconds / 60))
    : '';
  const [minutesDraft, setMinutesDraft] = useState(roundMinutes);
  const [addingSection, setAddingSection] = useState(false);
  const [showAssign, setShowAssign] = useState(false);

  // The drafts follow the saved values whenever those change (a save, a
  // refetch, a duplicate), so the screen never shows a value that is not in
  // effect.
  useEffect(() => setThresholdDraft(String(round.pass_threshold)), [round.pass_threshold]);
  useEffect(() => setMinutesDraft(roundMinutes), [roundMinutes]);

  const invalidate = () => void invalidateAfterHrMutation(qc);

  const renameMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (title: string) => updateRound(examId, round.id, { title }),
    onSuccess: () => {
      setEditingTitle(false);
      toast.success('Round renamed');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not rename the round.')),
  });

  const publishMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (status: 'draft' | 'published') => updateRound(examId, round.id, { status }),
    onSuccess: (r) => {
      toast.success(r.status === 'published' ? 'Round published' : 'Round unpublished');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not change the round status.')),
  });

  const thresholdMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (pass_threshold: number) => updateRound(examId, round.id, { pass_threshold }),
    onSuccess: () => {
      toast.success('Pass mark saved');
      invalidate();
    },
    onError: (e: unknown) => {
      // Put the saved value back: the input kept the refused one, so the screen
      // showed a pass mark that was not in effect (audit E4).
      setThresholdDraft(String(round.pass_threshold));
      toast.error(errorMessage(e, 'Could not save the pass mark.'));
    },
  });

  const timeLimitMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (time_limit_seconds: number | null) =>
      updateRound(examId, round.id, { time_limit_seconds }),
    onSuccess: () => {
      toast.success('Time limit saved');
      invalidate();
    },
    onError: (e: unknown) => {
      setMinutesDraft(roundMinutes);
      toast.error(errorMessage(e, 'Could not save the time limit.'));
    },
  });

  const advancesMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (advances_to_interview: boolean) =>
      updateRound(examId, round.id, { advances_to_interview }),
    onSuccess: () => {
      toast.success('Saved');
      invalidate();
    },
    onError: (e: unknown) =>
      toast.error(errorMessage(e, 'Could not update the interview step setting.')),
  });

  // Camera proctoring is per round and OFF by default, which is the right
  // default: it is the setting that turns a written test into something that
  // watches the candidate, so it should be a deliberate act. It had no control
  // anywhere in the app, so it could not be turned on at all.
  const cameraMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (camera_proctoring_required: boolean) =>
      updateRound(examId, round.id, { camera_proctoring_required }),
    onSuccess: () => {
      toast.success('Saved');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not update camera proctoring.')),
  });

  const deleteSectionMut = useMutation({
    mutationKey: editorKey,
    mutationFn: (sectionId: string) => deleteSection(examId, round.id, sectionId),
    onSuccess: () => {
      toast.success('Section deleted');
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not delete the section.')),
  });

  // PH4-D1: the way forward once this round is locked: duplicate it into a
  // fresh, editable draft round.
  const duplicateMut = useMutation({
    mutationKey: editorKey,
    mutationFn: () => duplicateRound(examId, round.id),
    onSuccess: (created) => {
      toast.success(`Duplicated as "${created.title}"`);
      invalidate();
    },
    onError: (e: unknown) => toast.error(errorMessage(e, 'Could not duplicate this round.')),
  });

  const isPublished = round.status === 'published';
  const attempts = round.attempt_count ?? 0;
  // Published, or taken. A taken round is never offered Unpublish: the server
  // refuses it (audit E2), so Duplicate is the only way forward.
  const lockReason = roundLockReason(round.status, attempts);
  const unpublishable = canUnpublishRound(round.status, attempts);
  const locked = Boolean(lockReason);

  return (
    <GlassCard className="p-0 overflow-hidden">
      {/* Round header */}
      <div className="flex flex-wrap items-center gap-2 px-5 py-3 border-b border-border">
        {/* Reorder */}
        <div className="flex shrink-0 items-center">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Move round ${round.title} up`}
            disabled={isFirst || reordering}
            className="text-muted-foreground"
            onClick={() => onMove(round.id, -1)}
          >
            <ChevronDown className="rotate-180" aria-hidden="true" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Move round ${round.title} down`}
            disabled={isLast || reordering}
            className="text-muted-foreground"
            onClick={() => onMove(round.id, 1)}
          >
            <ChevronDown aria-hidden="true" />
          </Button>
        </div>

        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} round ${round.title}`}
          className="text-muted-foreground"
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        </Button>

        <span
          title={`Round ${round.round_number}`}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--ui-line-strong)] bg-[var(--ui-inset)] font-mono text-[12px] font-semibold text-foreground"
        >
          {round.round_number}
        </span>

        {editingTitle ? (
          <form
            className="flex flex-1 items-center gap-2"
            aria-label={`Rename round ${round.title}`}
            onSubmit={(e) => {
              e.preventDefault();
              if (titleDraft.trim()) renameMut.mutate(titleDraft.trim());
            }}
          >
            <Input
              autoFocus
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              maxLength={200}
              className="flex-1"
              aria-label="Round title"
            />
            <Button type="submit" disabled={renameMut.isPending} aria-busy={renameMut.isPending}>
              {renameMut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Save
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Cancel"
              onClick={() => {
                setEditingTitle(false);
                setTitleDraft(round.title);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </form>
        ) : (
          <>
            <span className="min-w-0 flex-1 break-words text-[15px] font-semibold text-foreground">
              {round.title}
            </span>
            <StatusTag tone={roundStatusTone(round.status)}>
              {statusLabel(round.status, 'round')}
            </StatusTag>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Rename round ${round.title}`}
              className="text-muted-foreground"
              onClick={() => {
                setTitleDraft(round.title);
                setEditingTitle(true);
              }}
            >
              <Pencil aria-hidden="true" />
            </Button>
            {!isPublished ? (
              // Publish is the round's one primary action (audit A4).
              <Button
                type="button"
                disabled={publishMut.isPending}
                aria-busy={publishMut.isPending}
                onClick={() => publishMut.mutate('published')}
              >
                {publishMut.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : null}
                Publish
              </Button>
            ) : unpublishable ? (
              <Button
                type="button"
                variant="outline"
                disabled={publishMut.isPending}
                aria-busy={publishMut.isPending}
                onClick={() => publishMut.mutate('draft')}
              >
                {publishMut.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : null}
                Unpublish
              </Button>
            ) : null}
            {/* A locked round cannot be deleted (the server refuses it), and
                an exam keeps at least one round, so the control only appears
                when the delete can succeed (audit E5). */}
            {!locked && totalRounds > 1 ? (
              <ConfirmDeleteButton
                title={`Delete round ${round.title}`}
                confirmText="Delete round"
                pending={deleting}
                onConfirm={() => onDelete(round.id)}
              />
            ) : null}
          </>
        )}
      </div>

      {expanded && (
        <div className="px-5 py-4 space-y-4">
          {/* PH4-D1: this round's content is fixed; here is why, and the two
              ways forward. */}
          {lockReason ? (
            <LockBanner
              reason={lockReason}
              canUnpublish={unpublishable}
              onUnpublish={() => publishMut.mutate('draft')}
              unpublishing={publishMut.isPending}
              onDuplicate={() => duplicateMut.mutate()}
              duplicating={duplicateMut.isPending}
            />
          ) : null}

          {/* Settings row. The grading settings are frozen with the round
              (audit E4): disabled while locked, so nothing can be typed that
              the server would refuse. */}
          <div className="flex flex-wrap items-end gap-4">
            <FormField label="Pass threshold %">
              {(p) => (
                <Input
                  {...p}
                  type="number"
                  min={0}
                  max={100}
                  value={thresholdDraft}
                  disabled={locked || thresholdMut.isPending}
                  onChange={(e) => setThresholdDraft(e.target.value)}
                  onBlur={() => {
                    const v = parseThreshold(thresholdDraft, round.pass_threshold);
                    setThresholdDraft(String(v));
                    if (v !== round.pass_threshold) thresholdMut.mutate(v);
                  }}
                  className="w-24"
                  aria-label="Pass threshold"
                />
              )}
            </FormField>
            <FormField label="Time limit (min)">
              {(p) => (
                <Input
                  {...p}
                  type="number"
                  min={1}
                  placeholder="No limit"
                  value={minutesDraft}
                  disabled={locked || timeLimitMut.isPending}
                  onChange={(e) => setMinutesDraft(e.target.value)}
                  onBlur={() => {
                    const parsed = parseMinutes(minutesDraft);
                    if (parsed.kind === 'invalid') {
                      // Refused, not read as "no limit": the saved value goes back.
                      toast.error(INVALID_MINUTES_MESSAGE);
                      setMinutesDraft(roundMinutes);
                      return;
                    }
                    const next = parsed.kind === 'limit' ? parsed.seconds : null;
                    if (next !== round.time_limit_seconds) timeLimitMut.mutate(next);
                  }}
                  className="w-28"
                  aria-label="Round time limit in minutes (blank for no limit)"
                />
              )}
            </FormField>
            <label className="flex min-h-10 items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[var(--accent)] disabled:cursor-not-allowed"
                checked={round.advances_to_interview}
                disabled={locked || advancesMut.isPending}
                onChange={(e) => advancesMut.mutate(e.target.checked)}
              />
              Advances to interview on pass
            </label>
          </div>

          {/* ── Camera proctoring. Its own row, not tucked in with the pass
              threshold: this is the setting that decides whether a written
              test watches the candidate, and it deserves to be read before it
              is ticked rather than found by accident. Applies to every section
              in the round, MCQ and coding alike, since proctoring attaches to
              the attempt, not the section kind. ── */}
          <div className="mt-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3">
            <label className="flex items-start gap-2 text-[12.5px] text-[var(--ui-soft)]">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
                checked={round.camera_proctoring_required}
                disabled={cameraMut.isPending}
                onChange={(e) => cameraMut.mutate(e.target.checked)}
              />
              <span>
                <span className="font-medium text-foreground">Require camera proctoring</span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-[var(--ui-faint)]">
                  Candidates are asked for camera access before the round starts and cannot begin
                  without it. Detection runs entirely in their browser, and no video or image is
                  recorded or sent anywhere, only timestamped events. Leave off and the round still
                  records fullscreen, tab-switch and clipboard activity.
                </span>
              </span>
            </label>
          </div>

          {/* Sections */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-faint)]">
                Sections ({round.sections.length})
              </p>
              {!locked && (
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  onClick={() => setAddingSection((v) => !v)}
                  aria-expanded={addingSection}
                >
                  <Plus aria-hidden="true" /> Add section
                </Button>
              )}
            </div>

            {/* Add-section form */}
            {addingSection && !locked && (
              <AddSectionForm
                examId={examId}
                roundId={round.id}
                onAdded={invalidate}
                onClose={() => setAddingSection(false)}
              />
            )}

            {/* Section list */}
            {round.sections.length === 0 ? (
              <p className="text-[12.5px] text-muted-foreground">No sections yet. Add one above.</p>
            ) : (
              <div className="space-y-2">
                {round.sections.map((section) => (
                  <SectionPanel
                    key={section.id}
                    examId={examId}
                    section={section}
                    locked={locked}
                    onDelete={(sid) => deleteSectionMut.mutate(sid)}
                    deleting={
                      deleteSectionMut.isPending && deleteSectionMut.variables === section.id
                    }
                  />
                ))}
              </div>
            )}
          </div>

          {/* Assign & share */}
          <div>
            <Button
              type="button"
              variant="link"
              size="sm"
              onClick={() => setShowAssign((v) => !v)}
              aria-expanded={showAssign}
            >
              <Send aria-hidden="true" />
              {showAssign ? 'Hide assign and share' : 'Assign and share'}
            </Button>
            {showAssign && (
              <RoundAssignPanel
                examId={examId}
                roundId={round.id}
                isPublished={isPublished}
                isFirstRound={isFirst}
              />
            )}
          </div>
        </div>
      )}
    </GlassCard>
  );
}
