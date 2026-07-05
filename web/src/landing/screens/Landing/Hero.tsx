import { ShieldCheck, Circle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { HERO_VIDEOS } from '../../data/landing'

/** Webcam-style tile shell. */
function Tile({
  children, tag, tagColor, dotColor, name, sub, waveColor,
}: {
  children: React.ReactNode; tag: string; tagColor: string; dotColor: string
  name: string; sub: string; waveColor: string
}) {
  return (
    <div className="relative h-[clamp(240px,30vw,300px)] overflow-hidden rounded-[18px] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12),0_20px_50px_rgba(0,0,0,0.45)]">
      {children}
      <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_60px_rgba(0,0,0,0.55)]" />
      <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-pill bg-black/50 px-2.5 py-1 text-[10px] font-semibold backdrop-blur-sm" style={{ color: tagColor }}>
        <span className="h-[5px] w-[5px] rounded-full" style={{ background: dotColor, boxShadow: `0 0 6px ${dotColor}` }} />{tag}
      </div>
      <div className="absolute bottom-3 left-3 rounded-[10px] border border-white/[0.14] bg-[#030719]/70 px-2.5 py-1.5 backdrop-blur-md">
        <div className="text-[12px] font-semibold text-white">{name}</div>
        <div className="text-[9.5px] tracking-[0.3px] text-ash">{sub}</div>
      </div>
      <div className="absolute bottom-3.5 right-3 flex h-5 items-end gap-[2px]">
        {[55, 90, 100, 60, 85].map((h, i) => (
          <span key={i} className="w-[3px] rounded-full animate-wave" style={{ height: `${h}%`, background: waveColor, animationDelay: `${i * 0.1}s` }} />
        ))}
      </div>
    </div>
  )
}

export function Hero() {
  return (
    <section id="top" className="relative z-10 mx-auto max-w-[1200px] px-6 pb-16 pt-[150px]">
      <div className="relative overflow-hidden rounded-card shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12)]"
        style={{ background: 'linear-gradient(160deg,#000 0.85%,#112d72 33.4%,#4b52aa 49.68%,#a887dc 70.84%,#e6c4e7 95.8%,#fcdbef 107.19%)' }}>
        <div className="absolute inset-0 mix-blend-screen" style={{ background: 'radial-gradient(80% 60% at 75% 30%, rgba(0,136,255,0.18), transparent 60%)' }} />
        <div className="relative grid grid-cols-1 items-center gap-8 p-10 md:grid-cols-[1.05fr_0.95fr] md:p-16">
          {/* copy */}
          <div>
            <span className="mb-6 inline-flex items-center gap-2 rounded-pill border border-white/[0.16] bg-black/35 py-1.5 pl-2 pr-3 text-xs text-pearl backdrop-blur">
              <span className="inline-flex h-[18px] w-[18px] items-center justify-center rounded-full bg-[rgba(22,194,83,0.2)]">
                <span className="h-[7px] w-[7px] animate-dot-pulse rounded-full bg-forest shadow-[0_0_8px_#27c93f]" />
              </span>
              Live now · 22 Indian languages · Voice-first
            </span>
            <h1 className="animate__animated animate__fadeIn m-0 text-[clamp(40px,6vw,60px)] font-semibold leading-[1.02] tracking-display text-white [text-shadow:0_2px_40px_rgba(0,0,0,0.3)]">
              Talk to an AI interviewer.<br />Get hired faster.
            </h1>
            <p className="mt-5.5 max-w-[480px] text-[18px] leading-normal tracking-[-0.18px] text-white/[0.82]">
              Practice real interviews with a lifelike avatar that listens, asks follow-ups, and hands you a competency scorecard — in your language, in seconds.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/register" className="inline-flex items-center gap-2.5 rounded-[9px] bg-white px-5.5 py-3.5 text-[15px] font-semibold text-black shadow-[0_8px_30px_rgba(0,0,0,0.25)] transition-transform hover:-translate-y-0.5">
                <span className="h-2 w-2 rounded-full bg-electric shadow-[0_0_10px_#0088ff]" /> Start a mock interview
              </Link>
              <a href="mailto:support@intants.com?subject=Anterview%20demo%20request" className="inline-flex items-center gap-2 rounded-[9px] border border-white/[0.18] bg-charcoal/55 px-5.5 py-3.5 text-[15px] font-medium text-white backdrop-blur transition-colors hover:bg-charcoal/85">Book a demo</a>
            </div>
            <div className="mt-6.5 flex items-center gap-2.5 text-[12.5px] text-white/60">
              <ShieldCheck size={14} className="text-forest" /> DPDP-compliant · India data residency · Consent-led
            </div>
          </div>

          {/* two-way interview call */}
          <div className="relative flex min-h-[340px] flex-col justify-center gap-3.5">
            <span className="absolute -left-[3%] -top-[3%] z-[7] animate-float-chip rounded-pill border border-white/20 bg-black/50 px-3.5 py-1.5 text-[13px] font-semibold backdrop-blur-md">नमस्ते</span>
            <span className="absolute -right-[3%] bottom-[2%] z-[7] animate-float-chip rounded-pill border border-white/20 bg-black/50 px-3.5 py-1.5 text-[13px] font-semibold backdrop-blur-md [animation-delay:1s]">హలో</span>

            <div className="rounded-pill border border-white/10 bg-[#030719]/70 px-3 py-1.5 text-center font-mono text-[11px] text-mist backdrop-blur-md">
              <span className="text-ember">● REC</span> · Live interview · 1.8s latency
            </div>

            <div className="relative grid grid-cols-2 gap-3">
              {/* AI tile — the platform's real Tavus avatar ("Anna"); the live
                  interview mounts the same Tavus CVI / LiveKit track here in-app.
                  Still frame (not video) to keep the hero light. */}
              <Tile tag="AI" tagColor="#60a5fa" dotColor="#0088ff" name="Anna" sub="AI INTERVIEWER" waveColor="#0088ff">
                {/* Looping Tavus "Anna" clip, muted + slowed slightly so her
                    natural micro-movements read as attentive listening while the
                    candidate speaks (not talking). Only this one tile uses video. */}
                <video autoPlay muted loop playsInline preload="auto"
                  onLoadedMetadata={(e) => { e.currentTarget.playbackRate = 0.7 }}
                  className="absolute inset-0 h-full w-full object-cover object-[center_28%]">
                  <source src={HERO_VIDEOS.ai} type="video/mp4" />
                </video>
                <div className="absolute inset-0" style={{ background: 'linear-gradient(180deg, rgba(3,7,25,0.12) 38%, rgba(3,7,25,0.72) 100%)' }} />
                <div className="absolute left-1/2 top-10 h-[140px] w-[140px] -translate-x-1/2 animate-pulse-ring rounded-full border border-electric/45" />
              </Tile>

              {/* Human candidate tile */}
              <Tile tag="REC" tagColor="#ffb764" dotColor="#e6714f" name="You" sub="CANDIDATE · CAMERA" waveColor="#27c93f">
                <video autoPlay muted loop playsInline preload="metadata" className="absolute inset-0 h-full w-full object-cover">
                  <source src={HERO_VIDEOS.human} type="video/mp4" />
                </video>
                <div className="absolute inset-0" style={{ background: 'linear-gradient(180deg, transparent 55%, rgba(0,0,0,0.55) 100%)' }} />
              </Tile>

              {/* center connection glyph */}
              <div className="absolute left-1/2 top-1/2 z-[8] flex h-[42px] w-[42px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/[0.18] bg-[#030719]/90 shadow-[0_6px_20px_rgba(0,0,0,0.5)] backdrop-blur-md">
                <Circle size={10} className="text-sky" />
              </div>
            </div>

            <div className="self-center rounded-[12px] border border-white/[0.12] bg-[#030719]/[0.66] px-4 py-2.5 text-center text-[12.5px] leading-snug text-pearl backdrop-blur-md">
              <span className="font-semibold text-sky">Anna:</span> “Walk me through a project you&apos;re proud of.”
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
