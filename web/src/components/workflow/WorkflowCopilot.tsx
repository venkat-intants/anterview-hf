// WorkflowCopilot — D6. Design the hiring process by describing it.
//
// A second copilot surface rather than a reuse of the global CopilotPanel, for
// one structural reason: the builder needs the PROPOSALS, not just the reply.
// A drafted workflow is previewed as ghost rounds on the canvas beside this
// panel (D7), so the proposals have to reach the page that owns the canvas.
// The global panel is mounted in AppShell and hands its proposals to nobody.
//
// Everything else is shared: the same /agent/chat endpoint, the same agent
// runtime, the same commit model. The copilot cannot create a workflow. It
// returns a proposal describing the request, this panel hands it up, and the
// user presses a button on the canvas — at which point the browser fires that
// request with the user's own credentials against the ordinary HR endpoint.
//
// The two-step framing is stated in the UI on purpose. Committing a proposal
// produces a DRAFT, and a draft is invisible to candidates until the user
// publishes it separately. An HR manager should never be able to conclude from
// this panel that a conversation put a real process in front of real people.

import { useEffect, useRef, useState } from 'react';
import { askAgent, type Proposal } from '@/api/agent';
import { AlertTriangle, Loader2, Send, Sparkles } from '@/design/components/icons';
import { cn } from '@/lib/utils';

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  proposals?: Proposal[];
  stopReason?: string;
}

interface Props {
  requisitionId: string;
  jobTitle: string;
  /** True when the current version can still be changed. */
  editable: boolean;
  /** Whether this opening already has rounds — changes what to suggest. */
  hasRounds: boolean;
  /** Lifts drafted proposals to the builder, which previews them on the canvas. */
  onProposals: (proposals: Proposal[]) => void;
}

/** Openers, split by what the canvas currently holds. */
function suggestionsFor(hasRounds: boolean, jobTitle: string): string[] {
  return hasRounds
    ? [
        'Is anything important not being assessed?',
        'Is this process longer than it needs to be?',
        'Add a round that checks how they explain their work',
      ]
    : [
        `Design a hiring process for a ${jobTitle}`,
        'Something short — I only want one written test and a conversation',
        'What would you assess for this role, and in what order?',
      ];
}

export default function WorkflowCopilot({
  requisitionId,
  jobTitle,
  editable,
  hasRounds,
  onProposals,
}: Props): JSX.Element {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Feature-detected rather than called outright. `scrollIntoView` is
    // optional DOM: jsdom omits it entirely, and older embedded webviews
    // implement it partially. Auto-scrolling is a convenience, so it must
    // never be the reason a message fails to render.
    const end = endRef.current;
    if (typeof end?.scrollIntoView === 'function') {
      end.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [turns, busy]);

  async function send(message: string): Promise<void> {
    const text = message.trim();
    if (!text || busy) return;
    setInput('');
    setTurns((prev) => [...prev, { role: 'user', text }]);
    setBusy(true);
    try {
      const res = await askAgent(
        text,
        // Only the prose is replayed. Proposals are a rendering concern and
        // resending them would spend tokens re-describing drafts the user has
        // already seen — and possibly already acted on.
        turns.map((t) => ({ role: t.role, text: t.text })),
        { surface: 'workflow_builder', surfaceContext: { requisition_id: requisitionId } },
      );
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: res.reply,
          proposals: res.proposals,
          stopReason: res.stop_reason,
        },
      ]);
      if (res.proposals.length > 0) onProposals(res.proposals);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          text:
            err instanceof Error
              ? `I could not answer that: ${err.message}`
              : 'I could not reach the assistant. Try again in a moment.',
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-2 border-b border-white/[0.07] pb-3">
        <Sparkles className="h-4 w-4 text-[var(--accent)]" aria-hidden="true" />
        <span className="text-[13px] font-medium text-white">Design assistant</span>
        <span className="ml-auto text-[11px] text-[#70757c]">reads &amp; drafts only</span>
      </header>

      <div className="flex-1 overflow-y-auto py-3">
        {turns.length === 0 ? (
          <div>
            <p className="text-[12.5px] leading-relaxed text-[#888b91]">
              Describe the process you want and I&rsquo;ll lay it out on the canvas. I can
              read this opening&rsquo;s role model and your exams. I can&rsquo;t create
              anything — you approve every change, and it stays a draft until you publish.
            </p>
            <div className="mt-3 flex flex-col gap-1.5">
              {suggestionsFor(hasRounds, jobTitle).map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={!editable}
                  onClick={() => void send(s)}
                  className="rounded-[10px] border border-white/[0.08] px-3 py-2 text-left text-[12.5px] text-[#d5d7da] hover:border-[var(--accent)]/50 hover:text-white disabled:opacity-40"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {turns.map((turn, i) => (
              <div key={i}>
                {turn.role === 'user' ? (
                  <div className="ml-auto max-w-[85%] rounded-[12px] bg-white/[0.07] px-3 py-2 text-[12.5px] text-white">
                    {turn.text}
                  </div>
                ) : (
                  <div className="max-w-[95%] whitespace-pre-wrap text-[12.5px] leading-relaxed text-[#d5d7da]">
                    {turn.text}
                  </div>
                )}

                {/* Proposals are NOT committed here. They render on the canvas
                    as a preview; this line only says one arrived, so the two
                    surfaces cannot disagree about what is pending. */}
                {turn.proposals && turn.proposals.length > 0 ? (
                  <div className="mt-2 flex items-center gap-1.5 rounded-[10px] border border-[var(--accent)]/35 bg-[var(--accent)]/[0.07] px-3 py-2 text-[12px] text-[#d5d7da]">
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
                    {turn.proposals.length === 1
                      ? 'Previewed on the canvas — review it there.'
                      : `${turn.proposals.length} changes previewed on the canvas.`}
                  </div>
                ) : null}

                {turn.stopReason && turn.stopReason !== 'completed' ? (
                  <div className="mt-1.5 flex items-start gap-1.5 text-[11.5px] text-[#ffb764]">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                    {turn.stopReason === 'no_llm'
                      ? 'No language model is configured for this deployment.'
                      : turn.stopReason === 'max_steps'
                        ? 'I stopped early — try a narrower request.'
                        : 'That answer may be incomplete.'}
                  </div>
                ) : null}
              </div>
            ))}
            {busy ? (
              <div className="flex items-center gap-2 text-[12px] text-[#888b91]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                Reading the role and your exams…
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex items-center gap-2 border-t border-white/[0.07] pt-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={!editable || busy}
          aria-label="Ask the design assistant"
          placeholder={
            editable ? 'Describe the process you want…' : 'Published — clone to edit'
          }
          className="min-w-0 flex-1 rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[13px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!editable || busy || !input.trim()}
          aria-label="Send"
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-white text-black transition-opacity',
            (!editable || busy || !input.trim()) && 'opacity-40',
          )}
        >
          <Send className="h-4 w-4" aria-hidden="true" />
        </button>
      </form>
    </div>
  );
}
