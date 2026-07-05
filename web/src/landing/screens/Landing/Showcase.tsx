import { useState } from 'react'
import { Play, Check } from 'lucide-react'
import { Reveal } from '../../components/Reveal'
import { FEATURES, AVATARS, LANGUAGES, AUDIENCES, type AudienceKey } from '../../data/landing'

export function FeatureBento() {
  return (
    <section id="features" className="relative z-10 mx-auto max-w-[1200px] px-6 py-20">
      <div className="mb-12 text-center">
        <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">The platform</div>
        <Reveal><h2 className="text-[48px] font-semibold tracking-heading">Everything an interview needs.</h2></Reveal>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-6" style={{ gridAutoRows: '180px' }}>
        {FEATURES.map((f, i) => {
          const Icon = f.icon
          return (
            <div key={i} style={{ gridColumn: `span ${f.cols}`, gridRow: `span ${f.rows}`, background: f.bg }}
              className="relative overflow-hidden rounded-card border border-white/[0.08] transition-transform duration-300 hover:-translate-y-1.5 hover:border-electric/45">
              <Reveal kind={i % 2 ? 'right' : 'left'} amount={0.2} className="flex h-full flex-col justify-between gap-4 p-6.5">
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-[11px] border border-white/[0.08] bg-white/[0.04]">
                  <Icon size={20} className="text-white/90" aria-hidden="true" />
                </span>
                <div>
                  <div className="text-[clamp(26px,2.6vw,46px)] font-semibold leading-none tracking-[-1px] text-white">{f.big}</div>
                  <h3 className="mt-2.5 text-[15px] font-semibold text-white">{f.title}</h3>
                  <p className="mt-1.5 text-[13.5px] leading-snug text-ash">{f.desc}</p>
                </div>
              </Reveal>
            </div>
          )
        })}
      </div>
    </section>
  )
}

export function Avatars() {
  return (
    <section className="relative z-10 mx-auto max-w-[1200px] px-6 py-15">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">Meet the avatars</div>
          <h2 className="text-[40px] font-semibold tracking-[-2px]">Six interviewers. One fair bar.</h2>
        </div>
        <p className="max-w-[320px] text-[15px] text-ash">Pick a voice and persona that fits the role. Every avatar scores on the same rubric.</p>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {AVATARS.map((av, i) => (
          <Reveal key={i} kind="zoom" amount={0.2} className="group overflow-hidden rounded-[20px] border border-white/[0.08] bg-obsidian transition-transform hover:-translate-y-1.5">
            <div className="relative h-[150px] overflow-hidden" style={{ background: av.bg }}>
              {/* Real Tavus avatar replica — a still frame of the same face the
                  live interview uses (self-hosted image; no video, no lag). */}
              <img
                src={av.image}
                alt={`${av.name}, AI interviewer`}
                loading="lazy"
                className="h-full w-full object-cover object-[center_20%] transition-transform duration-500 group-hover:scale-105"
              />
              {/* fade the avatar into the card body */}
              <div className="absolute inset-0 bg-gradient-to-t from-[#0f0f10] via-[#0f0f10]/10 to-transparent" />
            </div>
            <div className="p-4">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-[15px] font-semibold">{av.name}</div>
                <button className="inline-flex items-center gap-1.5 rounded-pill border border-electric/35 bg-electric/15 px-2.5 py-1 text-[11px] text-sky"><Play size={9} fill="currentColor" /> voice</button>
              </div>
              <div className="mb-2.5 text-xs text-ash">{av.role}</div>
              <div className="flex flex-wrap gap-1.5">
                {av.langs.map((l) => <span key={l} className="rounded-pill bg-white/[0.06] px-1.5 py-0.5 text-[10px] text-mist">{l}</span>)}
              </div>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  )
}

export function Languages() {
  return (
    <section id="languages" className="relative z-10 mx-auto max-w-[1100px] px-6 py-20">
      <div className="mb-11 text-center">
        <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">22 official languages</div>
        <Reveal><h2 className="mb-3 text-[48px] font-semibold tracking-heading">Interview in your mother tongue.</h2></Reveal>
        <p className="mx-auto max-w-[500px] text-base text-mist">English, Hindi &amp; Telugu are <span className="text-forest">live today</span>. The rest of the Eighth Schedule is rolling out through 2026.</p>
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
        {LANGUAGES.map((lang, i) => (
          <div key={i} className="rounded-[12px] p-3.5 transition-transform hover:-translate-y-[3px]" style={{ background: lang.bg, border: `1px solid ${lang.bd}` }}>
            <div className="text-[18px] font-semibold tracking-[-0.4px] text-white">{lang.native}</div>
            <div className="mt-1 flex items-center justify-between">
              <span className="text-[11px] text-ash">{lang.name}</span>
              <span className="rounded-pill px-1.5 py-0.5 text-[9px] font-semibold tracking-[0.5px]" style={{ background: lang.tagBg, color: lang.tagC }}>{lang.tag}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

export function AudienceTabs() {
  const [key, setKey] = useState<AudienceKey>('colleges')
  const a = AUDIENCES[key]
  const Icon = a.icon
  return (
    <section id="audience" className="relative z-10 mx-auto max-w-[1100px] px-6 py-15">
      <div className="mb-9 text-center">
        <div className="mb-3.5 text-xs uppercase tracking-[1.5px] text-electric">Built for three worlds</div>
        <h2 className="text-[40px] font-semibold tracking-[-2px]">One platform. Every hiring mission.</h2>
      </div>
      <div className="mb-9 flex flex-wrap justify-center gap-2">
        {(Object.keys(AUDIENCES) as AudienceKey[]).map((k) => (
          <button key={k} onClick={() => setKey(k)}
            className="rounded-pill border px-5.5 py-2.5 text-sm font-medium transition-all"
            style={key === k ? { background: '#fff', color: '#000', borderColor: '#fff' } : { background: 'rgba(28,29,31,0.6)', color: '#b8babf', borderColor: 'rgba(255,255,255,0.1)' }}>
            {AUDIENCES[k].label}
          </button>
        ))}
      </div>
      <div className="relative overflow-hidden rounded-card border border-electric/[0.18]" style={{ background: 'linear-gradient(160deg,#001b33,#030719)' }}>
        {/* Premium image banner — copyright-free Pexels photo + dark gradient scrim */}
        <div className="relative h-48 w-full overflow-hidden md:h-56">
          <img
            key={a.image}
            src={a.image}
            alt={a.title}
            loading="lazy"
            className="h-full w-full animate-[fade-in_0.6s_ease] object-cover"
          />
          <div className="absolute inset-0" style={{ background: 'linear-gradient(180deg, rgba(3,7,25,0.25) 28%, rgba(3,7,25,0.94) 100%)' }} />
          <div className="absolute bottom-4 left-6 flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] border border-white/15 bg-white/10 backdrop-blur">
              <Icon size={18} className="text-white" />
            </span>
            <span className="rounded-pill border border-white/20 bg-black/40 px-3 py-1 text-[12px] font-medium text-white backdrop-blur">
              {a.label}
            </span>
          </div>
        </div>

        <div className="relative grid grid-cols-1 items-center gap-10 p-10 md:grid-cols-2">
          <div>
            <h3 className="mb-3 text-[30px] font-semibold tracking-heading">{a.title}</h3>
            <p className="mb-6 text-base leading-normal text-mist">{a.sub}</p>
            <a href={`mailto:support@intants.com?subject=${encodeURIComponent('Anterview — ' + a.label)}`} className="inline-flex items-center gap-2 rounded-[9px] bg-white px-5.5 py-3 text-sm font-semibold text-black transition-transform hover:-translate-y-0.5">{a.cta} →</a>
          </div>
          <div className="flex flex-col gap-3">
            {a.points.map((pt) => (
              <div key={pt} className="flex items-start gap-3 rounded-[14px] border border-white/[0.07] bg-black/30 p-4 transition-colors hover:border-electric/30">
                <span className="inline-flex h-6 w-6 flex-none items-center justify-center rounded-[7px] bg-electric/[0.16]"><Check size={13} className="text-electric" strokeWidth={2.5} /></span>
                <div className="text-[14.5px] leading-snug text-pearl">{pt}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
