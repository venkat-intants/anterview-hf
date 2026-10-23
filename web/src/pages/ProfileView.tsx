// ProfileView — read-only view of another user's profile.
// Reached at /u/:userId. The backend (GET /users/{id}/profile) only answers for
// HR managers, admins and super-admins, so candidates can't browse each other.
// The read-only twin of the Profile page: same header card, same section cards.

import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';

import { getUserProfile } from '@/api/profile';
import { ApiError } from '@/api/client';
import { errorMessage } from '@/lib/errorMessage';
import { roleLabel } from '@/lib/roleLabel';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Avatar, StatusTag } from '@/design/components/primitives';
import { safeAvatarSrc } from '@/lib/safeUrl';
import { EmptyState, Page, SectionCard } from '@/design/components/page';
import { initialsOf, gradientFor } from '@/design/data/shared';
import {
  ArrowLeft,
  Mail,
  Building2,
  MapPin,
  GraduationCap,
  Briefcase,
  FileText,
  Link2,
  AlertCircle,
  Phone,
  Globe,
  User,
} from '@/design/components/icons';

const STATUS_LABEL: Record<string, string> = { student: 'Student', employed: 'Employed' };

/** Return the URL only if it is a safe http(s) link, else null.
 *
 * linkedin_url / github_url are candidate-controlled and this page is opened by
 * hr_manager, super_admin and platform_owner on *another user's* profile. React
 * 18 does not block a `javascript:` URL in an href — it only warns in
 * development; blocking arrived in React 19 — so rendering the raw value would
 * let a self-registering candidate run script in the console's origin on one
 * click, and from there mint an access token via the JS-readable csrf_token.
 *
 * The server now rejects non-http(s) values on write, but this guard also
 * covers rows stored BEFORE that validator existed, which the server-side fix
 * alone does not clean up.
 */
function safeExternalUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === 'https:' || u.protocol === 'http:' ? raw : null;
  } catch {
    // Not an absolute URL at all — never emit it as an href.
    return null;
  }
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick} className="-ml-3 self-start">
      <ArrowLeft aria-hidden="true" /> Back
    </Button>
  );
}

function InfoRow({
  label,
  icon: Icon,
  children,
}: {
  label: string;
  icon: typeof Mail;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] font-semibold text-muted-foreground">{label}</dt>
      <dd className="mt-1 flex min-w-0 items-center gap-2 text-[14px] text-foreground">
        <Icon size={15} className="flex-none text-[var(--ui-faint)]" aria-hidden="true" />
        <span className="min-w-0 break-words">{children}</span>
      </dd>
    </div>
  );
}

const LINK_CLS =
  'inline-flex min-h-10 items-center gap-2 rounded-full border border-border bg-card px-4 text-[14px] font-medium text-foreground transition-colors hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

export default function ProfileView() {
  const { userId = '' } = useParams<{ userId: string }>();
  const navigate = useNavigate();

  const {
    data: p,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['user-profile', userId],
    queryFn: () => getUserProfile(userId),
    retry: false,
    enabled: Boolean(userId),
  });

  if (isLoading) {
    return (
      <Page width="form">
        <div aria-busy="true" aria-label="Loading profile" className="flex flex-col gap-6">
          <Skeleton className="h-9 w-24 rounded-full" />
          <Skeleton className="h-60 w-full rounded-card" />
          <Skeleton className="h-32 w-full rounded-card" />
        </div>
      </Page>
    );
  }

  if (isError || !p) {
    // Decided by the status, not the wording (AD-F28): the server's 403 text
    // ("Insufficient permissions for this action.") never matched a regex.
    const forbidden = error instanceof ApiError && error.status === 403;
    const notFound = !isError || (error instanceof ApiError && error.status === 404);
    return (
      <Page width="form">
        <BackButton onClick={() => void navigate(-1)} />
        <div role="alert">
          <EmptyState
            size="page"
            icon={AlertCircle}
            title={
              forbidden
                ? "You don't have access to this profile."
                : notFound
                  ? 'Profile not found.'
                  : 'Could not load this profile.'
            }
            description={
              forbidden
                ? 'Only HR managers and admins can view other people’s profiles.'
                : notFound
                  ? 'The link may be out of date, or the person may have left. Go back and try another.'
                  : errorMessage(error, 'Could not load this profile. Go back and try again.')
            }
            action={
              <Button type="button" variant="outline" onClick={() => void navigate(-1)}>
                Go back
              </Button>
            }
          />
        </div>
      </Page>
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
  const linkedin = safeExternalUrl(p.linkedin_url);
  const github = safeExternalUrl(p.github_url);
  const hasContact = Boolean(p.phone || p.official_email);

  return (
    <Page width="form">
      <BackButton onClick={() => void navigate(-1)} />

      {/* Header card */}
      <section
        aria-label="Profile summary"
        className="overflow-hidden rounded-card border border-border bg-card shadow-card"
      >
        <div className="h-24 bg-[image:var(--ui-grad-brand)] sm:h-28" aria-hidden="true" />
        <div className="px-6 pb-6">
          <div className="-mt-12">
            {safeAvatarSrc(p.avatar_url) ? (
              <img
                src={safeAvatarSrc(p.avatar_url) ?? undefined}
                alt={name}
                className="h-24 w-24 rounded-full border-4 border-card bg-card object-cover"
              />
            ) : (
              <Avatar
                initials={initials}
                gradient={gradient}
                size={96}
                className="border-4 border-card"
              />
            )}
          </div>
          <div className="mt-4 min-w-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <h1 className="break-words text-[24px] font-bold tracking-[-0.4px] text-foreground">
                {name}
              </h1>
              <StatusTag tone={role.tone}>{role.label}</StatusTag>
              {p.employment_status && STATUS_LABEL[p.employment_status] && (
                <StatusTag tone="neutral">{STATUS_LABEL[p.employment_status]}</StatusTag>
              )}
            </div>
            {p.headline && <p className="mt-1 text-[15px] text-[var(--ui-soft)]">{p.headline}</p>}
            {(p.location || p.company_name) && (
              <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13.5px] text-muted-foreground">
                {p.location && (
                  <span className="inline-flex items-center gap-1.5">
                    <MapPin size={14} aria-hidden="true" /> {p.location}
                  </span>
                )}
                {p.company_name && (
                  <span className="inline-flex items-center gap-1.5">
                    <Building2 size={14} aria-hidden="true" /> {p.company_name}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      <div className="space-y-5">
        {/* About */}
        <SectionCard title="Summary" icon={User}>
          {p.bio ? (
            <p className="whitespace-pre-line text-[14.5px] leading-relaxed text-foreground">
              {p.bio}
            </p>
          ) : (
            <p className="text-[14px] text-muted-foreground">
              {name} hasn&apos;t added a summary yet.
            </p>
          )}
        </SectionCard>

        {/* Candidate details */}
        {(desired.length > 0 || p.has_resume) && (
          <SectionCard title="Career" icon={GraduationCap}>
            <dl className="space-y-5">
              {desired.length > 0 && (
                <div>
                  <dt className="text-[12px] font-semibold text-muted-foreground">Desired roles</dt>
                  <dd className="mt-2">
                    <ul className="flex flex-wrap gap-2">
                      {desired.map((r) => (
                        <li
                          key={r}
                          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-[var(--ui-inset)] px-3 py-1 text-[13px] text-foreground"
                        >
                          <Briefcase size={12} aria-hidden="true" /> {r}
                        </li>
                      ))}
                    </ul>
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-[12px] font-semibold text-muted-foreground">Resume</dt>
                <dd className="mt-1 flex items-center gap-2 text-[14px]">
                  <FileText
                    size={15}
                    className={p.has_resume ? 'text-[var(--ui-ok)]' : 'text-[var(--ui-faint)]'}
                    aria-hidden="true"
                  />
                  <span className={p.has_resume ? 'text-foreground' : 'text-muted-foreground'}>
                    {p.has_resume ? 'Resume on file' : 'No resume uploaded'}
                  </span>
                </dd>
              </div>
            </dl>
          </SectionCard>
        )}

        {/* Contact & links */}
        {(hasContact || linkedin || github) && (
          <SectionCard title="Contact & links" icon={Link2}>
            {hasContact && (
              <dl className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
                {p.phone && (
                  <InfoRow label="Phone" icon={Phone}>
                    {p.phone}
                  </InfoRow>
                )}
                {p.official_email && (
                  <InfoRow label="Official email" icon={Mail}>
                    {p.official_email}
                  </InfoRow>
                )}
              </dl>
            )}
            {(linkedin || github) && (
              <div
                className={hasContact ? 'mt-5 flex flex-wrap gap-2.5' : 'flex flex-wrap gap-2.5'}
              >
                {linkedin && (
                  <a href={linkedin} target="_blank" rel="noreferrer" className={LINK_CLS}>
                    <Globe size={15} aria-hidden="true" /> LinkedIn
                  </a>
                )}
                {github && (
                  <a href={github} target="_blank" rel="noreferrer" className={LINK_CLS}>
                    <Globe size={15} aria-hidden="true" /> GitHub
                  </a>
                )}
              </div>
            )}
          </SectionCard>
        )}

        <div className="flex justify-end pb-2">
          <Button type="button" variant="outline" onClick={() => void navigate(-1)}>
            Done
          </Button>
        </div>
      </div>
    </Page>
  );
}
