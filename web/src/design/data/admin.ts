import type { Lang } from './shared';

export interface AdminStat {
  label: string;
  value: string;
  delta: string;
  trend: 'up' | 'down' | 'flat';
  spark: number[];
  feature?: boolean;
}

export const ADMIN_STATS: AdminStat[] = [
  { label: 'Active sessions', value: '128', delta: 'live now', trend: 'up', spark: [60, 72, 65, 88, 94, 110, 128], feature: true },
  { label: 'Interviews today', value: '1,204', delta: '+9% vs yest.', trend: 'up', spark: [820, 910, 880, 1010, 1120, 1180, 1204] },
  { label: 'Avg latency', value: '1.9s', delta: 'within SLA', trend: 'flat', spark: [2.4, 2.2, 2.1, 2.0, 1.95, 1.92, 1.9] },
  { label: 'Spend / interview', value: '₹11.40', delta: 'cap ₹12.00', trend: 'down', spark: [13.2, 12.8, 12.3, 12.0, 11.8, 11.6, 11.4] },
];

export interface ServiceHealth {
  name: string;
  status: 'healthy' | 'degraded' | 'down';
  latency: string;
  uptime: string;
}

export const SERVICES: ServiceHealth[] = [
  { name: 'API Gateway', status: 'healthy', latency: '42ms', uptime: '99.99%' },
  { name: 'Interview Orchestrator', status: 'healthy', latency: '88ms', uptime: '99.97%' },
  { name: 'Scoring Engine', status: 'healthy', latency: '210ms', uptime: '99.95%' },
  { name: 'Notifications', status: 'degraded', latency: '640ms', uptime: '99.20%' },
  { name: 'Media Relay', status: 'healthy', latency: '36ms', uptime: '99.98%' },
];

export interface ProviderHealth {
  name: string;
  role: string;
  status: 'operational' | 'degraded' | 'down';
}

export const PROVIDERS: ProviderHealth[] = [
  { name: 'Gemini', role: 'Reasoning / scoring', status: 'operational' },
  { name: 'Sarvam', role: 'Indic speech (HI/TE)', status: 'operational' },
  { name: 'Tavus', role: 'Avatar video', status: 'degraded' },
  { name: 'LiveKit', role: 'WebRTC transport', status: 'operational' },
];

export interface AdminInterviewRow {
  id: string;
  candidate: string;
  role: string;
  company: string;
  date: string;
  score: number | null;
  status: 'Completed' | 'In progress' | 'Flagged';
  lang: Lang;
  seed: number;
}

export const ADMIN_INTERVIEWS: AdminInterviewRow[] = [
  { id: 'in-1042', candidate: 'Sara Khan', role: 'Frontend · L2', company: 'Nexus Fintech', date: 'Jun 24 · 14:02', score: 86, status: 'Completed', lang: 'HI', seed: 2 },
  { id: 'in-1041', candidate: 'Arvind M', role: 'Backend · L2', company: 'Nexus Fintech', date: 'Jun 24 · 13:30', score: null, status: 'In progress', lang: 'TE', seed: 0 },
  { id: 'in-1040', candidate: 'Rahul Verma', role: 'Frontend · L2', company: 'Skill Bharat', date: 'Jun 24 · 12:10', score: 61, status: 'Flagged', lang: 'HI', seed: 0 },
  { id: 'in-1039', candidate: 'Kavya Iyer', role: 'Frontend · L2', company: 'Nexus Fintech', date: 'Jun 24 · 11:48', score: 81, status: 'Completed', lang: 'EN', seed: 5 },
  { id: 'in-1038', candidate: 'Dev Patel', role: 'PM · L1', company: 'Govt Skilling', date: 'Jun 24 · 10:22', score: 74, status: 'Completed', lang: 'HI', seed: 4 },
];

export interface ProctorFlag {
  t: string;
  label: string;
  severity: 'info' | 'warn' | 'high';
}

export const PROCTOR_FLAGS: ProctorFlag[] = [
  { t: '02:14', label: 'Face out of frame (2.1s)', severity: 'warn' },
  { t: '05:48', label: 'Second voice detected', severity: 'high' },
  { t: '09:03', label: 'Tab switch', severity: 'warn' },
  { t: '11:20', label: 'Lighting low', severity: 'info' },
];

export interface UsageBar {
  day: string;
  value: number;
}

export const ADMIN_USAGE: UsageBar[] = [
  { day: 'Mon', value: 64 },
  { day: 'Tue', value: 78 },
  { day: 'Wed', value: 72 },
  { day: 'Thu', value: 91 },
  { day: 'Fri', value: 100 },
  { day: 'Sat', value: 58 },
  { day: 'Sun', value: 40 },
];

export interface JdItem {
  id: string;
  title: string;
  level: string;
  competencies: string[];
  status: 'Published' | 'Draft';
  updated: string;
}

export const JOB_JDS: JdItem[] = [
  { id: 'jd1', title: 'Frontend Engineer', level: 'L2', competencies: ['React', 'TypeScript', 'System design'], status: 'Published', updated: '2d ago' },
  { id: 'jd2', title: 'Backend Engineer', level: 'L2', competencies: ['Node', 'Postgres', 'APIs'], status: 'Published', updated: '4d ago' },
  { id: 'jd3', title: 'Data Analyst', level: 'L1', competencies: ['SQL', 'Python', 'Storytelling'], status: 'Published', updated: '1w ago' },
  { id: 'jd4', title: 'Product Manager', level: 'L1', competencies: ['Product sense', 'Metrics', 'Comms'], status: 'Draft', updated: '3h ago' },
];

/* ── Super Admin ── */

export interface CompanyRow {
  id: string;
  name: string;
  plan: 'Starter' | 'Growth' | 'Enterprise';
  hrManagers: number;
  seats: string;
  interviews: number;
  status: 'Active' | 'Trial' | 'Suspended';
  seed: number;
}

export const COMPANIES: CompanyRow[] = [
  { id: 'co1', name: 'Nexus Fintech', plan: 'Enterprise', hrManagers: 12, seats: '480 / 500', interviews: 18420, status: 'Active', seed: 1 },
  { id: 'co2', name: 'Skill Bharat', plan: 'Growth', hrManagers: 6, seats: '180 / 200', interviews: 9240, status: 'Active', seed: 2 },
  { id: 'co3', name: 'Govt Skilling Mission', plan: 'Enterprise', hrManagers: 22, seats: '1,900 / 2,000', interviews: 51200, status: 'Active', seed: 3 },
  { id: 'co4', name: 'Coastal Logistics', plan: 'Starter', hrManagers: 2, seats: '40 / 50', interviews: 820, status: 'Trial', seed: 4 },
  { id: 'co5', name: 'Meridian BPO', plan: 'Growth', hrManagers: 8, seats: '210 / 250', interviews: 12080, status: 'Suspended', seed: 0 },
];

export interface FeatureFlag {
  id: string;
  name: string;
  desc: string;
  on: boolean;
}

export const FEATURE_FLAGS: FeatureFlag[] = [
  { id: 'ff1', name: 'Telugu speech model', desc: 'Sarvam TE pipeline for South India tenants', on: true },
  { id: 'ff2', name: 'Avatar proctoring v2', desc: 'MediaPipe gaze + multi-face detection', on: true },
  { id: 'ff3', name: 'Self-serve billing', desc: 'Let admins manage seats without sales', on: false },
  { id: 'ff4', name: 'Resume auto-parse', desc: 'Extract skills from uploaded CVs', on: true },
];

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  target: string;
  kind: 'consent' | 'erasure' | 'admin' | 'access';
  when: string;
}

export const AUDIT_LOG: AuditEntry[] = [
  { id: 'au1', actor: 'candidate:rahul.v', action: 'Granted recording consent', target: 'session in-1040', kind: 'consent', when: '12:10' },
  { id: 'au2', actor: 'candidate:meena.r', action: 'Requested data erasure (DPDP)', target: 'account meena.r', kind: 'erasure', when: '11:42' },
  { id: 'au3', actor: 'hr:sneha.reddy', action: 'Viewed scorecard', target: 'candidate sara.k', kind: 'access', when: '11:20' },
  { id: 'au4', actor: 'admin:platform', action: 'Suspended tenant', target: 'Meridian BPO', kind: 'admin', when: '10:05' },
  { id: 'au5', actor: 'candidate:dev.p', action: 'Granted recording consent', target: 'session in-1038', kind: 'consent', when: '10:22' },
];

export interface SuperStat {
  label: string;
  value: string;
  delta: string;
  trend: 'up' | 'down' | 'flat';
}

export const SUPER_STATS: SuperStat[] = [
  { label: 'Tenants', value: '48', delta: '+5 this month', trend: 'up' },
  { label: 'HR managers', value: '312', delta: '+24', trend: 'up' },
  { label: 'Interviews (MTD)', value: '91.4k', delta: '+12%', trend: 'up' },
  { label: 'Platform uptime', value: '99.98%', delta: '30-day', trend: 'flat' },
];
