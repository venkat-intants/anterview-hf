import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'
import { buttonVariants } from '@/components/ui/button'
import { cn } from '../../lib/cn'
import { Reveal } from '../../components/Reveal'
import { Card, Chip, SectionHeading } from '../../components/primitives'
import { ShotFrame } from '../../components/ShotFrame'
import { PortraitTile } from '../../components/PortraitTile'
import { HiringPreview } from './HiringPreview'
import { AUDIENCES, AVATARS, CANDIDATE_POINTS, LANGUAGES_LIVE, SHOTS, mailto, type AudienceKey } from '../../data/landing'

export function ForCandidates() {
  return (
    <section id="candidates" aria-labelledby="cand-title" className="mx-auto max-w-[1200px] scroll-mt-20 px-4 py-12 sm:px-6 sm:py-16">
      <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-2 lg:gap-16">
        <Reveal>
          <p className="mb-3 text-sm font-semibold text-[var(--ui-info)]">For candidates</p>
          <h2 id="cand-title" className="text-[clamp(28px,4vw,40px)] font-bold leading-[1.12] tracking-[-0.02em] text-[var(--lp-text)]">
            A fair interview, on your schedule.
          </h2>
          <p className="mt-4 max-w-[480px] text-base leading-relaxed text-[var(--lp-text-muted)]">
            Every applicant gets the same structured interview and is scored on the same rubric. Answer out loud, in the language you are most at ease in, whenever suits you.
          </p>
          <ul className="mt-6 flex flex-col gap-3">
            {CANDIDATE_POINTS.map((pt) => (
              <li key={pt} className="flex items-start gap-3 text-[15px] leading-snug text-[var(--lp-text)]">
                <span className="mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full bg-[var(--ui-info-wash)] text-[var(--ui-info)]">
                  <Check size={14} strokeWidth={2.5} aria-hidden="true" />
                </span>
                {pt}
              </li>
            ))}
          </ul>
          <Link to="/register" className={cn(buttonVariants({ size: 'lg' }), 'mt-8')}>Create your account</Link>
        </Reveal>
        <Reveal>
          <figure className="m-0">
            <ShotFrame shot={SHOTS.history} />
            <figcaption className="mt-3 text-center text-sm text-[var(--lp-text-muted)]">Candidates can see their interviews and where each application stands.</figcaption>
          </figure>
        </Reveal>
      </div>
    </section>
  )
}

const AUDIENCE_ORDER: AudienceKey[] = ['growing', 'talent', 'managers']

export function Audiences() {
  return (
    <section id="audience" aria-labelledby="aud-title" className="mx-auto max-w-[1200px] scroll-mt-20 px-4 py-20 sm:px-6 sm:py-24">
      <SectionHeading
        id="aud-title"
        eyebrow="For hiring teams"
        title="Built for teams that hire at scale."
        lede="From the first application to the final decision, AntHire runs the same structured process for every candidate, so your team can focus on the shortlist."
      />
      <Reveal className="mx-auto mb-10 max-w-[960px]">
        <HiringPreview />
      </Reveal>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {AUDIENCE_ORDER.map((k) => {
          const a = AUDIENCES[k]
          const Icon = a.icon
          return (
            <Reveal key={k} className="h-full">
              <Card className="flex h-full flex-col p-6 sm:p-7">
                <span className="mb-5 flex h-11 w-11 items-center justify-center rounded-full bg-[var(--ui-info-wash)] text-[var(--ui-info)]">
                  <Icon size={20} aria-hidden="true" />
                </span>
                <h3 className="text-xl font-bold leading-snug text-[var(--lp-text)]">{a.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--lp-text-muted)]">{a.sub}</p>
                <ul className="mt-5 flex flex-1 flex-col gap-2.5">
                  {a.points.map((pt) => (
                    <li key={pt} className="flex items-start gap-2.5 text-sm leading-snug text-[var(--lp-text)]">
                      <Check size={16} strokeWidth={2.5} className="mt-0.5 flex-none text-[var(--ui-ok)]" aria-hidden="true" />
                      {pt}
                    </li>
                  ))}
                </ul>
                <a href={mailto(a.subject)} className={cn(buttonVariants({ variant: 'outline' }), 'mt-7 w-full')}>
                  {a.cta}
                </a>
              </Card>
            </Reveal>
          )
        })}
      </div>
    </section>
  )
}

export function Avatars() {
  return (
    <section id="avatars" aria-labelledby="av-title" className="mx-auto max-w-[1200px] scroll-mt-20 px-4 py-12 sm:px-6 sm:py-16">
      <SectionHeading
        id="av-title"
        eyebrow="Meet your interviewers"
        title="Six interviewers. One rubric."
        lede="Candidates pick the face and voice they are most comfortable with. The scoring does not change with the interviewer."
      />
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-6">
        {AVATARS.map((av) => (
          <li key={av.name} className="h-full">
            <Card className="h-full overflow-hidden">
              <div className="aspect-[4/5] overflow-hidden bg-[var(--lp-surface-alt)]">
                <PortraitTile
                  src={av.image}
                  alt={`${av.name}, AI interviewer`}
                  name={av.name}
                  width={240}
                  height={300}
                  imgClassName="object-[center_20%]"
                  initialsClassName="text-5xl"
                />
              </div>
              <div className="p-3.5">
                <div className="text-[15px] font-bold leading-tight text-[var(--lp-text)]">{av.name}</div>
                <div className="mt-0.5 text-xs text-[var(--lp-text-muted)]">{av.tone}</div>
              </div>
            </Card>
          </li>
        ))}
      </ul>
      <p className="mx-auto mt-6 flex max-w-[640px] flex-wrap items-center justify-center gap-x-2 gap-y-2 text-center text-sm text-[var(--lp-text-muted)]">
        <span>Interview in</span>
        {LANGUAGES_LIVE.map((l) => (
          <Chip key={l.name}>
            <span lang={l.name === 'Hindi' ? 'hi' : l.name === 'Telugu' ? 'te' : 'en'}>{l.native}</span>
          </Chip>
        ))}
        <span>with any of them. Hear a short preview of each in the interviewer picker once you sign in.</span>
      </p>
    </section>
  )
}

export function Languages() {
  return (
    <section id="languages" aria-labelledby="lang-title" className="mx-auto max-w-[1200px] scroll-mt-20 px-4 py-20 sm:px-6 sm:py-24">
      <Card className="px-6 py-12 text-center sm:px-12 sm:py-16">
        <SectionHeading
          id="lang-title"
          eyebrow="Languages"
          title="Interview in the language you think in."
          lede="Candidates interview out loud in English, Hindi or Telugu. More languages are planned."
        />
        <ul className="mx-auto grid max-w-[640px] grid-cols-1 gap-3 sm:grid-cols-3">
          {LANGUAGES_LIVE.map((l) => (
            <li key={l.name} className="rounded-[16px] border border-[var(--lp-line)] bg-[var(--lp-surface-alt)] px-4 py-5">
              <div lang={l.name === 'Hindi' ? 'hi' : l.name === 'Telugu' ? 'te' : 'en'} className="font-display text-2xl font-bold text-[var(--lp-text)]">{l.native}</div>
              <div className="mt-1 text-sm text-[var(--lp-text-muted)]">{l.name}</div>
            </li>
          ))}
        </ul>
      </Card>
    </section>
  )
}
