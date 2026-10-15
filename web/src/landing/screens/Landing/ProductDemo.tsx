import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, Pause, Play, Target } from 'lucide-react'
import { cn } from '../../lib/cn'
import { usePrefersReducedMotion } from '../../lib/usePrefersReducedMotion'
import { PortraitTile } from '../../components/PortraitTile'

/**
 * "An interview in 20 seconds": a self-running, looping, illustrative stage.
 *
 * It is drawn in code (no video file, no network request) and labelled
 * "Product demo" because every word in it is invented. One clock `t` (ms)
 * drives everything, so pausing freezes the whole stage and a reduced-motion
 * visitor simply gets the final composed frame with no timer at all.
 */

const QUESTION = 'Tell me about a time a number in your work looked wrong. What did you do?'
const ANSWER = 'A weekly report showed sales doubling overnight. I traced it to a duplicated join, fixed the query and showed my manager both versions.'
const FOLLOW_UP = 'What would you check first next time?'
const Q_WORDS = QUESTION.split(' ')
const A_WORDS = ANSWER.split(' ')
const F_WORDS = FOLLOW_UP.split(' ')

const CARD = {
  score: 78,
  bars: [
    { name: 'Communication', pct: 84 },
    { name: 'Problem solving', pct: 76 },
    { name: 'Role knowledge', pct: 72 },
  ],
  strengths: ['Clear, structured explanation', 'Checked the data before blaming it'],
  improve: 'Put a number on the impact of your fix',
}

// Timeline, ms.
const T_ANSWER = 3600
const T_FOLLOW = 10200
const T_SCORE = 13400
const T_END = T_SCORE + 8200
const TICK = 100

const clamp01 = (n: number) => Math.min(1, Math.max(0, n))
const prog = (t: number, start: number, dur: number) => clamp01((t - start) / dur)
const wordsShown = (t: number, start: number, per: number, total: number) => (t < start ? 0 : Math.min(total, Math.floor((t - start) / per) + 1))

type Phase = 'ask' | 'listen' | 'follow' | 'score'
const phaseAt = (t: number): Phase => (t < T_ANSWER ? 'ask' : t < T_FOLLOW ? 'listen' : t < T_SCORE ? 'follow' : 'score')

const PHASE_LABEL: Record<Phase, string> = {
  ask: 'Anna is asking',
  listen: 'You are answering',
  follow: 'Anna is following up',
  score: 'Scoring the interview',
}
const DONE_LABEL = 'Interview complete'

/** Words revealed one by one; the full text is always laid out (transparent
 *  remainder) so the bubble never changes size and nothing shifts. */
function Typed({ words, count }: { words: string[]; count: number }) {
  return (
    <>
      {words.slice(0, count).join(' ')}
      <span className="text-transparent">{count > 0 && count < words.length ? ' ' : ''}{words.slice(count).join(' ')}</span>
    </>
  )
}

function Bubble({ who, visible, mine, children }: { who: string; visible: boolean; mine?: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        'rounded-[12px] px-3 py-2 transition-opacity duration-500 motion-reduce:transition-none',
        mine ? 'bg-[var(--lp-surface-alt)] text-[var(--lp-text)]' : 'bg-[var(--ui-info-wash)] text-[var(--lp-text)]',
        visible ? 'opacity-100' : 'opacity-0',
      )}
    >
      <p className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--lp-text-faint)]">{who}</p>
      <p className="text-[12.5px] leading-[1.4] sm:text-[13px]">{children}</p>
    </div>
  )
}

function Wave({ active, running }: { active: boolean; running: boolean }) {
  return (
    <div className="flex h-4 items-end gap-[3px]" aria-hidden="true">
      {Array.from({ length: 9 }).map((_, i) => (
        <span
          key={i}
          className={cn('w-[3px] rounded-full bg-[var(--lp-accent)] transition-opacity', active ? 'animate-wave opacity-100' : 'opacity-30')}
          style={{
            height: `${45 + Math.abs(Math.sin(i * 0.9)) * 55}%`,
            animationDelay: `${(i % 5) * 0.1}s`,
            animationPlayState: running ? 'running' : 'paused',
            transform: active ? undefined : 'scaleY(0.4)',
            transformOrigin: 'bottom',
          }}
        />
      ))}
    </div>
  )
}

function Scorecard({ t }: { t: number }) {
  const ring = prog(t, T_SCORE + 600, 1400)
  const size = 76
  const stroke = 8
  const r = size / 2 - stroke / 2
  const circ = 2 * Math.PI * r
  return (
    <div className="flex h-full flex-col gap-2.5">
      <div className="flex items-center gap-3">
        <div className="relative flex-none" style={{ width: size, height: size }}>
          <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: 'rotate(-90deg)' }}>
            <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--lp-inset)" strokeWidth={stroke} />
            <circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke="var(--lp-accent)"
              strokeWidth={stroke}
              strokeLinecap="round"
              strokeDasharray={circ}
              strokeDashoffset={circ * (1 - (ring * CARD.score) / 100)}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center font-display text-[24px] font-bold leading-none tracking-[-0.04em] text-[var(--lp-text)]">
            {Math.round(ring * CARD.score)}
          </div>
        </div>
        <div>
          <p className="text-[13px] font-bold text-[var(--lp-text)]">Scorecard</p>
          <p className="text-[11px] leading-snug text-[var(--lp-text-muted)]">Junior data analyst, sample role</p>
        </div>
      </div>
      <ul className="flex flex-col gap-1.5">
        {CARD.bars.map((b, i) => (
          <li key={b.name} className="flex items-center gap-2 text-[11.5px] text-[var(--lp-text-soft)]">
            <span className="w-[104px] flex-none truncate sm:w-[112px]">{b.name}</span>
            <span className="h-1.5 flex-1 overflow-hidden rounded-pill bg-[var(--lp-inset)]">
              <span className="block h-full rounded-pill bg-[var(--lp-accent)]" style={{ width: `${b.pct * prog(t, T_SCORE + 1400 + i * 350, 900)}%` }} />
            </span>
          </li>
        ))}
      </ul>
      <ul className="flex flex-col gap-1">
        {CARD.strengths.map((s, i) => (
          <li
            key={s}
            className={cn(
              'flex items-start gap-1.5 text-[11.5px] leading-snug text-[var(--lp-text)] transition-opacity duration-500 motion-reduce:transition-none',
              t >= T_SCORE + 3200 + i * 600 ? 'opacity-100' : 'opacity-0',
            )}
          >
            <Check size={13} strokeWidth={2.6} className="mt-px flex-none text-[var(--ui-ok)]" aria-hidden="true" />
            {s}
          </li>
        ))}
        <li
          className={cn(
            'flex items-start gap-1.5 text-[11.5px] leading-snug text-[var(--lp-text)] transition-opacity duration-500 motion-reduce:transition-none',
            t >= T_SCORE + 5000 ? 'opacity-100' : 'opacity-0',
          )}
        >
          <Target size={13} strokeWidth={2.4} className="mt-px flex-none text-[var(--ui-info)]" aria-hidden="true" />
          <span>
            <span className="font-semibold">To work on: </span>
            {CARD.improve}
          </span>
        </li>
      </ul>
    </div>
  )
}

export function ProductDemo({ className }: { className?: string }) {
  const reduce = usePrefersReducedMotion()
  const [playing, setPlaying] = useState(true)
  const [inView, setInView] = useState(true)
  const [tabVisible, setTabVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
  const [clock, setClock] = useState(0)
  const root = useRef<HTMLElement>(null)

  // Pause while off-screen and while the tab is hidden: no timer, no repaint.
  useEffect(() => {
    const el = root.current
    if (!el || typeof IntersectionObserver === 'undefined') return undefined
    const io = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.2 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  useEffect(() => {
    const onVis = () => setTabVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  const running = playing && inView && tabVisible && !reduce
  useEffect(() => {
    if (!running) return undefined
    const id = window.setInterval(() => setClock((c) => (c + TICK >= T_END ? 0 : c + TICK)), TICK)
    return () => window.clearInterval(id)
  }, [running])

  const t = reduce ? T_END - 1 : clock
  const phase = phaseAt(t)
  const showCard = t >= T_SCORE
  const speaker = phase === 'ask' || phase === 'follow'

  return (
    <figure ref={root} aria-label="Product demo" className={cn('relative m-0', className)}>
      <div className="overflow-hidden rounded-card border border-[var(--lp-line)] bg-[var(--lp-surface)] shadow-elevated">
        {/* Window bar. The pause control is real UI, so it sits outside the aria-hidden stage. */}
        <div className="flex h-11 items-center justify-between gap-3 border-b border-[var(--lp-line)] px-3.5">
          <div className="flex items-center gap-2.5">
            <span className="flex gap-1.5" aria-hidden="true">
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--lp-inset)]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--lp-inset)]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--lp-inset)]" />
            </span>
            <span className="rounded-pill bg-[var(--ui-info-wash)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--ui-info)]">Product demo</span>
          </div>
          {!reduce && (
            <button
              type="button"
              onClick={() => setPlaying((p) => !p)}
              aria-pressed={!playing}
              className="inline-flex h-8 items-center gap-1.5 rounded-pill border border-[var(--lp-line)] px-3 text-xs font-medium text-[var(--lp-text-soft)] transition-colors hover:bg-[var(--lp-surface-alt)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--lp-accent)]"
            >
              {playing ? <Pause size={13} aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
              {playing ? 'Pause demo' : 'Play demo'}
            </button>
          )}
        </div>

        <div aria-hidden="true" className="grid aspect-[4/5] grid-rows-[auto_1fr] gap-3 p-3 sm:aspect-[27/21] sm:grid-cols-[0.78fr_1.22fr] sm:grid-rows-1 sm:gap-4 sm:p-4">
          {/* Interviewer */}
          <div className="flex items-center gap-3 sm:flex-col sm:items-stretch sm:gap-3">
            <div
              className={cn(
                'relative h-14 w-14 flex-none overflow-hidden rounded-[14px] bg-[var(--lp-surface-alt)] ring-2 ring-offset-2 ring-offset-[var(--lp-surface)] transition-shadow sm:h-auto sm:w-full sm:flex-1 sm:rounded-[16px]',
                speaker ? 'ring-[var(--lp-accent)]' : 'ring-transparent',
              )}
            >
              <PortraitTile
                src="/avatars/anna.jpg"
                alt="Anna, one of AntHire’s AI interviewers"
                name="Anna"
                width={216}
                height={270}
                eager
                imgClassName="object-[center_22%]"
                initialsClassName="text-2xl sm:text-5xl"
              />
              <span className="absolute inset-x-2 bottom-2 hidden items-center gap-1.5 rounded-pill bg-[image:var(--ui-grad-brand)] px-2.5 py-1 text-[11px] font-medium text-[var(--ui-brand-ink)] sm:inline-flex">
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--ui-brand-ink)]" />
                Anna · AI interviewer
              </span>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <span className="text-[11px] font-semibold text-[var(--lp-text)] sm:hidden">Anna · AI interviewer</span>
              <div className="flex items-center gap-2.5">
                <Wave active={phase !== 'score'} running={running} />
                <span className="truncate text-[11px] text-[var(--lp-text-muted)]">{t >= T_SCORE + 3000 ? DONE_LABEL : PHASE_LABEL[phase]}</span>
              </div>
            </div>
          </div>

          {/* Conversation, then scorecard, stacked in one fixed pane */}
          <div className="relative min-h-0 overflow-hidden rounded-[14px] border border-[var(--lp-line)] bg-[var(--lp-canvas)] p-3">
            <div className={cn('flex flex-col gap-2 transition-opacity duration-500 motion-reduce:transition-none', showCard ? 'opacity-0' : 'opacity-100')}>
              <Bubble who="Anna" visible={t >= 300}>
                <Typed words={Q_WORDS} count={wordsShown(t, 300, 140, Q_WORDS.length)} />
              </Bubble>
              <Bubble who="You" mine visible={t >= T_ANSWER}>
                <Typed words={A_WORDS} count={wordsShown(t, T_ANSWER, 230, A_WORDS.length)} />
              </Bubble>
              <Bubble who="Anna" visible={t >= T_FOLLOW}>
                <Typed words={F_WORDS} count={wordsShown(t, T_FOLLOW + 200, 170, F_WORDS.length)} />
              </Bubble>
            </div>
            <div className={cn('absolute inset-0 bg-[var(--lp-canvas)] p-3 transition-opacity duration-500 motion-reduce:transition-none', showCard ? 'opacity-100' : 'opacity-0')}>
              <Scorecard t={t} />
            </div>
          </div>
        </div>
      </div>
      <figcaption className="sr-only">
        Product demo, with invented content. An AI interviewer asks a question, the candidate answers out loud and the interviewer asks a follow-up. The interview then
        ends with a sample scorecard: an overall score of {CARD.score} out of 100, three competency scores, two strengths and one area to work on.
      </figcaption>
    </figure>
  )
}
