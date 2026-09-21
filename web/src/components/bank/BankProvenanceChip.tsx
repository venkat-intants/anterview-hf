// BankProvenanceChip — PH4-D1. "From bank · vN" on an exam question that was
// copied from a question bank, so HR can see on the exam's own list which
// questions are reused and at which version. Renders nothing for a question
// written directly in the exam.
//
// Shared by the MCQ list (ExamEditor.tsx) and the coding list
// (CodingAuthoringSection.tsx). The fields come from QuestionOut /
// CodingQuestionOut; until d8d714c neither response carried them, so the chip
// could never render — which no test caught, because nothing rendered it.

import { StatusTag } from '@/design/components/primitives';

export default function BankProvenanceChip({
  rootId,
  version,
}: {
  rootId?: string | null;
  version?: number | null;
}): JSX.Element | null {
  if (!rootId) return null;
  return (
    <StatusTag tone="electric" className="text-[10px]">
      From bank{version != null ? ` · v${version}` : ''}
    </StatusTag>
  );
}
