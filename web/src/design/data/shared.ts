/** Shared domain types + helpers used across roles. */

export type Lang = 'EN' | 'HI' | 'TE';

export interface Competency {
  name: string;
  score: number; // 0–100
}

export interface Gradiented {
  initials: string;
  gradient: string;
}

export const GRADIENTS: string[] = [
  'linear-gradient(135deg,#0088ff,#a887dc)',
  'linear-gradient(135deg,#16c253,#0088ff)',
  'linear-gradient(135deg,#dd55e7,#a887dc)',
  'linear-gradient(135deg,#ffb764,#dd55e7)',
  'linear-gradient(135deg,#0fb7fa,#16c253)',
  'linear-gradient(135deg,#a887dc,#0088ff)',
];

export function gradientFor(seed: number): string {
  return GRADIENTS[Math.abs(seed) % GRADIENTS.length];
}

export function initialsOf(name: string): string {
  return name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
}

export function scoreTone(score: number): 'forest' | 'electric' | 'amber' | 'ember' {
  if (score >= 85) return 'forest';
  if (score >= 70) return 'electric';
  if (score >= 55) return 'amber';
  return 'ember';
}

export function scoreColor(score: number): string {
  if (score >= 85) return '#27c93f';
  if (score >= 70) return '#0088ff';
  if (score >= 55) return '#ffb764';
  return '#e6714f';
}

export const LANG_LABEL: Record<Lang, string> = { EN: 'English', HI: 'हिन्दी', TE: 'తెలుగు' };
