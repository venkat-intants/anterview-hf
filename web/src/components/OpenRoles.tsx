// OpenRoles — real jobs a candidate can apply to, inside their own account.
//
// The half of the bridge that was missing. Applying already worked and the
// applications page already showed what happened next; what a signed-in
// candidate could not do was FIND anything. Their Jobs page listed practice
// roles for mock interviews, and real openings were reachable only by a link
// somebody sent them.
//
// THE DISTINCTION THIS COMPONENT HAS TO CARRY
//
// It sits above the practice catalogue on the same page, and the two mean
// completely different things: one applies you to a job at a company, the
// other starts a mock interview with nobody on the other end. Every label here
// exists to keep those apart — the heading says "real", the company is on
// every card, and the button says "View & apply" rather than anything that
// could be read as practice.
//
// Getting that wrong would be worse than not building this: a candidate who
// thinks they have applied, and has not, finds out by never hearing back.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { listOpenRoles, type OpenRole } from '@/api/applications';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Building2, Check, ExternalLink } from '@/design/components/icons';

const EMPLOYMENT_LABELS: Record<string, string> = {
  full_time: 'Full-time',
  part_time: 'Part-time',
  contract: 'Contract',
  internship: 'Internship',
  temporary: 'Temporary',
};

/** "Bengaluru · Full-time · Engineering", skipping whatever is missing. */
function metaLine(role: OpenRole): string {
  const employment = role.employment_type
    ? (EMPLOYMENT_LABELS[role.employment_type] ?? role.employment_type)
    : null;
  return [role.location, employment, role.department].filter(Boolean).join(' · ');
}

function experienceLine(role: OpenRole): string | null {
  const { experience_min_years: lo, experience_max_years: hi } = role;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null) return lo === hi ? `${lo} years` : `${lo}–${hi} years`;
  return lo != null ? `${lo}+ years` : `Up to ${hi} years`;
}

function salaryLine(role: OpenRole): string | null {
  const { salary_min: lo, salary_max: hi } = role;
  if (lo == null && hi == null) return null;
  const currency = role.salary_currency ?? '';
  const money = (n: number) => `${currency} ${n.toLocaleString()}`.trim();
  if (lo != null && hi != null) return `${money(lo)} – ${money(hi)}`;
  return lo != null ? `From ${money(lo)}` : `Up to ${money(hi as number)}`;
}

function RoleCard({ role }: { role: OpenRole }) {
  const meta = metaLine(role);
  const experience = experienceLine(role);
  const salary = salaryLine(role);

  return (
    <GlassCard className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[15.5px] font-semibold text-white">{role.title}</h3>
          <p className="mt-0.5 flex items-center gap-1.5 text-[12.5px] text-[#b8babf]">
            <Building2 size={12} aria-hidden="true" />
            {role.company_name}
            {meta ? <span className="text-[#70757c]">· {meta}</span> : null}
          </p>
        </div>
        {salary ? (
          <span className="shrink-0 rounded-[10px] border border-white/[0.1] bg-white/[0.04] px-2.5 py-1 text-[12px] font-medium text-white">
            {salary}
          </span>
        ) : null}
      </div>

      {role.skills.length > 0 ? (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {role.skills.map((s) => (
            <span
              key={s}
              className="rounded-full border border-white/[0.1] bg-white/[0.04] px-2 py-0.5 text-[11.5px] text-[#d5d7da]"
            >
              {s}
            </span>
          ))}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11.5px] text-[#70757c]">
          {role.level} level{experience ? ` · ${experience}` : ''}
        </span>

        {/* Applied already: the card says so and offers their own record
            rather than a second application. Inviting one they cannot make
            would waste their time and look broken when it was refused. */}
        {role.already_applied ? (
          <span className="flex items-center gap-2">
            <StatusTag tone="forest">
              <Check size={10} aria-hidden="true" /> Applied
            </StatusTag>
            <Link
              to="/applications"
              className="text-[12px] text-[#60a5fa] hover:underline underline-offset-4"
            >
              Track it
            </Link>
          </span>
        ) : (
          <Link
            to={`/apply/${role.requisition_id}`}
            className="inline-flex items-center gap-1.5 rounded-[10px] bg-white px-3.5 py-1.5 text-[12.5px] font-medium text-black transition-opacity hover:opacity-90"
          >
            View &amp; apply
          </Link>
        )}
      </div>
    </GlassCard>
  );
}

export default function OpenRoles() {
  const roles = useQuery({
    queryKey: ['open-roles'],
    queryFn: listOpenRoles,
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const items = roles.data ?? [];

  // Nothing open is a real answer, and a section that renders an empty box on
  // a page that also has practice roles just adds confusion. It disappears.
  if (!roles.isLoading && !roles.isError && items.length === 0) return null;

  return (
    <section aria-labelledby="open-roles-heading" className="mb-8">
      <div className="mb-3">
        <h2 id="open-roles-heading" className="text-[18px] font-semibold text-white">
          Open roles
        </h2>
        {/* The load-bearing sentence on this page. Below it are practice
            interviews; these are applications to real companies. */}
        <p className="mt-0.5 text-[13px] text-[#888b91]">
          Real jobs at companies hiring on Anterview — applying here reaches their
          hiring team.
        </p>
      </div>

      {roles.isError ? (
        <p className="text-[13px] text-[#888b91]">
          Could not load open roles just now. Refresh to try again.
        </p>
      ) : null}

      <div className="flex flex-col gap-2.5">
        {roles.isLoading ? (
          <GlassCard className="h-[110px] animate-pulse">
            <span className="sr-only">Loading open roles…</span>
          </GlassCard>
        ) : null}
        {items.map((role) => (
          <RoleCard key={role.requisition_id} role={role} />
        ))}
      </div>

      {items.length > 0 ? (
        <p className="mt-3 flex items-center gap-1.5 text-[12px] text-[#70757c]">
          <ExternalLink size={12} aria-hidden="true" />
          Practice interviews are below — those are for rehearsing, not applying.
        </p>
      ) : null}
    </section>
  );
}
