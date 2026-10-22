// PublicApply — the candidate's front door. Group E, E4. No login.
//
// The only page in this product a stranger reaches. Everything about it is
// shaped by that: no shell, no nav, no branding of ours competing with the
// hiring company's, and no jargon from the console on the other side of the
// wall — this person does not know what a "requisition" or an "enrolment" is
// and never needs to.
//
// THE CONSENT CHECKBOX IS THE POINT. It is the lawful basis for holding this
// person's CV under the DPDP Act, so it is unticked by default, the submit
// button stays disabled until it is ticked, and the text next to it says what
// is actually being stored and by whom. A pre-ticked box, or one buried in a
// link, would make the ledger entry the server writes a record of something
// that did not happen.
//
// Failures are written for the applicant, not the operator. Someone whose PDF
// will not parse needs to know to re-export it, not to see a status code.

import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  AlertTriangle,
  Briefcase,
  Check,
  CheckCircle2,
  FileText,
  Loader2,
  Upload,
} from '@/design/components/icons';
import {
  getPosting,
  saveDraft,
  startDraft,
  submitApplication,
  uploadDraftResume,
  type ApplicationResult,
  type AnswerValue,
  type DraftFields,
  type Posting,
} from '@/api/publicApply';
import { isTransientApiError } from '@/api/client';
import { ApplyQuestionField } from '@/components/candidate/ApplyQuestionField';
import { requiredAnswered as allRequiredAnswered } from '@/components/candidate/applyAnswers';
import { levelLabel, pageLanguage, useCandidateError } from '@/lib/candidateCopy';
import { useAuth } from '@/context/AuthContext';
import { isGuestOnly } from '@/lib/guest';
import {
  filesFromDrop,
  isPdf,
  nameFromFilename,
  seedEmailFor,
} from './publicApplyDrop';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { PublicFrame, PublicNotice } from '@/components/PublicFrame';
import { PublicNext } from '@/components/PublicNext';
import { SUPPORT_EMAIL } from '@/lib/contact';
import { ProgressMeter } from '@/design/components/page';

const MAX_BYTES = 5 * 1024 * 1024;

// Seeding a whole folder of CVs exists ONLY in a dev build. `import.meta.env.DEV`
// is replaced by a literal at build time, so this whole branch — the folder
// picker, the batch panel, the fabricated names and emails — is dropped from
// the production bundle rather than merely hidden. A real candidate on a real
// posting cannot reach it, and it cannot be turned on by a URL or a flag.
const SEEDING_ENABLED = import.meta.env.DEV;

/** One CV in a seeding run. */
interface SeedRow {
  file: File;
  status: 'waiting' | 'sending' | 'done' | 'duplicate' | 'failed';
  detail?: string;
}

/* ── States ─────────────────────────────────────────────────────────────── */

function Shell({
  children,
  company,
  step,
}: {
  children: React.ReactNode;
  company?: string;
  step?: React.ReactNode;
}) {
  return (
    <PublicFrame company={company} step={step}>
      {children}
    </PublicFrame>
  );
}

function Panel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-card border border-border bg-card p-5 shadow-card sm:p-6', className)}>
      {children}
    </div>
  );
}

function Unavailable() {
  const { t } = useTranslation();
  // No "ask the person who invited you": this is a public, shareable posting,
  // and most people reach it from a job board, not an invitation.
  return (
    <Shell>
      <PublicNotice
        icon={<Briefcase size={22} aria-hidden="true" />}
        title={t('apply.unavailableTitle')}
        description={t('apply.unavailableDesc')}
      />
    </Shell>
  );
}

/** The posting could not be loaded for a reason that may pass: offline, a 5xx,
 *  a 429. Telling this person the opening is closed would lose them for good. */
function LoadFailed({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  const { t } = useTranslation();
  return (
    <Shell>
      <PublicNotice
        title={t('apply.loadFailedTitle')}
        description={t('apply.loadFailedDesc')}
        action={
          <Button className="h-11 px-6" onClick={onRetry} disabled={retrying}>
            {retrying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {t('apply.retry')}
          </Button>
        }
      />
    </Shell>
  );
}

function Submitted({
  result,
  title,
  company,
}: {
  result: ApplicationResult;
  title: string;
  company: string;
}) {
  const { t } = useTranslation();
  return (
    <Shell company={company}>
      <PublicNotice
        tone="ok"
        icon={<CheckCircle2 size={22} aria-hidden="true" />}
        title={result.already_applied ? t('apply.alreadyApplied') : t('apply.received')}
        description={result.message}
      />
      {/* Said plainly because the alternative — silence — is what makes
          candidates assume they were rejected. */}
      <PublicNext className="mt-6">{t('apply.reviewNote', { title })}</PublicNext>
    </Shell>
  );
}

/* ── Seeding panel (development builds only) ─────────────────────────────── */

const SEED_TONE: Record<SeedRow['status'], string> = {
  waiting: 'text-[var(--ui-faint)]',
  sending: 'text-[var(--accent)]',
  done: 'text-[var(--ui-ok)]',
  duplicate: 'text-[var(--ui-warn)]',
  failed: 'text-[var(--ui-danger)]',
};

const SEED_LABEL: Record<SeedRow['status'], string> = {
  waiting: 'queued',
  sending: 'sending…',
  done: 'applied',
  duplicate: 'already applied',
  failed: 'failed',
};

function SeedPanel({
  rows,
  running,
  onRun,
  onClear,
}: {
  rows: SeedRow[];
  running: boolean;
  onRun: () => void;
  onClear: () => void;
}) {
  const done = rows.filter((r) => r.status === 'done' || r.status === 'duplicate').length;

  return (
    <Panel className="mt-5 border-[var(--ui-warn)]/30">
      <div className="flex flex-wrap items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
        <h2 className="text-[15px] font-semibold text-foreground">
          Seed {rows.length} test application{rows.length === 1 ? '' : 's'}
        </h2>
        <span className="ml-auto text-[12.5px] text-[var(--ui-soft)]">
          {done}/{rows.length}
        </span>
      </div>

      <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">
        Development build only - this panel is not in a production bundle. Each CV is sent
        as its own application with a name guessed from the filename and a
        {' '}<code className="text-[var(--ui-soft)]">@seed.example.com</code> address, so nothing here can
        email a real person. Sent one at a time: the endpoint allows six a minute and this
        waits rather than failing when it hits that.
      </p>

      <ul className="mt-3 flex max-h-[240px] list-none flex-col gap-1 overflow-y-auto">
        {rows.map((row, i) => (
          <li
            key={`${row.file.name}-${i}`}
            className="flex items-center gap-2 text-[12px]"
          >
            <span className="min-w-0 flex-1 truncate text-[var(--ui-soft)]">{row.file.name}</span>
            <span className={cn('shrink-0', SEED_TONE[row.status])}>
              {row.detail ?? SEED_LABEL[row.status]}
            </span>
          </li>
        ))}
      </ul>

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-[12px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          {running ? 'Seeding…' : `Send ${rows.length} applications`}
        </button>
        <button
          type="button"
          onClick={onClear}
          disabled={running}
          className="rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
        >
          Clear
        </button>
      </div>
    </Panel>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

// ---------------------------------------------------------------------------
// The advert
//
// Everything here degrades to nothing. Most openings predate these fields —
// including every one the Group B backfill minted from a bare job title — so
// each block renders only when it has something to say. A heading over an
// empty list reads as "we lost your data"; an absent heading reads as an
// advert that did not mention it, which is the truth.
// ---------------------------------------------------------------------------

/**
 * Employment-type labels, duplicated from `api/requisitions.ts` rather than
 * imported from it.
 *
 * That module pulls in the authenticated client, and this page deliberately
 * has no session — importing it would drag the 401 -> refresh -> redirect-to-
 * login machinery into the one page an applicant reaches without an account.
 * Five strings is a cheaper duplication than that import.
 *
 * An unknown value falls back to the raw string: the set is constrained by a
 * database check, so a value outside it means someone added a type, and
 * showing `contract_to_hire` beats showing nothing.
 */
// Same closed server vocabulary the careers board renders, so it reads the
// same keys. Two copies of these labels would drift apart.
function employmentLabel(t: TFunction, value: string): string {
  const key = `careers.employment.${value}`;
  const label = t(key);
  return label === key ? value : label;
}

/** "Bengaluru · Full-time · Engineering", skipping whatever is missing. */
function metaLine(t: TFunction, job: Posting): string {
  const employment = job.employment_type
    ? employmentLabel(t, job.employment_type)
    : null;
  return [job.location, employment, job.department].filter(Boolean).join(' · ');
}

/** "4–8 years", "4+ years", "Up to 8 years", or nothing. */
function experienceLine(t: TFunction, job: Posting): string | null {
  const { experience_min_years: lo, experience_max_years: hi } = job;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null) {
    return lo === hi
      ? t('careers.yearsExact', { years: lo })
      : t('careers.yearsRange', { lo, hi });
  }
  return lo != null ? t('careers.yearsMin', { lo }) : t('careers.yearsMax', { hi });
}

/**
 * The band, or nothing. Null means "not disclosed" and the server sends null
 * for both a withheld salary and an unrecorded one — so there is deliberately
 * no "salary not specified" copy here to distinguish them.
 */
function salaryLine(t: TFunction, job: Posting): string | null {
  const { salary_min: lo, salary_max: hi } = job;
  if (lo == null && hi == null) return null;
  const currency = job.salary_currency ?? '';
  const money = (n: number) => `${currency} ${n.toLocaleString()}`.trim();
  if (lo != null && hi != null) {
    return t('careers.salaryRange', { lo: money(lo), hi: money(hi) });
  }
  return lo != null
    ? t('careers.salaryFrom', { value: money(lo) })
    : t('careers.salaryUpTo', { value: money(hi as number) });
}

function BulletPanel({ heading, items }: { heading: string; items?: string[] }) {
  // Defensive despite the type: this is the one page reached without an
  // account, and a thrown render loses an applicant rather than hiding a
  // section. An older cached response is enough to do it.
  if (!items || items.length === 0) return null;
  return (
    <Panel className="mb-5">
      <h2 className="mb-2 text-[17px] font-bold text-foreground">{heading}</h2>
      <ul className="flex list-disc flex-col gap-1.5 pl-5 text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Panel>
  );
}

function SkillTags({ heading, skills }: { heading: string; skills?: string[] }) {
  if (!skills || skills.length === 0) return null;
  return (
    <div>
      <h3 className="mb-2 text-[12.5px] font-medium text-[var(--ui-soft)]">{heading}</h3>
      <div className="flex flex-wrap gap-1.5">
        {skills.map((skill) => (
          <span
            key={skill}
            className="rounded-full border border-border bg-[var(--ui-inset)] px-2.5 py-1 text-[12px] text-[var(--ui-soft)]"
          >
            {skill}
          </span>
        ))}
      </div>
    </div>
  );
}


/**
 * The steps, and why there are several rather than one long form.
 *
 * The single page this replaces asked for a name, an email and a CV — three
 * things, so one page was right. Asking for eight on one page is where people
 * abandon an application, and the abandonment is silent: nothing is stored
 * until consent is given, so a half-filled form is simply a candidate the
 * company never hears from.
 *
 * Step two is entirely optional and says so. It exists because a recruiter
 * screening two hundred CVs wants "6 years, currently at Acme" without reading
 * each one — but a required field there would turn a preference into a barrier,
 * so it can be skipped in one click.
 *
 * CONSENT STAYS ON THE LAST STEP, with the submit button, and nothing is
 * uploaded before it. Splitting the form must not create a moment where a CV
 * has been chosen and consent has not yet been refused — the file never leaves
 * the browser until the final submit.
 */
function stepsFor(t: TFunction, questionCount: number): string[] {
  // The questions step exists only when there is something on it. Most
  // openings ask nothing, and an empty "Questions" step is a click that makes
  // the form look longer than it is.
  const you = t('apply.stepYou');
  const experience = t('apply.stepExperience');
  const cv = t('apply.stepCv');
  const review = t('apply.stepReview');
  return questionCount > 0
    ? [you, experience, cv, t('apply.stepQuestions'), review]
    : [you, experience, cv, review];
}

function Stepper({ current, steps }: { current: number; steps: string[] }) {
  const { t } = useTranslation();
  return (
    <div className="mb-5">
      <p className="mb-2 text-[13px] font-medium text-foreground sm:hidden">
        {current + 1} / {steps.length} · {steps[current]}
      </p>
      <ProgressMeter
        className="mb-3 sm:hidden"
        value={((current + 1) / steps.length) * 100}
        label={t('apply.progressAria')}
      />
    <ol className="hidden items-center gap-2 sm:flex" aria-label={t('apply.progressAria')}>
      {steps.map((label, i) => (
        <li key={label} className="flex flex-1 items-center gap-2">
          <span
            className={cn(
              // Same look as the shared Stepper (design/components/page): done is
              // the primary action colour, current the highlight tint with a ring.
              // The shared component is not used itself because it carries
              // English-only "Step n of N" text and this flow is localised.
              'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12.5px] font-bold',
              i < current && 'bg-primary text-primary-foreground',
              i === current && 'bg-[var(--ui-highlight)] text-[var(--ui-highlight-ink)] ring-2 ring-[var(--accent)]',
              i > current && 'border border-[var(--ui-line-strong)] text-muted-foreground',
            )}
            aria-current={i === current ? 'step' : undefined}
          >
            {i < current ? <Check className="h-3 w-3" aria-hidden="true" /> : i + 1}
          </span>
          <span
            className={cn(
              'text-[13.5px]',
              i === current ? 'font-semibold text-foreground' : 'font-medium text-muted-foreground',
            )}
          >
            {label}
          </span>
          {i < steps.length - 1 ? (
            <span className="h-px flex-1 bg-[var(--ui-line-strong)]" aria-hidden="true" />
          ) : null}
        </li>
      ))}
    </ol>
    </div>
  );
}

/** One line of the review step. Missing answers say so rather than sitting blank. */
function ReviewRow({ label, value }: { label: string; value: string | null }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2 last:border-b-0">
      <span className="text-[13px] text-muted-foreground">{label}</span>
      <span
        className={cn(
          'min-w-0 break-words text-right text-[14px]',
          value ? 'text-foreground' : 'italic text-[var(--ui-soft)]',
        )}
      >
        {value || t('apply.notProvided')}
      </span>
    </div>
  );
}

/** A plausible address: something@something.tld, no spaces. The server's
 *  EmailStr is the real check; this only stops an obvious typo reaching it
 *  three steps later, after the CV is chosen. */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export default function PublicApply(): JSX.Element {
  const { t, i18n } = useTranslation();
  const errorText = useCandidateError();
  // A signed-in candidate applies as their account. Typing another address
  // here would create a second, unlinked application and placeholder account
  // that never shows up under My applications (audit CA-M12).
  const { isAuthenticated, user } = useAuth();
  const accountEmail =
    isAuthenticated && user?.email && !isGuestOnly(user.roles) ? user.email : null;
  const { requisitionId = '' } = useParams();
  // The tracked link's campaign tag (PH3-B1). Read once, here, and then only
  // ever used through what the server echoes back: the vocabulary belongs to
  // the server, and a page that normalised `?src=` itself would be a second
  // implementation of it.
  const [searchParams] = useSearchParams();
  const src = searchParams.get('src');
  const [fullName, setFullName] = useState('');
  const [typedEmail, setEmail] = useState('');
  // The account's address wins once the session is known, even if it arrives
  // after the first render.
  const email = accountEmail ?? typedEmail;
  const [emailTouched, setEmailTouched] = useState(false);
  const [resume, setResume] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  // PH5-E3 (D5-1) — a SECOND, INDEPENDENT opt-in, unticked by default. Never
  // read by `readyBase`/`ready` below: bundling it into the application
  // consent gate would make it non-optional, which DPDP §6(1) forbids and
  // D5-1's "opt-in" rules out. Getting this wrong (joining the gate, or
  // defaulting it true) would be the worst outcome this screen could ship.
  const [rediscoveryConsent, setRediscoveryConsent] = useState(false);
  // The language of the emails this application sends — EN, HI or TE.
  // Defaults to the language the page is being read in: a Telugu reader who
  // never scrolls to this control should not get English emails.
  const [language, setLanguage] = useState<'en' | 'hi' | 'te'>(() =>
    pageLanguage(i18n.language),
  );
  // Step two. All optional — see STEPS below for why the step exists at all.
  const [phone, setPhone] = useState('');
  const [yearsExperience, setYearsExperience] = useState('');
  const [currentCompany, setCurrentCompany] = useState('');
  const [currentTitle, setCurrentTitle] = useState('');
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [githubUrl, setGithubUrl] = useState('');
  const [step, setStep] = useState(0);
  // Keyed by question id. Absent means unanswered — deliberately distinct from
  // an empty string, which is what a touched-then-cleared field leaves behind
  // and which the server also reads as unanswered.
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({});
  const [fileError, setFileError] = useState('');
  const [dragging, setDragging] = useState(false);
  // Dev-only seeding state. Never populated in a production build, because
  // nothing that sets it survives the SEEDING_ENABLED branch.
  const [seedRows, setSeedRows] = useState<SeedRow[]>([]);
  const [seeding, setSeeding] = useState(false);
  const folderInput = useRef<HTMLInputElement>(null);

  const posting = useQuery({
    // `src` is in the key: two campaign links to the same opening are two
    // different tracked views, and a cached response from the first would
    // attribute the second to the wrong channel.
    queryKey: ['public', 'posting', requisitionId, src],
    queryFn: () => getPosting(requisitionId, src),
    enabled: Boolean(requisitionId),
    // A 404 is definitive (closed, or not public); a network blip or a 5xx is
    // not, and is worth a quiet retry before the candidate sees anything.
    retry: (failureCount, error) => isTransientApiError(error) && failureCount < 2,
  });

  const submit = useMutation({
    mutationFn: () =>
      submitApplication(requisitionId, {
        fullName: fullName.trim(),
        email: email.trim(),
        resume: resume as File,
        consentGranted: consent,
        rediscoveryOptIn: rediscoveryConsent,
        language,
        phone,
        yearsExperience: yearsExperience === '' ? null : Number(yearsExperience),
        currentCompany,
        currentTitle,
        linkedinUrl,
        githubUrl,
        answers,
        // What the server said this view's channel was, not the raw `?src=`.
        source: posting.data?.source,
      }),
  });

  // PH3-B4c. Starting a draft is what records consent — it is the first moment
  // this person's email is stored — so it carries the checkbox's value rather
  // than assuming it, and the server refuses without it.
  // PH5-E3: the rediscovery tick travels with the draft too. It sits directly
  // above "Save for later", so leaving it off this path meant a candidate who
  // ticked it and saved would have that choice silently discarded — and no
  // screen would ever say so. A dropped consent is worse than an absent
  // feature. Found in code review, 2026-09-26.
  //
  // Starting a draft stores only the email; the rest of what the candidate has
  // typed by now (it is the last step) is saved onto it straight after, and the
  // chosen CV uploaded. Before this the copy said their progress was stored
  // while everything but the email was thrown away, and the link opened an
  // empty draft (audit CA-H2). If a follow-up step fails the draft and its link
  // still exist, so the link is shown and the copy says what is missing.
  const saveLater = useMutation({
    mutationFn: async () => {
      const started = await startDraft(requisitionId, {
        email: email.trim(),
        consentGranted: consent,
        rediscoveryOptIn: rediscoveryConsent,
        language,
        src: posting.data?.source,
      });
      let complete = true;
      try {
        await saveDraft(started.resume_token, typedFields());
      } catch {
        complete = false;
      }
      if (resume) {
        try {
          await uploadDraftResume(started.resume_token, resume);
        } catch {
          complete = false;
        }
      }
      return { token: started.resume_token, complete };
    },
  });

  /** What the candidate has typed, in the draft's shape. Blank fields are left
   *  out rather than sent empty, the same rule `submitApplication` follows. */
  function typedFields(): DraftFields {
    const out: DraftFields = {};
    const text: [keyof DraftFields, string][] = [
      ['full_name', fullName],
      ['phone', phone],
      ['current_company', currentCompany],
      ['current_title', currentTitle],
      ['linkedin_url', linkedinUrl],
      ['github_url', githubUrl],
    ];
    for (const [key, value] of text) {
      if (value.trim() !== '') (out as Record<string, string>)[key] = value.trim();
    }
    if (yearsExperience !== '') out.years_experience = Number(yearsExperience);
    if (Object.keys(answers).length > 0) out.answers = answers;
    return out;
  }

  function pickFile(file: File | null): void {
    setFileError('');
    if (!file) {
      setResume(null);
      return;
    }
    // Checked here as well as server-side so someone does not upload 30 MB over
    // a phone connection to be told no at the end of it.
    if (file.size > MAX_BYTES) {
      setFileError(t('apply.errTooBig'));
      setResume(null);
      return;
    }
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setFileError(t('apply.errNotPdf'));
      setResume(null);
      return;
    }
    setResume(file);
  }

  /** One drop, however it arrived: one CV, or a folder of them. */
  function acceptDropped(files: File[]): void {
    setFileError('');
    if (files.length === 0) {
      setFileError(t('apply.errNoPdfs'));
      return;
    }
    if (files.length === 1 || !SEEDING_ENABLED) {
      // A real applicant dropping several files means the first one, not a
      // batch — and outside a dev build a batch is not on offer at all.
      pickFile(files[0]);
      return;
    }
    setSeedRows(files.map((file) => ({ file, status: 'waiting' })));
  }

  /**
   * Submit a seeded batch, one application at a time.
   *
   * Sequential on purpose. The endpoint is rate-limited per IP (six a minute),
   * and that limit is the real control here — batching client-side cannot get
   * round it. Firing them in parallel would just convert the whole run into
   * 429s; going one at a time and waiting when told to means a folder of
   * thirty seeds in about five minutes without a single rejected request.
   */
  async function runSeed(): Promise<void> {
    setSeeding(true);
    for (let i = 0; i < seedRows.length; i++) {
      const row = seedRows[i];
      setSeedRows((prev) =>
        prev.map((r, j) => (j === i ? { ...r, status: 'sending' } : r)),
      );
      try {
        const result = await submitApplication(requisitionId, {
          fullName: nameFromFilename(row.file.name),
          email: seedEmailFor(row.file.name),
          resume: row.file,
          consentGranted: true,
          source: posting.data?.source,
        });
        setSeedRows((prev) =>
          prev.map((r, j) =>
            j === i
              ? { ...r, status: result.already_applied ? 'duplicate' : 'done' }
              : r,
          ),
        );
      } catch (err) {
        const message = err instanceof Error && err.message ? err.message : 'failed';
        // A 429 is not a failure, it is the server pacing us. Wait out the
        // fixed one-minute window and retry the same file rather than
        // recording a loss the operator would have to chase.
        if (/too many requests/i.test(message)) {
          setSeedRows((prev) =>
            prev.map((r, j) =>
              j === i ? { ...r, status: 'waiting', detail: 'rate limited - waiting' } : r,
            ),
          );
          await new Promise((resolve) => setTimeout(resolve, 61_000));
          i -= 1;
          continue;
        }
        setSeedRows((prev) =>
          prev.map((r, j) => (j === i ? { ...r, status: 'failed', detail: message } : r)),
        );
      }
    }
    setSeeding(false);
  }

  if (posting.isLoading) {
    return (
      <Shell>
        <div role="status" className="flex items-center justify-center gap-2 py-20 text-[14px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {t('apply.loading')}
        </div>
      </Shell>
    );
  }
  if (posting.isError && isTransientApiError(posting.error)) {
    return (
      <LoadFailed onRetry={() => void posting.refetch()} retrying={posting.isFetching} />
    );
  }
  if (posting.isError || !posting.data) return <Unavailable />;
  if (submit.isSuccess) {
    return (
      <Submitted
        result={submit.data}
        title={posting.data.title}
        company={posting.data.company_name}
      />
    );
  }

  const job = posting.data;
  // `requiredAnswered` is folded in below rather than here, because it is
  // derived from the posting and this line runs before the posting is read.
  const emailValid = EMAIL_SHAPE.test(email.trim());
  const readyBase = fullName.trim().length >= 2 && emailValid && resume && consent;
  // What each step needs before it will let you move on. Step 2 asks for
  // nothing, which is the whole point of it being skippable; the last step is
  // gated by `ready` on its own submit button, so consent is never a
  // "Continue" — it stays the last thing that happens before an upload.
  const questions = job.questions ?? [];
  const steps = stepsFor(t, questions.length);
  const questionsStep = questions.length > 0 ? 3 : -1;

  const requiredAnswered = allRequiredAnswered(questions, answers);
  const ready = readyBase && requiredAnswered;

  const canContinue =
    step === 0
      ? fullName.trim().length >= 2 && emailValid
      : step === 2
        ? Boolean(resume)
        : step === questionsStep
          ? requiredAnswered
          : true;
  const field =
    'flex min-h-11 w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-base text-foreground placeholder:text-muted-foreground hover:border-[var(--ui-line-strong)] focus-visible:border-ring focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/20 md:text-sm';

  return (
    <Shell company={job.company_name} step={t('public.step.apply')}>
      <header className="mb-6">
        {/* The company name sits in the frame's header, so it is not repeated here. */}
        <h1 className="text-[28px] font-bold leading-[1.15] tracking-[-0.6px] text-foreground sm:text-[32px]">
          {job.title}
        </h1>
        {metaLine(t, job) ? (
          <div className="mt-2 text-[15px] text-[var(--ui-soft)]">{metaLine(t, job)}</div>
        ) : null}
        <div className="mt-1 text-[13.5px] text-muted-foreground">
          {levelLabel(t, job.level)}
          {experienceLine(t, job) ? ` · ${experienceLine(t, job)}` : ''}
          {job.closes_at
            ? ` · ${t('apply.closesOn', {
                date: new Date(job.closes_at).toLocaleDateString(),
              })}`
            : ''}
        </div>
        {salaryLine(t, job) ? (
          <div className="mt-3 inline-block rounded-full border border-border bg-[var(--ui-inset)] px-3.5 py-1.5 text-[13.5px] font-medium text-foreground">
            {salaryLine(t, job)}
          </div>
        ) : null}
      </header>

      {job.jd_text ? (
        <Panel className="mb-5">
          <h2 className="mb-2 text-[17px] font-bold text-foreground">
            {t('apply.aboutRole')}
          </h2>
          <p className="whitespace-pre-wrap text-[14.5px] leading-relaxed text-[var(--ui-soft)]">
            {job.jd_text}
          </p>
        </Panel>
      ) : null}

      <BulletPanel heading={t('apply.whatYouWillDo')} items={job.responsibilities} />

      {(job.required_skills?.length ?? 0) > 0 || (job.nice_to_have_skills?.length ?? 0) > 0 ? (
        <Panel className="mb-5">
          <div className="flex flex-col gap-4">
            <SkillTags heading={t('apply.requiredSkills')} skills={job.required_skills} />
            <SkillTags heading={t('apply.niceToHave')} skills={job.nice_to_have_skills} />
          </div>
        </Panel>
      ) : null}

      <Panel>
        <h2 className="text-[20px] font-bold tracking-[-0.3px] text-foreground">
          {t('apply.applyHeading')}
        </h2>

        <div className="mt-4">
          <Stepper current={step} steps={steps} />
        </div>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            // Only the final step submits. Enter on an earlier step advances,
            // which is what a keyboard user expects and what stops a
            // half-filled application being sent by a stray keypress.
            if (step < steps.length - 1) {
              if (step === 0) setEmailTouched(true);
              if (canContinue) setStep(step + 1);
              return;
            }
            if (ready) submit.mutate();
          }}
        >
          {step === 0 ? (
            <>
          <div>
            <label htmlFor="name" className="mb-1.5 block text-[13px] font-medium text-foreground">
              {t('apply.yourName')}
            </label>
            <input
              id="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
              className={field}
            />
          </div>

          <div>
            <label htmlFor="email" className="mb-1.5 block text-[13px] font-medium text-foreground">
              {t('apply.email')}
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setEmailTouched(true)}
              autoComplete="email"
              readOnly={accountEmail !== null}
              aria-invalid={emailTouched && !emailValid ? true : undefined}
              aria-describedby="email-hint"
              className={cn(field, accountEmail !== null && 'bg-[var(--ui-inset)]')}
            />
            {emailTouched && !emailValid ? (
              <p id="email-hint" role="alert" className="mt-1 text-[12.5px] text-[var(--ui-danger)]">
                {t('apply.errEmail')}
              </p>
            ) : (
              <p id="email-hint" className="mt-1 text-[12.5px] text-[var(--ui-soft)]">
                {accountEmail !== null ? t('apply.emailAccountHint') : t('apply.emailHint')}
              </p>
            )}
          </div>

              <div>
                <label
                  htmlFor="phone"
                  className="mb-1.5 block text-[13px] font-medium text-foreground"
                >
                  {t('apply.phone')}{' '}
                  <span className="font-normal text-[var(--ui-soft)]">
                    {t('apply.optional')}
                  </span>
                </label>
                <input
                  id="phone"
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  autoComplete="tel"
                  className={field}
                />
              </div>
            </>
          ) : null}

          {step === 1 ? (
            <>
              <p className="text-[13.5px] leading-relaxed text-muted-foreground">
                {t('apply.optionalIntro')}
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="years"
                    className="mb-1.5 block text-[13px] font-medium text-foreground"
                  >
                    {t('apply.years')}
                  </label>
                  <input
                    id="years"
                    type="number"
                    min={0}
                    max={60}
                    value={yearsExperience}
                    onChange={(e) => setYearsExperience(e.target.value)}
                    className={field}
                  />
                </div>
                <div>
                  <label
                    htmlFor="company"
                    className="mb-1.5 block text-[13px] font-medium text-foreground"
                  >
                    {t('apply.currentCompany')}
                  </label>
                  <input
                    id="company"
                    value={currentCompany}
                    onChange={(e) => setCurrentCompany(e.target.value)}
                    autoComplete="organization"
                    className={field}
                  />
                </div>
              </div>
              <div>
                <label
                  htmlFor="title"
                  className="mb-1.5 block text-[13px] font-medium text-foreground"
                >
                  {t('apply.currentRole')}
                </label>
                <input
                  id="title"
                  value={currentTitle}
                  onChange={(e) => setCurrentTitle(e.target.value)}
                  autoComplete="organization-title"
                  className={field}
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="linkedin"
                    className="mb-1.5 block text-[13px] font-medium text-foreground"
                  >
                    {t('apply.linkedin')}
                  </label>
                  <input
                    id="linkedin"
                    type="url"
                    inputMode="url"
                    value={linkedinUrl}
                    onChange={(e) => setLinkedinUrl(e.target.value)}
                    className={field}
                  />
                </div>
                <div>
                  <label
                    htmlFor="github"
                    className="mb-1.5 block text-[13px] font-medium text-foreground"
                  >
                    {t('apply.github')}
                  </label>
                  <input
                    id="github"
                    type="url"
                    inputMode="url"
                    value={githubUrl}
                    onChange={(e) => setGithubUrl(e.target.value)}
                    className={field}
                  />
                </div>
              </div>
            </>
          ) : null}

          {step === 2 ? <>          <div>
            <label htmlFor="cv" className="mb-1.5 block text-[13px] font-medium text-foreground">
              {t('apply.cvLabel')}
            </label>
            <label
              htmlFor="cv"
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                // Read the entries synchronously — dataTransfer.items is
                // emptied the moment this handler returns, so anything read
                // after an await comes back empty.
                void filesFromDrop(e.dataTransfer).then(acceptDropped);
              }}
              className={cn(
                'flex cursor-pointer items-center gap-3 rounded-[16px] border border-dashed px-4 py-6 transition-colors focus-within:ring-2 focus-within:ring-[var(--accent)]',
                resume
                  ? 'border-[var(--ui-ok)] bg-[var(--ui-ok-wash)]'
                  : dragging
                    ? 'border-[var(--accent)] bg-[var(--ui-inset)]'
                    : 'border-[var(--ui-line-strong)] hover:border-foreground/40',
              )}
            >
              {resume ? (
                <FileText className="h-5 w-5 shrink-0 text-[var(--ui-ok)]" aria-hidden="true" />
              ) : (
                <Upload className="h-5 w-5 shrink-0 text-[var(--ui-soft)]" aria-hidden="true" />
              )}
              <span className="min-w-0 flex-1 truncate text-[14px] text-foreground">
                {resume
                  ? resume.name
                  : dragging
                    ? t('apply.dropHere')
                    : t('apply.dropPrompt')}
              </span>
            </label>
            <input
              id="cv"
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
            {fileError ? (
              <p className="mt-2 text-[13px] text-[var(--ui-danger)]" role="alert">
                {fileError}
              </p>
            ) : (
              <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">
                {t('apply.cvHint')}
              </p>
            )}
          </div>

</> : null}

          {questionsStep >= 0 && step === questionsStep ? (
            <div className="flex flex-col gap-4">
              {questions.map((q) => (
                <ApplyQuestionField
                  key={q.id}
                  question={q}
                  value={answers[q.id]}
                  onChange={(next) => setAnswers({ ...answers, [q.id]: next })}
                />
              ))}
            </div>
          ) : null}

          {step === steps.length - 1 ? (
            <>
              <div className="rounded-[12px] bg-[var(--ui-inset-soft)] px-4 py-1">
                <ReviewRow label={t('apply.reviewName')} value={fullName.trim() || null} />
                <ReviewRow label={t('apply.email')} value={email.trim() || null} />
                <ReviewRow label={t('apply.phone')} value={phone.trim() || null} />
                <ReviewRow
                  label={t('apply.stepExperience')}
                  value={
                    yearsExperience === ''
                      ? null
                      : t('apply.experienceYears', { count: Number(yearsExperience) })
                  }
                />
                <ReviewRow
                  label={t('apply.currentCompany')}
                  value={currentCompany.trim() || null}
                />
                <ReviewRow label={t('apply.currentRole')} value={currentTitle.trim() || null} />
                <ReviewRow label={t('apply.linkedin')} value={linkedinUrl.trim() || null} />
                <ReviewRow label={t('apply.github')} value={githubUrl.trim() || null} />
                <ReviewRow label={t('apply.reviewCv')} value={resume ? resume.name : null} />
                {questions.map((q) => (
                  <ReviewRow
                    key={q.id}
                    label={q.prompt}
                    value={
                      answers[q.id] === undefined
                        ? null
                        : Array.isArray(answers[q.id])
                          ? (answers[q.id] as string[]).join(', ') || null
                          : typeof answers[q.id] === 'boolean'
                            ? ((answers[q.id] as boolean) ? t('apply.yes') : t('apply.no'))
                            : (answers[q.id] as string).trim() || null
                    }
                  />
                ))}
              </div>

          <div>
            <label
              htmlFor="apply-language"
              className="mb-1.5 block text-[13px] font-medium text-foreground"
            >
              {t('apply.emailLanguage')}
            </label>
            <select
              id="apply-language"
              value={language}
              onChange={(e) => setLanguage(e.target.value as 'en' | 'hi' | 'te')}
              className={field}
            >
              <option value="en">English</option>
              <option value="hi">हिन्दी (Hindi)</option>
              <option value="te">తెలుగు (Telugu)</option>
            </select>
          </div>

          {/* The lawful basis. Unticked by default, and the button below cannot
              be pressed until it is ticked. */}
          <label className="flex cursor-pointer items-start gap-3 rounded-[16px] border border-border p-4">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className="mt-0.5 h-5 w-5 shrink-0 accent-[var(--accent)]"
            />
            <span className="text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
              {t('apply.consent', { company: job.company_name, supportEmail: SUPPORT_EMAIL })}
            </span>
          </label>

          {/* PH5-E3 (D5-1) — a SECOND, INDEPENDENT, unticked checkbox. It
              must NEVER join `ready` below: this is a separate DPDP §6(1)
              consent (being found again for a FUTURE opening), and bundling
              it with the application consent above would make it
              non-optional, which D5-1's "opt-in" design rules out. */}
          <label className="flex cursor-pointer items-start gap-3 rounded-[16px] border border-border p-4">
            <input
              type="checkbox"
              checked={rediscoveryConsent}
              onChange={(e) => setRediscoveryConsent(e.target.checked)}
              className="mt-0.5 h-5 w-5 shrink-0 accent-[var(--accent)]"
            />
            <span className="text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
              {t('apply.rediscoveryConsent', { company: job.company_name, months: 12 })}
            </span>
          </label>

          {/* PH3-B4c — save and come back.
              Deliberately BELOW the consent box and disabled until it is
              ticked: saving stores this person's email, and the server refuses
              a draft without recorded permission. The button is not hidden
              when consent is missing — it is visible and inert, so the reason
              it cannot be pressed is the checkbox directly above it. */}
          <div className="rounded-[12px] bg-[var(--ui-inset-soft)] p-4">
            {saveLater.data ? (
              <>
                <p className="text-[14px] font-semibold text-foreground">
                  {t('apply.savedTitle')}
                </p>
                <input
                  readOnly
                  aria-label={t('apply.resumeLinkAria')}
                  value={`${window.location.origin}/apply/draft#${saveLater.data.token}`}
                  onFocus={(e) => e.currentTarget.select()}
                  className="mt-2 w-full rounded-[12px] border border-border bg-transparent px-3 py-2.5 text-[13px] text-[var(--ui-soft)]"
                />
                <p
                  className={cn(
                    'mt-1.5 text-[12.5px]',
                    saveLater.data.complete ? 'text-[var(--ui-soft)]' : 'text-[var(--ui-warn)]',
                  )}
                  role={saveLater.data.complete ? undefined : 'alert'}
                >
                  {saveLater.data.complete
                    ? t('apply.savedAgainst', { email: email.trim() })
                    : t('apply.savedPartial', { email: email.trim() })}
                </p>
              </>
            ) : (
              <>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => saveLater.mutate()}
                  disabled={!consent || !emailValid || saveLater.isPending}
                  className="h-11"
                >
                  {saveLater.isPending ? t('apply.saving') : t('apply.saveLater')}
                </Button>
                <p className="mt-1 text-[12.5px] text-[var(--ui-soft)]">
                  {consent ? t('apply.saveHint') : t('apply.saveNeedsConsent')}
                </p>
                {saveLater.isError ? (
                  <p className="mt-1.5 text-[12.5px] text-[var(--ui-danger)]">
                    {errorText(saveLater.error, t('apply.errSave'))}
                  </p>
                ) : null}
              </>
            )}
          </div>

          {submit.isError ? (
            <div
              className="flex items-start gap-2 rounded-[16px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-4 text-[13.5px] leading-relaxed text-foreground"
              role="alert"
            >
              <AlertTriangle
                className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-danger)]"
                aria-hidden="true"
              />
              {errorText(submit.error, t('apply.errSubmit'))}
            </div>
          ) : null}

          <Button type="submit" disabled={!ready || submit.isPending} className="h-12 w-full text-[15px]">
            {submit.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            {t('apply.send')}
          </Button>
            </>
          ) : null}

          {/* Navigation. The submit button lives inside the last step with the
              consent box, so this row never carries one. */}
          {step < steps.length - 1 ? (
            <div className="flex items-center justify-between gap-3">
              {step > 0 ? (
                <Button type="button" variant="outline" className="h-11" onClick={() => setStep(step - 1)}>
                  {t('apply.back')}
                </Button>
              ) : (
                <span />
              )}
              <div className="flex items-center gap-2">
                {step === 1 ? (
                  <Button type="button" variant="ghost" className="h-11" onClick={() => setStep(step + 1)}>
                    {t('apply.skip')}
                  </Button>
                ) : null}
                <Button type="submit" disabled={!canContinue} className="h-11 px-6">
                  {t('apply.continue')}
                </Button>
              </div>
            </div>
          ) : (
            <Button type="button" variant="outline" className="h-11 self-start" onClick={() => setStep(step - 1)}>
              {t('apply.back')}
            </Button>
          )}
        </form>
      </Panel>

      {SEEDING_ENABLED ? (
        seedRows.length > 0 ? (
          <SeedPanel
            rows={seedRows}
            running={seeding}
            onRun={() => void runSeed()}
            onClear={() => setSeedRows([])}
          />
        ) : (
          <div className="mt-4 text-center">
            <button
              type="button"
              onClick={() => folderInput.current?.click()}
              className="text-[12px] text-[var(--ui-soft)] underline-offset-2 hover:text-muted-foreground hover:underline"
            >
              Dev: drop or choose a folder of CVs to seed test applications
            </button>
            <input
              ref={folderInput}
              type="file"
              multiple
              // Non-standard but universally implemented, and the reliable way
              // to pick a folder — dropping one only works in Chromium.
              {...{ webkitdirectory: '', directory: '' }}
              className="sr-only"
              onChange={(e) => {
                void acceptDropped(Array.from(e.target.files ?? []).filter(isPdf));
                e.target.value = '';
              }}
            />
          </div>
        )
      ) : null}

      <p className="mt-4 text-center text-[12.5px] text-[var(--ui-soft)]">
        {t('apply.humanReview', { company: job.company_name })}
      </p>
    </Shell>
  );
}
