// careers.ts — the public job board. NO LOGIN.
//
// Same posture as `publicApply.ts` and for the same reason: a visitor browsing
// roles has no session, and the shared client's 401 → refresh → redirect-to-
// login path would replace the board with a sign-in page for an account they
// have not got. So this module uses plain `fetch` and never touches the
// authenticated client.
//
// One request serves the whole page — cards, total, and the filter options —
// because a board is useless until all three have arrived, and two round trips
// would render a page whose dropdowns pop in afterwards.

import { ApiError } from './client';

const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

/** One role, as a card. Enough to decide whether to open it. */
export interface JobCard {
  requisition_id: string;
  title: string;
  level: string;
  department: string | null;
  location: string | null;
  employment_type: string | null;
  experience_min_years: number | null;
  experience_max_years: number | null;
  /** Trimmed server-side — a card is a glance, the detail page has them all. */
  skills: string[];
  posted_at: string;
  /**
   * Present only when the opening publishes its band. Null covers both a
   * withheld salary and an unrecorded one, deliberately: this page cannot tell
   * them apart and therefore cannot leak which it is.
   */
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
}

/**
 * What this company actually has open, so a dropdown offers only real choices.
 *
 * Computed across every visible role rather than the filtered result — a
 * department list that shrinks as you filter cannot be used to change your
 * mind, which is most of what a filter is for.
 */
export interface BoardFilters {
  departments: string[];
  locations: string[];
  employment_types: string[];
}

export interface CareersBoard {
  company_name: string;
  company_slug: string;
  total: number;
  page: number;
  per_page: number;
  filters: BoardFilters;
  items: JobCard[];
}

export interface BoardQuery {
  q?: string;
  department?: string;
  employment_type?: string;
  location?: string;
  /** "I have this much experience" — matches roles asking for no more. */
  max_experience_years?: number;
  /**
   * "Pays at least this much." Roles that do not publish a salary are still
   * returned — absence is not a mismatch, and excluding them would empty most
   * boards.
   */
  min_salary?: number;
  /**
   * Ordering. `relevance` needs a search term to mean anything and the server
   * falls back to `newest` without one.
   */
  sort?: 'newest' | 'relevance';
  page?: number;
}

async function readError(res: Response): Promise<never> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`;
  throw new ApiError(detail, res.status);
}

/**
 * One company's open roles.
 *
 * 404 covers an unknown slug and a deactivated company alike — there is no
 * useful distinction to draw for a visitor, and drawing one would say whether
 * a company exists.
 */
export async function getCareersBoard(
  slug: string,
  query: BoardQuery = {},
): Promise<CareersBoard> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    // Empty string is how a cleared <select> reports itself, and sending it
    // would filter on "" and return nothing. Omitted means unfiltered.
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/careers/${encodeURIComponent(slug)}${qs ? `?${qs}` : ''}`,
  );
  if (!res.ok) return readError(res);
  return (await res.json()) as CareersBoard;
}
