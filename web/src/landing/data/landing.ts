import {
  Globe, Zap, Repeat, Users, FileText, Shield, Headphones, Target, Mic, BarChart3,
  Rocket, GraduationCap, Building2, Landmark, Scale, ClipboardCheck, MapPin, Trash2,
  type LucideIcon,
} from 'lucide-react'

/** Hero interview video clips.
 *  `ai`  — the platform's actual default Tavus avatar ("Anna" / replica
 *          rf4e9d9790f0), the same face the live interview uses, so the hero
 *          shows a real AI interviewer rather than a stock clip.
 *  human — a candidate-on-camera clip (Pexels, free license). */
export const HERO_VIDEOS = {
  ai: 'https://cdn.replica.tavus.io/39895/8c44fce6.mp4',
  human: 'https://videos.pexels.com/video-files/8048256/8048256-hd_1920_1080_25fps.mp4',
}

export const NAV_LINKS = [
  { href: '#how', label: 'How it works' },
  { href: '#features', label: 'Product' },
  { href: '#audience', label: 'For Colleges' },
  { href: '#audience', label: 'For HR' },
  { href: '#languages', label: 'Languages' },
]

// Third-party logos and named-institution testimonials were removed on
// 2026-09-08. Both put other organisations' names on our page as endorsements
// we cannot substantiate — NSDC, APSSDC, IIT Hyderabad and the rest are not
// customers, and the quotes were written copy attributed to named people at
// named colleges. Nothing replaces them: an honest page with no logo wall is
// better than a logo wall we would have to defend in a bid.


export const STEPS: { no: string; icon: LucideIcon; title: string; desc: string; bg: string }[] = [
  { no: 'STEP 01', icon: Target, title: 'Pick a role', desc: 'Choose a job role and level. AntHire loads the right questions and rubric.', bg: 'rgba(0,136,255,0.16)' },
  { no: 'STEP 02', icon: Mic, title: 'Talk to the avatar', desc: 'Have a real, voice-first conversation. It listens and asks smart follow-ups.', bg: 'rgba(168,135,220,0.18)' },
  { no: 'STEP 03', icon: BarChart3, title: 'Get a scorecard', desc: 'Receive a competency breakdown with strengths, gaps and transcript highlights.', bg: 'rgba(22,194,83,0.16)' },
  { no: 'STEP 04', icon: Rocket, title: 'Share & improve', desc: 'Download the PDF, share with recruiters, retake to climb your score.', bg: 'rgba(221,85,231,0.16)' },
]

export type Feature = { cols: number; rows: number; bg: string; icon: LucideIcon; big: string; title: string; desc: string }
// Surfaces are landing-theme tokens (see landing/styles/anthire.css) so the
// bento reads on the light grey page as well as the dark one.
export const FEATURES: Feature[] = [
  { cols: 3, rows: 2, bg: 'var(--lp-grad-deep)', icon: Globe, big: '22', title: 'Indian languages', desc: 'Interview in the language candidates actually think in — voice-first, end to end.' },
  { cols: 3, rows: 1, bg: 'var(--lp-surface)', icon: Zap, big: '<2s', title: 'Real-time latency', desc: 'Conversations that feel human, not laggy.' },
  { cols: 3, rows: 1, bg: 'var(--lp-surface)', icon: Repeat, big: '∞', title: 'Adaptive follow-ups', desc: 'It probes deeper based on your answers.' },
  { cols: 2, rows: 1, bg: 'var(--lp-surface)', icon: Users, big: '6', title: 'Avatars', desc: '3 male, 3 female personas.' },
  { cols: 2, rows: 1, bg: 'var(--lp-surface)', icon: FileText, big: 'PDF', title: 'Scorecards', desc: 'Shareable, structured.' },
  { cols: 2, rows: 1, bg: 'var(--lp-surface)', icon: Shield, big: '24×7', title: 'Proctoring', desc: 'Integrity, monitored.' },
  { cols: 6, rows: 1, bg: 'var(--lp-grad-wash)', icon: Headphones, big: 'Voice-first', title: 'No typing, no friction', desc: 'Designed for mobile-first Bharat — talk the way you would in a real room.' },
]

// `video` = the platform's REAL Tavus avatar replica clips (cdn.replica.tavus.io)
// — the same faces the live interview uses, one per interviewer persona.
// Real Tavus replica faces (still frames captured from the live avatar clips,
// self-hosted under /public/avatars to keep the landing snappy — no video).
// Names match the avatars' actual identities so faces and names line up.
export const AVATARS = [
  { name: 'Anna', role: 'Warm · behavioural', langs: ['EN', 'हि', 'తె'], bg: 'linear-gradient(160deg,#112d72,#a887dc)', image: '/avatars/anna.jpg' },
  { name: 'Lucas', role: 'Sharp · technical', langs: ['EN', 'हि'], bg: 'linear-gradient(160deg,#001b33,#0088ff)', image: '/avatars/lucas.jpg' },
  { name: 'Gloria', role: 'Calm · HR screen', langs: ['EN', 'తె'], bg: 'linear-gradient(160deg,#1a0a20,#dd55e7)', image: '/avatars/gloria_warm.jpg' },
  { name: 'Raj', role: 'Brisk · case-based', langs: ['EN', 'हि'], bg: 'linear-gradient(160deg,#031310,#16c253)', image: '/avatars/raj.jpg' },
  { name: 'Maya', role: 'Friendly · campus', langs: ['EN', 'हि', 'తె'], bg: 'linear-gradient(160deg,#200401,#ffb764)', image: '/avatars/gloria.jpg' },
  { name: 'Benjamin', role: 'Direct · senior', langs: ['EN'], bg: 'linear-gradient(160deg,#030719,#4b52aa)', image: '/avatars/benjamin.jpg' },
]

const live = { tag: 'LIVE', tagBg: 'rgba(39,201,63,0.16)', tagC: '#27c93f', bg: 'var(--lp-grad-deep)', bd: 'rgba(0,136,255,0.3)' }
const soon = { tag: '2026', tagBg: 'var(--lp-chip)', tagC: 'var(--lp-text-faint)', bg: 'var(--lp-surface)', bd: 'var(--lp-line)' }
export const LANGUAGES = [
  ['English', 'English', true], ['हिन्दी', 'Hindi', true], ['తెలుగు', 'Telugu', true], ['தமிழ்', 'Tamil', false],
  ['বাংলা', 'Bengali', false], ['मराठी', 'Marathi', false], ['ગુજરાતી', 'Gujarati', false], ['ಕನ್ನಡ', 'Kannada', false],
  ['മലയാളം', 'Malayalam', false], ['ਪੰਜਾਬੀ', 'Punjabi', false], ['ଓଡ଼ିଆ', 'Odia', false], ['অসমীয়া', 'Assamese', false],
  ['اردو', 'Urdu', false], ['संस्कृतम्', 'Sanskrit', false], ['कोंकणी', 'Konkani', false], ['मैथिली', 'Maithili', false],
  ['डोगरी', 'Dogri', false], ['बड़ो', 'Bodo', false], ['ᱥᱟᱱᱛᱟᱲᱤ', 'Santali', false], ['कॉशुर', 'Kashmiri', false],
  ['नेपाली', 'Nepali', false], ['মৈতৈলোন্', 'Manipuri', false],
].map(([native, name, isLive]) => ({ native: native as string, name: name as string, ...(isLive ? live : soon) }))

export type AudienceKey = 'colleges' | 'hr' | 'gov'
// `image` URLs are copyright-free Pexels photos (free license, hotlinkable).
export const AUDIENCES: Record<AudienceKey, { icon: LucideIcon; label: string; title: string; sub: string; cta: string; points: string[]; image: string }> = {
  colleges: { icon: GraduationCap, label: 'Colleges & Universities', title: 'Colleges & Universities', sub: 'Run placement-ready mock interviews for every student, score them fairly, and show recruiters verified readiness.', cta: 'Talk to placements team', points: ['Bulk mock interviews across departments', 'Branch-wise readiness analytics for the TPO', 'Recruiter-shareable verified scorecards', 'Practice in the regional language students think in'], image: 'https://images.pexels.com/photos/1438072/pexels-photo-1438072.jpeg?auto=compress&cs=tinysrgb&w=900' },
  hr: { icon: Building2, label: 'Corporate HR', title: 'Corporate HR', sub: 'Screen thousands of applicants in days, not weeks — with structured, bias-checked AI first rounds.', cta: 'Book an HR demo', points: ['Magic-link invites, zero scheduling', 'Role-specific question banks & rubrics', 'Kanban pipeline + ATS-ready exports', 'Under ₹12 per fully-scored session'], image: 'https://images.pexels.com/photos/3182812/pexels-photo-3182812.jpeg?auto=compress&cs=tinysrgb&w=900' },
  gov: { icon: Landmark, label: 'Government Skilling', title: 'Government Skilling', sub: 'Assess lakhs of candidates across a state in their mother tongue, with full DPDP audit trails.', cta: 'Request L1 bid pricing', points: ['22-language coverage for true reach', 'Consent ledger & right-to-erasure built in', 'India data residency, no exceptions', 'Proven at 20-lakh-candidate scale'], image: 'https://images.pexels.com/photos/7688336/pexels-photo-7688336.jpeg?auto=compress&cs=tinysrgb&w=900' },
}

export const COMPETENCIES = [
  { name: 'Communication', score: '9.0 / 10', pct: 90 },
  { name: 'Problem Solving', score: '8.4 / 10', pct: 84 },
  { name: 'Role Knowledge', score: '8.8 / 10', pct: 88 },
  { name: 'Structured Thinking', score: '7.9 / 10', pct: 79 },
  { name: 'Confidence', score: '8.6 / 10', pct: 86 },
]

// Capability, not track record: the platform is built to these numbers, it has
// not yet run them. "20 lakh+ sessions run" was a claim about a demo tier that
// has run none, and the sort of line a procurement reviewer checks.
export const METRICS = [
  { value: '20 lakh', label: 'candidates per cycle, by design' },
  { value: '<2s', label: 'target response latency' },
  { value: '22', label: 'Indian languages planned' },
  { value: '₹12', label: 'target cost per session' },
]


export const COMPLIANCE: { icon: LucideIcon; title: string; desc: string }[] = [
  { icon: Scale, title: 'DPDP Act 2023', desc: 'Consent-led data processing aligned to India’s data protection law.' },
  { icon: ClipboardCheck, title: 'Consent ledger', desc: 'Every consent event is logged and auditable, end to end.' },
  { icon: MapPin, title: 'India residency', desc: 'All data stored and processed within India. No exceptions.' },
  { icon: Trash2, title: 'Right to erasure', desc: 'Candidates can self-serve delete their data anytime.' },
]

export const FAQS = [
  ['Is AntHire really conversational, or just recorded questions?', 'Fully conversational. The avatar listens to your answer, asks relevant follow-ups, and adapts difficulty in real time — averaging under 2 seconds to respond.'],
  ['Which languages can I interview in today?', 'English, Hindi and Telugu are live right now. The remaining 19 Eighth-Schedule languages are rolling out through 2026, voice-first.'],
  ['How is my data handled?', 'All processing happens within India. We run on a consent-led model with a full consent ledger and self-serve right-to-erasure, aligned to the DPDP Act 2023.'],
  ['What does the scorecard actually measure?', 'A role-specific competency rubric — communication, problem solving, role knowledge and more — with strengths, gaps, and transcript highlights, exportable as PDF.'],
  ['Can institutions run this at scale?', 'Yes. The platform is built to interview up to 20 lakh candidates per cycle at under ₹12 per session, with HR and admin consoles for management.'],
  ['Do candidates need to install anything?', 'No. Everything runs in the browser with a quick mic check. Invites can be sent as magic links — no account required to take an interview.'],
] as const
