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
import { useParams } from 'react-router-dom';
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
  submitApplication,
  type ApplicationResult,
  type AnswerValue,
  type Posting,
  type PostingQuestion,
} from '@/api/publicApply';
import {
  filesFromDrop,
  isPdf,
  nameFromFilename,
  seedEmailFor,
} from './publicApplyDrop';
import { cn } from '@/lib/utils';

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

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/* ── States ─────────────────────────────────────────────────────────────── */

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-card px-4 py-10">
      <div className="mx-auto w-full max-w-[680px]">{children}</div>
    </div>
  );
}

function Panel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-[20px] border border-border bg-card p-6', className)}>
      {children}
    </div>
  );
}

function Unavailable() {
  return (
    <Shell>
      <Panel className="text-center">
        <Briefcase className="mx-auto h-8 w-8 text-[var(--ui-faint)]" aria-hidden="true" />
        <h1 className="mt-4 text-[20px] font-semibold text-foreground">
          This opening is not accepting applications
        </h1>
        <p className="mx-auto mt-2 max-w-[48ch] text-[13.5px] leading-relaxed text-muted-foreground">
          The role may have been filled or closed. If you were sent this link recently,
          check with whoever shared it — they will have the current one.
        </p>
      </Panel>
    </Shell>
  );
}

function Submitted({ result, title }: { result: ApplicationResult; title: string }) {
  return (
    <Shell>
      <Panel className="text-center">
        <CheckCircle2 className="mx-auto h-9 w-9 text-[var(--ui-ok)]" aria-hidden="true" />
        <h1 className="mt-4 text-[20px] font-semibold text-foreground">
          {result.already_applied ? 'You have already applied' : 'Application received'}
        </h1>
        <p className="mx-auto mt-2 max-w-[50ch] text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
          {result.message}
        </p>
        <p className="mx-auto mt-4 max-w-[50ch] text-[12.5px] leading-relaxed text-muted-foreground">
          {/* Said plainly because the alternative — silence — is what makes
              candidates assume they were rejected. */}
          Your CV is with the hiring team for <span className="text-foreground">{title}</span>.
          If they move you forward you will get an email with the next step; nothing is
          decided automatically.
        </p>
      </Panel>
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
        <span className="ml-auto text-[11.5px] text-[var(--ui-faint)]">
          {done}/{rows.length}
        </span>
      </div>

      <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">
        Development build only — this panel is not in a production bundle. Each CV is sent
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
const EMPLOYMENT_LABELS: Record<string, string> = {
  full_time: 'Full-time',
  part_time: 'Part-time',
  contract: 'Contract',
  internship: 'Internship',
  temporary: 'Temporary',
};

/** "Bengaluru · Full-time · Engineering", skipping whatever is missing. */
function metaLine(job: Posting): string {
  const employment = job.employment_type
    ? (EMPLOYMENT_LABELS[job.employment_type] ?? job.employment_type)
    : null;
  return [job.location, employment, job.department].filter(Boolean).join(' · ');
}

/** "4–8 years", "4+ years", "Up to 8 years", or nothing. */
function experienceLine(job: Posting): string | null {
  const { experience_min_years: lo, experience_max_years: hi } = job;
  if (lo == null && hi == null) return null;
  if (lo != null && hi != null) return lo === hi ? `${lo} years` : `${lo}–${hi} years`;
  return lo != null ? `${lo}+ years` : `Up to ${hi} years`;
}

/**
 * The band, or nothing. Null means "not disclosed" and the server sends null
 * for both a withheld salary and an unrecorded one — so there is deliberately
 * no "salary not specified" copy here to distinguish them.
 */
function salaryLine(job: Posting): string | null {
  const { salary_min: lo, salary_max: hi } = job;
  if (lo == null && hi == null) return null;
  const currency = job.salary_currency ?? '';
  const money = (n: number) => `${currency} ${n.toLocaleString()}`.trim();
  if (lo != null && hi != null) return `${money(lo)} – ${money(hi)}`;
  return lo != null ? `From ${money(lo)}` : `Up to ${money(hi as number)}`;
}

function BulletPanel({ heading, items }: { heading: string; items?: string[] }) {
  // Defensive despite the type: this is the one page reached without an
  // account, and a thrown render loses an applicant rather than hiding a
  // section. An older cached response is enough to do it.
  if (!items || items.length === 0) return null;
  return (
    <Panel className="mb-5">
      <h2 className="mb-2 text-[14px] font-medium text-foreground">{heading}</h2>
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
 * One recruiter-defined question.
 *
 * Every kind is a plain form control rather than anything clever: this is the
 * one page a stranger fills in on a phone, and a custom widget is a thing that
 * can fail to work there. The server validates all of it again regardless.
 */
function QuestionField({
  question,
  value,
  onChange,
}: {
  question: PostingQuestion;
  value: AnswerValue | undefined;
  onChange: (next: AnswerValue) => void;
}) {
  const id = `q-${question.id}`;
  const inputClass =
    'w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';

  const label = (
    <span className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]">
      {question.prompt}
      {question.required ? (
        <span className="ml-1 text-[var(--ui-danger)]" aria-hidden="true">
          *
        </span>
      ) : (
        <span className="ml-1 font-normal text-[var(--ui-faint)]">(optional)</span>
      )}
    </span>
  );

  const help = question.help_text ? (
    <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">{question.help_text}</p>
  ) : null;

  if (question.kind === 'yes_no') {
    return (
      <fieldset>
        <legend className="mb-1.5 text-[12.5px] font-medium text-[var(--ui-soft)]">
          {question.prompt}
          {question.required ? null : (
            <span className="ml-1 font-normal text-[var(--ui-faint)]">(optional)</span>
          )}
        </legend>
        <div className="flex gap-2">
          {[
            { label: 'Yes', v: true },
            { label: 'No', v: false },
          ].map((opt) => (
            <label
              key={opt.label}
              className={cn(
                'cursor-pointer rounded-[10px] border px-4 py-2 text-[13.5px]',
                value === opt.v
                  ? 'border-[var(--accent)] text-foreground'
                  : 'border-border text-[var(--ui-soft)]',
              )}
            >
              <input
                type="radio"
                name={id}
                className="sr-only"
                checked={value === opt.v}
                onChange={() => onChange(opt.v)}
              />
              {opt.label}
            </label>
          ))}
        </div>
        {help}
      </fieldset>
    );
  }

  if (question.kind === 'single_choice') {
    return (
      <div>
        <label htmlFor={id}>{label}</label>
        <select
          id={id}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          className={inputClass}
        >
          <option value="">Choose one…</option>
          {question.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        {help}
      </div>
    );
  }

  if (question.kind === 'multi_choice') {
    const chosen = Array.isArray(value) ? value : [];
    return (
      <fieldset>
        <legend className="mb-1.5 text-[12.5px] font-medium text-[var(--ui-soft)]">
          {question.prompt}
          {question.required ? null : (
            <span className="ml-1 font-normal text-[var(--ui-faint)]">(optional)</span>
          )}
        </legend>
        <div className="flex flex-wrap gap-2">
          {question.options.map((o) => (
            <label
              key={o}
              className={cn(
                'cursor-pointer rounded-[10px] border px-3 py-1.5 text-[13px]',
                chosen.includes(o)
                  ? 'border-[var(--accent)] text-foreground'
                  : 'border-border text-[var(--ui-soft)]',
              )}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={chosen.includes(o)}
                onChange={() =>
                  onChange(
                    chosen.includes(o) ? chosen.filter((c) => c !== o) : [...chosen, o],
                  )
                }
              />
              {o}
            </label>
          ))}
        </div>
        {help}
      </fieldset>
    );
  }

  if (question.kind === 'long_text') {
    return (
      <div>
        <label htmlFor={id}>{label}</label>
        <textarea
          id={id}
          rows={4}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          className={inputClass}
        />
        {help}
      </div>
    );
  }

  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type={question.kind === 'number' ? 'number' : 'text'}
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      />
      {help}
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
function stepsFor(questionCount: number): string[] {
  // The questions step exists only when there is something on it. Most
  // openings ask nothing, and an empty "Questions" step is a click that makes
  // the form look longer than it is.
  return questionCount > 0
    ? ['You', 'Experience', 'Your CV', 'Questions', 'Review']
    : ['You', 'Experience', 'Your CV', 'Review'];
}

function Stepper({ current, steps }: { current: number; steps: string[] }) {
  return (
    <ol className="mb-5 flex items-center gap-2" aria-label="Application progress">
      {steps.map((label, i) => (
        <li key={label} className="flex flex-1 items-center gap-2">
          <span
            className={cn(
              'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium',
              i < current && 'border-[var(--ui-line-strong)] bg-[var(--ui-inset-strong)] text-foreground',
              i === current && 'border-[var(--accent)] text-foreground',
              i > current && 'border-border text-[var(--ui-faint)]',
            )}
            aria-current={i === current ? 'step' : undefined}
          >
            {i < current ? <Check className="h-3 w-3" aria-hidden="true" /> : i + 1}
          </span>
          <span
            className={cn(
              'hidden text-[12px] sm:inline',
              i === current ? 'text-foreground' : 'text-[var(--ui-faint)]',
            )}
          >
            {label}
          </span>
          {i < steps.length - 1 ? (
            <span className="h-px flex-1 bg-[var(--ui-inset-strong)]" aria-hidden="true" />
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/** One line of the review step. Missing answers say so rather than sitting blank. */
function ReviewRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border py-2 last:border-b-0">
      <span className="text-[12.5px] text-muted-foreground">{label}</span>
      <span
        className={cn(
          'text-right text-[13px]',
          value ? 'text-foreground' : 'italic text-[var(--ui-faint)]',
        )}
      >
        {value || 'Not provided'}
      </span>
    </div>
  );
}

export default function PublicApply(): JSX.Element {
  const { requisitionId = '' } = useParams();
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [resume, setResume] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
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
    queryKey: ['public', 'posting', requisitionId],
    queryFn: () => getPosting(requisitionId),
    enabled: Boolean(requisitionId),
    retry: false,
  });

  const submit = useMutation({
    mutationFn: () =>
      submitApplication(requisitionId, {
        fullName: fullName.trim(),
        email: email.trim(),
        resume: resume as File,
        consentGranted: consent,
        phone,
        yearsExperience: yearsExperience === '' ? null : Number(yearsExperience),
        currentCompany,
        currentTitle,
        linkedinUrl,
        githubUrl,
        answers,
      }),
  });

  function pickFile(file: File | null): void {
    setFileError('');
    if (!file) {
      setResume(null);
      return;
    }
    // Checked here as well as server-side so someone does not upload 30 MB over
    // a phone connection to be told no at the end of it.
    if (file.size > MAX_BYTES) {
      setFileError('That file is over 5 MB. Please upload a smaller PDF.');
      setResume(null);
      return;
    }
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setFileError('Please upload your CV as a PDF.');
      setResume(null);
      return;
    }
    setResume(file);
  }

  /** One drop, however it arrived: one CV, or a folder of them. */
  function acceptDropped(files: File[]): void {
    setFileError('');
    if (files.length === 0) {
      setFileError('No PDFs in there. A CV needs to be a PDF.');
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
        });
        setSeedRows((prev) =>
          prev.map((r, j) =>
            j === i
              ? { ...r, status: result.already_applied ? 'duplicate' : 'done' }
              : r,
          ),
        );
      } catch (err) {
        const message = errText(err, 'failed');
        // A 429 is not a failure, it is the server pacing us. Wait out the
        // fixed one-minute window and retry the same file rather than
        // recording a loss the operator would have to chase.
        if (/too many requests/i.test(message)) {
          setSeedRows((prev) =>
            prev.map((r, j) =>
              j === i ? { ...r, status: 'waiting', detail: 'rate limited — waiting' } : r,
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
        <div className="flex items-center justify-center gap-2 py-20 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </div>
      </Shell>
    );
  }
  if (posting.isError || !posting.data) return <Unavailable />;
  if (submit.isSuccess) {
    return <Submitted result={submit.data} title={posting.data.title} />;
  }

  const job = posting.data;
  // `requiredAnswered` is folded in below rather than here, because it is
  // derived from the posting and this line runs before the posting is read.
  const readyBase =
    fullName.trim().length >= 2 && email.trim().length > 3 && resume && consent;
  // What each step needs before it will let you move on. Step 2 asks for
  // nothing, which is the whole point of it being skippable; the last step is
  // gated by `ready` on its own submit button, so consent is never a
  // "Continue" — it stays the last thing that happens before an upload.
  const questions = job.questions ?? [];
  const steps = stepsFor(questions.length);
  const questionsStep = questions.length > 0 ? 3 : -1;

  /** Whether a required question has an answer that is not blank. */
  const answered = (q: PostingQuestion): boolean => {
    const v = answers[q.id];
    if (v === undefined || v === null) return false;
    if (typeof v === 'string') return v.trim() !== '';
    if (Array.isArray(v)) return v.length > 0;
    return true;
  };
  const requiredAnswered = questions.filter((q) => q.required).every(answered);
  const ready = readyBase && requiredAnswered;

  const canContinue =
    step === 0
      ? fullName.trim().length >= 2 && email.trim().length > 3
      : step === 2
        ? Boolean(resume)
        : step === questionsStep
          ? requiredAnswered
          : true;
  const field =
    'w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none';

  return (
    <Shell>
      <header className="mb-5">
        <div className="text-[12.5px] uppercase tracking-[1.2px] text-[var(--ui-faint)]">
          {job.company_name}
        </div>
        <h1 className="mt-1 text-[28px] font-semibold tracking-[-0.8px] text-foreground">
          {job.title}
        </h1>
        {metaLine(job) ? (
          <div className="mt-1.5 text-[13.5px] text-[var(--ui-soft)]">{metaLine(job)}</div>
        ) : null}
        <div className="mt-1 text-[13px] text-muted-foreground">
          {job.level} level
          {experienceLine(job) ? ` · ${experienceLine(job)}` : ''}
          {job.closes_at
            ? ` · closes ${new Date(job.closes_at).toLocaleDateString()}`
            : ''}
        </div>
        {salaryLine(job) ? (
          <div className="mt-2 inline-block rounded-[10px] border border-border bg-[var(--ui-inset)] px-3 py-1.5 text-[13.5px] font-medium text-foreground">
            {salaryLine(job)}
          </div>
        ) : null}
      </header>

      {job.jd_text ? (
        <Panel className="mb-5">
          <h2 className="mb-2 text-[14px] font-medium text-foreground">About the role</h2>
          <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
            {job.jd_text}
          </p>
        </Panel>
      ) : null}

      <BulletPanel heading="What you will do" items={job.responsibilities} />

      {(job.required_skills?.length ?? 0) > 0 || (job.nice_to_have_skills?.length ?? 0) > 0 ? (
        <Panel className="mb-5">
          <div className="flex flex-col gap-4">
            <SkillTags heading="Required skills" skills={job.required_skills} />
            <SkillTags heading="Nice to have" skills={job.nice_to_have_skills} />
          </div>
        </Panel>
      ) : null}

      <Panel>
        <h2 className="text-[16px] font-semibold text-foreground">Apply</h2>

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
              if (canContinue) setStep(step + 1);
              return;
            }
            if (ready) submit.mutate();
          }}
        >
          {step === 0 ? (
            <>
          <div>
            <label htmlFor="name" className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]">
              Your name
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
            <label htmlFor="email" className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              className={field}
            />
            <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
              This is how the hiring team will reach you.
            </p>
          </div>

              <div>
                <label
                  htmlFor="phone"
                  className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]"
                >
                  Phone <span className="font-normal text-[var(--ui-faint)]">(optional)</span>
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
              <p className="text-[12.5px] leading-relaxed text-muted-foreground">
                All optional. It helps the hiring team place you quickly, but skip
                anything you would rather not answer.
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="years"
                    className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]"
                  >
                    Years of experience
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
                    className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]"
                  >
                    Current company
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
                  className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]"
                >
                  Current role
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
                    className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]"
                  >
                    LinkedIn
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
                    className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]"
                  >
                    GitHub
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
            <label htmlFor="cv" className="mb-1.5 block text-[12.5px] font-medium text-[var(--ui-soft)]">
              Your CV (PDF)
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
                'flex cursor-pointer items-center gap-3 rounded-[12px] border border-dashed px-3.5 py-4 transition-colors',
                dragging
                  ? 'border-[var(--accent)] bg-[var(--accent)]/[0.06]'
                  : 'border-[var(--ui-line-strong)] hover:border-[var(--accent)]/50',
              )}
            >
              {resume ? (
                <FileText className="h-5 w-5 shrink-0 text-[var(--ui-ok)]" aria-hidden="true" />
              ) : (
                <Upload className="h-5 w-5 shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
              )}
              <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--ui-soft)]">
                {resume
                  ? resume.name
                  : dragging
                    ? 'Drop it here'
                    : 'Drop a PDF here, or click to choose — up to 5 MB'}
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
              <p className="mt-1.5 text-[12px] text-[var(--ui-danger)]" role="alert">
                {fileError}
              </p>
            ) : (
              <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">
                A text-based PDF, not a scan — a scanned image cannot be read.
              </p>
            )}
          </div>

</> : null}

          {questionsStep >= 0 && step === questionsStep ? (
            <div className="flex flex-col gap-4">
              {questions.map((q) => (
                <QuestionField
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
              <div className="rounded-[12px] border border-border p-3">
                <ReviewRow label="Name" value={fullName.trim() || null} />
                <ReviewRow label="Email" value={email.trim() || null} />
                <ReviewRow label="Phone" value={phone.trim() || null} />
                <ReviewRow
                  label="Experience"
                  value={yearsExperience === '' ? null : yearsExperience + ' years'}
                />
                <ReviewRow label="Current company" value={currentCompany.trim() || null} />
                <ReviewRow label="Current role" value={currentTitle.trim() || null} />
                <ReviewRow label="LinkedIn" value={linkedinUrl.trim() || null} />
                <ReviewRow label="GitHub" value={githubUrl.trim() || null} />
                <ReviewRow label="CV" value={resume ? resume.name : null} />
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
                            ? ((answers[q.id] as boolean) ? 'Yes' : 'No')
                            : (answers[q.id] as string).trim() || null
                    }
                  />
                ))}
              </div>

          {/* The lawful basis. Unticked by default, and the button below cannot
              be pressed until it is ticked. */}
          <label className="flex cursor-pointer items-start gap-2.5 rounded-[12px] border border-border p-3">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
            />
            <span className="text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
              I agree that {job.company_name} may store my name, email and CV to consider
              me for this role, and may contact me about it. I can ask them to delete my
              data at any time.
            </span>
          </label>

          {submit.isError ? (
            <div
              className="flex items-start gap-2 rounded-[12px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger)]/[0.07] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
              role="alert"
            >
              <AlertTriangle
                className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-danger)]"
                aria-hidden="true"
              />
              {errText(submit.error, 'Something went wrong. Please try again.')}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={!ready || submit.isPending}
            className="inline-flex items-center justify-center gap-1.5 rounded-[12px] bg-primary px-5 py-3 text-[14px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {submit.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            Send application
          </button>
            </>
          ) : null}

          {/* Navigation. The submit button lives inside the last step with the
              consent box, so this row never carries one. */}
          {step < steps.length - 1 ? (
            <div className="flex items-center justify-between gap-3">
              {step > 0 ? (
                <button
                  type="button"
                  onClick={() => setStep(step - 1)}
                  className="rounded-[12px] border border-border px-4 py-2.5 text-[13.5px] text-[var(--ui-soft)] hover:text-foreground"
                >
                  Back
                </button>
              ) : (
                <span />
              )}
              <div className="flex items-center gap-2">
                {step === 1 ? (
                  <button
                    type="button"
                    onClick={() => setStep(step + 1)}
                    className="rounded-[12px] px-3 py-2.5 text-[13.5px] text-muted-foreground hover:text-foreground"
                  >
                    Skip
                  </button>
                ) : null}
                <button
                  type="submit"
                  disabled={!canContinue}
                  className="rounded-[12px] bg-primary px-5 py-2.5 text-[14px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  Continue
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setStep(step - 1)}
              className="self-start rounded-[12px] border border-border px-4 py-2.5 text-[13.5px] text-[var(--ui-soft)] hover:text-foreground"
            >
              Back
            </button>
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
              className="text-[12px] text-[var(--ui-faint)] underline-offset-2 hover:text-muted-foreground hover:underline"
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

      <p className="mt-4 text-center text-[11.5px] text-[var(--ui-faint)]">
        Your application is reviewed by people at {job.company_name}. Assessments may be
        scored automatically, but no hiring decision is made without a person.
      </p>
    </Shell>
  );
}
