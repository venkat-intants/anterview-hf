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
  /** Whether a rubric (competencies + anchors) applies to this kind. */
  supportsCriteria: boolean;
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
  },
  human_review: {
    kind: 'human_review',
    label: 'Human review',
    blurb: 'A deliberate stop. Nothing advances until someone on your team looks.',
    icon: Users,
    tone: 'amber',
    needsExam: false,
    needsThreshold: false,
    supportsCriteria: false,
  },
};

export const ROUND_KIND_ORDER: RoundKind[] = ['mcq', 'coding', 'ai_interview', 'human_review'];

/**
 * Starting points — the "standardised template" half of the two ways in (D4).
 *
 * These are client-side presets, not server objects: each one is just a
 * sequence of addRound calls, so a template produces exactly the workflow HR
 * would have built by hand and stays fully editable afterwards. Nothing about a
 * templated workflow is special once it exists, which is deliberate — a
 * template that could not be edited would be a straitjacket, and one that were
 * stored separately would drift from the rounds it created.
 *
 * Thresholds here are conservative defaults, not recommendations. HR overrides
 * them in the inspector; the point of the preset is the SHAPE.
 */
export interface WorkflowTemplate {
  key: string;
  name: string;
  description: string;
  rounds: { title: string; kind: RoundKind; pass_threshold: number | null }[];
}

export const TEMPLATES: WorkflowTemplate[] = [
  {
    key: 'screen-interview',
    name: 'Screen, then interview',
    description:
      'A knowledge check to size the field, then a conversation with whoever clears it.',
    rounds: [
      { title: 'Screening test', kind: 'mcq', pass_threshold: 60 },
      { title: 'AI interview', kind: 'ai_interview', pass_threshold: 60 },
    ],
  },
  {
    key: 'technical',
    name: 'Technical hire',
    description: 'Knowledge, then code, then a conversation, then your own read.',
    rounds: [
      { title: 'Fundamentals', kind: 'mcq', pass_threshold: 60 },
      { title: 'Coding test', kind: 'coding', pass_threshold: 50 },
      { title: 'AI interview', kind: 'ai_interview', pass_threshold: 60 },
      { title: 'Hiring manager review', kind: 'human_review', pass_threshold: null },
    ],
  },
  {
    key: 'interview-only',
    name: 'Interview only',
    description:
      'For roles where a written test tells you nothing. Straight to the conversation.',
    rounds: [
      { title: 'AI interview', kind: 'ai_interview', pass_threshold: 60 },
      { title: 'Final review', kind: 'human_review', pass_threshold: null },
    ],
  },
];
