// What each kind of round is, in one table — D2/D4.
//
// The builder asks four different questions about a round kind (what icon, what
// does it mean, does it need questions attached, does it need a threshold), and
// every one of those answers is enforced somewhere in
// services/data_gateway/app/workflows.py::validate_chain. Keeping them in one
// place is what stops the canvas from offering a configuration the server will
// refuse at publish time — the worst possible moment to find out.

import {
  ClipboardList,
  Code2,
  Video,
  Users,
  type LucideIcon,
} from '@/design/components/icons';
import type { TagTone } from '@/design/components/primitives';
import type { RoundKind } from '@/api/workflows';

export interface RoundKindMeta {
  kind: RoundKind;
  label: string;
  /** One line, written for an HR manager rather than an engineer. */
  blurb: string;
  icon: LucideIcon;
  tone: TagTone;
  /** Questions come from an exam, so publishing is blocked until one is attached. */
  needsExam: boolean;
  /**
   * Every kind except human_review must carry an advance threshold —
   * validate_chain refuses to publish without one. A human review round has
   * nothing to score, so it has nothing to threshold.
   */
  needsThreshold: boolean;
  /**
   * Whether a rubric (competencies + anchors) applies to this kind. For a
   * human review it is the reviewer's checklist, shown on the decision queue.
   */
  supportsCriteria: boolean;
  /**
   * Who decides whether a candidate moves on (D1). A scored round is decided by
   * the system against its threshold — and even then only ever holds, never
   * rejects; a human review is decided by a person.
   */
  decidedBy: 'system' | 'person';
}

export const ROUND_KIND_META: Record<RoundKind, RoundKindMeta> = {
  mcq: {
    kind: 'mcq',
    label: 'MCQ exam',
    blurb: 'Multiple-choice questions from one of your exams. Scored automatically.',
    icon: ClipboardList,
    tone: 'electric',
    needsExam: true,
    needsThreshold: true,
    supportsCriteria: true,
    decidedBy: 'system',
  },
  coding: {
    kind: 'coding',
    label: 'Coding test',
    blurb: 'Programming problems from an exam, run against test cases.',
    icon: Code2,
    tone: 'lavender',
    needsExam: true,
    needsThreshold: true,
    supportsCriteria: true,
    decidedBy: 'system',
  },
  ai_interview: {
    kind: 'ai_interview',
    label: 'AI interview',
    blurb:
      'A live voice interview. The competencies you pick here are what it probes — and only those.',
    icon: Video,
    tone: 'pink',
    needsExam: false,
    needsThreshold: true,
    supportsCriteria: true,
    decidedBy: 'system',
  },
  human_review: {
    kind: 'human_review',
    label: 'Human review',
    blurb: 'A deliberate stop. Nothing advances until someone on your team looks.',
    icon: Users,
    tone: 'amber',
    needsExam: false,
    needsThreshold: false,
    supportsCriteria: true,
    decidedBy: 'person',
  },
};

export const ROUND_KIND_ORDER: RoundKind[] = ['mcq', 'coding', 'ai_interview', 'human_review'];

