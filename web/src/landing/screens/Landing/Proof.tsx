import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { animate, useInView, useMotionValue } from 'framer-motion'
import { Reveal } from '../../components/Reveal'
import { ScoreRing } from '../../components/primitives'
import { COMPETENCIES, METRICS, COMPLIANCE, FAQS } from '../../data/landing'

export function ScorecardPreview() {
  return (
    <section className="relative z-10 mx-auto max-w-[1100px] px-6 py-20">
      <div className="grid grid-cols-1 items-center gap-10 md:grid-cols-[0.85fr_1.15fr]">
        <div>
          <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">The scorecard</div>
          <h2 className="mb-4 text-[40px] font-semibold tracking-[-2px]">This is what every candidate gets.</h2>
          <p className="text-base leading-normal text-[var(--lp-text-muted)]">A transparent, competency-level breakdown with strengths, gaps, and transcript highlights — downloadable as a PDF, in seconds.</p>
        </div>
        <Reveal kind="right" className="rounded-card border p-9" style={{ borderColor: 'var(--lp-line)', background: 'var(--lp-surface)', boxShadow: 'var(--lp-shadow-card)' }}>
          <div className="mb-7 flex items-center gap-7">
            <ScoreRing pct={86} value={86} />
            <div>
              <div className="mb-1 text-[13px] text-[var(--lp-text-muted)]">Role · Frontend Engineer (L2)</div>
              <div className="inline-flex items-center gap-1.5 rounded-pill border border-forest/35 bg-forest/15 px-3 py-1.5 text-[13px] font-semibold text-forest">Strong Hire</div>
              <div className="mt-3 font-mono text-[13px] text-[var(--lp-text-faint)]">12 questions · 14 min · हिन्दी</div>
            </div>
          </div>
          <div className="flex flex-col gap-3.5">
            {COMPETENCIES.map((c) => (
              <div key={c.name}>
                <div className="mb-1.5 flex justify-between text-[13px]"><span className="text-[var(--lp-text-soft)]">{c.name}</span><span className="font-mono text-[var(--lp-text-muted)]">{c.score}</span></div>
                <div className="h-[7px] overflow-hidden rounded-pill" style={{ background: 'var(--lp-inset)' }}>
                  <div className="h-full rounded-pill" style={{ width: `${c.pct}%`, background: 'linear-gradient(90deg,#0088ff,#a887dc)' }} />
                </div>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  )
}

/** Counts the numeric part of a metric up from 0 when scrolled into view. */
function AnimatedNumber({ value }: { value: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, amount: 0.6 })
  const mv = useMotionValue(0)
  const match = value.match(/^([^\d]*)([\d.,]+)(.*)$/)
  const [text, setText] = useState(value)
  useEffect(() => {
    const m = value.match(/^([^\d]*)([\d.,]+)(.*)$/)
    if (!m || !inView) return
    const target = parseFloat(m[2].replace(/,/g, ''))
    const decimals = m[2].includes('.') ? (m[2].split('.')[1]?.length ?? 0) : 0
    const grouped = m[2].includes(',')
    setText(`${m[1]}0${m[3]}`)
    const controls = animate(mv, target, {
      duration: 1.5,
      ease: [0.2, 0.7, 0.2, 1],
      onUpdate: (v) => {
        const n = decimals ? v.toFixed(decimals) : Math.round(v).toString()
        const shown = grouped ? Number(n).toLocaleString('en-IN') : n
        setText(`${m[1]}${shown}${m[3]}`)
      },
    })
    return () => controls.stop()
  }, [inView, value, mv])
  if (!match) return <>{value}</>
  return <span ref={ref}>{text}</span>
}

export function Metrics() {
  return (
    <section className="relative z-10 mx-auto max-w-[1200px] px-6 py-10">
      <div className="grid grid-cols-2 gap-6 rounded-card border px-10 py-12 md:grid-cols-4" style={{ background: 'var(--lp-grad-wash)', borderColor: 'var(--lp-line)' }}>
        {METRICS.map((m, i) => (
          <Reveal key={i} kind="zoom" amount={0.5} className="text-center">
            <div className="bg-clip-text text-[46px] font-semibold tracking-[-2px] text-transparent" style={{ backgroundImage: i % 2 ? 'linear-gradient(90deg,var(--lp-text),#0088ff)' : 'linear-gradient(90deg,var(--lp-text),#a887dc)' }}>
              <AnimatedNumber value={m.value} />
            </div>
            <div className="mt-1.5 text-[13px] text-[var(--lp-text-muted)]">{m.label}</div>
          </Reveal>
        ))}
      </div>
    </section>
  )
}

export function Compliance() {
  return (
    <section className="relative z-10 mx-auto max-w-[1100px] px-6 py-15">
      <div className="rounded-card border p-11" style={{ borderColor: 'var(--lp-line)', background: 'var(--lp-surface)' }}>
        <h2 className="mb-2 text-[28px] font-semibold tracking-[-1px]">Compliance you can defend.</h2>
        <p className="mb-8 max-w-[600px] text-[15px] text-[var(--lp-text-muted)]">Engineered for government and enterprise procurement from day one.</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {COMPLIANCE.map((c) => {
            const Icon = c.icon
            return (
              <div key={c.title} className="rounded-[16px] border border-electric/[0.18] p-5.5" style={{ background: 'var(--lp-grad-deep)' }}>
                <Icon size={22} className="mb-3 text-electric" />
                <div className="mb-1.5 text-[15px] font-semibold text-[var(--lp-on-deep)]">{c.title}</div>
                <div className="text-[13px] leading-snug text-[var(--lp-on-deep-muted)]">{c.desc}</div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

export function FAQ() {
  const [open, setOpen] = useState(-1)
  return (
    <section className="relative z-10 mx-auto max-w-[760px] px-6 py-15">
      <h2 className="mb-10 text-center text-[40px] font-semibold tracking-[-2px]">Questions, answered.</h2>
      <div className="flex flex-col gap-2.5">
        {FAQS.map(([q, a], i) => (
          <div key={i} className="overflow-hidden rounded-[16px] transition-colors" style={{ background: 'var(--lp-surface)', border: `1px solid ${open === i ? 'rgba(0,136,255,0.35)' : 'var(--lp-line)'}` }}>
            <button onClick={() => setOpen(open === i ? -1 : i)} className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left">
              <span className="text-base font-medium text-[var(--lp-text)]">{q}</span>
              <span className="flex-none text-[22px] text-electric transition-transform" style={{ transform: open === i ? 'rotate(45deg)' : 'none' }}>+</span>
            </button>
            <div className="overflow-hidden transition-[max-height] duration-300" style={{ maxHeight: open === i ? 200 : 0 }}>
              <p className="px-6 pb-5.5 text-[14.5px] leading-relaxed text-[var(--lp-text-muted)]">{a}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

export function FinalCTA() {
  return (
    <section id="cta" className="relative z-10 mx-auto max-w-[1200px] px-6 pb-[90px] pt-15">
      <div className="relative overflow-hidden rounded-card px-10 py-20 text-center" style={{ background: 'var(--lp-grad-cta)' }}>
        <div className="absolute inset-0 mix-blend-screen" style={{ background: 'radial-gradient(60% 80% at 50% 0%, rgba(0,136,255,0.25), transparent 60%)' }} />
        <div className="relative">
          <h2 className="mb-4 text-[54px] font-semibold tracking-display text-white">Start your first interview free.</h2>
          <p className="mx-auto mb-9 max-w-[440px] text-[18px] text-white/[0.82]">No card, no setup. Talk to Aanya in under 30 seconds.</p>
          <div className="flex flex-wrap justify-center gap-3">
            <Link to="/register" className="rounded-[9px] bg-white px-7.5 py-3.5 text-base font-semibold text-black shadow-[0_12px_40px_rgba(0,0,0,0.3)] transition-transform hover:-translate-y-0.5">Start a mock interview</Link>
            <a href="mailto:support@intants.com?subject=AntHire%20demo%20request" className="rounded-[9px] border border-white/25 bg-white/[0.12] px-7.5 py-3.5 text-base font-medium text-white backdrop-blur transition-colors hover:bg-white/20">Book a demo</a>
          </div>
        </div>
      </div>
    </section>
  )
}
