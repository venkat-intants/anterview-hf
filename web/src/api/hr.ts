// hr.ts — platform-owner + company-super-admin management (three-tier hierarchy).
// Calls data_gateway (VITE_API_BASE_URL) via the central client (token + refresh).
//
//   platform_owner  → companies + the ONE company super admin (this file's
//                     /admin/companies endpoints)
//   super_admin     → HR managers in its OWN company (/admin/hr-managers)

import { apiGet, apiPost, apiPut, apiDelete } from './client';

export interface Company {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  hr_count: number;
  /** Whether this company already has its (single) super admin. */
  has_admin: boolean;
  admin_email: string | null;
  created_at: string;
}

export interface CompanyAdmin {
  user_id: string;
  email: string;
  full_name: string;
  company_id: string;
  must_change_password: boolean;
  created_at: string;
}

export interface HrManager {
  user_id: string;
  email: string;
  full_name: string;
  company_id: string;
  must_change_password: boolean;
  created_at: string;
}

// ── Platform owner — real dashboard stats ───────────────────────────────────

export interface PlatformStats {
  companies: number;
  super_admins: number;
  hr_managers: number;
  candidates: number;
  interviews_total: number;
  interviews_30d: number;
}

export function getPlatformStats(): Promise<PlatformStats> {
  return apiGet<PlatformStats>('/admin/platform-stats');
}

// ── Platform owner — companies ──────────────────────────────────────────────

export function listCompanies(): Promise<Company[]> {
  return apiGet<Company[]>('/admin/companies');
}

export function createCompany(name: string, slug?: string): Promise<Company> {
  return apiPost<Company>('/admin/companies', slug ? { name, slug } : { name });
}

/** Soft-delete a company and all its member users (super admin + HR managers). */
export function deleteCompany(companyId: string): Promise<void> {
  return apiDelete<void>(`/admin/companies/${companyId}`);
}

// ── Platform owner — the one super admin per company ────────────────────────

export function getCompanyAdmin(companyId: string): Promise<CompanyAdmin> {
  return apiGet<CompanyAdmin>(`/admin/companies/${companyId}/admin`);
}

export function createCompanyAdmin(
  companyId: string,
  body: { email: string; full_name: string; password?: string },
): Promise<CompanyAdmin> {
  return apiPost<CompanyAdmin>(`/admin/companies/${companyId}/admin`, body);
}

/** Remove a company's super admin (a fresh one can then be created). */
export function deleteCompanyAdmin(companyId: string): Promise<void> {
  return apiDelete<void>(`/admin/companies/${companyId}/admin`);
}

/** Platform-owner read-only view of a company's HR managers. */
export function listCompanyHrManagers(companyId: string): Promise<HrManager[]> {
  return apiGet<HrManager[]>(`/admin/companies/${companyId}/hr-managers`);
}

// ── Company super admin — HR managers in the caller's own company ───────────

export function listMyHrManagers(): Promise<HrManager[]> {
  return apiGet<HrManager[]>('/admin/hr-managers');
}

export function createMyHrManager(
  body: { email: string; full_name: string; password?: string },
): Promise<HrManager> {
  return apiPost<HrManager>('/admin/hr-managers', body);
}

/** Soft-delete an HR manager in the caller's own company. */
export function deleteMyHrManager(userId: string): Promise<void> {
  return apiDelete<void>(`/admin/hr-managers/${userId}`);
}

// ── DPDP audit log (platform owner) ─────────────────────────────────────────

export interface AuditEvent {
  ts: string;
  kind: string; // admin_action | consent_granted | consent_denied | consent_revoked
  summary: string;
  actor: string;
}

export function listAuditLog(limit = 100): Promise<AuditEvent[]> {
  return apiGet<AuditEvent[]>(`/admin/audit-log?limit=${limit}`);
}

// ── Platform feature flags (platform owner) ─────────────────────────────────

export interface FeatureFlag {
  key: string;
  label: string;
  description: string | null;
  enabled: boolean;
  updated_at: string | null;
}

export function listFeatureFlags(): Promise<FeatureFlag[]> {
  return apiGet<FeatureFlag[]>('/admin/feature-flags');
}

export function setFeatureFlag(key: string, enabled: boolean): Promise<FeatureFlag> {
  return apiPut<FeatureFlag>(`/admin/feature-flags/${key}`, { enabled });
}

// ── HR hiring analytics (funnel + averages) — powers the HR console stats ─────

export interface HrFunnel {
  total_applicants: number;
  shortlisted: number;
  exam_taken: number;
  exam_passed: number;
  interview_invited: number;
  interview_completed: number;
  hired: number;
  rejected: number;
}

export interface HrAverages {
  avg_ats: number | null;
  avg_exam_percent: number | null;
  avg_interview_composite: number | null;
}

export interface HrAnalytics {
  funnel: HrFunnel;
  averages: HrAverages;
}

export function getHrAnalytics(): Promise<HrAnalytics> {
  return apiGet<HrAnalytics>('/hr/analytics');
}
