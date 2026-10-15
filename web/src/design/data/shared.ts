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

// Avatar fills. They carry WHITE initials, so every stop is dark enough for
// white text at 4.5:1 or better (the lightest here is 5.6:1). The previous set
// included #ffb764 and #0fb7fa, on which white was 1.7:1 and 2.3:1.
export const GRADIENTS: string[] = [
  'linear-gradient(135deg,#5b38f0,#7a3fe0)',
  'linear-gradient(135deg,#0f6d33,#0a6b78)',
  'linear-gradient(135deg,#b02a66,#6d4aa8)',
  'linear-gradient(135deg,#9a4a0b,#b02a66)',
  'linear-gradient(135deg,#0a6b78,#0b62d6)',
  'linear-gradient(135deg,#6d4aa8,#0b62d6)',
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

export const LANG_LABEL: Record<Lang, string> = { EN: 'English', HI: 'हिन्दी', TE: 'తెలుగు' };
