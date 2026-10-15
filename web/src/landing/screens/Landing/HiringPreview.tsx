import { UserCheck } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Card, Chip } from '../../components/primitives'

/**
 * The hiring side, drawn in code. There is no staff screenshot to ship (mock
 * mode only signs in as a candidate), so this is an honest composition of the
 * same pieces the HR workspace uses — a list of applicants with their results,
 * and a scorecard to review. Every name and number is invented and the block
 * says so; there are no logos, no customers and no usage figures here.
 */

const APPLICANTS = [
  { id: 'Applicant 01', stage: 'Interviewed', score: 78, selected: true },
  { id: 'Applicant 02', stage: 'Exam done', score: 64, selected: false },
  { id: 'Applicant 03', stage: 'Interviewed', score: 71, selected: false },
  { id: 'Applicant 04', stage: 'Invited', score: null, selected: false },
] as const

const BARS = [
  { name: 'Communication', pct: 84 },
  { name: 'Problem solving', pct: 76 },
  { name: 'Role knowledge', pct: 72 },
]

export function HiringPreview({ className }: { className?: string }) {
  return (
    <figure className={cn('m-0', className)}>
      <Card className="overflow-hidden shadow-elevated">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--lp-line)] px-4 py-3 sm:px-5">
          <div>
            <p className="text-sm font-bold text-[var(--lp-text)]">Applicants</p>
            <p className="text-xs text-[var(--lp-text-muted)]">Junior data analyst</p>
          </div>
          <Chip>Illustration, invented data</Chip>
        </div>
        <div aria-hidden="true" className="grid grid-cols-1 lg:grid-cols-[1.25fr_0.75fr]">
          <ul className="divide-y divide-[var(--lp-line)]">
            {APPLICANTS.map((a) => (
              <li key={a.id} className={cn('flex items-center gap-3 px-4 py-3.5 sm:px-5', a.selected && 'bg-[var(--ui-info-wash)]')}>
                <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-[var(--lp-surface-alt)] text-xs font-bold text-[var(--lp-text-soft)]">
                  {a.id.slice(-2)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-[var(--lp-text)]">{a.id}</p>
                  <p className="text-xs text-[var(--lp-text-muted)]">{a.stage}</p>
                </div>
                {a.score === null ? (
                  <span className="text-xs text-[var(--lp-text-faint)]">Awaiting interview</span>
                ) : (
                  <div className="flex w-28 flex-none items-center gap-2.5 sm:w-36">
                    <span className="h-1.5 flex-1 overflow-hidden rounded-pill bg-[var(--lp-inset)]">
                      <span className="block h-full rounded-pill bg-[var(--lp-accent)]" style={{ width: `${a.score}%` }} />
                    </span>
                    <span className="w-6 text-right font-display text-sm font-bold text-[var(--lp-text)]">{a.score}</span>
                  </div>
                )}
              </li>
            ))}
          </ul>
          <div className="border-t border-[var(--lp-line)] bg-[var(--lp-canvas)] p-4 sm:p-5 lg:border-l lg:border-t-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-[var(--lp-text-faint)]">Scorecard to review</p>
            <ul className="mt-3 flex flex-col gap-2.5">
              {BARS.map((b) => (
                <li key={b.name}>
                  <div className="mb-1 flex justify-between text-xs text-[var(--lp-text-soft)]">
                    <span>{b.name}</span>
                    <span className="font-semibold text-[var(--lp-text)]">{b.pct}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-pill bg-[var(--lp-inset)]">
                    <div className="h-full rounded-pill bg-[var(--lp-accent)]" style={{ width: `${b.pct}%` }} />
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-4 flex items-start gap-2 rounded-[12px] border border-[var(--lp-line)] bg-[var(--lp-surface)] p-3 text-xs leading-snug text-[var(--lp-text-muted)]">
              <UserCheck size={15} className="mt-px flex-none text-[var(--ui-info)]" />
              The scorecard informs the decision. Your hiring team makes it.
            </p>
          </div>
        </div>
      </Card>
      <figcaption className="sr-only">
        Illustration with invented data: the HR workspace lists applicants with their interview and exam stage and score, and opens a scorecard to review. People make
        the hiring decision.
      </figcaption>
    </figure>
  )
}
