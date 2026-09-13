// companyBoard.ts — the company super admin's hiring board (E3). Read-only.

import { apiGet } from './client';
import type { RequisitionDashboard } from './requisitions';

export type HealthBand = 'on_track' | 'at_risk' | 'watch' | 'not_published';

export interface OpeningHealth {
  band: HealthBand;
  /** The reason, in words — computed from how candidates have actually moved. */
  reason: string;
  projected_fill_date: string | null;
  hires_per_week: number | null;
  /** True while the hire ratio is an assumption, before the opening has its own. */
  assumed_hire_ratio: boolean;
}

export interface BoardOpening {
  requisition_id: string;
  title: string;
  location: string | null;
  status: 'open' | 'paused';
  /** Published, a draft only, or no workflow at all. */
  workflow_state: 'published' | 'draft' | 'none';
  published_version: number | null;
  draft_version: number | null;
  accepting_applications: boolean;
  applied: number;
  in_play: number;
  target_hires: number | null;
  hired: number;
  awaiting_decision: number;
  closes_at: string | null;
  health: OpeningHealth;
}

export interface HiringBoard {
  generated_at: string;
  summary: Record<HealthBand, number>;
  /** Worst health first. */
  openings: BoardOpening[];
}

export function getHiringBoard(): Promise<HiringBoard> {
  return apiGet<HiringBoard>('/admin/hiring-board');
}

/** One opening's dashboard, as the company super admin may see it: read-only. */
export function getCompanyRequisitionDashboard(id: string): Promise<RequisitionDashboard> {
  return apiGet<RequisitionDashboard>(`/admin/requisitions/${id}/dashboard`);
}
