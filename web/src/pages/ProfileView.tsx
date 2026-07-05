// ProfileView — read-only view of another user's profile.
// Reached at /u/:userId. The backend (GET /users/{id}/profile) only answers for
// HR managers, admins and super-admins, so candidates can't browse each other.

import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { getUserProfile } from '@/api/profile';
import { Reveal } from '@/design/components/Reveal';
import { GlassCard, Avatar, Pill } from '@/design/components/primitives';
import { Badge, type TrustChip, TrustStrip } from '@/design/components/banners';
import { initialsOf, gradientFor } from '@/design/data/shared';
import {
  ArrowLeft, Loader2, Mail, Building2, MapPin, GraduationCap, Briefcase,
  FileText, Link2, AlertCircle, Phone, Globe,
} from '@/design/components/icons';

function roleLabel(roles: string[]): { label: string; tone: 'electric' | 'forest' | 'lavender' } {
  if (roles.includes('super_admin')) return { label: 'Super Admin', tone: 'lavender' };
  if (roles.includes('admin')) return { label: 'Platform Admin', tone: 'lavender' };
  if (roles.includes('hr_manager')) return { label: 'HR Manager', tone: 'forest' };
  return { label: 'Candidate', tone: 'electric' };
}

const STATUS_LABEL: Record<string, string> = { student: 'Student', employed: 'Employed' };

export default function ProfileView() {
  const { userId = '' } = useParams<{ userId: string }>();
  const navigate = useNavigate();

  const { data: p, isLoading, isError, error } = useQuery({
    queryKey: ['user-profile', userId],
    queryFn: () => getUserProfile(userId),
    retry: false,
    enabled: Boolean(userId),
  });

  if (isLoading) {
    return (
      <div className="mx-auto flex max-w-[900px] items-center justify-center px-6 py-24">
        <Loader2 className="h-6 w-6 animate-spin text-[#60a5fa]" aria-hidden="true" />
      </div>
    );
  }

  if (isError || !p) {
    const msg = error instanceof Error ? error.message : 'Could not load this profile.';
    const forbidden = /403|forbidden/i.test(msg);
    return (
      <div className="mx-auto max-w-[900px] px-6 py-16">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-[#888b91] hover:text-white"
        >
          <ArrowLeft size={15} aria-hidden="true" /> Back
        </button>
        <GlassCard className="flex flex-col items-center gap-3 py-16 text-center">
          <AlertCircle className="h-9 w-9 text-[#e6714f]" aria-hidden="true" />
          <p className="text-[14px] text-[#b8babf]">
            {forbidden ? "You don't have access to this profile." : 'Profile not found.'}
          </p>
        </GlassCard>
      </div>
    );
  }

  const name = p.full_name || 'Unnamed user';
  const initials = initialsOf(name);
  const gradient = gradientFor(name.charCodeAt(0));
  const role = roleLabel(p.roles);
  const desired = (p.desired_roles ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  const links: TrustChip[] = [];
  if (p.location) links.push({ icon: MapPin, label: p.location });
  if (p.phone) links.push({ icon: Phone, label: p.phone });
  if (p.official_email) links.push({ icon: Mail, label: p.official_email });
  if (p.company_name) links.push({ icon: Building2, label: p.company_name });

  return (
    <div className="mx-auto max-w-[900px] px-6 py-8 lg:px-8 space-y-5">
      <button
        type="button"
        onClick={() => navigate(-1)}
        className="inline-flex items-center gap-1.5 text-[13px] text-[#888b91] transition-colors hover:text-white"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Back
      </button>

      {/* Identity */}
      <Reveal>
        <GlassCard feature className="p-6">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
            {p.avatar_url ? (
              <img
                src={p.avatar_url}
                alt={name}
                className="h-[92px] w-[92px] shrink-0 rounded-full border border-white/15 object-cover"
              />
            ) : (
              <Avatar initials={initials} gradient={gradient} size={92} />
            )}
            <div className="min-w-0 flex-1">
              <div className="mb-2 flex flex-wrap items-center gap-2.5">
                <h1 className="text-[24px] font-semibold tracking-[-0.6px]">{name}</h1>
                <Badge tone={role.tone}>{role.label}</Badge>
                {p.employment_status && STATUS_LABEL[p.employment_status] && (
                  <Badge tone="electric">{STATUS_LABEL[p.employment_status]}</Badge>
                )}
              </div>
              {p.headline && <p className="text-[14.5px] text-[#cccccc]">{p.headline}</p>}
              {(p.location || p.phone || p.official_email || p.company_name) && (
                <TrustStrip className="mt-3" items={links} />
              )}
            </div>
          </div>
        </GlassCard>
      </Reveal>

      {/* About */}
      {p.bio && (
        <Reveal>
          <GlassCard className="p-6">
            <h3 className="mb-3 text-[15px] font-semibold">Summary</h3>
            <p className="whitespace-pre-line text-[14px] leading-relaxed text-[#b8babf]">{p.bio}</p>
          </GlassCard>
        </Reveal>
      )}

      {/* Candidate details */}
      {(desired.length > 0 || p.has_resume) && (
        <Reveal>
          <GlassCard className="p-6">
            <h3 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
              <GraduationCap size={16} className="text-[#60a5fa]" aria-hidden="true" /> Career
            </h3>
            {desired.length > 0 && (
              <div className="mb-4">
                <span className="mb-2 block text-[12px] font-medium text-[#b8babf]">Desired roles</span>
                <div className="flex flex-wrap gap-1.5">
                  {desired.map((r) => (
                    <span
                      key={r}
                      className="inline-flex items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[12px] text-[#cccccc]"
                    >
                      <Briefcase size={11} className="text-[#60a5fa]" aria-hidden="true" /> {r}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="flex items-center gap-2 text-[13.5px]">
              <FileText size={15} className={p.has_resume ? 'text-[#27c93f]' : 'text-[#70757c]'} aria-hidden="true" />
              <span className={p.has_resume ? 'text-white' : 'text-[#888b91]'}>
                {p.has_resume ? 'Resume on file' : 'No resume uploaded'}
              </span>
            </div>
          </GlassCard>
        </Reveal>
      )}

      {/* Links */}
      {(p.linkedin_url || p.github_url) && (
        <Reveal>
          <GlassCard className="p-6">
            <h3 className="mb-3 flex items-center gap-2 text-[15px] font-semibold">
              <Link2 size={16} className="text-[#60a5fa]" aria-hidden="true" /> Links
            </h3>
            <div className="flex flex-wrap gap-2.5">
              {p.linkedin_url && (
                <a
                  href={p.linkedin_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.03] px-3.5 py-2 text-[13px] text-[#cccccc] transition-colors hover:border-[rgba(var(--accent-rgb),0.4)] hover:text-white"
                >
                  <Globe size={14} aria-hidden="true" /> LinkedIn
                </a>
              )}
              {p.github_url && (
                <a
                  href={p.github_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.03] px-3.5 py-2 text-[13px] text-[#cccccc] transition-colors hover:border-[rgba(var(--accent-rgb),0.4)] hover:text-white"
                >
                  <Globe size={14} aria-hidden="true" /> GitHub
                </a>
              )}
            </div>
          </GlassCard>
        </Reveal>
      )}

      <div className="flex justify-end pb-2">
        <Pill variant="ghost" type="button" onClick={() => navigate(-1)} className="px-5 py-2.5">
          Done
        </Pill>
      </div>
    </div>
  );
}
