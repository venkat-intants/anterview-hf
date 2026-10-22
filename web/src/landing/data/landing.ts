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

export const NAV_LINKS = [
  { href: '#how', label: 'How it works' },
  { href: '#audience', label: 'For hiring teams' },
  { href: '#candidates', label: 'For candidates' },
  { href: '#languages', label: 'Languages' },
  { href: '#faq', label: 'FAQ' },
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
  { value: '6', label: 'round types: exams, coding, AI and human interviews, tasks and portfolios' },
  { value: '3', label: 'interview languages: English, Hindi and Telugu' },
  { value: '6', label: 'AI interviewers candidates can choose from' },
  { value: 'PDF', label: 'scorecard for every interview' },
]

// Real screenshots of the app in demo mode (invented data), captured at 2x and
// stored as WebP under /public/landing as a light and a dark twin. Sizes are the
// files' intrinsic pixels, so the <img> reserves its space and nothing shifts.
export const SHOTS: Record<'roles' | 'devices' | 'score' | 'history', Shot> = {
  roles: { stem: 'roles', width: 1400, height: 447, alt: 'The roles page in AntHire: filters for level and language above role cards such as Junior Java Developer and Sales Associate, each with a Start Interview button.' },
  devices: { stem: 'devices', width: 688, height: 1782, alt: 'The microphone and device check before an interview: microphone, connection and browser each marked Ready, a consent note, and the chosen role, level, language and interviewer.' },
  score: { stem: 'score', width: 1400, height: 1150, alt: 'A scorecard: an overall score of 71 out of 100, a radar chart, a score for each competency, key strengths and areas for improvement.' },
  history: { stem: 'history', width: 1400, height: 367, alt: 'Interview history: a table of past sessions with the role, the interview language, the status, the duration and a link to each scorecard.' },
}

export const WALKTHROUGH: { title: string; desc: string; shot: keyof typeof SHOTS; maxWidth: number }[] = [
  {
    title: 'Candidates choose how to interview',
    desc: 'AntHire builds the interview questions and the scoring rubric around each role. Candidates pick one of six interviewers and the language they want to speak.',
    shot: 'roles',
    maxWidth: 760,
  },
  {
    title: 'Candidates interview out loud',
    desc: 'After a microphone check and consent, each candidate has a real conversation. The interviewer listens and asks follow-up questions based on what they actually said.',
    shot: 'devices',
    maxWidth: 300,
  },
  {
    title: 'Your team reviews the scorecard',
    desc: 'Every interview produces a breakdown by competency, with strengths and gaps, ready as a PDF. Your team compares candidates on the same rubric and makes the call.',
    shot: 'score',
    maxWidth: 760,
  },
]

export const CANDIDATE_POINTS = [
  'Interview out loud, in English, Hindi or Telugu',
  'Follow-up questions that react to what you actually said',
  'Take it from any browser, at a time that suits you',
  'Every hiring decision is made by a person',
]

// The six interviewers in services/interview_core/app/avatars.py. Names match
// that catalogue; the images are still frames of the same faces, self-hosted
// under /public/avatars so the page loads no video.
export const AVATARS: { name: string; tone: string; image: string }[] = [
  { name: 'Anna', tone: 'Female interviewer', image: '/avatars/anna.jpg' },
  { name: 'Gloria (Warm)', tone: 'Female interviewer', image: '/avatars/gloria_warm.jpg' },
  { name: 'Lucas', tone: 'Male interviewer', image: '/avatars/lucas.jpg' },
  { name: 'Raj', tone: 'Male interviewer', image: '/avatars/raj.jpg' },
  { name: 'Benjamin', tone: 'Male interviewer', image: '/avatars/benjamin.jpg' },
  { name: 'Gloria (Greenscreen)', tone: 'Female interviewer', image: '/avatars/gloria.jpg' },
]

export const LANGUAGES_LIVE = [
  { native: 'English', name: 'English' },
  { native: 'हिन्दी', name: 'Hindi' },
  { native: 'తెలుగు', name: 'Telugu' },
]

export type AudienceKey = 'growing' | 'talent' | 'managers'
export const AUDIENCES: Record<AudienceKey, { icon: LucideIcon; title: string; sub: string; cta: string; subject: string; points: string[] }> = {
  growing: {
    icon: Building2,
    title: 'Growing companies',
    sub: 'Open a role, share the apply link and let a structured first round do the screening, without adding headcount to your hiring team.',
    cta: 'Book a demo',
    subject: 'AntHire demo request',
    points: [
      'A careers page and an apply link for every opening',
      'CVs scored against the role, unreadable ones flagged for a person',
      'Exams, coding rounds and AI voice interviews',
      'A decision queue that shows who needs your call',
    ],
  },
  talent: {
    icon: Briefcase,
    title: 'Talent acquisition teams',
    sub: 'One hiring workflow per role, approved once and run the same way for every applicant.',
    cta: 'Talk to us about your hiring',
    subject: 'AntHire for talent acquisition',
    points: [
      'Workflows with rounds, thresholds and approvals',
      'Human interviews scheduled with kits and scorecards',
      'Offers and preboarding in the same place',
      'Analytics across openings, sources and quality of hire',
    ],
  },
  managers: {
    icon: ClipboardCheck,
    title: 'Hiring managers and interviewers',
    sub: 'Review the evidence for each candidate in one place and spend your time on the shortlist.',
    cta: 'See it in a demo',
    subject: 'AntHire demo request',
    points: [
      'CV, scores and interview results side by side',
      'Interview kits with what to assess',
      'Private notes and structured scorecards',
      'People make the hiring decision; scorecards inform it',
    ],
  },
}

export const PRIVACY: { icon: LucideIcon; title: string; desc: string }[] = [
  { icon: ShieldCheck, title: 'Consent first', desc: 'Candidates are asked for consent before an interview starts.' },
  { icon: UserCheck, title: 'People decide', desc: 'Scorecards support a hiring decision. They never make one.' },
  { icon: Eraser, title: 'Erasure on request', desc: 'Candidates can ask for their data to be erased.' },
  { icon: Building, title: 'Organisations kept apart', desc: 'Staff only see the candidates of their own organisation.' },
]

export const FAQS: readonly (readonly [string, string])[] = [
  ['Is it really a conversation, or just recorded questions?', 'It is a conversation. The avatar listens to your answer and asks follow-up questions based on what you said.'],
  ['Which languages can candidates interview in?', 'English, Hindi and Telugu today. More languages are planned.'],
  ['How is candidate data handled?', 'Candidates are asked for consent before an interview starts and can ask for their data to be erased. Each company only sees its own candidates. If you are evaluating AntHire, ask us where data is stored and processed and we will answer in writing.'],
  ['What does the scorecard show?', 'Each interview is scored against a rubric for the role: a breakdown by competency with strengths and gaps, downloadable as a PDF.'],
  ['Does the AI decide who gets hired?', 'No. AntHire screens, assesses and summarises. Every hire or reject decision is made by a person on your team.'],
  ['Can we run every applicant through it?', 'Yes. Each opening has its own workflow, and every applicant goes through the same rounds. Tell us about your hiring volume and we will walk you through it.'],
  ['Do candidates need to install anything?', 'No. It runs in the browser. Candidates need a microphone and to allow access when asked.'],
]
