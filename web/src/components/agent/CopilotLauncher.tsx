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
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open assistant"
        className="fixed bottom-6 right-6 z-40 px-4 py-3 rounded-full shadow-lg font-medium text-sm transition hover:scale-105"
        style={{ background: 'var(--accent)', color: '#08131f' }}
      >
        Ask assistant
      </button>
      <CopilotPanel open={open} onClose={() => setOpen(false)} onCommitted={onCommitted} />
    </>
  );
}
