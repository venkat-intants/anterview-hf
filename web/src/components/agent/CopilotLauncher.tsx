// CopilotLauncher — floating button + the panel it opens.
//
// One mount per console gives every page in that console an assistant without
// each page wiring its own state. Self-hiding when the backend reports the
// assistant unavailable (no model key, or AGENTS_ENABLED=false), so a
// deployment without Gemini simply has no button rather than a button that
// apologises.

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';
import { getAgentStatus } from '@/api/agent';
import { Button } from '@/components/ui/button';
import { Sparkles } from '@/design/components/icons';
import CopilotPanel from './CopilotPanel';

interface CopilotLauncherProps {
  /** Refetch console data after a proposal is committed. */
  onCommitted?: () => void;
}

export default function CopilotLauncher({ onCommitted }: CopilotLauncherProps): JSX.Element | null {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    // Any navigation closes the panel. Its source chips are links and the
    // panel is a full-screen overlay, so following one used to change the
    // page UNDER the panel, which on a phone (where the panel covers
    // everything) looked like the click did nothing (audit CP-F10). The
    // conversation survives closing, so nothing is lost.
    setOpen(false);
  }, [location.key]);

  const { data: status } = useQuery({
    queryKey: ['agent-status'],
    queryFn: getAgentStatus,
    staleTime: 5 * 60_000,
    // A 403 means this role has no copilot — a permanent answer, so retrying
    // would just be noise on every console load. A failed load is not shown
    // on purpose: the launcher is an optional affordance, and no button is
    // the honest state for an assistant whose availability is unknown.
    retry: false,
  });

  if (!status?.enabled) return null;

  return (
    <>
      {/* A floating control over page content, so it is the one primary
          element on the layer above the page: 48px, the primary fill, tucked
          into the corner (icon-only below `sm` so it does not cover page
          actions on a phone; the label returns from `sm` up). Focus comes
          back here when the panel closes (the Radix Sheet in CopilotPanel). */}
      <Button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open assistant"
        aria-haspopup="dialog"
        aria-expanded={open}
        className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-4 z-40 h-12 min-w-12 px-3.5 shadow-elevated sm:bottom-6 sm:right-6 sm:px-5 [&_svg]:size-[18px]"
      >
        <Sparkles size={18} aria-hidden="true" />
        <span className="hidden sm:inline">Ask assistant</span>
      </Button>
      <CopilotPanel open={open} onClose={() => setOpen(false)} onCommitted={onCommitted} />
    </>
  );
}
