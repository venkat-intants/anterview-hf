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
// The conversation (turns AND what happened to each proposal) lives here,
// which stays mounted while the panel is closed, so closing and reopening
// keeps both. The dialog itself mounts only while open, which is what makes
// its focus trap, Escape handling and focus return to the launcher exact.
//
// Deliberately NOT streaming. The agent loops over tool calls, so most of the
// wait is tool execution, not token generation; a streaming cursor would sit
// still through the interesting part and then dump the answer at once. A
// progress line naming the tools is more honest about what it is doing.

import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AGENT_MESSAGE_MAX_CHARS,
  askAgent,
  getAgentStatus,
  type AgentChatResponse,
  type CitationState,
  type HistoryTurn,
  type Proposal,
} from '@/api/agent';
import { GlassCard } from '@/design/components/primitives';
import { Eye, Lock, Plus, Send, Sparkles, X } from '@/design/components/icons';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { errorMessage } from '@/lib/errorMessage';
import AnswerText from './AnswerText';
import CitationChips from './CitationChips';
import EvidenceBanner from './EvidenceBanner';
import ProposalCard, { type CommitOutcome } from './ProposalCard';

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
  /** When the reply arrived in this browser; proposal ages count from here. */
  receivedAt?: number;
  /**
   * True for a request that failed and for the error shown in its place. Both
   * stay on screen and both stay OUT of the history replayed to the server: a
   * message the server refused once (too long, say) would otherwise be resent
   * with every later message and refused every time (audit CP-F2).
   */
  failed?: boolean;
  /** Drafts dropped because the same request was already approved earlier. */
  repeatsDropped?: string[];
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

/**
 * Explanation shown when a run ended early, so a short answer is not a mystery.
 * Only for the stop reasons whose reply does not already say it: on llm_error
 * and no_llm the reply IS the explanation, and a note repeating it underneath
 * said the same thing twice (audit CP-L3).
 */
const STOP_NOTE: Partial<Record<AgentChatResponse['stop_reason'], string>> = {
  max_steps: 'I hit my step limit - try asking something more specific.',
  token_budget: 'That needed more context than I can hold at once. Try narrowing it.',
};

/** Show the character count once the message is this close to the cap. */
const COUNTER_FROM = Math.floor(AGENT_MESSAGE_MAX_CHARS * 0.8);

/** Same request, same body: a re-draft of something already approved. */
function commitKey(p: Proposal): string {
  return `${p.commit.method} ${p.commit.path} ${JSON.stringify(p.commit.body)}`;
}

/**
 * The history line for one assistant turn. The model sees only text, so a turn
 * whose drafts were approved carries a line saying so; otherwise a follow-up
 * ("who else should I interview?") re-drafts the same invite as a new card
 * (audit CP-F4). The note goes at the end and the reply is shortened to make
 * room, so the server's per-turn cap can never cut the note off.
 */
function historyText(turn: Turn, outcomes: Record<string, CommitOutcome>): string {
  const approved = (turn.proposals ?? [])
    .filter((p) => outcomes[p.id]?.state === 'done')
    .map((p) => p.title);
  if (approved.length === 0) return turn.text.slice(0, AGENT_MESSAGE_MAX_CHARS);
  const note = `\n[Already approved by the user in this conversation: ${approved.join('; ')}]`.slice(
    0,
    AGENT_MESSAGE_MAX_CHARS,
  );
  return turn.text.slice(0, AGENT_MESSAGE_MAX_CHARS - note.length) + note;
}

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
  // Keyed by proposal id. Lives beside `turns` rather than inside each card,
  // so a committed proposal stays committed across close and reopen.
  const [outcomes, setOutcomes] = useState<Record<string, CommitOutcome>>({});
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  // Read out by a polite live region: the reply lands outside the busy line's
  // own live region, so without this a screen reader heard nothing arrive.
  const [announcement, setAnnouncement] = useState('');
  // Bumped by "New chat", so an answer still in flight for the conversation
  // that was cleared cannot land in the new one.
  const conversation = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  // The latest conversation, for reading after an await: a proposal approved
  // while the next answer was on its way must still count as approved.
  const latest = useRef({ turns, outcomes });
  latest.current = { turns, outcomes };

  // Errors are not rendered on purpose: the launcher only opens this panel
  // after the same query reported the assistant enabled, so a failure here
  // means the copy below falls back to its generic forms, nothing more.
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
    // Back to the composer once an answer lands. Sending from a suggestion
    // button removes that button, and sending from "Ask" disables it, and
    // either way focus would otherwise fall to <body> (audit CP-F6). Only
    // when focus is lost or still in the composer, though: a user who tabbed
    // to a proposal button or a source chip while waiting keeps their place.
    if (!open || busy) return;
    const active = document.activeElement;
    if (!active || active === document.body || formRef.current?.contains(active)) {
      inputRef.current?.focus();
    }
  }, [busy, open]);

  function recordOutcome(proposalId: string, outcome: CommitOutcome): void {
    setOutcomes((prev) => ({ ...prev, [proposalId]: outcome }));
  }

  async function send(message: string): Promise<void> {
    const text = message.trim();
    if (!text || busy) return;
    if (text.length > AGENT_MESSAGE_MAX_CHARS) return;

    const generation = conversation.current;
    // History excludes the turn being sent (the server appends it) and every
    // turn of a failed exchange.
    const history: HistoryTurn[] = turns
      .filter((t) => !t.failed)
      .map((t) => ({
        role: t.role,
        text: t.role === 'assistant' ? historyText(t, outcomes) : t.text,
      }));
    const userIndex = turns.length;
    setTurns((prev) => [...prev, { role: 'user', text }]);
    setInput('');
    setBusy(true);
    setAnnouncement('');

    try {
      const res = await askAgent(text, history);
      if (generation !== conversation.current) return;
      // A draft whose exact request was already approved in this
      // conversation is not shown again as a fresh, live card.
      const approvedKeys = new Set(
        latest.current.turns
          .flatMap((t) => t.proposals ?? [])
          .filter((p) => latest.current.outcomes[p.id]?.state === 'done')
          .map(commitKey),
      );
      const fresh = res.proposals.filter((p) => !approvedKeys.has(commitKey(p)));
      const repeats = res.proposals.filter((p) => approvedKeys.has(commitKey(p)));
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: res.reply,
          proposals: fresh,
          citations: res.citations,
          toolsUsed: res.tools_used,
          stopReason: res.stop_reason,
          citationState: res.citation_state,
          receivedAt: Date.now(),
          repeatsDropped: repeats.map((p) => p.title),
        },
      ]);
      setAnnouncement(
        fresh.length > 0
          ? `Assistant replied with ${fresh.length} draft${fresh.length === 1 ? '' : 's'} to review.`
          : 'Assistant replied.',
      );
    } catch (err) {
      if (generation !== conversation.current) return;
      const shown = errorMessage(err, 'Could not get an answer.');
      setTurns((prev) => [
        ...prev.map((t, i) => (i === userIndex ? { ...t, failed: true } : t)),
        { role: 'assistant', text: shown, failed: true },
      ]);
      setAnnouncement(shown);
    } finally {
      if (generation === conversation.current) setBusy(false);
    }
  }

  function newChat(): void {
    conversation.current += 1;
    setTurns([]);
    setOutcomes({});
    setInput('');
    setBusy(false);
    setAnnouncement('Started a new chat.');
    inputRef.current?.focus();
  }

  if (!open) return null;

  const unavailable = status !== undefined && !status.enabled;
  const suggestions = SUGGESTIONS[status?.console ?? 'hr_manager'] ?? [];
  const scope = (status ? SCOPE[status.console] : undefined) ?? SCOPE_FALLBACK;
  const showCounter = input.length >= COUNTER_FROM;

  return (
    <CopilotDialog onClose={onClose} initialFocus={inputRef}>
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
        <div className="flex flex-none items-center gap-1">
          {/* A way out of a conversation that has gone wrong, short of
              reloading the page (audit CP-F9). */}
          <button
            type="button"
            onClick={newChat}
            disabled={turns.length === 0 && !busy}
            className="inline-flex h-10 items-center gap-1.5 rounded-full px-3 text-[13px] font-semibold text-muted-foreground transition hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus size={16} aria-hidden="true" />
            New chat
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            aria-label="Close assistant"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
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

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

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
              {turn.failed && (
                <span className="text-[12px] text-muted-foreground">
                  Not sent to the assistant. It will not be included as context.
                </span>
              )}
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

              {turn.failed ? (
                <p className="text-sm font-medium text-[var(--ui-danger)]">{turn.text}</p>
              ) : (
                <AnswerText text={turn.text} citations={turn.citations ?? []} />
              )}

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

              {(turn.repeatsDropped ?? []).length > 0 && (
                <p className="text-[12.5px] text-muted-foreground">
                  Already approved earlier in this chat, so not drafted again:{' '}
                  {(turn.repeatsDropped ?? []).join(', ')}.
                </p>
              )}

              {turn.proposals?.map((p) => (
                <ProposalCard
                  key={p.id}
                  proposal={p}
                  onCommitted={onCommitted}
                  citationState={turn.citationState}
                  outcome={outcomes[p.id]}
                  onOutcome={recordOutcome}
                  receivedAt={turn.receivedAt}
                />
              ))}
            </div>
          ),
        )}

        {busy && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <span
              aria-hidden="true"
              className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]"
            />
            Looking through your data…
          </p>
        )}
      </div>

      <form
        ref={formRef}
        className="border-t border-border bg-card p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <div className="flex gap-2">
          {/* readOnly, not disabled, while waiting: a disabled control drops
              keyboard focus to <body>, where Escape and Tab stop working. */}
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            readOnly={busy}
            aria-disabled={busy || unavailable}
            disabled={unavailable}
            maxLength={AGENT_MESSAGE_MAX_CHARS}
            placeholder={unavailable ? 'Assistant unavailable' : 'Ask about your pipeline…'}
            className="min-h-11 min-w-0 flex-1 rounded-[10px] border border-input bg-card px-3.5 py-2 text-sm text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] read-only:bg-[var(--ui-inset-soft)] disabled:cursor-not-allowed disabled:bg-[var(--ui-inset-soft)] disabled:opacity-60"
            aria-label="Message"
            aria-describedby={showCounter ? 'copilot-message-count' : undefined}
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
        </div>
        {showCounter && (
          <p id="copilot-message-count" className="mt-1.5 text-right text-[12px] text-muted-foreground">
            {input.length.toLocaleString('en-IN')}/{AGENT_MESSAGE_MAX_CHARS.toLocaleString('en-IN')}{' '}
            characters
          </p>
        )}
      </form>
    </CopilotDialog>
  );
}

/**
 * The modal frame. Mounted only while open, so `useDialogFocus` gives it a
 * real trap: Tab stays inside, Escape closes from anywhere (not only while
 * focus happens to be inside), and focus goes back to the launcher on close
 * (audit CP-F6, CP-F7). The page behind does not scroll while it is open
 * (CP-L8). The scrim is mouse-only on purpose: keyboard users have Escape and
 * the Close button, and a full-height invisible button was the first Tab stop.
 */
function CopilotDialog({
  onClose,
  initialFocus,
  children,
}: {
  onClose: () => void;
  initialFocus: RefObject<HTMLInputElement>;
  children: ReactNode;
}): JSX.Element {
  const panelRef = useDialogFocus<HTMLElement>(onClose);

  useEffect(() => {
    // After the hook's own first-focusable focus: the composer, never a
    // ProposalCard's button, so a stray Enter cannot commit anything.
    initialFocus.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const { body } = document;
    const previous = body.style.overflow;
    body.style.overflow = 'hidden';
    return () => {
      body.style.overflow = previous;
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div aria-hidden="true" className="flex-1 bg-foreground/40" onClick={onClose} />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Assistant"
        tabIndex={-1}
        className="flex h-full w-full max-w-[480px] flex-col border-l border-border bg-card shadow-elevated focus:outline-none"
      >
        {children}
      </aside>
    </div>
  );
}
