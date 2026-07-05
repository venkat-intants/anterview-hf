import type { Lang } from './shared';

export type Stage = 'applied' | 'screened' | 'interviewed' | 'offer' | 'hired';

export interface PipelineCard {
  id: string;
  name: string;
  role: string;
  lang: Lang;
  score?: number;
  seed: number;
}

export const PIPELINE: Record<Stage, PipelineCard[]> = {
  applied: [
    { id: 'a1', name: 'Rahul Verma', role: 'Frontend · L2', lang: 'HI', seed: 0 },
    { id: 'a2', name: 'Anjali Nair', role: 'Data Analyst', lang: 'EN', seed: 1 },
    { id: 'a3', name: 'Mohit Rao', role: 'Backend · L2', lang: 'TE', seed: 3 },
  ],
  screened: [
    { id: 's1', name: 'Kavya Iyer', role: 'Frontend · L2', lang: 'EN', score: 81, seed: 5 },
    { id: 's2', name: 'Dev Patel', role: 'PM · L1', lang: 'HI', score: 74, seed: 4 },
  ],
  interviewed: [
    { id: 'i1', name: 'Sara Khan', role: 'Frontend · L2', lang: 'HI', score: 86, seed: 2 },
    { id: 'i2', name: 'Arvind M', role: 'Backend · L2', lang: 'TE', score: 79, seed: 0 },
  ],
  offer: [{ id: 'o1', name: 'Priya Sharma', role: 'Frontend · L2', lang: 'HI', score: 88, seed: 5 }],
  hired: [{ id: 'h1', name: 'Vikram Joshi', role: 'Data Analyst', lang: 'EN', score: 90, seed: 1 }],
};

export const STAGE_META: { key: Stage; title: string; dot: string }[] = [
  { key: 'applied', title: 'Applied', dot: '#888b91' },
  { key: 'screened', title: 'Screened', dot: '#0088ff' },
  { key: 'interviewed', title: 'Interviewed', dot: '#a887dc' },
  { key: 'offer', title: 'Offer', dot: '#ffb764' },
  { key: 'hired', title: 'Hired', dot: '#27c93f' },
];

export interface Applicant {
  id: string;
  name: string;
  email: string;
  role: string;
  stage: Stage;
  score: number | null;
  applied: string;
  lang: Lang;
  seed: number;
}

export const APPLICANTS: Applicant[] = [
  { id: 'ap1', name: 'Sara Khan', email: 'sara.k@email.com', role: 'Frontend · L2', stage: 'interviewed', score: 86, applied: '2d ago', lang: 'HI', seed: 2 },
  { id: 'ap2', name: 'Priya Sharma', email: 'priya.s@email.com', role: 'Frontend · L2', stage: 'offer', score: 88, applied: '3d ago', lang: 'HI', seed: 5 },
  { id: 'ap3', name: 'Vikram Joshi', email: 'vikram.j@email.com', role: 'Data Analyst', stage: 'hired', score: 90, applied: '5d ago', lang: 'EN', seed: 1 },
  { id: 'ap4', name: 'Arvind M', email: 'arvind.m@email.com', role: 'Backend · L2', stage: 'interviewed', score: 79, applied: '2d ago', lang: 'TE', seed: 0 },
  { id: 'ap5', name: 'Kavya Iyer', email: 'kavya.i@email.com', role: 'Frontend · L2', stage: 'screened', score: 81, applied: '1d ago', lang: 'EN', seed: 5 },
  { id: 'ap6', name: 'Dev Patel', email: 'dev.p@email.com', role: 'PM · L1', stage: 'screened', score: 74, applied: '1d ago', lang: 'HI', seed: 4 },
  { id: 'ap7', name: 'Rahul Verma', email: 'rahul.v@email.com', role: 'Frontend · L2', stage: 'applied', score: null, applied: '4h ago', lang: 'HI', seed: 0 },
];

export interface ExamItem {
  id: string;
  title: string;
  meta: string;
  questions: number;
  status: 'live' | 'draft';
  attempts: number;
  avgScore: number;
}

export const EXAMS: ExamItem[] = [
  { id: 'ex-fe', title: 'Frontend Engineer L2', meta: '10 questions · 15 min · EN/HI', questions: 10, status: 'live', attempts: 142, avgScore: 79 },
  { id: 'ex-be', title: 'Backend Engineer L2', meta: '12 questions · 18 min · EN/HI', questions: 12, status: 'live', attempts: 88, avgScore: 76 },
  { id: 'ex-da', title: 'Data Analyst L1', meta: '8 questions · 12 min · EN/TE', questions: 8, status: 'live', attempts: 61, avgScore: 81 },
  { id: 'ex-pm', title: 'Product Manager L1', meta: '10 questions · 16 min · EN/HI', questions: 10, status: 'draft', attempts: 0, avgScore: 0 },
  { id: 'ex-sa', title: 'Sales Associate', meta: '8 questions · 10 min · HI/TE', questions: 8, status: 'draft', attempts: 0, avgScore: 0 },
];

export interface ExamQuestionDraft {
  id: string;
  text: string;
  competency: string;
  seconds: number;
}

export const EXAM_DRAFT: ExamQuestionDraft[] = [
  { id: 'q1', text: 'Tell me about a frontend project you are proud of.', competency: 'Communication', seconds: 90 },
  { id: 'q2', text: 'How do you optimise rendering for a large list?', competency: 'Role Knowledge', seconds: 120 },
  { id: 'q3', text: 'Describe debugging a tricky layout bug.', competency: 'Problem Solving', seconds: 120 },
  { id: 'q4', text: 'How do you handle disagreement in code review?', competency: 'Culture Fit', seconds: 90 },
];

export const COMPETENCY_OPTIONS: string[] = ['Communication', 'Role Knowledge', 'Problem Solving', 'Culture Fit', 'Confidence'];

export interface ExamResultRow {
  id: string;
  name: string;
  score: number;
  duration: string;
  lang: Lang;
  flagged: boolean;
  seed: number;
}

export const EXAM_RESULTS: ExamResultRow[] = [
  { id: 'r1', name: 'Priya Sharma', score: 88, duration: '14:20', lang: 'HI', flagged: false, seed: 5 },
  { id: 'r2', name: 'Sara Khan', score: 86, duration: '13:55', lang: 'HI', flagged: false, seed: 2 },
  { id: 'r3', name: 'Kavya Iyer', score: 81, duration: '15:01', lang: 'EN', flagged: false, seed: 5 },
  { id: 'r4', name: 'Arvind M', score: 79, duration: '16:12', lang: 'TE', flagged: true, seed: 0 },
  { id: 'r5', name: 'Dev Patel', score: 74, duration: '14:48', lang: 'HI', flagged: false, seed: 4 },
  { id: 'r6', name: 'Rahul Verma', score: 61, duration: '12:30', lang: 'HI', flagged: true, seed: 0 },
];

export interface FunnelStep {
  label: string;
  value: number;
  pct: number;
}

export const FUNNEL: FunnelStep[] = [
  { label: 'Applied', value: 1240, pct: 100 },
  { label: 'Screened', value: 820, pct: 66 },
  { label: 'Interviewed', value: 410, pct: 33 },
  { label: 'Offer', value: 95, pct: 18 },
  { label: 'Hired', value: 62, pct: 12 },
];

export interface DistBucket {
  label: string;
  value: number;
}

export const SCORE_DIST: DistBucket[] = [
  { label: '0-50', value: 8 },
  { label: '50-60', value: 14 },
  { label: '60-70', value: 34 },
  { label: '70-80', value: 62 },
  { label: '80-90', value: 88 },
  { label: '90-100', value: 46 },
];

export interface LangSlice {
  label: string;
  value: number;
  color: string;
}

export const LANG_MIX: LangSlice[] = [
  { label: 'हिन्दी', value: 46, color: '#0088ff' },
  { label: 'English', value: 28, color: '#a887dc' },
  { label: 'తెలుగు', value: 26, color: '#16c253' },
];

export interface TrendPoint {
  day: string;
  interviews: number;
  avg: number;
}

export const HR_TREND: TrendPoint[] = [
  { day: 'Mon', interviews: 38, avg: 74 },
  { day: 'Tue', interviews: 52, avg: 76 },
  { day: 'Wed', interviews: 47, avg: 79 },
  { day: 'Thu', interviews: 61, avg: 78 },
  { day: 'Fri', interviews: 73, avg: 81 },
  { day: 'Sat', interviews: 44, avg: 80 },
  { day: 'Sun', interviews: 29, avg: 77 },
];

export interface HRStat {
  label: string;
  value: string;
  delta: string;
  trend: 'up' | 'down' | 'flat';
  feature?: boolean;
}

export const HR_STATS: HRStat[] = [
  { label: 'Open roles', value: '8', delta: '+2 this week', trend: 'up' },
  { label: 'In pipeline', value: '24', delta: '6 need review', trend: 'flat' },
  { label: 'Interviews / week', value: '344', delta: '+18%', trend: 'up', feature: true },
  { label: 'Avg score', value: '79', delta: '+4 pts', trend: 'up' },
  { label: 'Time to hire', value: '3.2d', delta: '−9 days', trend: 'up' },
];

export interface ActivityItem {
  id: string;
  who: string;
  what: string;
  when: string;
  tone: 'forest' | 'electric' | 'amber' | 'lavender';
}

export const HR_ACTIVITY: ActivityItem[] = [
  { id: 'av1', who: 'Sara Khan', what: 'completed Frontend L2 — scored 86', when: '12 min ago', tone: 'forest' },
  { id: 'av2', who: 'You', what: 'invited 8 applicants to Backend exam', when: '1 hour ago', tone: 'electric' },
  { id: 'av3', who: 'Dev Patel', what: 'moved to Interviewed stage', when: '2 hours ago', tone: 'amber' },
  { id: 'av4', who: 'You', what: 'published “Data Analyst L1” exam', when: 'Yesterday', tone: 'lavender' },
  { id: 'av5', who: 'Vikram Joshi', what: 'accepted offer — Data Analyst', when: 'Yesterday', tone: 'forest' },
];

export const HR_INTERVIEWS: { id: string; name: string; role: string; when: string; status: 'Scheduled' | 'Completed' | 'In progress'; score: number | null; seed: number }[] = [
  { id: 'hi1', name: 'Sara Khan', role: 'Frontend · L2', when: 'Today · 14:00', status: 'Completed', score: 86, seed: 2 },
  { id: 'hi2', name: 'Arvind M', role: 'Backend · L2', when: 'Today · 15:30', status: 'In progress', score: null, seed: 0 },
  { id: 'hi3', name: 'Kavya Iyer', role: 'Frontend · L2', when: 'Tomorrow · 11:00', status: 'Scheduled', score: null, seed: 5 },
  { id: 'hi4', name: 'Dev Patel', role: 'PM · L1', when: 'Jun 26 · 16:00', status: 'Scheduled', score: null, seed: 4 },
];
