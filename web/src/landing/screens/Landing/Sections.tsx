import { Link } from 'react-router-dom'
import { Reveal } from '../../components/Reveal'
import { Marquee } from '../../components/primitives'
import { LOGOS, STEPS } from '../../data/landing'

export function TrustMarquee() {
  return (
    <section className="relative z-10 mx-auto max-w-[1200px] px-6 py-7">
      <p className="mb-5 text-center text-xs uppercase tracking-[1.5px] text-fog">Trusted across India&apos;s campuses, employers &amp; skilling missions</p>
      <Marquee>
        {LOGOS.map((lg, i) => (
          <div key={i} className="flex items-center gap-2.5 whitespace-nowrap text-[18px] font-semibold tracking-[-0.4px] text-pearl opacity-55 grayscale transition hover:opacity-100 hover:grayscale-0">
            <span className="inline-block h-[22px] w-[22px] rounded-[6px]" style={{ background: lg.c }} />{lg.n}
          </div>
        ))}
      </Marquee>
    </section>
  )
}

export function ProblemSolution() {
  return (
    <section className="relative z-10 mx-auto max-w-[1100px] px-6 py-20">
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <Reveal kind="left" className="rounded-card border border-white/[0.12] bg-[#16171b] p-10">
          <div className="mb-4.5 text-xs uppercase tracking-[1.5px] text-fog">The old way</div>
          <h2 className="mb-4 text-[30px] font-semibold tracking-heading text-[#c7ccd4]">Traditional interviews don&apos;t scale.</h2>
          <p className="mb-7 text-base leading-normal text-[#9aa1aa]">Panels are slow, biased, English-only, and impossible to run for lakhs of candidates. Most never get a fair first round.</p>
          <div className="flex gap-7">
            <div><div className="text-[38px] font-semibold tracking-[-1.5px] text-[#aab0b8]">14 days</div><div className="text-[13px] text-[#787e87]">avg. time-to-first-round</div></div>
            <div><div className="text-[38px] font-semibold tracking-[-1.5px] text-[#aab0b8]">9%</div><div className="text-[13px] text-[#787e87]">get personalised feedback</div></div>
          </div>
        </Reveal>
        <Reveal kind="right" className="rounded-card border border-electric/25 p-10 shadow-[0_0_60px_rgba(0,136,255,0.08)]" >
          <div style={{ background: 'linear-gradient(160deg,#001b33,#030719)' }} className="-m-10 rounded-card p-10">
            <div className="mb-4.5 text-xs uppercase tracking-[1.5px] text-electric">The Anterview way</div>
            <h2 className="mb-4 text-[30px] font-semibold tracking-heading text-white">AI that interviews 20 lakh candidates.</h2>
            <p className="mb-7 text-base leading-normal text-mist">A voice-first avatar runs structured interviews 24×7, in 22 languages, and returns a fair scorecard in seconds — at under ₹12 a session.</p>
            <div className="flex gap-7">
              <div><div className="text-[38px] font-semibold tracking-[-1.5px] text-white">20 lakh</div><div className="text-[13px] text-mist">candidates / cycle</div></div>
              <div><div className="text-[38px] font-semibold tracking-[-1.5px] text-white">100%</div><div className="text-[13px] text-mist">get a scorecard</div></div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  )
}

export function HowItWorks() {
  return (
    <section id="how" className="relative z-10 mx-auto max-w-[1200px] px-6 py-12">
      <div className="mb-12 text-center">
        <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">How it works</div>
        <h2 className="text-[48px] font-semibold tracking-heading">Four steps to a fair interview.</h2>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => {
          const Icon = s.icon
          return (
            <Reveal key={i} kind="zoom" amount={0.4} className="rounded-card border border-white/[0.08] bg-obsidian/70 p-7 backdrop-blur-md">
              <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-[12px]" style={{ background: s.bg }}>
                <Icon size={22} className="text-white" />
              </div>
              <div className="mb-2 font-mono text-xs text-electric">{s.no}</div>
              <h3 className="mb-2 text-[19px] font-semibold tracking-[-0.4px] text-white">{s.title}</h3>
              <p className="text-sm leading-normal text-ash">{s.desc}</p>
            </Reveal>
          )
        })}
      </div>
    </section>
  )
}

export function LiveDemo() {
  return (
    <section id="demo" className="relative z-10 mx-auto max-w-[1000px] px-6 py-20">
      <Reveal kind="fade" className="relative overflow-hidden rounded-card border border-white/10" >
        <div style={{ background: 'linear-gradient(160deg,#030719,#000)' }}>
          <div className="absolute inset-0" style={{ background: 'radial-gradient(70% 60% at 50% 0%, rgba(0,136,255,0.16), transparent 60%)' }} />
          <div className="relative px-10 py-14 text-center">
            <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">Try it now · no signup</div>
            <h2 className="mb-2.5 text-[36px] font-semibold tracking-[-1.6px]">Say hello to your interviewer.</h2>
            <p className="mx-auto mb-9 max-w-[440px] text-base text-mist">Tap the mic. Aanya will ask you a real warm-up question — live.</p>
            <Link to="/register" aria-label="Start a mock interview" className="relative mx-auto flex h-[84px] w-[84px] items-center justify-center rounded-full bg-white text-black shadow-[0_10px_40px_rgba(168,135,220,0.4)] transition-transform hover:scale-105">
              <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><line x1="12" y1="19" x2="12" y2="22" /></svg>
            </Link>
            <div className="mt-6 font-mono text-[13px] text-fog">tap to begin · sign in to talk to Aanya</div>
          </div>
        </div>
      </Reveal>
    </section>
  )
}
