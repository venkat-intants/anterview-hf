// JobCard — shadcn Card with level Badge, language tag, description snippet,
// and a "Start Interview" CTA. Used by JobsList.

import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { Job } from '@/types/interview';

// Level → translation key (label resolved via t() so it follows the UI language)
const LEVEL_KEY: Record<string, string> = {
  entry: 'jobs.levelEntry',
  mid: 'jobs.levelMid',
  senior: 'jobs.levelSenior',
};

// Level chip tints — light-system tinted pills
const LEVEL_CLASS: Record<string, string> = {
  entry: 'border-transparent bg-emerald-500/15 text-emerald-400',
  mid: 'border-transparent bg-blue-500/15 text-blue-400',
  senior: 'border-transparent bg-violet-500/15 text-violet-400',
};

// Language names stay in their own script across all locales (proper nouns).
const LANGUAGE_LABEL: Record<string, string> = {
  en: 'English',
  hi: 'हिंदी',
  te: 'తెలుగు',
};

interface JobCardProps {
  job: Job;
  onStartInterview: (jobId: string) => void;
  isStarting: boolean;
}

export default function JobCard({ job, onStartInterview, isStarting }: JobCardProps) {
  const { t } = useTranslation();
  const levelLabel = LEVEL_KEY[job.level] ? t(LEVEL_KEY[job.level]) : job.level;
  const levelClass =
    LEVEL_CLASS[job.level] ?? 'border-transparent bg-secondary text-muted-foreground';
  const langLabel = LANGUAGE_LABEL[job.language] ?? job.language.toUpperCase();

  return (
    <Card
      className={cn(
        'flex flex-col h-full transition-shadow duration-200',
        'hover:shadow-card-hover',
      )}
      aria-label={t('jobs.jobAria', { title: job.title })}
    >
      <CardHeader className="pb-3 gap-2">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-body-lg font-semibold text-foreground leading-snug flex-1">
            {job.title}
          </h2>
          <Badge
            variant="outline"
            className={cn('shrink-0 text-xs font-medium', levelClass)}
            aria-label={t('jobs.levelAria', { level: levelLabel })}
          >
            {levelLabel}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="flex-1 pt-0">
        <p className="text-body-sm text-muted-foreground leading-relaxed line-clamp-3">
          {job.description}
        </p>
      </CardContent>

      <CardFooter className="pt-4 border-t border-border flex items-center justify-between gap-3">
        {/* Language tag */}
        <Badge
          variant="secondary"
          className="text-xs font-medium"
          aria-label={t('jobs.languageAria', { language: langLabel })}
        >
          {langLabel}
        </Badge>

        <Button
          type="button"
          size="sm"
          onClick={() => onStartInterview(job.id)}
          disabled={isStarting}
          aria-label={t('jobs.startAria', { title: job.title })}
          aria-busy={isStarting}
        >
          {isStarting ? t('jobs.starting') : t('jobs.start')}
        </Button>
      </CardFooter>
    </Card>
  );
}
