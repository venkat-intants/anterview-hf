"""Calibration arithmetic — PH4-O5. Pure: rows in, numbers out.

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
the interviewer included, the outlier stands out and the others do not. On a
panel of two nobody can tell who is off — both show the same gap, as they
should.

SMALL CELLS ARE WITHHELD. An average over one candidate IS that candidate's
score: a narrow filter (one round, a one-second window) would otherwise read
back an individual's evaluation — and let a panellist who has not submitted
yet see what their peers gave (security review M1; the independence rule of
PH4-A1). So every figure is published only when it rests on at least
``MIN_CANDIDATES`` distinct applications, per interviewer and per competency
cell alike; below that the numbers are withheld and the report says so.

Nothing here can change a scorecard, a status or a decision: it returns a
report, and the report names interviewers, never candidates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import fmean

# A difference is only called out when it is both large and repeated: three
# quarters of a point on the 1–5 scale, over at least five shared judgements.
MIN_PAIRS = 5
MEANINGFUL_DELTA = 0.75
# No figure is published from fewer distinct applications than this.
MIN_CANDIDATES = 5


@dataclass(frozen=True)
class ScoreRow:
    scorecard_id: str
    interviewer_id: str
    enrolment_id: str
    round_id: str
    competency_id: str
    score: int | None  # None = not assessed


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
    flag: str | None = None  # "higher" | "lower" | None

    @property
    def not_assessed_rate(self) -> float | None:
        if self.suppressed:
            return None
        total = self.scores + self.not_assessed
        return round(self.not_assessed / total, 3) if total else None


def calibrate(
    rows: Iterable[ScoreRow], *, min_candidates: int = MIN_CANDIDATES
) -> list[InterviewerCalibration]:
    """One entry per interviewer, those with the largest paired gap first."""
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
    for (enrolment, _round, _comp), scored in shared.items():
        if len(scored) < 2:
            continue
        panel = fmean(scored.values())
        for who, s in scored.items():
            deltas[who].append(s - panel)
            delta_people[who].add(enrolment)

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
            if c.pairs >= MIN_PAIRS and abs(c.mean_delta) >= MEANINGFUL_DELTA:
                c.flag = "higher" if c.mean_delta > 0 else "lower"

    return sorted(per.values(), key=lambda c: (-(abs(c.mean_delta or 0.0)), c.interviewer_id))
