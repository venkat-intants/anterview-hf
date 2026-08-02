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
  type HistoryTurn,
  type Proposal,
} from '@/api/agent';
import { GlassCard } from '@/design/components/primitives';
import ProposalCard from './ProposalCard';

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  proposals?: Proposal[];
  citations?: AgentChatResponse['citations'];
  toolsUsed?: AgentChatResponse['tools_used'];
  stopReason?: AgentChatResponse['stop_reason'];
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

  const { data: status } = useQuery({
    queryKey: ['agent-status'],
    queryFn: getAgentStatus,
    // The answer only changes on redeploy; refetching per open is waste.
    staleTime: 5 * 60_000,
    enabled: open,
  });

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [turns, busy]);

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
        },
      ]);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          text:
            err instanceof Error
              ? `Something went wrong: ${err.message}`
              : 'Something went wrong.',
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  const unavailable = status && !status.enabled;
  const suggestions = SUGGESTIONS[status?.console ?? 'hr_manager'] ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label="Assistant">
      <button
        type="button"
        aria-label="Close assistant"
        className="flex-1 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />

      <aside className="w-full max-w-[460px] h-full flex flex-col bg-[var(--surface,#0b1622)] border-l border-white/10">
        <header className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
          <div>
            <h2 className="font-semibold">Assistant</h2>
            {/* Capability line, always visible. The user should never be
                unsure whether this thing can act on its own. */}
            <p className="text-xs opacity-60">Reads your data · drafts actions · you approve</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-2 py-1 rounded hover:bg-white/10 transition"
            aria-label="Close"
          >
            ✕
          </button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {unavailable && (
            <GlassCard className="p-3 text-sm opacity-80">
              {status?.note ?? 'The assistant is unavailable in this deployment.'}
            </GlassCard>
          )}

          {turns.length === 0 && !unavailable && (
            <div className="space-y-3">
              <p className="text-sm opacity-70">
                I can read your applicants, exams, scorecards and analytics, and draft
                invitations or shortlists for you to approve. I can&apos;t change anything myself.
              </p>
              <div className="flex flex-col gap-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void send(s)}
                    className="text-left text-sm px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 transition"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn, i) => (
            <div key={i} className={turn.role === 'user' ? 'flex justify-end' : ''}>
              {turn.role === 'user' ? (
                <div
                  className="max-w-[85%] px-3 py-2 rounded-2xl rounded-br-sm text-sm"
                  style={{ background: 'var(--accent)', color: '#08131f' }}
                >
                  {turn.text}
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-sm whitespace-pre-wrap leading-relaxed">{turn.text}</p>

                  {turn.stopReason && STOP_NOTE[turn.stopReason] && (
                    <p className="text-xs opacity-50 italic">{STOP_NOTE[turn.stopReason]}</p>
                  )}

                  {turn.citations && turn.citations.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {turn.citations.map((c) => (
                        <a
                          key={`${c.kind}:${c.id}`}
                          href={c.href ?? '#'}
                          className="text-xs px-2 py-0.5 rounded-full bg-white/5 hover:bg-white/10 transition"
                          title={c.kind}
                        >
                          {c.label}
                        </a>
                      ))}
                    </div>
                  )}

                  {turn.proposals?.map((p) => (
                    <ProposalCard key={p.id} proposal={p} onCommitted={onCommitted} />
                  ))}
                </div>
              )}
            </div>
          ))}

          {busy && (
            <p className="text-sm opacity-50" aria-live="polite">
              Looking through your data…
            </p>
          )}
        </div>

        <form
          className="p-3 border-t border-white/10 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy || unavailable}
            placeholder={unavailable ? 'Assistant unavailable' : 'Ask about your pipeline…'}
            className="flex-1 px-3 py-2 rounded-lg bg-white/5 text-sm outline-none focus:ring-1 focus:ring-[var(--accent)] disabled:opacity-50"
            aria-label="Message"
          />
          <button
            type="submit"
            disabled={busy || !input.trim() || unavailable}
            className="px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition"
            style={{ background: 'var(--accent)', color: '#08131f' }}
          >
            Ask
          </button>
        </form>
      </aside>
    </div>
  );
}
