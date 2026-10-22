// Applications — where a candidate stands on every job they applied to.
//
// This page exists because the information already did. An applicant's stage,
// the round they are on and every transition were recorded from the day they
// applied, and the only people who could see any of it were the hiring team.
//
// ORDER IS THE POINT. The page answers "whose move is it?" before anything
// else: applications waiting on the candidate first ("Your move"), then those
// with the hiring team, then closed ones. Interviews and offers the server has
// not tied to an application get their own section after those, and the
// rediscovery consent — a setting, not a status — comes last.
//
// What it deliberately does NOT show, because the API does not send it: any
// score, any threshold, any note written about them. Progress and next steps
// only. The one place that shades toward tact is the internal `held` state,
// which reaches this page already worded as "Under review" — a candidate below
// a round threshold has not been rejected, a person decides that, and telling
// them they fell short of a bar that cannot reject them would be both unkind
// and untrue.
//
// The track under each title is `Applied → Round n of N → Decision` and nothing
// more: the server sends the CURRENT round only, so past and future rounds are
// not named. Drawing a five-stage tracker would invent four of the stages.
//
// The empty state carries real weight here. Two very different people see it:
// someone who only practises and has applied to nothing, and an applicant who
// signed in with an account that was never linked to their application. The
// second needs to know their application is safe and what to do about it.

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getMyApplication, type MyApplication } from '@/api/applications';
import type { CandidateLoop } from '@/api/scheduling';
import type { CandidateOffer } from '@/api/offers';
import { toast } from '@/lib/toast';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useCandidateFormat } from '@/lib/candidateCopy';
import type { TFunction } from 'i18next';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Page, PageHeader, EmptyState, Stepper, type Step } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { Section } from '@/design/components/rows';
import { TurnBadge } from '@/design/components/next-step';
import { StatusTag } from '@/design/components/primitives';
import { useCandidateActions } from '@/hooks/useCandidateActions';
import { actionsForApplication, turnOf, type CandidateAction } from '@/lib/candidateActions';
import { nextStepText, stageText, stageTone } from '@/lib/applicationStage';
import YourInterviews, { EmbeddedLoops } from '@/components/candidate/YourInterviews';
import YourOffers, { EmbeddedOffers } from '@/components/candidate/YourOffers';
import YourRediscovery from '@/components/candidate/YourRediscovery';
import ApplicationActionRow from '@/components/candidate/ApplicationActionRow';
import { actionTitle } from '@/components/candidate/actionText';
import {
  AlertCircle,
  Briefcase,
  Check,
  ChevronDown,
  ChevronRight,
  User,
} from '@/design/components/icons';

/** What the middle step of the track says: the round, or "In review" before one is assigned. */
function roundStep(app: MyApplication, t: TFunction): { label: string; hint?: string } {
  const title = app.current_round_title;
  if (app.round_number && app.total_rounds) {
    return {
      label: t('track.roundOf', { n: app.round_number, total: app.total_rounds }),
      hint: title ?? undefined,
    };
  }
  if (title) return { label: title };
  return { label: t('track.inReview') };
}

/**
 * `Applied → Round n of N (round title) → Decision`, via the shared Stepper. A
 * closed application sits on "Decision"; a live one on the round. Below `sm` the
 * Stepper collapses to one line that carries the round too.
 */
function ApplicationTrack({ app }: { app: MyApplication }) {
  const { t } = useTranslation();
  const round = roundStep(app, t);
  const steps: Step[] = [
    { key: 'applied', label: t('track.applied') },
    { key: 'progress', label: round.label, hint: round.hint },
    { key: 'decision', label: t('track.decision') },
  ];
  const compactLabel = app.closed
    ? `${t('pageBlocks.stepOf', { n: 3, total: 3 })} · ${t('track.decision')}`
    : `${t('pageBlocks.stepOf', { n: 2, total: 3 })} · ${round.label}${round.hint ? ` · ${round.hint}` : ''}`;
  return (
    <Stepper
      steps={steps}
      current={app.closed ? 'decision' : 'progress'}
      label={t('track.label')}
      compactLabel={compactLabel}
    />
  );
}

function ApplicationCard({
  app,
  mine,
  loops,
  offers,
}: {
  app: MyApplication;
  /** Everything waiting on the candidate for THIS application, in queue order. */
  mine: CandidateAction[];
  loops: CandidateLoop[];
  offers: CandidateOffer[];
}) {
  const { t } = useTranslation();
  const fmt = useCandidateFormat();
  const [open, setOpen] = useState(false);
  const [alsoOpen, setAlsoOpen] = useState(false);
  const turn = turnOf(app, mine);
  const [top, ...others] = mine;

  // The history is only fetched when someone asks for it. Most people want the
  // headline; loading a timeline per card would be work nobody requested.
  const {
    data: detail,
    isLoading,
    isError: detailError,
    refetch: refetchDetail,
    isFetching: detailFetching,
  } = useQuery({
    queryKey: ['my-application', app.id],
    queryFn: () => getMyApplication(app.id),
    enabled: open,
    staleTime: 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  return (
    <Card
      as="article"
      titleAs="h3"
      data-testid={`application-${app.id}`}
      title={app.job_title}
      description={`${app.company_name} · ${t('applications.applied', { date: fmt.date(app.applied_at) })}`}
      actions={
        <span className="flex flex-wrap items-center justify-end gap-2">
          <StatusTag tone={stageTone(app)} dot>
            {stageText(t, app)}
          </StatusTag>
          <TurnBadge turn={turn} label={t(`turn.${turn}`)} />
        </span>
      }
    >
      <div className="flex flex-col gap-4">
        <ApplicationTrack app={app} />

        {/* One action row for the application's most pressing item. The rest
            stay one press away under "Also waiting": none is removed. */}
        {top ? <ApplicationActionRow key={top.id} action={top} /> : null}

        {others.length > 0 ? (
          <div>
            <button
              type="button"
              onClick={() => setAlsoOpen((v) => !v)}
              aria-expanded={alsoOpen}
              className="inline-flex min-h-[40px] items-center gap-1 rounded text-[13px] font-semibold text-[var(--ui-info)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {alsoOpen ? (
                <ChevronDown size={14} aria-hidden="true" />
              ) : (
                <ChevronRight size={14} aria-hidden="true" />
              )}
              {t('nextStep.alsoWaiting')} · {others.length}
            </button>
            {alsoOpen ? (
              <div className="mt-2 flex flex-col gap-3">
                {others.map((a) => (
                  <ApplicationActionRow key={a.id} action={a} />
                ))}
              </div>
            ) : (
              // Collapsed, the titles are still readable by assistive tech and
              // by anyone scanning: a hidden item is not an unknown item.
              <ul className="sr-only">
                {others.map((a) => (
                  <li key={a.id}>{actionTitle(t, a)}</li>
                ))}
              </ul>
            )}
          </div>
        ) : null}

        {/* With nothing waiting on the candidate, say where things stand. With
            something waiting, the action row above already says what to do. */}
        {top ? null : (
          <p className="text-[14px] leading-relaxed text-muted-foreground">
            {nextStepText(t, app)}
          </p>
        )}

        {loops.length > 0 ? <EmbeddedLoops loops={loops} /> : null}
        {offers.length > 0 ? <EmbeddedOffers offers={offers} /> : null}

        <div>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="inline-flex min-h-[40px] items-center gap-1 rounded text-[13px] font-semibold text-[var(--ui-info)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {open ? (
              <ChevronDown size={14} aria-hidden="true" />
            ) : (
              <ChevronRight size={14} aria-hidden="true" />
            )}
            {open ? t('applications.hideHistory') : t('applications.showHistory')}
          </button>

          {open && (
            <div className="mt-2 border-t border-border pt-3">
              {isLoading && (
                <p className="text-[13.5px] text-muted-foreground">{t('applications.loading')}</p>
              )}
              {detailError && !detail && (
                // A failed load used to leave the panel blank (audit CA-L2).
                <p role="alert" className="flex flex-wrap items-center gap-2 text-[13.5px] text-muted-foreground">
                  {t('applications.historyLoadError')}
                  <button
                    type="button"
                    onClick={() => void refetchDetail()}
                    disabled={detailFetching}
                    className="inline-flex min-h-[40px] items-center rounded font-semibold text-[var(--ui-info)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50"
                  >
                    {detailFetching ? t('error.tryingAgain') : t('error.tryAgain')}
                  </button>
                </p>
              )}
              {detail && detail.history.length === 0 && (
                <p className="text-[13.5px] text-muted-foreground">
                  {t('applications.nothingChanged')}
                </p>
              )}
              {detail && detail.history.length > 0 && (
                <ol className="flex flex-col gap-2.5">
                  {detail.history.map((e, i) => (
                    <li key={`${e.occurred_at}-${i}`} className="flex items-start gap-2.5">
                      <span
                        className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--ui-highlight-wash)] text-foreground"
                        aria-hidden="true"
                      >
                        <Check size={11} />
                      </span>
                      <span className="text-[13.5px] text-foreground">
                        {stageText(t, e)}
                        <span className="text-muted-foreground">
                          {' '}
                          · {fmt.date(e.occurred_at)}
                          {/* Worth surfacing: it answers "did a person look at
                              this, or did the system move me?" — which is exactly
                              what someone waiting wants to know. */}
                          {e.by_a_person && (
                            <>
                              {' '}
                              <User
                                size={11}
                                className="inline align-baseline"
                                aria-hidden="true"
                              />{' '}
                              {t('applications.byTeam')}
                            </>
                          )}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function ErrorState() {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="rounded-card border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-8 text-center"
    >
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-card text-[var(--ui-danger)]">
        <AlertCircle className="h-6 w-6" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-[18px] font-bold text-foreground">{t('applications.errorTitle')}</h2>
      <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-muted-foreground">
        {t('applications.errorBody')}
      </p>
    </div>
  );
}

function NoApplications() {
  const { t } = useTranslation();
  return (
    <EmptyState
      size="page"
      icon={Briefcase}
      title={t('applications.emptyTitle')}
      description={
        <>
          {t('applications.emptyDesc')}
          <span className="mt-3 block text-[13px]">{t('applications.emptyNote')}</span>
        </>
      }
      action={
        <Link to="/jobs" className={buttonVariants()}>
          {t('applications.browse')}
        </Link>
      }
    />
  );
}

/** A section title with a plain count after it. The count is a number, not a plural. */
function SectionTitle({ children, count }: { children: string; count: number }) {
  return (
    <>
      {children} <span className="font-normal text-muted-foreground">{count}</span>
    </>
  );
}

export default function Applications() {
  const { t } = useTranslation();
  const {
    applications: apps,
    loops,
    offers,
    actions,
    isLoading,
    isError,
    error,
    loopsFailed,
    offersFailed,
  } = useCandidateActions({ alwaysFetchSecondary: true });

  // One toast per distinct error, never one per render.
  useEffect(() => {
    if (isError) {
      toast.error(error instanceof Error ? error.message : t('applications.loadFailedToast'));
    }
  }, [isError, error, t]);

  // Each application's own queue items, and from them whose move it is.
  const mineById = useMemo(
    () => new Map(apps.map((a) => [a.id, actionsForApplication(actions, a.id)] as const)),
    [apps, actions],
  );
  const mineOf = (a: MyApplication): CandidateAction[] => mineById.get(a.id) ?? [];

  const yourMove = apps.filter((a) => mineOf(a).length > 0);
  const withTeam = apps.filter((a) => mineOf(a).length === 0 && !a.closed);
  const closed = apps.filter((a) => mineOf(a).length === 0 && a.closed);

  // A loop or offer goes on its application's card only when the server said
  // which application it belongs to AND that application is in this list.
  // Otherwise it renders in its own section: never matched on job title.
  const appIds = new Set(apps.map((a) => a.id));
  const loopsOn = (a: MyApplication): CandidateLoop[] =>
    loops.filter((l) => l.application_id === a.id);
  const offersOn = (a: MyApplication): CandidateOffer[] =>
    offers.filter((o) => o.application_id === a.id);
  const placedLoops = new Set(
    loops.filter((l) => l.application_id && appIds.has(l.application_id)).map((l) => l.id),
  );
  const placedOffers = new Set(
    offers.filter((o) => o.application_id && appIds.has(o.application_id)).map((o) => o.id),
  );
  const loose =
    loops.some((l) => !placedLoops.has(l.id)) ||
    offers.some((o) => !placedOffers.has(o.id)) ||
    loopsFailed ||
    offersFailed;

  const renderCard = (a: MyApplication) => (
    <ApplicationCard key={a.id} app={a} mine={mineOf(a)} loops={loopsOn(a)} offers={offersOn(a)} />
  );

  return (
    <Page width="form">
      <PageHeader
        title={<span id="applications-heading">{t('applications.title')}</span>}
        description={t('applications.subtitle')}
      />

      {/* The page's body, named by its heading. `contents` leaves the Page's
          24px rhythm to the children while keeping the landmark. */}
      <section aria-labelledby="applications-heading" className="contents">
        {isLoading && (
          <div aria-busy="true" className="flex flex-col gap-4">
            <Skeleton className="h-[132px] w-full rounded-card" />
            <Skeleton className="h-[132px] w-full rounded-card" />
            <span className="sr-only">{t('applications.loadingSr')}</span>
          </div>
        )}

        {/* An error is NOT an empty list. Falling through to the empty state
          here told an applicant they had applied to nothing because a fetch
          failed — the one message this page must never send by accident. */}
        {!isLoading && isError && <ErrorState />}
        {!isLoading && !isError && apps.length === 0 && <NoApplications />}

        {/* The sections wait for the whole queue (applications, interviews and
          offers). Drawn as soon as the applications arrive, a card would sit
          under "With the hiring team" and jump to "Your move" when the offers
          answered a moment later. */}
        {!isLoading && yourMove.length > 0 && (
          <Section title={<SectionTitle count={yourMove.length}>{t('turn.you')}</SectionTitle>}>
            <div className="flex flex-col gap-4">{yourMove.map(renderCard)}</div>
          </Section>
        )}

        {!isLoading && withTeam.length > 0 && (
          <Section title={<SectionTitle count={withTeam.length}>{t('turn.them')}</SectionTitle>}>
            <div className="flex flex-col gap-4">{withTeam.map(renderCard)}</div>
          </Section>
        )}

        {/* Closed applications are kept, below the live ones rather than hidden: a
          candidate looking for "did I hear back from that one?" needs them,
          and they should not compete with the live ones for attention. */}
        {!isLoading && closed.length > 0 && (
          <Section title={<SectionTitle count={closed.length}>{t('turn.closed')}</SectionTitle>}>
            <div className="flex flex-col gap-4">{closed.map(renderCard)}</div>
          </Section>
        )}

        {/* PH4-A2 / PH4-A3 — interviews the hiring team has scheduled and offers,
          for any the server has not tied to an application. Hidden when empty. */}
        {!isLoading && loose && (
          <Section title={t('track.scheduledAndOffers')} data-testid="scheduled-and-offers">
            <div className="flex flex-col gap-4">
              <YourInterviews exclude={placedLoops} hideWhenEmpty />
              <YourOffers exclude={placedOffers} />
            </div>
          </Section>
        )}

        {/* PH5-E3 (D5-1) — whether this candidate has opted in to be found again
          for a future opening. Last: a setting, not a status. */}
        <YourRediscovery />
      </section>
    </Page>
  );
}
