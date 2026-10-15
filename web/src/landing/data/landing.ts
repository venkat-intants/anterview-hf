import type { Shot } from '../components/ShotFrame'
import {
  GraduationCap, Building2, Landmark,
  ShieldCheck, UserCheck, Eraser, Building,
  type LucideIcon,
} from 'lucide-react'

export const CONTACT_EMAIL = 'support@intants.com'

/** mailto link for the "talk to us" calls to action. */
export const mailto = (subject: string) => `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}`

export const NAV_LINKS = [
  { href: '#how', label: 'How it works' },
  { href: '#candidates', label: 'For candidates' },
  { href: '#audience', label: 'For institutions' },
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
  { value: '3', label: 'languages today: English, Hindi and Telugu' },
  { value: '6', label: 'AI interviewers to choose from' },
  { value: 'Voice', label: 'first: talk, don’t type' },
  { value: 'PDF', label: 'scorecard after every interview' },
]

// Real screenshots of the app in demo mode (invented data), captured at 2x and
// stored as WebP under /public/landing as a light and a dark twin. Sizes are the
// files' intrinsic pixels, so the <img> reserves its space and nothing shifts.
export const SHOTS: Record<'roles' | 'devices' | 'score' | 'history', Shot> = {
  roles: { stem: 'roles', width: 1400, height: 447, alt: 'The practice roles page in AntHire: filters for level and language above role cards such as Junior Java Developer and Sales Associate, each with a Start Interview button.' },
  devices: { stem: 'devices', width: 688, height: 1782, alt: 'The microphone and device check before an interview: microphone, connection and browser each marked Ready, a consent note, and the chosen role, level, language and interviewer.' },
  score: { stem: 'score', width: 1400, height: 1150, alt: 'A scorecard: an overall score of 71 out of 100, a radar chart, a score for each competency, key strengths and areas for improvement.' },
  history: { stem: 'history', width: 1400, height: 367, alt: 'Interview history: a table of past sessions with the role, the interview language, the status, the duration and a link to each scorecard.' },
}

export const WALKTHROUGH: { title: string; desc: string; shot: keyof typeof SHOTS; maxWidth: number }[] = [
  {
    title: 'Pick a role and an interviewer',
    desc: 'Choose the job you are preparing for. AntHire builds the questions and the scoring rubric around it. Then pick one of six interviewers and the language you want to speak.',
    shot: 'roles',
    maxWidth: 760,
  },
  {
    title: 'Talk it through',
    desc: 'Check your microphone, give consent, then have a real conversation out loud. The interviewer listens and asks follow-up questions based on what you actually said.',
    shot: 'devices',
    maxWidth: 300,
  },
  {
    title: 'Get your scorecard',
    desc: 'See a breakdown by competency, with strengths and gaps, right after the interview. Save it as a PDF, then practise again and see what changes.',
    shot: 'score',
    maxWidth: 760,
  },
]

export const CANDIDATE_POINTS = [
  'Practise out loud, in English, Hindi or Telugu',
  'Follow-up questions that react to what you actually said',
  'A scorecard with strengths and gaps after every session',
  'Practise again and compare your scorecards',
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

export type AudienceKey = 'colleges' | 'hr' | 'gov'
export const AUDIENCES: Record<AudienceKey, { icon: LucideIcon; title: string; sub: string; cta: string; subject: string; points: string[] }> = {
  colleges: {
    icon: GraduationCap,
    title: 'Colleges and skill universities',
    sub: 'Give every student a realistic first round before the real ones, in the language they are most at ease in.',
    cta: 'Talk to us about placements',
    subject: 'AntHire for colleges and universities',
    points: [
      'Voice interviews with no scheduling and no panel to book',
      'A scorecard for every practice session',
      'English, Hindi and Telugu today',
      'Written exams and coding rounds that match the role',
    ],
  },
  hr: {
    icon: Building2,
    title: 'Recruitment and L&D teams',
    sub: 'Run a structured first round for every applicant and review the scorecards before you decide.',
    cta: 'Book a demo',
    subject: 'AntHire demo request',
    points: [
      'Role-aware questions and scoring rubrics',
      'An HR workspace for roles, applicants and interviews',
      'Exams and interviews that assess the same job',
      'People make the hiring decision; scorecards inform it',
    ],
  },
  gov: {
    icon: Landmark,
    title: 'Government skilling programmes',
    sub: 'Assess candidates who are more comfortable speaking than typing, in their own language.',
    cta: 'Talk to us about your programme',
    subject: 'AntHire for skilling programmes',
    points: [
      'Voice-first, with nothing to install',
      'English, Hindi and Telugu today; more Indian languages planned',
      'Admin analytics for programme owners',
      'Consent asked for before each interview',
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
  ['Which languages can I interview in?', 'English, Hindi and Telugu today. More Indian languages are planned.'],
  ['How is my data handled?', 'You are asked for consent before an interview starts, and candidates can ask for their data to be erased. If you are evaluating AntHire for an institution, ask us where data is stored and processed and we will answer in writing.'],
  ['What does the scorecard show?', 'The interview is scored against a rubric for the role. You see a breakdown by competency with strengths and gaps, and you can download it as a PDF.'],
  ['Can an institution or company use this for many candidates?', 'AntHire includes an HR workspace and admin analytics for teams running interviews for many candidates. Tell us about your cohort and we will walk you through it.'],
  ['Do I need to install anything?', 'No. It runs in your browser. You will need a microphone and to allow access when asked.'],
]
