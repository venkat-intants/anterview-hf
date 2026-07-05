// Auth types — mirror backend Pydantic models in data_gateway
// Shape matches LLD §3.1 JWT spec

export interface LoginResponse {
  access_token: string;
  expires_in: number;
  user_id: string;
  roles: string[];
}

export interface RegisterResponse {
  /** True when the account was created but NOT signed in — email must be confirmed first. */
  verification_required?: boolean;
  /** Present only when auto-logged-in (verification not required). */
  access_token?: string | null;
  expires_in?: number | null;
  user_id: string;
  roles: string[];
}

export interface MeResponse {
  user_id: string;
  full_name: string;
  email: string;
  roles: string[];
  has_resume: boolean;
  /** HR workflow — true when the account still has its bootstrap password. */
  must_change_password?: boolean;
  /** Email system — whether the address has been confirmed. */
  email_verified?: boolean;
  /** Email system — opt-in for the "new sign-in" alert email. */
  notify_login_email?: boolean;
  // Editable self-service profile (candidate / HR / admin).
  linkedin_url?: string | null;
  github_url?: string | null;
  phone?: string | null;
  preferred_language?: string | null;
  avatar_url?: string | null;
  headline?: string | null;
  bio?: string | null;
  /** Candidate: 'student' | 'employed'. */
  employment_status?: string | null;
  desired_roles?: string | null;
  /** HR: work/official email. */
  official_email?: string | null;
  location?: string | null;
  /** Read-only tenant context (HR's company, set by admins). */
  company_id?: string | null;
  company_name?: string | null;
}

/** Body for PATCH /auth/me/profile — all fields optional; '' clears a field. */
export interface ProfileUpdate {
  full_name?: string;
  phone?: string;
  preferred_language?: string;
  linkedin_url?: string;
  github_url?: string;
  avatar_url?: string;
  headline?: string;
  bio?: string;
  employment_status?: string;
  desired_roles?: string;
  official_email?: string;
  location?: string;
}

/** Read-only view of another user's profile (GET /users/{id}/profile). */
export interface PublicProfile {
  user_id: string;
  full_name: string | null;
  email: string | null;
  roles: string[];
  avatar_url?: string | null;
  headline?: string | null;
  bio?: string | null;
  employment_status?: string | null;
  desired_roles?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  location?: string | null;
  phone?: string | null;
  official_email?: string | null;
  has_resume: boolean;
  company_name?: string | null;
  created_at?: string | null;
}

export interface LogoutResponse {
  ok: boolean;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  full_name: string;
  email: string;
  password: string;
}

export interface AuthUser {
  user_id: string;
  full_name: string;
  email: string;
  roles: string[];
  /** HR workflow — drives the force-password-change redirect on first login. */
  must_change_password?: boolean;
}

export interface AuthState {
  accessToken: string | null;
  user: AuthUser | null;
  isAuthenticated: boolean;
}
