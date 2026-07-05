// Profile — editable, role-aware self-profile for candidates, HR and admins.
// Candidates: photo, name, headline, status (student/employed), desired roles,
//   summary, resume link, language, contact + links.
// HR: photo, name, title, about, official email, company (read-only), links.
// Admin/super-admin: photo, name, headline, about, links.
// Avatars are downscaled client-side to a small data URI (no object storage).

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { getMe } from '@/api/auth';
import { updateProfile, imageFileToDataUrl } from '@/api/profile';
import type { MeResponse, ProfileUpdate } from '@/types/auth';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';

import { Reveal } from '@/design/components/Reveal';
import { GlassCard, Pill, Avatar } from '@/design/components/primitives';
import { Badge } from '@/design/components/banners';
import { initialsOf, gradientFor } from '@/design/data/shared';
import {
  Camera, Check, Loader2, Mail, Building2, GraduationCap,
  FileText, ShieldCheck, Link2, Sparkles,
} from '@/design/components/icons';

// ── Small dark-theme form primitives ────────────────────────────────────────

const INPUT_CLS =
  'w-full rounded-[10px] border border-white/[0.08] bg-white/[0.03] px-3.5 py-2.5 text-[14px] text-white placeholder:text-[#70757c] outline-none transition-colors focus:border-[rgba(var(--accent-rgb),0.5)] focus:bg-white/[0.05]';

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-[#b8babf]">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-[#70757c]">{hint}</span>}
    </label>
  );
}

function ReadOnlyField({ label, value, icon: Icon }: { label: string; value: string; icon?: typeof Mail }) {
  return (
    <div>
      <span className="mb-1.5 block text-[12px] font-medium text-[#b8babf]">{label}</span>
      <div className="flex items-center gap-2 rounded-[10px] border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-[14px] text-[#cccccc]">
        {Icon && <Icon size={14} className="text-[#70757c]" aria-hidden="true" />}
        <span className="truncate">{value || '—'}</span>
      </div>
    </div>
  );
}

const SECTION_TITLE = 'mb-4 flex items-center gap-2 text-[15px] font-semibold text-white';

type Form = {
  full_name: string;
  headline: string;
  bio: string;
  location: string;
  phone: string;
  linkedin_url: string;
  github_url: string;
  employment_status: string;
  desired_roles: string;
  preferred_language: string;
  official_email: string;
  avatar_url: string;
};

const EMPTY: Form = {
  full_name: '', headline: '', bio: '', location: '', phone: '', linkedin_url: '',
  github_url: '', employment_status: '', desired_roles: '', preferred_language: 'en',
  official_email: '', avatar_url: '',
};

function fromMe(me: MeResponse): Form {
  return {
    full_name: me.full_name ?? '',
    headline: me.headline ?? '',
    bio: me.bio ?? '',
    location: me.location ?? '',
    phone: me.phone ?? '',
    linkedin_url: me.linkedin_url ?? '',
    github_url: me.github_url ?? '',
    employment_status: me.employment_status ?? '',
    desired_roles: me.desired_roles ?? '',
    preferred_language: me.preferred_language ?? 'en',
    official_email: me.official_email ?? '',
    avatar_url: me.avatar_url ?? '',
  };
}

export default function Profile() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [hydrated, setHydrated] = useState(false);

  const { data: me, isLoading } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => getMe(),
    staleTime: 60_000,
  });

  // Hydrate the form once when the profile arrives (don't clobber edits on refetch).
  useEffect(() => {
    if (me && !hydrated) {
      setForm(fromMe(me));
      setHydrated(true);
    }
  }, [me, hydrated]);

  const roles = me?.roles ?? user?.roles ?? [];
  const isHr = roles.includes('hr_manager');
  const isAdmin = roles.includes('admin') || roles.includes('super_admin');
  const isCandidate = !isHr && !isAdmin;

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  const mutation = useMutation({
    mutationFn: (body: ProfileUpdate) => updateProfile(body),
    onSuccess: (updated) => {
      queryClient.setQueryData(['auth', 'me'], updated);
      toast.success('Profile saved.');
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Could not save profile.'),
  });

  const onSave = () => {
    const body: ProfileUpdate = {
      full_name: form.full_name.trim() || undefined,
      headline: form.headline,
      bio: form.bio,
      location: form.location,
      phone: form.phone,
      linkedin_url: form.linkedin_url,
      github_url: form.github_url,
      avatar_url: form.avatar_url,
    };
    if (isCandidate) {
      body.employment_status = form.employment_status;
      body.desired_roles = form.desired_roles;
      body.preferred_language = form.preferred_language || 'en';
    }
    if (isHr) body.official_email = form.official_email;
    mutation.mutate(body);
  };

  const onPickPhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      toast.error('Please choose an image file.');
      return;
    }
    if (file.size > 6 * 1024 * 1024) {
      toast.error('Image is too large (max 6 MB).');
      return;
    }
    try {
      const dataUrl = await imageFileToDataUrl(file, 256);
      set('avatar_url', dataUrl);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not read that image.');
    }
  };

  const roleLabel = isAdmin
    ? roles.includes('super_admin') ? 'Super Admin' : 'Platform Admin'
    : isHr ? 'HR Manager' : 'Candidate';
  const roleTone: 'electric' | 'forest' | 'lavender' = isHr ? 'forest' : isAdmin ? 'lavender' : 'electric';

  if (isLoading) {
    return (
      <div className="mx-auto flex max-w-[960px] items-center justify-center px-6 py-24">
        <Loader2 className="h-6 w-6 animate-spin text-[#60a5fa]" aria-hidden="true" />
      </div>
    );
  }

  const initials = initialsOf(form.full_name || me?.email || 'U');
  const gradient = gradientFor((form.full_name || me?.email || 'U').charCodeAt(0));

  return (
    <div className="mx-auto max-w-[960px] px-6 py-8 lg:px-8 space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-[-0.8px]">Your profile</h1>
          <p className="mt-1 text-[13.5px] text-[#888b91]">
            {isCandidate
              ? 'Keep this current — it personalises your AI interviews and is what recruiters see.'
              : isHr
                ? 'How candidates and your team see you across the hiring workspace.'
                : 'Your account details across the platform.'}
          </p>
        </div>
        <Pill type="button" onClick={onSave} disabled={mutation.isPending} className="px-5 py-2.5">
          {mutation.isPending ? (
            <><Loader2 size={15} className="animate-spin" aria-hidden="true" /> Saving…</>
          ) : (
            <><Check size={15} aria-hidden="true" /> Save changes</>
          )}
        </Pill>
      </div>

      {/* Identity card */}
      <Reveal>
        <GlassCard feature className="p-6">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
            {/* Editable avatar */}
            <div className="relative shrink-0">
              {form.avatar_url ? (
                <img
                  src={form.avatar_url}
                  alt="Profile"
                  className="h-[92px] w-[92px] rounded-full border border-white/15 object-cover"
                />
              ) : (
                <Avatar initials={initials} gradient={gradient} size={92} />
              )}
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                aria-label="Change photo"
                className="absolute -bottom-1 -right-1 inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/20 bg-[#0f0f10] text-white transition-colors hover:bg-[#1c1d1f] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                <Camera size={15} aria-hidden="true" />
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => void onPickPhoto(e)}
              />
            </div>

            <div className="min-w-0 flex-1">
              <div className="mb-2 flex items-center gap-2.5">
                <Badge tone={roleTone}>{roleLabel}</Badge>
                {isHr && me?.company_name && (
                  <span className="inline-flex items-center gap-1 text-[12px] text-[#888b91]">
                    <Building2 size={12} aria-hidden="true" /> {me.company_name}
                  </span>
                )}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Full name">
                  <input
                    className={INPUT_CLS}
                    value={form.full_name}
                    onChange={(e) => set('full_name', e.target.value)}
                    placeholder="Your name"
                  />
                </Field>
                <Field label={isHr ? 'Title / role' : 'Headline'}>
                  <input
                    className={INPUT_CLS}
                    value={form.headline}
                    onChange={(e) => set('headline', e.target.value)}
                    placeholder={isHr ? 'e.g. Talent Acquisition Lead' : 'e.g. Frontend Engineer · React'}
                  />
                </Field>
              </div>
            </div>
          </div>
        </GlassCard>
      </Reveal>

      {/* About */}
      <Reveal>
        <GlassCard className="p-6">
          <h3 className={SECTION_TITLE}>
            <Sparkles size={16} className="text-[#60a5fa]" aria-hidden="true" />
            {isHr ? 'About you & your team' : 'Summary'}
          </h3>
          <Field
            label={isCandidate ? 'Tell recruiters about yourself' : 'Description'}
            hint={isCandidate ? 'A short summary of your experience and strengths.' : undefined}
          >
            <textarea
              className={cn(INPUT_CLS, 'min-h-[110px] resize-y leading-relaxed')}
              value={form.bio}
              onChange={(e) => set('bio', e.target.value)}
              placeholder={
                isCandidate
                  ? 'Final-year CS student passionate about building accessible web apps…'
                  : isHr
                    ? 'We hire across engineering and product. We value structured, fair interviews…'
                    : 'A short description.'
              }
              maxLength={2000}
            />
          </Field>
        </GlassCard>
      </Reveal>

      {/* Role-specific */}
      {isCandidate && (
        <Reveal>
          <GlassCard className="p-6">
            <h3 className={SECTION_TITLE}>
              <GraduationCap size={16} className="text-[#60a5fa]" aria-hidden="true" />
              Career
            </h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Current status">
                <select
                  className={INPUT_CLS}
                  value={form.employment_status}
                  onChange={(e) => set('employment_status', e.target.value)}
                >
                  <option value="">Prefer not to say</option>
                  <option value="student">Student</option>
                  <option value="employed">Employed</option>
                </select>
              </Field>
              <Field label="Preferred interview language">
                <select
                  className={INPUT_CLS}
                  value={form.preferred_language}
                  onChange={(e) => set('preferred_language', e.target.value)}
                >
                  <option value="en">English</option>
                  <option value="hi">हिन्दी (Hindi)</option>
                  <option value="te">తెలుగు (Telugu)</option>
                </select>
              </Field>
              <Field label="Desired roles" hint="Comma-separated, e.g. Frontend Engineer, UI Developer">
                <input
                  className={INPUT_CLS}
                  value={form.desired_roles}
                  onChange={(e) => set('desired_roles', e.target.value)}
                  placeholder="Frontend Engineer, Full-stack Developer"
                />
              </Field>
              <div>
                <span className="mb-1.5 block text-[12px] font-medium text-[#b8babf]">Resume</span>
                <Link
                  to="/resume"
                  className="flex items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.03] px-3.5 py-2.5 text-[14px] transition-colors hover:border-[rgba(var(--accent-rgb),0.4)]"
                >
                  <FileText size={15} className="text-[#60a5fa]" aria-hidden="true" />
                  <span className={me?.has_resume ? 'text-white' : 'text-[#888b91]'}>
                    {me?.has_resume ? 'Resume on file — manage versions' : 'Upload your resume →'}
                  </span>
                </Link>
              </div>
            </div>
          </GlassCard>
        </Reveal>
      )}

      {isHr && (
        <Reveal>
          <GlassCard className="p-6">
            <h3 className={SECTION_TITLE}>
              <Building2 size={16} className="text-[#27c93f]" aria-hidden="true" />
              Company & work details
            </h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Official email" hint="Your verified work email.">
                <input
                  className={INPUT_CLS}
                  type="email"
                  value={form.official_email}
                  onChange={(e) => set('official_email', e.target.value)}
                  placeholder="you@company.com"
                />
              </Field>
              <ReadOnlyField label="Company" value={me?.company_name ?? '—'} icon={Building2} />
            </div>
          </GlassCard>
        </Reveal>
      )}

      {/* Contact & links */}
      <Reveal>
        <GlassCard className="p-6">
          <h3 className={SECTION_TITLE}>
            <Link2 size={16} className="text-[#60a5fa]" aria-hidden="true" />
            Contact &amp; links
          </h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <ReadOnlyField label="Account email" value={me?.email ?? '—'} icon={Mail} />
            <Field label="Phone">
              <input
                className={INPUT_CLS}
                value={form.phone}
                onChange={(e) => set('phone', e.target.value)}
                placeholder="+91 …"
              />
            </Field>
            <Field label="Location">
              <input
                className={INPUT_CLS}
                value={form.location}
                onChange={(e) => set('location', e.target.value)}
                placeholder="City, State"
              />
            </Field>
            <Field label="LinkedIn">
              <input
                className={INPUT_CLS}
                value={form.linkedin_url}
                onChange={(e) => set('linkedin_url', e.target.value)}
                placeholder="https://linkedin.com/in/…"
              />
            </Field>
            <Field label="GitHub">
              <input
                className={INPUT_CLS}
                value={form.github_url}
                onChange={(e) => set('github_url', e.target.value)}
                placeholder="https://github.com/…"
              />
            </Field>
          </div>
        </GlassCard>
      </Reveal>

      {/* Footer save */}
      <div className="flex items-center justify-end gap-3 pb-2">
        <span className="inline-flex items-center gap-1.5 text-[12px] text-[#70757c]">
          <ShieldCheck size={13} aria-hidden="true" /> Your details are private and DPDP-compliant.
        </span>
        <Pill type="button" onClick={onSave} disabled={mutation.isPending} className="px-5 py-2.5">
          {mutation.isPending ? (
            <><Loader2 size={15} className="animate-spin" aria-hidden="true" /> Saving…</>
          ) : (
            <><Check size={15} aria-hidden="true" /> Save changes</>
          )}
        </Pill>
      </div>
    </div>
  );
}
