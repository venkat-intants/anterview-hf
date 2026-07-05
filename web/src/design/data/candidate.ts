import type { Competency, Lang } from './shared';

export interface JobItem {
  id: string;
  title: string;
  company: string;
  level: string;
  location: string;
  langs: Lang[];
  questions: number;
  minutes: number;
  tags: string[];
  gradient: string;
}

export const JOBS: JobItem[] = [
  { id: 'frontend-l2', title: 'Frontend Engineer', company: 'Nexus Fintech', level: 'L2', location: 'Bengaluru · Hybrid', langs: ['EN', 'HI'], questions: 10, minutes: 15, tags: ['React', 'TypeScript', 'CSS'], gradient: 'linear-gradient(135deg,#0088ff,#a887dc)' },
  { id: 'backend-l2', title: 'Backend Engineer', company: 'Nexus Fintech', level: 'L2', location: 'Remote · India', langs: ['EN', 'HI'], questions: 12, minutes: 18, tags: ['Node', 'Postgres', 'System design'], gradient: 'linear-gradient(135deg,#16c253,#0088ff)' },
  { id: 'data-l1', title: 'Data Analyst', company: 'Nexus Fintech', level: 'L1', location: 'Hyderabad · Onsite', langs: ['EN', 'TE'], questions: 8, minutes: 12, tags: ['SQL', 'Python', 'Dashboards'], gradient: 'linear-gradient(135deg,#0fb7fa,#16c253)' },
  { id: 'pm-l1', title: 'Associate Product Manager', company: 'Nexus Fintech', level: 'L1', location: 'Bengaluru · Hybrid', langs: ['EN', 'HI'], questions: 10, minutes: 16, tags: ['Product sense', 'Metrics', 'Comms'], gradient: 'linear-gradient(135deg,#a887dc,#0088ff)' },
  { id: 'sales-assoc', title: 'Sales Associate', company: 'Nexus Fintech', level: 'Entry', location: 'Multiple · India', langs: ['HI', 'TE'], questions: 8, minutes: 10, tags: ['Persuasion', 'CRM', 'Rapport'], gradient: 'linear-gradient(135deg,#ffb764,#dd55e7)' },
];

export interface DashStat {
  label: string;
  value: string;
  delta: string;
  trend: 'up' | 'down' | 'flat';
}

export const CANDIDATE_STATS: DashStat[] = [
  { label: 'Readiness', value: '78', delta: '+12 this week', trend: 'up' },
  { label: 'Interviews taken', value: '6', delta: '2 this week', trend: 'up' },
  { label: 'Best score', value: '88', delta: 'Frontend L2', trend: 'flat' },
  { label: 'Practice streak', value: '5d', delta: 'Keep it up', trend: 'up' },
];

export interface NudgeItem {
  id: string;
  title: string;
  body: string;
  tone: 'electric' | 'amber' | 'forest';
}

export const NUDGES: NudgeItem[] = [
  { id: 'n1', title: 'Polish your STAR answers', body: 'Your problem-solving dipped on the last attempt — try 2 timed drills.', tone: 'amber' },
  { id: 'n2', title: 'New role matches you', body: 'Backend Engineer L2 fits your top skills. Take the exam this week.', tone: 'electric' },
  { id: 'n3', title: 'Streak milestone', body: 'One more day of practice unlocks your 6-day streak badge.', tone: 'forest' },
];

export interface HistoryRow {
  id: string;
  role: string;
  date: string;
  score: number;
  lang: Lang;
  status: 'Completed' | 'Reviewed' | 'Offer';
}

export const CANDIDATE_HISTORY: HistoryRow[] = [
  { id: 'h1', role: 'Frontend Engineer · L2', date: 'Jun 18, 2026', score: 88, lang: 'HI', status: 'Offer' },
  { id: 'h2', role: 'Backend Engineer · L2', date: 'Jun 14, 2026', score: 79, lang: 'EN', status: 'Reviewed' },
  { id: 'h3', role: 'Data Analyst · L1', date: 'Jun 09, 2026', score: 72, lang: 'TE', status: 'Completed' },
  { id: 'h4', role: 'Frontend Engineer · L2', date: 'Jun 02, 2026', score: 66, lang: 'HI', status: 'Completed' },
];

export interface TranscriptTurn {
  speaker: 'AI' | 'You';
  text: string;
  t: string;
}

export const SCORE_COMPETENCIES: Competency[] = [
  { name: 'Communication', score: 88 },
  { name: 'Role Knowledge', score: 84 },
  { name: 'Problem Solving', score: 76 },
  { name: 'Culture Fit', score: 82 },
  { name: 'Confidence', score: 80 },
];

export const SCORE_STRENGTHS: string[] = [
  'Clear, structured answers with concrete examples',
  'Strong grasp of React rendering & state',
  'Calm pacing and good rapport with the interviewer',
];

export const SCORE_GAPS: string[] = [
  'Edge-case reasoning under time pressure',
  'Could quantify impact with more metrics',
];

export const SCORE_TRANSCRIPT: TranscriptTurn[] = [
  { speaker: 'AI', text: 'Walk me through how you would optimise a slow React list of 10,000 rows.', t: '00:12' },
  { speaker: 'You', text: 'I would virtualise the list so only visible rows render, memoise row components, and stabilise keys to avoid re-mounts.', t: '00:21' },
  { speaker: 'AI', text: 'Good. What trade-offs does virtualisation introduce?', t: '00:48' },
  { speaker: 'You', text: 'Scroll restoration and variable row heights get trickier, and find-in-page stops working for off-screen rows.', t: '00:55' },
];

export interface InterviewQuestion {
  id: number;
  prompt: string;
  competency: string;
}

export const LIVE_QUESTIONS: InterviewQuestion[] = [
  { id: 1, prompt: 'Tell me about a project you are proud of and your specific role in it.', competency: 'Communication' },
  { id: 2, prompt: 'How would you optimise a React list rendering 10,000 rows?', competency: 'Role Knowledge' },
  { id: 3, prompt: 'Describe a time you disagreed with a teammate. How did you resolve it?', competency: 'Culture Fit' },
  { id: 4, prompt: 'A user reports the app is slow only on mobile. How do you debug it?', competency: 'Problem Solving' },
];
