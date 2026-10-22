import type { Shot } from '../components/ShotFrame'
import {
  Building2, Briefcase, ClipboardCheck,
  ShieldCheck, UserCheck, Eraser, Building,
  type LucideIcon,
} from 'lucide-react'
import { SUPPORT_EMAIL } from '@/lib/contact'

export const CONTACT_EMAIL = SUPPORT_EMAIL

/** mailto link for the "talk to us" calls to action. */
export const mailto = (subject: string) => `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}`

// Every visible string is an i18n key under `lp` (lib/i18n.ts): the page is
// read in EN/HI/TE like every other public page (audit CA-M16).
export const NAV_LINKS = [
  { href: '#how', labelKey: 'lp.nav.how' },
  { href: '#audience', labelKey: 'lp.nav.teams' },
  { href: '#candidates', labelKey: 'lp.nav.candidates' },
  { href: '#languages', labelKey: 'lp.nav.languages' },
  { href: '#faq', labelKey: 'lp.nav.faq' },
]

// Everything below describes what the product does today (CLAUDE.md "Current
// Phase"). Earlier versions of this page carried invented proof — third-party
// logos and named testimonials (removed 2026-09-08), then made-up statistics,
// "22 languages live", 24x7 proctoring, India data residency and DPDP
// "compliance" claims (removed in the 2026 UI revamp). docs/ACCEPTED-RISKS.md
// records that the demo tier has no India residency, so nothing here asserts
// it. Targets that the platform has not yet run (20 lakh candidates, <2s,
// the per-session cost) are deliberately absent: the last is an internal margin.

export const PROOF = [
  { value: '6', labelKey: 'lp.proof.rounds' },
  { value: '3', labelKey: 'lp.proof.languages' },
  { value: '6', labelKey: 'lp.proof.avatars' },
  { value: 'PDF', labelKey: 'lp.proof.scorecard' },
]

// Real screenshots of the app in demo mode (invented data), captured at 2x and
// stored as WebP under /public/landing as a light and a dark twin. Sizes are the
// files' intrinsic pixels, so the <img> reserves its space and nothing shifts.
export const SHOTS: Record<'roles' | 'devices' | 'score' | 'history', Shot> = {
  roles: { stem: 'roles', width: 1400, height: 447, alt: 'lp.shot.roles' },
  devices: { stem: 'devices', width: 688, height: 1782, alt: 'lp.shot.devices' },
  score: { stem: 'score', width: 1400, height: 1150, alt: 'lp.shot.score' },
  history: { stem: 'history', width: 1400, height: 367, alt: 'lp.shot.history' },
}

export const WALKTHROUGH: { titleKey: string; descKey: string; shot: keyof typeof SHOTS; maxWidth: number }[] = [
  { titleKey: 'lp.how.s1Title', descKey: 'lp.how.s1Desc', shot: 'roles', maxWidth: 760 },
  { titleKey: 'lp.how.s2Title', descKey: 'lp.how.s2Desc', shot: 'devices', maxWidth: 300 },
  { titleKey: 'lp.how.s3Title', descKey: 'lp.how.s3Desc', shot: 'score', maxWidth: 760 },
]

export const CANDIDATE_POINTS = ['lp.cand.p1', 'lp.cand.p2', 'lp.cand.p3', 'lp.cand.p4']

// The six interviewers in services/interview_core/app/avatars.py. Names match
// that catalogue; the images are still frames of the same faces, self-hosted
// under /public/avatars so the page loads no video.
export const AVATARS: { name: string; toneKey: string; image: string }[] = [
  { name: 'Anna', toneKey: 'lp.av.female', image: '/avatars/anna.jpg' },
  { name: 'Gloria (Warm)', toneKey: 'lp.av.female', image: '/avatars/gloria_warm.jpg' },
  { name: 'Lucas', toneKey: 'lp.av.male', image: '/avatars/lucas.jpg' },
  { name: 'Raj', toneKey: 'lp.av.male', image: '/avatars/raj.jpg' },
  { name: 'Benjamin', toneKey: 'lp.av.male', image: '/avatars/benjamin.jpg' },
  { name: 'Gloria (Greenscreen)', toneKey: 'lp.av.female', image: '/avatars/gloria.jpg' },
]

export const LANGUAGES_LIVE = [
  { native: 'English', nameKey: 'lp.langs.en', lang: 'en' },
  { native: 'हिन्दी', nameKey: 'lp.langs.hi', lang: 'hi' },
  { native: 'తెలుగు', nameKey: 'lp.langs.te', lang: 'te' },
]

export type AudienceKey = 'growing' | 'talent' | 'managers'
export const AUDIENCES: Record<AudienceKey, { icon: LucideIcon; titleKey: string; subKey: string; ctaKey: string; subject: string; pointKeys: string[] }> = {
  growing: {
    icon: Building2,
    titleKey: 'lp.aud.growingTitle',
    subKey: 'lp.aud.growingSub',
    ctaKey: 'lp.aud.growingCta',
    subject: 'AntHire demo request',
    pointKeys: ['lp.aud.growing1', 'lp.aud.growing2', 'lp.aud.growing3', 'lp.aud.growing4'],
  },
  talent: {
    icon: Briefcase,
    titleKey: 'lp.aud.talentTitle',
    subKey: 'lp.aud.talentSub',
    ctaKey: 'lp.aud.talentCta',
    subject: 'AntHire for talent acquisition',
    pointKeys: ['lp.aud.talent1', 'lp.aud.talent2', 'lp.aud.talent3', 'lp.aud.talent4'],
  },
  managers: {
    icon: ClipboardCheck,
    titleKey: 'lp.aud.managersTitle',
    subKey: 'lp.aud.managersSub',
    ctaKey: 'lp.aud.managersCta',
    subject: 'AntHire demo request',
    pointKeys: ['lp.aud.managers1', 'lp.aud.managers2', 'lp.aud.managers3', 'lp.aud.managers4'],
  },
}

export const PRIVACY: { icon: LucideIcon; titleKey: string; descKey: string }[] = [
  { icon: ShieldCheck, titleKey: 'lp.priv.consentTitle', descKey: 'lp.priv.consentDesc' },
  { icon: UserCheck, titleKey: 'lp.priv.peopleTitle', descKey: 'lp.priv.peopleDesc' },
  { icon: Eraser, titleKey: 'lp.priv.erasureTitle', descKey: 'lp.priv.erasureDesc' },
  { icon: Building, titleKey: 'lp.priv.apartTitle', descKey: 'lp.priv.apartDesc' },
]

/** [question key, answer key] pairs. */
export const FAQS: readonly (readonly [string, string])[] = [1, 2, 3, 4, 5, 6, 7].map(
  (n) => [`lp.faq.q${n}`, `lp.faq.a${n}`] as const,
)
