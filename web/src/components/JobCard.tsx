// JobCard — a practice role: title, level and language chips, a short
// description, and one "Start Interview" action. Used by JobsList.

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { Job } from '@/types/interview';

// Level → translation key (label resolved via t() so it follows the UI language)
const LEVEL_KEY: Record<string, string> = {
  entry: 'jobs.levelEntry',
  mid: 'jobs.levelMid',
  senior: 'jobs.levelSenior',
};

// Level chip tints — the per-mode status washes, so text holds contrast on
// white as well as on ink. The level is also spelled out, never colour alone.
const LEVEL_CLASS: Record<string, string> = {
  entry: 'bg-[var(--ui-ok-wash)] text-[var(--ui-ok)]',
  mid: 'bg-[var(--ui-info-wash)] text-[var(--ui-info)]',
  senior: 'bg-[var(--ui-lavender-wash)] text-[var(--ui-lavender)]',
};

// Language names stay in their own script across all locales (proper nouns).
const LANGUAGE_LABEL: Record<string, string> = {
  en: 'English',
  hi: 'हिंदी',
  te: 'తెలుగు',
};

const CHIP = 'inline-flex items-center rounded-full px-2.5 py-1 text-[12px] font-semibold';

interface JobCardProps {
  job: Job;
  onStartInterview: (jobId: string) => void;
  isStarting: boolean;
}

export default function JobCard({ job, onStartInterview, isStarting }: JobCardProps) {
  const { t } = useTranslation();
  const levelLabel = LEVEL_KEY[job.level] ? t(LEVEL_KEY[job.level]) : job.level;
  const levelClass = LEVEL_CLASS[job.level] ?? 'bg-[var(--ui-inset-strong)] text-[var(--ui-soft)]';
  const langLabel = LANGUAGE_LABEL[job.language] ?? job.language.toUpperCase();

  return (
    <article
      className="flex h-full flex-col rounded-card border border-border bg-card p-5 shadow-card transition-[box-shadow,border-color] hover:border-[var(--ui-line-strong)] hover:shadow-elevated"
      aria-label={t('jobs.jobAria', { title: job.title })}
    >
      <h2 className="text-[17px] font-bold leading-snug tracking-[-0.2px] text-foreground">
        {job.title}
      </h2>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <span
          className={cn(CHIP, levelClass)}
          aria-label={t('jobs.levelAria', { level: levelLabel })}
        >
          {levelLabel}
        </span>
        <span
          className={cn(CHIP, 'bg-[var(--ui-inset-strong)] text-[var(--ui-soft)]')}
          aria-label={t('jobs.languageAria', { language: langLabel })}
        >
          {langLabel}
        </span>
      </div>

      <p className="mt-3 flex-1 text-[14px] leading-relaxed text-muted-foreground line-clamp-3">
        {job.description}
      </p>

      <div className="mt-5">
        <Button
          type="button"
          onClick={() => onStartInterview(job.id)}
          disabled={isStarting}
          aria-label={t('jobs.startAria', { title: job.title })}
          aria-busy={isStarting}
        >
          {isStarting ? t('jobs.starting') : t('jobs.start')}
        </Button>
      </div>
    </article>
  );
}
