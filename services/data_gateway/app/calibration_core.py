"""Calibration arithmetic — PH4-O5, extended by PH5-E4. Pure: rows in, numbers out.

The question calibration answers is "does this interviewer score the SAME
candidates differently from the rest of the panel?". A raw average cannot say
that — an interviewer who happened to see the strongest candidates would look
generous. So the headline figure is PAIRED: for every (application, round,
competency) at least two interviewers scored, each interviewer's score minus
that judgement's panel mean. Averaged, that is how far above or below the panel
someone lands on identical evidence.

The panel mean INCLUDES the interviewer. Leaving them out sounds purer but
indicts the innocent: on a panel of three with one generous scorer, the other
two would each sit a full point "below" a mean the generous one inflated. With
the interviewer included, the generous scorer's own gap stands out and the
others' do not. On a panel of two nobody can tell who is off — both show the
same gap, as they should.

SMALL CELLS ARE WITHHELD. An average over one candidate IS that candidate's
score: a narrow filter (one round, a one-second window) would otherwise read
back an individual's evaluation — and let a panellist who has not submitted
yet see what their peers gave (security review M1; the independence rule of
PH4-A1). So every figure is published only when it rests on at least
``min_candidates`` distinct applications, per interviewer and per criterion
cell alike; below that the numbers are withheld and the report says so.

PH5-E4 adds three things, all gated by the SAME thresholds — now read from a
governed :class:`~app.metrics.definitions.CalibrationSpec` rather than bare
module constants (kept below as aliases so existing imports still work):

* ``same_direction_share`` and a SIGN-CONSISTENCY gate on the interviewer
  flag: a single wild score among otherwise-ordinary judgements no longer
  trips "higher"/"lower" on its own — the gap must point the same way in most
  of the shared judgements it is built from.
* ``by_criterion``: the SAME paired-gap method, restricted to one frozen
  criterion ``(round_id, competency_id)`` at a time, so a mixed pattern across
  different rounds is not averaged into invisibility. Cells below
  ``min_candidates`` are simply omitted — the same convention ``by_competency``
  already used, not a separate suppressed placeholder.
* :func:`criterion_baselines`: the PANEL's own baseline per frozen criterion —
  never an interviewer's figure — including ``disagreement``, the mean (over
  judgements at least two interviewers shared) of (max score - min score). A
  wide disagreement points at the rubric or the anchors, not at a person.

Nothing here can change a scorecard, a status or a decision: it returns a
report, and the report names interviewers, never candidates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import fmean
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.metrics.definitions import CalibrationSpec

# A difference is only called out when it is both large and repeated: three
# quarters of a point on the 1-5 scale, over at least five shared judgements.
MIN_PAIRS = 5
MEANINGFUL_DELTA = 0.75
# No figure is published from fewer distinct applications than this.
MIN_CANDIDATES = 5
# PH5-E4: in at least this share of shared judgements the gap must point the
# same way before it is called "higher"/"lower" — one wild score among
# otherwise-unremarkable ones must not trip the flag alone.
MIN_SAME_DIRECTION_SHARE = 0.7
# PH5-E4: a panel baseline needs more than one person's scores, or it is not
# a "panel" baseline at all.
MIN_INTERVIEWERS_FOR_BASELINE = 2
# PH5-E4: a criterion's own disagreement signal — interviewers scoring the
# same candidate this far apart, on average, points at the rubric.
WIDE_DISAGREEMENT_RANGE = 1.5

#: The full default threshold set, mirroring
#: ``app.metrics.definitions.CALIBRATION_SPECS["interviewer_calibration@1"].thresholds``
#: byte-for-byte (pinned by a cross-module test) — used only when a caller
#: does not pass a ``spec`` (pure-arithmetic unit tests; existing callers of
#: ``calibrate(rows)``), so this module has no import-time dependency on the
#: registry actually agreeing with it.
_DEFAULT_THRESHOLDS: dict[str, Any] = {
    "min_candidates": MIN_CANDIDATES,
    "min_pairs": MIN_PAIRS,
    "meaningful_delta": MEANINGFUL_DELTA,
    "min_same_direction_share": MIN_SAME_DIRECTION_SHARE,
    "min_interviewers_for_baseline": MIN_INTERVIEWERS_FOR_BASELINE,
    "wide_disagreement_range": WIDE_DISAGREEMENT_RANGE,
    "min_span_days": 7,
    "max_span_days": 366,
    "scale": "1-5",
}


def _thresholds(spec: CalibrationSpec | None) -> dict[str, Any]:
    return dict(spec.thresholds) if spec is not None else dict(_DEFAULT_THRESHOLDS)


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


@dataclass(frozen=True)
class ScoreRow:
    scorecard_id: str
    interviewer_id: str
    enrolment_id: str
    round_id: str
    competency_id: str
    score: int | None  # None = not assessed


@dataclass(frozen=True)
class CriterionGap:
    """One interviewer's paired gap on ONE frozen criterion (``round_id``,
    ``competency_id``) — the same paired-gap method as the interviewer's
    overall figure, restricted to judgements sharing this exact criterion.

    Omitted from an interviewer's ``by_criterion`` list below
    ``min_candidates`` — the same small-cell convention ``by_competency``
    already uses, not a separate suppressed placeholder.
    """

    criterion_key: str
    round_id: str
    competency_id: str
    candidates: int
    shared_judgements: int
    gap: float
    signal: str | None  # "higher" | "lower" | None


@dataclass
class CriterionBaseline:
    """The PANEL's own baseline on one frozen criterion — never an
    interviewer's figure. ``disagreement`` is the mean, over judgements at
    least two interviewers shared, of (max score - min score); a WIDE
    disagreement points at the rubric or the anchors, not at a person.

    Suppressed (``suppressed=True``) when fewer than ``min_candidates``
    distinct applications, or fewer than ``min_interviewers_for_baseline``
    distinct interviewers, contributed — otherwise a "panel" baseline could
    be one person's scores. When suppressed, ``distribution``, ``mean`` and
    ``disagreement`` (the figures that would describe too few people's
    scores) are already ``None`` here, not left for a caller to hide later —
    the safer default, so a caller cannot forget to. ``candidates``,
    ``interviewers`` and ``shared_judgements`` (population SIZES, not
    scores) stay populated either way. ``scores`` and ``not_assessed`` are
    the one exception: this dataclass always populates them with the real
    computed value, and it is ``panel_workload.py``'s serialised response —
    the same split :class:`InterviewerCalibration` uses for its own
    ``scores`` — that nulls them alongside the rest for a suppressed row
    before it ever reaches an HTTP response.
    """

    criterion_key: str
    round_id: str
    competency_id: str
    candidates: int = 0
    interviewers: int = 0
    scores: int = 0
    not_assessed: int = 0
    suppressed: bool = False
    distribution: dict[int, int] | None = None
    mean: float | None = None
    shared_judgements: int = 0
    disagreement: float | None = None
    signal: str | None = None  # "wide_disagreement" | None


@dataclass
class InterviewerCalibration:
    interviewer_id: str
    scorecards: int = 0
    candidates: int = 0
    scores: int = 0
    not_assessed: int = 0
    suppressed: bool = False
    mean: float | None = None
    distribution: dict[int, int] | None = field(
        default_factory=lambda: {k: 0 for k in range(1, 6)}
    )
    by_competency: dict[str, float] = field(default_factory=dict)
    pairs: int = 0
    mean_delta: float | None = None
    same_direction_share: float | None = None
    #: The RAW count behind ``same_direction_share`` — e.g. 6 of ``pairs``.
    #: Returned so a caller (the web sentence "in 6 of 7 the gap was the same
    #: way") reads the server's own number rather than reconstructing it as
    #: ``round(same_direction_share * pairs)``, which can disagree with the
    #: real count at the rounding boundary (code review, PH5-E4 sign-off:
    #: the Wave 1 anti-pattern).
    same_direction_count: int | None = None
    flag: str | None = None  # "higher" | "lower" | None
    by_criterion: list[CriterionGap] = field(default_factory=list)

    @property
    def not_assessed_rate(self) -> float | None:
        if self.suppressed:
            return None
        total = self.scores + self.not_assessed
        return round(self.not_assessed / total, 3) if total else None


def calibrate(
    rows: Iterable[ScoreRow], spec: CalibrationSpec | None = None,
) -> list[InterviewerCalibration]:
    """One entry per interviewer, those with the largest paired gap first.

    ``spec`` supplies the thresholds (see :data:`_DEFAULT_THRESHOLDS` for
    what is used when it is omitted, which existing pure-arithmetic callers
    still do). Passing the real
    ``app.metrics.definitions.calibration_spec()`` is what
    ``app.panel_workload.calibration`` does.
    """
    th = _thresholds(spec)
    min_candidates = int(th["min_candidates"])
    min_pairs = int(th["min_pairs"])
    meaningful_delta = float(th["meaningful_delta"])
    min_same_direction_share = float(th["min_same_direction_share"])

    rows = list(rows)
    per: dict[str, InterviewerCalibration] = {}
    cards: dict[str, set[str]] = defaultdict(set)
    people: dict[str, set[str]] = defaultdict(set)
    comp_scores: dict[tuple[str, str], list[int]] = defaultdict(list)
    comp_people: dict[tuple[str, str], set[str]] = defaultdict(set)
    shared: dict[tuple[str, str, str], dict[str, int]] = defaultdict(dict)

    for r in rows:
        c = per.setdefault(r.interviewer_id, InterviewerCalibration(r.interviewer_id))
        cards[r.interviewer_id].add(r.scorecard_id)
        people[r.interviewer_id].add(r.enrolment_id)
        if r.score is None:
            c.not_assessed += 1
            continue
        c.scores += 1
        if c.distribution is not None:
            c.distribution[r.score] = c.distribution.get(r.score, 0) + 1
        comp_scores[(r.interviewer_id, r.competency_id)].append(r.score)
        comp_people[(r.interviewer_id, r.competency_id)].add(r.enrolment_id)
        # One score per interviewer per judgement: a later row (a correction
        # already resolved upstream) would replace, never double-count.
        shared[(r.enrolment_id, r.round_id, r.competency_id)][r.interviewer_id] = r.score

    deltas: dict[str, list[float]] = defaultdict(list)
    delta_people: dict[str, set[str]] = defaultdict(set)
    # PH5-E4: the SAME paired gaps, additionally keyed by frozen criterion
    # (interviewer, round_id, competency_id) so a per-criterion breakdown
    # never merges two rounds that happen to share a competency id.
    criterion_deltas: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    criterion_people: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (enrolment, rnd, comp), scored in shared.items():
        if len(scored) < 2:
            continue
        panel = fmean(scored.values())
        for who, s in scored.items():
            gap = s - panel
            deltas[who].append(gap)
            delta_people[who].add(enrolment)
            criterion_deltas[(who, rnd, comp)].append(gap)
            criterion_people[(who, rnd, comp)].add(enrolment)

    # PH5-E4 code review: grouped by interviewer ONCE, rather than each
    # interviewer below re-scanning every (interviewer, round, competency)
    # key in criterion_deltas — O(keys) instead of O(interviewers x keys).
    criterion_keys_by_interviewer: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for w, rnd, comp in sorted(criterion_deltas):
        criterion_keys_by_interviewer[w].append((rnd, comp))

    for who, c in per.items():
        c.scorecards = len(cards[who])
        c.candidates = len(people[who])
        if c.candidates < min_candidates:
            # Too few applications to publish anything without describing one.
            c.suppressed = True
            c.distribution = None
            c.by_competency = {}
            c.pairs = len(deltas.get(who, []))
            continue
        own = [s for (w, _), ss in comp_scores.items() if w == who for s in ss]
        c.mean = round(fmean(own), 3) if own else None
        c.by_competency = {
            comp: round(fmean(ss), 3)
            for (w, comp), ss in sorted(comp_scores.items())
            if w == who and len(comp_people[(w, comp)]) >= min_candidates
        }
        d = deltas.get(who, [])
        c.pairs = len(d)
        if d and len(delta_people[who]) >= min_candidates:
            c.mean_delta = round(fmean(d), 3)
            same = sum(1 for x in d if _sign(x) == _sign(c.mean_delta))
            c.same_direction_count = same
            c.same_direction_share = round(same / len(d), 3)
            if (
                c.pairs >= min_pairs
                and abs(c.mean_delta) >= meaningful_delta
                and c.same_direction_share >= min_same_direction_share
            ):
                c.flag = "higher" if c.mean_delta > 0 else "lower"

        # PH5-E4: the per-criterion breakdown, same gate, one frozen
        # criterion at a time. Cells below min_candidates are omitted, like
        # by_competency above.
        by_criterion: list[CriterionGap] = []
        for rnd, comp in criterion_keys_by_interviewer.get(who, []):
            vals = criterion_deltas[(who, rnd, comp)]
            n_candidates = len(criterion_people[(who, rnd, comp)])
            if n_candidates < min_candidates:
                continue
            gap = round(fmean(vals), 3)
            shared_n = len(vals)
            same_c = sum(1 for x in vals if _sign(x) == _sign(gap)) / shared_n if shared_n else 0.0
            signal = None
            if (
                shared_n >= min_pairs
                and abs(gap) >= meaningful_delta
                and same_c >= min_same_direction_share
            ):
                signal = "higher" if gap > 0 else "lower"
            by_criterion.append(
                CriterionGap(
                    criterion_key=f"{rnd}:{comp}", round_id=rnd, competency_id=comp,
                    candidates=n_candidates, shared_judgements=shared_n, gap=gap, signal=signal,
                )
            )
        c.by_criterion = by_criterion

    return sorted(per.values(), key=lambda c: (-(abs(c.mean_delta or 0.0)), c.interviewer_id))


def criterion_baselines(
    rows: Iterable[ScoreRow], spec: CalibrationSpec | None = None,
) -> list[CriterionBaseline]:
    """The panel's OWN baseline per frozen criterion ``(round_id,
    competency_id)`` — independent of :func:`calibrate`: this reports the
    CRITERION, never an interviewer. Sorted by disagreement, widest first.
    """
    th = _thresholds(spec)
    min_candidates = int(th["min_candidates"])
    min_pairs = int(th["min_pairs"])
    min_interviewers = int(th["min_interviewers_for_baseline"])
    wide_range = float(th["wide_disagreement_range"])

    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    interviewers: dict[tuple[str, str], set[str]] = defaultdict(set)
    scores_by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    not_assessed: dict[tuple[str, str], int] = defaultdict(int)
    shared: dict[tuple[str, str, str], dict[str, int]] = defaultdict(dict)

    for r in rows:
        key = (r.round_id, r.competency_id)
        candidates[key].add(r.enrolment_id)
        interviewers[key].add(r.interviewer_id)
        if r.score is None:
            not_assessed[key] += 1
            continue
        scores_by_key[key].append(r.score)
        shared[(r.enrolment_id, r.round_id, r.competency_id)][r.interviewer_id] = r.score

    ranges: dict[tuple[str, str], list[int]] = defaultdict(list)
    for (_enrolment, rnd, comp), scored in shared.items():
        if len(scored) < 2:
            continue
        vals = list(scored.values())
        ranges[(rnd, comp)].append(max(vals) - min(vals))

    out: list[CriterionBaseline] = []
    for key in sorted(candidates):
        rnd, comp = key
        n_candidates = len(candidates[key])
        n_interviewers = len(interviewers[key])
        suppressed = n_candidates < min_candidates or n_interviewers < min_interviewers
        scores = scores_by_key.get(key, [])
        mean = round(fmean(scores), 3) if scores else None
        dist = {k: 0 for k in range(1, 6)}
        for s in scores:
            dist[s] = dist.get(s, 0) + 1
        shared_n = len(ranges.get(key, []))
        disagreement = round(fmean(ranges[key]), 3) if ranges.get(key) else None
        signal = None
        if (
            not suppressed
            and disagreement is not None
            and shared_n >= min_pairs
            and n_candidates >= min_candidates
            and disagreement >= wide_range
        ):
            signal = "wide_disagreement"
        out.append(
            CriterionBaseline(
                criterion_key=f"{rnd}:{comp}", round_id=rnd, competency_id=comp,
                candidates=n_candidates, interviewers=n_interviewers,
                scores=len(scores), not_assessed=not_assessed.get(key, 0),
                suppressed=suppressed,
                distribution=None if suppressed else dist,
                mean=None if suppressed else mean,
                shared_judgements=shared_n,
                disagreement=None if suppressed else disagreement,
                signal=signal,
            )
        )
    return sorted(out, key=lambda b: (-(b.disagreement or 0.0), b.criterion_key))
