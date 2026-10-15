// CopilotPanel — the console assistant, as a slide-in side panel.
//
// Mounted once per console (HR, super-admin, platform, analytics); the backend
// picks WHICH copilot from the caller's role, so this component is
// console-agnostic and there is no role logic here to drift out of sync.
//
// What it renders per assistant turn:
//   - the reply
//   - source chips (every record the agent read, deep-linked)
//   - a ProposalCard per drafted action, which is where anything actually
//     happens — and only on a click
//
// Deliberately NOT streaming. The agent loops over tool calls, so most of the
// wait is tool execution, not token generation; a streaming cursor would sit
// still through the interesting part and then dump the answer at once. A
// progress line naming the tools is more honest about what it is doing.

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  askAgent,
  getAgentStatus,
  type AgentChatResponse,
  type CitationState,
  type HistoryTurn,
  type Proposal,
} from '@/api/agent';
import { GlassCard } from '@/design/components/primitives';
import { Eye, Lock, Send, Sparkles, X } from '@/design/components/icons';
import AnswerText from './AnswerText';
import CitationChips from './CitationChips';
import EvidenceBanner from './EvidenceBanner';
import ProposalCard from './ProposalCard';

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  proposals?: Proposal[];
  citations?: AgentChatResponse['citations'];
  toolsUsed?: AgentChatResponse['tools_used'];
  stopReason?: AgentChatResponse['stop_reason'];
  /**
   * Undefined for a user turn and for an error message that never reached the
   * server — the banner only speaks about an actual answer, computed by the
   * server, never assumed here from whether `citations` happens to be empty.
   */
  citationState?: CitationState;
}

/** Console-specific starter prompts. Keyed by the backend's console string. */
const SUGGESTIONS: Record<string, string[]> = {
  hr_manager: [
    'Who should I interview next, and why?',
    'Which candidates have signals that disagree?',
    'Where is my pipeline losing people?',
    'Are any of my exam questions broken?',
  ],
  super_admin: [
    'Where are candidates getting stuck?',
    'Which roles are not converting to interviews?',
    'How is each of my HR managers doing on throughput?',
  ],
  platform_owner: [
    'Give me a platform health summary.',
    'Which companies are most active?',
    'How are scores trending across the platform?',
  ],
  admin: [
    'How are interview scores trending this month?',
    'What is the language mix of interviews?',
  ],
};

/**
 * What each console's assistant can read, in words. This mirrors the data-class
 * rules in CLAUDE.md ("a console cannot read outside its remit") — it is the
 * user-facing statement of a server-side limit, not a control: the server
 * decides what is readable, this only tells the user so.
 */
const SCOPE: Record<string, string> = {
  hr_manager: "Your company's applicants, exams, interviews, scorecards and pipeline analytics.",
  super_admin:
    "Your company's hiring operations, openings and staff. It does not list named candidates.",
  platform_owner: 'Platform-wide totals and trends only. No individual candidates.',
  admin: 'Platform-wide aggregate analytics only. No individual candidates.',
};
const SCOPE_FALLBACK = 'Only the records your role is allowed to see.';

/** Explanation shown when a run ended early, so a short answer is not a mystery. */
const STOP_NOTE: Partial<Record<AgentChatResponse['stop_reason'], string>> = {
  max_steps: 'I hit my step limit — try asking something more specific.',
  token_budget: 'That needed more context than I can hold at once. Try narrowing it.',
  llm_error: 'The language model was unreachable. Your data is fine — try again.',
  no_llm: 'No language model is configured for this deployment.',
};

interface CopilotPanelProps {
  open: boolean;
  onClose: () => void;
  /** Refetch lists after a proposal is committed. */
  onCommitted?: () => void;
}

export default function CopilotPanel({
  open,
  onClose,
  onCommitted,
}: CopilotPanelProps): JSX.Element | null {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: status } = useQuery({
    queryKey: ['agent-status'],
    queryFn: getAgentStatus,
    // The answer only changes on redeploy; refetching per open is waste.
    staleTime: 5 * 60_000,
    enabled: open,
  });

  useEffect(() => {
    // Feature-detected rather than called outright, same reason as the
    // workflow copilot's auto-scroll: jsdom does not implement `scrollTo` on
    // an element at all, and auto-scrolling is a convenience that must never
    // be the reason a turn fails to render.
    const el = scrollRef.current;
    if (typeof el?.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [turns, busy]);

  useEffect(() => {
    // The composer takes focus when the panel opens. Never a ProposalCard's
    // button: a stray Enter must not be able to commit anything.
    if (open) inputRef.current?.focus();
  }, [open]);

  async function send(message: string): Promise<void> {
    const text = message.trim();
    if (!text || busy) return;

    // History excludes the turn being sent — the server appends it.
    const history: HistoryTurn[] = turns.map((t) => ({ role: t.role, text: t.text }));
    setTurns((prev) => [...prev, { role: 'user', text }]);
    setInput('');
    setBusy(true);

    try {
      const res = await askAgent(text, history);
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: res.reply,
          proposals: res.proposals,
          citations: res.citations,
          toolsUsed: res.tools_used,
          stopReason: res.stop_reason,
          citationState: res.citation_state,
        },
      ]);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          text:
            err instanceof Error ? `Something went wrong: ${err.message}` : 'Something went wrong.',
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  const unavailable = status && !status.enabled;
  const suggestions = SUGGESTIONS[status?.console ?? 'hr_manager'] ?? [];
  const scope = (status ? SCOPE[status.console] : undefined) ?? SCOPE_FALLBACK;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="Assistant"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
    >
      <button
        type="button"
        aria-label="Close assistant"
        className="flex-1 bg-foreground/40"
        onClick={onClose}
      />

      <aside className="flex h-full w-full max-w-[480px] flex-col border-l border-border bg-card shadow-elevated">
        <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-[var(--ui-info-wash)] text-[var(--ui-info)]"
            >
              <Sparkles size={17} />
            </span>
            <div className="min-w-0">
              <h2 className="text-[17px] font-bold leading-tight text-foreground">Assistant</h2>
              {/* Capability line, always visible. The user should never be
                  unsure whether this thing can act on its own. */}
              <p className="text-[12.5px] text-muted-foreground">
                Reads your data · drafts actions · you approve
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 flex-none items-center justify-center rounded-full text-muted-foreground transition hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            aria-label="Close"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        {/* What this console's assistant can and cannot do, stated up front. */}
        <div className="space-y-1.5 border-b border-border bg-[var(--ui-inset-soft)] px-5 py-3 text-[12.5px] leading-relaxed">
          <p className="flex items-start gap-2 text-foreground">
            <Eye size={14} className="mt-0.5 flex-none text-[var(--ui-info)]" aria-hidden="true" />
            <span>
              <span className="font-semibold">Can see: </span>
              {scope}
            </span>
          </p>
          <p className="flex items-start gap-2 text-muted-foreground">
            <Lock size={14} className="mt-0.5 flex-none" aria-hidden="true" />
            <span>
              <span className="font-semibold text-foreground">Cannot: </span>
              change anything. Every action is a proposal you review and approve.
            </span>
          </p>
        </div>

        <div ref={scrollRef} className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
          {unavailable && (
            <GlassCard className="p-4 text-sm text-muted-foreground">
              {status?.note ?? 'The assistant is unavailable in this deployment.'}
            </GlassCard>
          )}

          {turns.length === 0 && !unavailable && (
            <div className="space-y-3">
              <p className="text-[15px] font-semibold text-foreground">How can I help?</p>
              <p className="text-sm leading-relaxed text-muted-foreground">
                I can read the records your role allows and draft actions for you to approve. I
                can&apos;t change anything myself.
              </p>
              <p className="pt-1 text-[12.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
                Try asking
              </p>
              <div className="flex flex-col gap-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void send(s)}
                    className="min-h-10 rounded-[12px] border border-input bg-card px-3.5 py-2 text-left text-sm text-foreground transition hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn, i) =>
            turn.role === 'user' ? (
              <div key={i} role="group" aria-label="You" className="flex flex-col items-end gap-1">
                <span className="text-[12px] font-semibold text-muted-foreground">You</span>
                <div className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-highlight px-3.5 py-2.5 text-sm text-highlight-foreground">
                  {turn.text}
                </div>
              </div>
            ) : (
              <div key={i} role="group" aria-label="Assistant" className="space-y-3">
                <div className="flex items-center gap-2 text-[12px] font-semibold text-muted-foreground">
                  <Sparkles size={13} className="text-[var(--ui-info)]" aria-hidden="true" />
                  Assistant
                </div>

                {turn.citationState !== undefined && (
                  <EvidenceBanner citationState={turn.citationState} />
                )}

                <AnswerText text={turn.text} citations={turn.citations ?? []} />

                {turn.stopReason && STOP_NOTE[turn.stopReason] && (
                  <p className="text-[12.5px] italic text-muted-foreground">
                    {STOP_NOTE[turn.stopReason]}
                  </p>
                )}

                {(turn.citations ?? []).length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-[12px] font-semibold text-muted-foreground">Sources</p>
                    <CitationChips citations={turn.citations ?? []} variant="strip" />
                  </div>
                )}

                {turn.proposals?.map((p) => (
                  <ProposalCard
                    key={p.id}
                    proposal={p}
                    onCommitted={onCommitted}
                    citationState={turn.citationState}
                  />
                ))}
              </div>
            ),
          )}

          {busy && (
            <p
              role="status"
              className="flex items-center gap-2 text-sm text-muted-foreground"
              aria-live="polite"
            >
              <span
                aria-hidden="true"
                className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]"
              />
              Looking through your data…
            </p>
          )}
        </div>

        <form
          className="flex gap-2 border-t border-border bg-card p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || unavailable}
            placeholder={unavailable ? 'Assistant unavailable' : 'Ask about your pipeline…'}
            className="min-h-11 min-w-0 flex-1 rounded-[10px] border border-input bg-card px-3.5 py-2 text-sm text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:bg-[var(--ui-inset-soft)] disabled:opacity-60"
            aria-label="Message"
          />
          <button
            type="submit"
            disabled={busy || !input.trim() || unavailable}
            aria-busy={busy}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-full bg-primary px-5 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Send size={15} aria-hidden="true" />
            Ask
          </button>
        </form>
      </aside>
    </div>
  );
}
