// CopilotLauncher — floating button + the panel it opens.
//
// One mount per console gives every page in that console an assistant without
// each page wiring its own state. Self-hiding when the backend reports the
// assistant unavailable (no model key, or AGENTS_ENABLED=false), so a
// deployment without Gemini simply has no button rather than a button that
// apologises.

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAgentStatus } from '@/api/agent';
import { Sparkles } from '@/design/components/icons';
import CopilotPanel from './CopilotPanel';

interface CopilotLauncherProps {
  /** Refetch console data after a proposal is committed. */
  onCommitted?: () => void;
}

export default function CopilotLauncher({ onCommitted }: CopilotLauncherProps): JSX.Element | null {
  const [open, setOpen] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['agent-status'],
    queryFn: getAgentStatus,
    staleTime: 5 * 60_000,
    // A 403 means this role has no copilot — a permanent answer, so retrying
    // would just be noise on every console load.
    retry: false,
  });

  if (!status?.enabled) return null;

  return (
    <>
      {/* A floating control over page content, so it is the one primary
          element on the layer above the page: 48px, the primary fill, tucked
          into the corner (icon-only below `sm` so it does not cover page
          actions on a phone; the label returns from `sm` up). */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open assistant"
        className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-4 z-40 inline-flex h-12 min-w-12 items-center justify-center gap-2 rounded-full bg-primary px-3.5 text-sm font-semibold text-primary-foreground shadow-elevated transition hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:bottom-6 sm:right-6 sm:px-5"
      >
        <Sparkles size={18} aria-hidden="true" />
        <span className="hidden sm:inline">Ask assistant</span>
      </button>
      <CopilotPanel open={open} onClose={() => setOpen(false)} onCommitted={onCommitted} />
    </>
  );
}
