// Profile — role-aware self-profile for candidates, HR and admins.
// Read mode is a header card, a profile-strength checklist and one titled card
// per section. Each section is edited in its own dialog, so a person changes
// one thing without wading through a long form, and Cancel discards the draft.
// Candidates: photo, name, headline, status (student/employed), desired roles,
//   summary, resume link, language, contact + links.
// HR: photo, name, title, about, official email, company (read-only), links.
// Admin/super-admin: photo, name, headline, about, links.
// Avatars are downscaled client-side to a small data URI (no object storage).
// The API only writes the fields it is sent, so each dialog sends just its own.
// Every string goes through t() under `profile.*`.

import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { getMe } from '@/api/auth';
import { updateProfile, imageFileToDataUrl } from '@/api/profile';
import type { MeResponse, ProfileUpdate } from '@/types/auth';
import { useAuth } from '@/context/AuthContext';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { safeAvatarSrc } from '@/lib/safeUrl';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Avatar, StatusTag } from '@/design/components/primitives';
import { EmptyState, Page, PageHeader, ProgressMeter, SectionCard } from '@/design/components/page';
import { initialsOf, gradientFor } from '@/design/data/shared';
import {
  AlertCircle, Briefcase, Building2, Camera, Check, CheckCircle2, ChevronRight, FileText,
  Link2, Loader2, Mail, MapPin, Pencil, Phone, ShieldCheck, Sparkles,
} from '@/design/components/icons';

// ── Form model ──────────────────────────────────────────────────────────────

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
  };
}

type EditorKey = 'profile' | 'about' | 'career' | 'company' | 'contact';

// Language names are shown in their own script on purpose, in every UI language.
const LANGUAGE_LABEL: Record<string, string> = {
  en: 'English',
  hi: 'हिन्दी (Hindi)',
  te: 'తెలుగు (Telugu)',
};

// ── Small building blocks ───────────────────────────────────────────────────

const CONTROL_CLS =
  'w-full rounded-[10px] border border-input bg-card px-3.5 text-base text-foreground ring-offset-background transition-[border-color,box-shadow] placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] focus-visible:outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/20 md:text-sm';

/** A label bound to a single control, with an optional hint read out after it. */
function FormField({
  label, hint, children,
}: { label: string; hint?: string; children: (ids: { id: string; describedBy?: string }) => ReactNode }) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-[13px] font-semibold text-foreground">{label}</label>
      {children({ id, describedBy: hint ? hintId : undefined })}
      {hint && <p id={hintId} className="text-[12.5px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function TextField({
  label, hint, value, onChange, placeholder, type = 'text', maxLength,
}: {
  label: string; hint?: string; value: string; onChange: (v: string) => void;
  placeholder?: string; type?: string; maxLength?: number;
}) {
  return (
    <FormField label={label} hint={hint}>
      {({ id, describedBy }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          maxLength={maxLength}
        />
      )}
    </FormField>
  );
}

/** A saved value in read mode: small label over the value, or a quiet "Not added". */
function InfoRow({
  label, icon: Icon, children,
}: { label: string; icon?: typeof Mail; children?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="min-w-0">
      <dt className="text-[12.5px] font-semibold text-muted-foreground">{label}</dt>
      <dd className="mt-1 flex min-w-0 items-center gap-2 text-[14px] text-foreground">
        {Icon && <Icon size={15} className="flex-none text-[var(--ui-faint)]" aria-hidden="true" />}
        {children ? (
          <span className="min-w-0 break-words">{children}</span>
        ) : (
          <span className="text-muted-foreground">{t('profile.notAdded')}</span>
        )}
      </dd>
    </div>
  );
}

function EditButton({ label, onClick }: { label: string; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <Button type="button" variant="outline" size="sm" onClick={onClick} aria-label={label}>
      <Pencil aria-hidden="true" /> {t('profile.edit')}
    </Button>
  );
}

function ProfileSkeleton() {
  const { t } = useTranslation();
  return (
    <div className="space-y-6" aria-busy="true" aria-label={t('profile.loading')}>
      <Skeleton className="h-9 w-56 rounded-full" />
      <Skeleton className="h-64 w-full rounded-card" />
      <Skeleton className="h-40 w-full rounded-card" />
      <Skeleton className="h-40 w-full rounded-card" />
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function Profile() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState<EditorKey | null>(null);
  const [draft, setDraft] = useState<Form | null>(null);

  const { data: me, isLoading, isError, refetch } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => getMe(),
    staleTime: 60_000,
  });

  const roles = me?.roles ?? user?.roles ?? [];
  const isHr = roles.includes('hr_manager');
  const isAdmin = roles.includes('admin') || roles.includes('super_admin');
  const isCandidate = !isHr && !isAdmin;

  const mutation = useMutation({
    mutationFn: (body: ProfileUpdate) => updateProfile(body),
    onSuccess: (updated) => {
      queryClient.setQueryData(['auth', 'me'], updated);
      toast.success(t('profile.saved'));
      setEditing(null);
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t('profile.saveFailed')),
  });

  // The photo saves on its own mutation. Sharing the editors' one meant a photo
  // upload finishing would run their onSuccess — closing a dialog the person had
  // since opened and discarding what they had typed into it.
  const avatarMutation = useMutation({
    mutationFn: (avatarUrl: string) => updateProfile({ avatar_url: avatarUrl }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['auth', 'me'], updated);
      toast.success(t('profile.saved'));
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : t('profile.saveFailed')),
  });

  // What the dialog renders. It outlives `editing` by one close animation: Radix
  // keeps the content mounted while it fades out, and clearing the form at the
  // same instant left an empty bordered shell collapsing on screen.
  const lastEditing = useRef<EditorKey | null>(null);
  if (editing) lastEditing.current = editing;
  const shown = editing ?? lastEditing.current;

  const openEditor = (key: EditorKey) => {
    if (!me) return;
    setDraft(fromMe(me));
    setEditing(key);
  };

  const closeEditor = () => {
    if (mutation.isPending) return;
    setEditing(null);
  };

  const set = <K extends keyof Form>(k: K, v: Form[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  /** Only the open section's fields; candidate-only and HR-only fields stay role-gated. */
  const buildBody = (key: EditorKey, f: Form): ProfileUpdate => {
    switch (key) {
      case 'profile':
        return { full_name: f.full_name.trim() || undefined, headline: f.headline, location: f.location };
      case 'about':
        return { bio: f.bio };
      case 'career': {
        const body: ProfileUpdate = {};
        if (isCandidate) {
          body.employment_status = f.employment_status;
          body.desired_roles = f.desired_roles;
          body.preferred_language = f.preferred_language || 'en';
        }
        return body;
      }
      case 'company': {
        const body: ProfileUpdate = {};
        if (isHr) body.official_email = f.official_email;
        return body;
      }
      case 'contact':
        return { phone: f.phone, linkedin_url: f.linkedin_url, github_url: f.github_url };
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!editing || !draft) return;
    mutation.mutate(buildBody(editing, draft));
  };

  const onPickPhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      toast.error(t('profile.photoNotImage'));
      return;
    }
    if (file.size > 6 * 1024 * 1024) {
      toast.error(t('profile.photoTooLarge'));
      return;
    }
    try {
      const dataUrl = await imageFileToDataUrl(file, 256);
      avatarMutation.mutate(dataUrl);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('profile.photoUnreadable'));
    }
  };

  const roleLabel = isAdmin
    ? roles.includes('super_admin') ? t('profile.roleSuperAdmin') : t('profile.rolePlatformAdmin')
    : isHr ? t('profile.roleHr') : t('profile.roleCandidate');
  const roleTone: 'electric' | 'forest' | 'lavender' = isHr ? 'forest' : isAdmin ? 'lavender' : 'electric';

  const description = isCandidate
    ? t('profile.descCandidate')
    : isHr
      ? t('profile.descHr')
      : t('profile.descAdmin');

  const statusLabel = (v: string): string =>
    v === 'student' ? t('profile.statusStudent') : v === 'employed' ? t('profile.statusEmployed') : v;

  if (isLoading) {
    return (
      <Page width="form">
        <ProfileSkeleton />
      </Page>
    );
  }

  if (isError || !me) {
    return (
      <Page width="form">
        <PageHeader title={t('profile.title')} description={description} />
        <div role="alert">
          <EmptyState
            icon={AlertCircle}
            title={t('profile.loadErrorTitle')}
            description={t('profile.loadErrorDesc')}
            action={<Button type="button" onClick={() => void refetch()}>{t('profile.tryAgain')}</Button>}
          />
        </div>
      </Page>
    );
  }

  const nameForInitials = me.full_name || me.email || 'U';
  const initials = initialsOf(nameForInitials);
  const gradient = gradientFor(nameForInitials.charCodeAt(0));
  const desired = (me.desired_roles ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const hasLinks = Boolean(me.linkedin_url || me.github_url);

  // ── Profile strength: derived purely from fields the API already returns ──
  type Step = { key: string; label: string; done: boolean; to?: string; open?: EditorKey };
  const steps: Step[] = [
    { key: 'photo', label: t('profile.stepPhoto'), done: Boolean(me.avatar_url) },
    { key: 'headline', label: isHr ? t('profile.addTitle') : t('profile.stepHeadline'), done: Boolean(me.headline), open: 'profile' },
    { key: 'summary', label: isHr ? t('profile.stepBio') : t('profile.stepSummary'), done: Boolean(me.bio), open: 'about' },
    { key: 'location', label: t('profile.stepLocation'), done: Boolean(me.location), open: 'profile' },
    ...(isCandidate
      ? [
          { key: 'resume', label: t('profile.stepResume'), done: me.has_resume, to: '/resume' },
          { key: 'roles', label: t('profile.stepRoles'), done: desired.length > 0, open: 'career' as const },
        ]
      : []),
    ...(isHr
      ? [{ key: 'email', label: t('profile.stepEmail'), done: Boolean(me.official_email), open: 'company' as const }]
      : []),
    { key: 'links', label: t('profile.stepLinks'), done: hasLinks, open: 'contact' },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const percent = (doneCount / steps.length) * 100;
  const todo = steps.filter((s) => !s.done);

  const pickPhoto = () => fileRef.current?.click();

  const itemCls =
    'flex min-h-10 w-full items-center gap-3 rounded-[10px] px-3 py-2 text-left text-[14px] text-foreground transition-colors hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';

  const editorTitle: Record<EditorKey, string> = {
    profile: t('profile.editProfile'),
    about: isHr ? t('profile.editAboutHrTitle') : t('profile.editSummary'),
    career: t('profile.editCareer'),
    company: t('profile.editCompanyTitle'),
    contact: t('profile.editContactTitle'),
  };
  const editorHint: Record<EditorKey, string> = {
    profile: t('profile.hintProfile'),
    about: t('profile.hintAbout'),
    career: t('profile.hintCareer'),
    company: t('profile.hintCompany'),
    contact: t('profile.hintContact'),
  };

  return (
    <Page width="form">
      <PageHeader title={t('profile.title')} description={description} />

      {/* Hidden picker — the camera button on the header card opens it. */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => void onPickPhoto(e)}
      />

      {/* Header card: brand-surface banner, the photo overlapping it on a
          card-coloured ring, then name, role and headline on the card itself.
          The camera and Edit controls sit below the banner, on the card. */}
      <section
        aria-label={t('profile.summaryAria')}
        className="overflow-hidden rounded-card border border-border bg-card shadow-card"
      >
        <div className="h-24 bg-[image:var(--ui-grad-brand)] sm:h-28" aria-hidden="true" />
        <div className="px-6 pb-6">
          <div className="-mt-12 flex items-end justify-between gap-3">
            <div className="relative flex-none">
              {safeAvatarSrc(me.avatar_url) ? (
                <img
                  src={safeAvatarSrc(me.avatar_url) ?? undefined}
                  alt={t('profile.photoAlt')}
                  className="h-24 w-24 rounded-full border-4 border-card bg-card object-cover"
                />
              ) : (
                <Avatar initials={initials} gradient={gradient} size={96} className="border-4 border-card" />
              )}
              <button
                type="button"
                onClick={pickPhoto}
                disabled={avatarMutation.isPending}
                aria-label={t('profile.changePhoto')}
                className="absolute -bottom-1 -right-1 inline-flex h-10 w-10 items-center justify-center rounded-full border border-border bg-card text-foreground shadow-card transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50"
              >
                {avatarMutation.isPending ? (
                  <Loader2 size={16} className="animate-spin" aria-hidden="true" />
                ) : (
                  <Camera size={16} aria-hidden="true" />
                )}
              </button>
            </div>
            <Button type="button" variant="outline" onClick={() => openEditor('profile')} aria-label={t('profile.editProfile')}>
              <Pencil aria-hidden="true" /> {t('profile.edit')}
            </Button>
          </div>

          <div className="mt-4 min-w-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <h2 className="break-words text-[24px] font-bold tracking-[-0.4px] text-foreground">
                {me.full_name || t('profile.addName')}
              </h2>
              <StatusTag tone={roleTone}>{roleLabel}</StatusTag>
            </div>
            {me.headline ? (
              <p className="mt-1 text-[15px] text-[var(--ui-soft)]">{me.headline}</p>
            ) : (
              <button
                type="button"
                onClick={() => openEditor('profile')}
                className="mt-1 min-h-10 rounded text-left text-[14px] text-muted-foreground underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                {isHr ? t('profile.addTitle') : t('profile.addHeadline')}
              </button>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13px] text-muted-foreground">
              {me.location && (
                <span className="inline-flex items-center gap-1.5">
                  <MapPin size={14} aria-hidden="true" /> {me.location}
                </span>
              )}
              {isHr && me.company_name && (
                <span className="inline-flex items-center gap-1.5">
                  <Building2 size={14} aria-hidden="true" /> {me.company_name}
                </span>
              )}
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        {/* Profile strength — first in the DOM so it leads on small screens. */}
        <SectionCard
          title={t('profile.strengthTitle')}
          className="h-fit lg:sticky lg:top-6 lg:order-2"
          data-testid="profile-strength"
        >
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-[24px] font-bold tabular-nums tracking-[-0.4px] text-foreground">{Math.round(percent)}%</p>
            <p className="text-[13px] text-muted-foreground">
              {t('profile.doneOf', { done: doneCount, total: steps.length })}
            </p>
          </div>
          <ProgressMeter value={percent} label={t('profile.strengthTitle')} className="mt-2" />
          {todo.length === 0 ? (
            <p className="mt-4 flex items-start gap-2 rounded-[10px] bg-[var(--ui-ok-wash)] px-3 py-3 text-[14px] font-medium text-foreground">
              <CheckCircle2 size={18} className="mt-0.5 flex-none text-[var(--ui-ok)]" aria-hidden="true" />
              {t('profile.complete')}
            </p>
          ) : (
            <>
              <p className="mt-4 text-[13px] text-muted-foreground">{t('profile.onYourWay')}</p>
              <ul className="mt-3 space-y-1">
                {todo.map((s) => (
                  <li key={s.key}>
                    {s.to ? (
                      <Link to={s.to} className={itemCls}>
                        <span className="min-w-0 flex-1">{s.label}</span>
                        <ChevronRight size={16} className="flex-none text-[var(--ui-faint)]" aria-hidden="true" />
                      </Link>
                    ) : (
                      <button
                        type="button"
                        className={itemCls}
                        onClick={() => (s.open ? openEditor(s.open) : pickPhoto())}
                      >
                        <span className="min-w-0 flex-1">{s.label}</span>
                        <ChevronRight size={16} className="flex-none text-[var(--ui-faint)]" aria-hidden="true" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </SectionCard>

        <div className="min-w-0 space-y-6 lg:order-1">
          {/* About */}
          <SectionCard
            title={isHr ? t('profile.aboutHr') : t('profile.summary')}
            action={me.bio ? <EditButton label={isHr ? t('profile.editAboutHr') : t('profile.editSummary')} onClick={() => openEditor('about')} /> : undefined}
          >
            {me.bio ? (
              <p className="whitespace-pre-line text-[14.5px] leading-relaxed text-foreground">{me.bio}</p>
            ) : (
              <EmptyState
                icon={Sparkles}
                title={isCandidate ? t('profile.emptyAboutCandTitle') : t('profile.emptyAboutOtherTitle')}
                description={isCandidate ? t('profile.emptyAboutCandDesc') : t('profile.emptyAboutOtherDesc')}
                action={
                  <Button type="button" onClick={() => openEditor('about')}>
                    {isCandidate ? t('profile.addSummary') : t('profile.addDescription')}
                  </Button>
                }
              />
            )}
          </SectionCard>

          {/* Career (candidates) */}
          {isCandidate && (
            <SectionCard
              title={t('profile.career')}
              action={<EditButton label={t('profile.editCareer')} onClick={() => openEditor('career')} />}
            >
              {!me.employment_status && desired.length === 0 && (
                <EmptyState
                  icon={Briefcase}
                  title={t('profile.emptyCareerTitle')}
                  description={t('profile.emptyCareerDesc')}
                  action={<Button type="button" onClick={() => openEditor('career')}>{t('profile.addCareer')}</Button>}
                  className="mb-5"
                />
              )}
              <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
                {me.employment_status && (
                  <InfoRow label={t('profile.currentStatus')}>
                    {statusLabel(me.employment_status)}
                  </InfoRow>
                )}
                <InfoRow label={t('profile.preferredLanguage')}>
                  {LANGUAGE_LABEL[me.preferred_language ?? 'en'] ?? me.preferred_language}
                </InfoRow>
                {desired.length > 0 && (
                  <div className="min-w-0 sm:col-span-2">
                    <dt className="text-[12.5px] font-semibold text-muted-foreground">{t('profile.desiredRoles')}</dt>
                    <dd className="mt-2">
                      <ul className="flex flex-wrap gap-2">
                        {desired.map((r) => (
                          <li
                            key={r}
                            className="inline-flex items-center rounded-full border border-border bg-[var(--ui-inset)] px-3 py-1 text-[13px] text-foreground"
                          >
                            {r}
                          </li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                )}
                <div className="min-w-0 sm:col-span-2">
                  <dt className="text-[12.5px] font-semibold text-muted-foreground">{t('profile.resume')}</dt>
                  <dd className="mt-1">
                    <Link
                      to="/resume"
                      className="inline-flex min-h-10 items-center gap-2 rounded-full text-[14px] font-medium text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                    >
                      <FileText size={15} aria-hidden="true" />
                      {me.has_resume ? t('profile.resumeOnFile') : t('profile.resumeUpload')}
                    </Link>
                  </dd>
                </div>
              </dl>
            </SectionCard>
          )}

          {/* Company & work details (HR) */}
          {isHr && (
            <SectionCard
              title={t('profile.companyTitle')}
              action={me.official_email ? <EditButton label={t('profile.editCompany')} onClick={() => openEditor('company')} /> : undefined}
            >
              <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
                <InfoRow label={t('profile.company')} icon={Building2}>{me.company_name}</InfoRow>
                {me.official_email && <InfoRow label={t('profile.officialEmail')} icon={Mail}>{me.official_email}</InfoRow>}
              </dl>
              {!me.official_email && (
                <EmptyState
                  icon={Mail}
                  title={t('profile.stepEmail')}
                  description={t('profile.emptyEmailDesc')}
                  action={<Button type="button" onClick={() => openEditor('company')}>{t('profile.addOfficialEmail')}</Button>}
                  className="mt-5"
                />
              )}
            </SectionCard>
          )}

          {/* Contact & links */}
          <SectionCard
            title={t('profile.contactTitle')}
            action={
              me.phone || hasLinks
                ? <EditButton label={t('profile.editContact')} onClick={() => openEditor('contact')} />
                : undefined
            }
          >
            <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
              <InfoRow label={t('profile.accountEmail')} icon={Mail}>{me.email}</InfoRow>
              {(me.phone || hasLinks) && (
                <>
                  <InfoRow label={t('profile.phone')} icon={Phone}>{me.phone}</InfoRow>
                  <InfoRow label="LinkedIn" icon={Link2}>{me.linkedin_url}</InfoRow>
                  <InfoRow label="GitHub" icon={Link2}>{me.github_url}</InfoRow>
                </>
              )}
            </dl>
            {!me.phone && !hasLinks && (
              <EmptyState
                icon={Link2}
                title={t('profile.emptyContactTitle')}
                description={t('profile.emptyContactDesc')}
                action={<Button type="button" onClick={() => openEditor('contact')}>{t('profile.addContact')}</Button>}
                className="mt-5"
              />
            )}
          </SectionCard>

          <p className="flex items-center gap-1.5 pb-2 text-[12.5px] text-muted-foreground">
            <ShieldCheck size={13} aria-hidden="true" /> {t('profile.privacy')}
          </p>
        </div>
      </div>

      {/* One focused editor at a time; its draft is discarded on Cancel. */}
      <Dialog open={editing !== null} onOpenChange={(o) => { if (!o) closeEditor(); }}>
        <DialogContent className={cn('max-h-[90vh] w-[calc(100%-2rem)] overflow-y-auto rounded-card')}>
          {shown && draft && (
            <form onSubmit={onSubmit} className="grid gap-5">
              <DialogHeader>
                <DialogTitle>{editorTitle[shown]}</DialogTitle>
                <DialogDescription>{editorHint[shown]}</DialogDescription>
              </DialogHeader>

              <div className="grid gap-4">
                {shown === 'profile' && (
                  <>
                    <TextField label={t('profile.fullName')} value={draft.full_name} onChange={(v) => set('full_name', v)} placeholder={t('profile.fullNamePh')} />
                    <TextField
                      label={isHr ? t('profile.titleRole') : t('profile.headline')}
                      value={draft.headline}
                      onChange={(v) => set('headline', v)}
                      placeholder={isHr ? t('profile.titleRolePh') : t('profile.headlinePh')}
                    />
                    <TextField label={t('profile.location')} value={draft.location} onChange={(v) => set('location', v)} placeholder={t('profile.locationPh')} />
                  </>
                )}

                {shown === 'about' && (
                  <FormField
                    label={isCandidate ? t('profile.emptyAboutCandTitle') : t('profile.description')}
                    hint={isCandidate ? t('profile.aboutHint') : undefined}
                  >
                    {({ id, describedBy }) => (
                      <textarea
                        id={id}
                        aria-describedby={describedBy}
                        className={cn(CONTROL_CLS, 'min-h-[140px] resize-y py-2.5 leading-relaxed')}
                        value={draft.bio}
                        onChange={(e) => set('bio', e.target.value)}
                        placeholder={
                          isCandidate
                            ? t('profile.bioPhCandidate')
                            : isHr
                              ? t('profile.bioPhHr')
                              : t('profile.bioPhAdmin')
                        }
                        maxLength={2000}
                      />
                    )}
                  </FormField>
                )}

                {shown === 'career' && isCandidate && (
                  <>
                    <FormField label={t('profile.currentStatus')}>
                      {({ id }) => (
                        <select
                          id={id}
                          className={cn(CONTROL_CLS, 'h-11')}
                          value={draft.employment_status}
                          onChange={(e) => set('employment_status', e.target.value)}
                        >
                          <option value="">{t('profile.preferNotToSay')}</option>
                          <option value="student">{t('profile.statusStudent')}</option>
                          <option value="employed">{t('profile.statusEmployed')}</option>
                        </select>
                      )}
                    </FormField>
                    <FormField label={t('profile.preferredLanguage')}>
                      {({ id }) => (
                        <select
                          id={id}
                          className={cn(CONTROL_CLS, 'h-11')}
                          value={draft.preferred_language}
                          onChange={(e) => set('preferred_language', e.target.value)}
                        >
                          <option value="en">{LANGUAGE_LABEL.en}</option>
                          <option value="hi">{LANGUAGE_LABEL.hi}</option>
                          <option value="te">{LANGUAGE_LABEL.te}</option>
                        </select>
                      )}
                    </FormField>
                    <TextField
                      label={t('profile.desiredRoles')}
                      hint={t('profile.desiredRolesHint')}
                      value={draft.desired_roles}
                      onChange={(v) => set('desired_roles', v)}
                      placeholder={t('profile.desiredRolesPh')}
                    />
                  </>
                )}

                {shown === 'company' && isHr && (
                  <>
                    <TextField
                      label={t('profile.officialEmail')}
                      hint={t('profile.officialEmailHint')}
                      type="email"
                      value={draft.official_email}
                      onChange={(v) => set('official_email', v)}
                      placeholder="you@company.com"
                    />
                    <FormField label={t('profile.company')}>
                      {({ id }) => (
                        <Input id={id} value={me.company_name ?? '—'} readOnly aria-readonly="true" className="bg-[var(--ui-inset-soft)] text-[var(--ui-soft)]" />
                      )}
                    </FormField>
                  </>
                )}

                {shown === 'contact' && (
                  <>
                    <FormField label={t('profile.accountEmail')}>
                      {({ id }) => (
                        <Input id={id} value={me.email} readOnly aria-readonly="true" className="bg-[var(--ui-inset-soft)] text-[var(--ui-soft)]" />
                      )}
                    </FormField>
                    <TextField label={t('profile.phone')} value={draft.phone} onChange={(v) => set('phone', v)} placeholder="+91 …" />
                    <TextField label="LinkedIn" value={draft.linkedin_url} onChange={(v) => set('linkedin_url', v)} placeholder="https://linkedin.com/in/…" />
                    <TextField label="GitHub" value={draft.github_url} onChange={(v) => set('github_url', v)} placeholder="https://github.com/…" />
                  </>
                )}
              </div>

              <DialogFooter className="gap-2 sm:gap-0">
                <Button type="button" variant="outline" onClick={closeEditor} disabled={mutation.isPending}>
                  {t('profile.cancel')}
                </Button>
                <Button type="submit" disabled={mutation.isPending}>
                  {mutation.isPending ? (
                    <><Loader2 className="animate-spin" aria-hidden="true" /> {t('profile.saving')}</>
                  ) : (
                    <><Check aria-hidden="true" /> {t('profile.saveChanges')}</>
                  )}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </Page>
  );
}
