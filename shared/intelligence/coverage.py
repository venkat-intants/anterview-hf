"""Deterministic interview planning — which competency each turn probes.

The problem this replaces
-------------------------
Question planning used to live entirely in prose. The graph asked the model to
"inspect the conversation history and pick the competency covered LEAST so
far"; the live worker hardcoded "Q2-Q6 technical, Q7-Q9 behavioural" regardless
of the role. Both are unverifiable. A model that decided to keep drilling one
topic produced an interview with three competencies never asked about — and the
scorer then had to score them anyway, from nothing.

Planning here is pure, deterministic and testable: same profile + same turn
budget always yields the same plan. The LLM still writes the actual question
(that is what it is good at) and still follows the candidate's answers; it is
simply told *what this turn is for*.

Slot allocation uses largest-remainder on competency weight, then spreads each
competency's slots evenly across the interview rather than blocking them
together. Blocking is what makes an interview feel like an interrogation on one
topic and leaves the last competency crammed at the end when the candidate is
tired.
"""

from __future__ import annotations

import structlog

from shared.intelligence.schema import (
    CoverageReport,
    CoverageRow,
    RoleProfile,
    TurnPlan,
)

log = structlog.get_logger(__name__)

# Below this many turns there is no room for a dedicated intro and wrap, so we
# spend everything on probes. Four is the smallest interview that still gets a
# real opener, two substantive questions and a close.
_MIN_TURNS_FOR_BOOKENDS: int = 4


def allocate_by_weight(profile: RoleProfile, slots: int) -> dict[str, int]:
    """Distribute ``slots`` across competencies by weight. Public wrapper.

    Shared by interview planning and exam generation so a written test covers
    the same competencies, in the same proportion, as the conversation. Two
    allocators would drift, and then a candidate's exam and interview would
    quietly be assessing different jobs.
    """
    return _allocate_slots(profile, slots)


def _allocate_slots(profile: RoleProfile, probe_slots: int) -> dict[str, int]:
    """Distribute ``probe_slots`` across competencies by weight.

    Largest-remainder (Hare quota): floor every share, then hand the leftover
    slots to the largest fractional remainders. This is the allocation that
    minimises the gap between a competency's weight and its actual airtime —
    which matters because that gap is exactly what ``CoverageReport`` reports
    to HR.

    With fewer slots than competencies some competencies get zero. That is a
    real property of a 10-question interview against a 7-competency role, and
    it is surfaced rather than hidden (see ``coverage_report``).
    """
    if probe_slots <= 0:
        return {c.id: 0 for c in profile.competencies}

    exact = {c.id: c.weight * probe_slots for c in profile.competencies}
    allocation = {cid: int(value) for cid, value in exact.items()}

    remaining = probe_slots - sum(allocation.values())
    if remaining > 0:
        # Ties broken by descending weight then id, so allocation is stable
        # across runs (a dict-order-dependent tiebreak would make the plan
        # non-reproducible, defeating the point of planning in code).
        weights = {c.id: c.weight for c in profile.competencies}
        order = sorted(
            exact,
            key=lambda cid: (-(exact[cid] - allocation[cid]), -weights[cid], cid),
        )
        for cid in order[:remaining]:
            allocation[cid] += 1

    return allocation


def _spread(allocation: dict[str, int], profile: RoleProfile) -> list[str]:
    """Order allocated slots so each competency's turns are spaced apart.

    Each of a competency's ``n`` slots is given a virtual position
    ``(k + 0.5) / n`` on a 0-1 timeline; sorting all slots by that position
    interleaves them evenly. A competency with 3 slots lands at 0.17 / 0.50 /
    0.83; one with 1 slot lands at 0.50. Ties break by descending weight so the
    heaviest competency leads — screening interviews should establish core role
    fit early, while the candidate is freshest.

    A final pass swaps any remaining adjacent duplicates forward, so the same
    competency is never probed twice in a row unless it is the only one with
    slots left.
    """
    weights = {c.id: c.weight for c in profile.competencies}
    positioned: list[tuple[float, float, str, str]] = []
    for cid, count in allocation.items():
        for k in range(count):
            positioned.append(((k + 0.5) / count, -weights[cid], cid, cid))
    positioned.sort()
    order = [cid for _, _, cid, _ in positioned]

    # Break adjacent duplicates by swapping with the next different element.
    for i in range(1, len(order)):
        if order[i] != order[i - 1]:
            continue
        for j in range(i + 1, len(order)):
            if order[j] != order[i - 1] and (j + 1 >= len(order) or order[j] != order[j + 1]):
                order[i], order[j] = order[j], order[i]
                break

    return order


def plan_interview(profile: RoleProfile, max_turns: int) -> list[TurnPlan]:
    """Build the full turn-by-turn plan for one interview.

    Args:
        profile: the role model driving competency selection.
        max_turns: number of candidate answers the session budgets for. The
            live worker enforces this in code (``MAX_CANDIDATE_ANSWERS``); the
            plan must agree with it or the last competency never gets asked.

    Returns:
        Exactly ``max_turns`` ``TurnPlan`` entries, 1-indexed. Turn 1 is always
        the intro and the last turn is always the wrap when there is room for
        bookends — those two turns carry no competency because scoring a
        candidate's self-introduction as "technical depth" is noise.
    """
    turns = max(1, max_turns)

    if turns < _MIN_TURNS_FOR_BOOKENDS:
        head_intro, tail_wrap = (turns >= 2), False
    else:
        head_intro, tail_wrap = True, True

    probe_slots = turns - int(head_intro) - int(tail_wrap)
    allocation = _allocate_slots(profile, probe_slots)
    order = _spread(allocation, profile)

    plans: list[TurnPlan] = []
    index = 1

    if head_intro:
        plans.append(
            TurnPlan(
                turn_index=index,
                kind="intro",
                rationale="Opening — let the candidate introduce themselves and set context.",
            )
        )
        index += 1

    # Rotate probe stems so a competency with 3 slots asks 3 different things
    # rather than the same stem reworded.
    probe_cursor: dict[str, int] = {}
    for cid in order:
        comp = profile.competency(cid)
        if comp is None:  # defensive — _spread only emits ids from the profile
            continue
        cursor = probe_cursor.get(cid, 0)
        probe_cursor[cid] = cursor + 1
        plans.append(
            TurnPlan(
                turn_index=index,
                kind="probe",
                competency_id=comp.id,
                competency_name=comp.name,
                probe_hint=comp.probes[cursor % len(comp.probes)],
                rationale=(
                    f"Probe '{comp.name}' "
                    f"({comp.kind}, weight {comp.weight:.2f} for this role)."
                ),
            )
        )
        index += 1

    if tail_wrap:
        plans.append(
            TurnPlan(
                turn_index=index,
                kind="wrap",
                rationale="Close — warm wrap-up and the candidate's own questions.",
            )
        )
        index += 1

    # Pad if rounding left us short (only possible when probe_slots was 0 and
    # bookends did not fill the budget). Extra turns go to the heaviest
    # competency — a plan that is shorter than the enforced turn budget would
    # leave the model unguided for the final questions.
    heaviest = max(profile.competencies, key=lambda c: c.weight)
    while len(plans) < turns:
        plans.insert(
            max(0, len(plans) - int(tail_wrap)),
            TurnPlan(
                turn_index=0,  # renumbered below
                kind="probe",
                competency_id=heaviest.id,
                competency_name=heaviest.name,
                probe_hint=heaviest.probes[0],
                rationale=f"Extra turn — deepen '{heaviest.name}' (highest-weight competency).",
            ),
        )

    plans = plans[:turns]
    for i, plan in enumerate(plans, start=1):
        plan.turn_index = i

    log.info(
        "intelligence.plan_interview",
        profile_id=profile.profile_id,
        domain_family=profile.domain_family,
        turns=turns,
        probe_slots=probe_slots,
        allocation=allocation,
    )
    return plans


def plan_for_turn(profile: RoleProfile, turn_index: int, max_turns: int) -> TurnPlan:
    """Return the plan for a single 1-based turn.

    Convenience for the streaming paths (live worker, graph ``follow_up``) that
    only need the current turn. Recomputing the whole plan each turn is cheap
    (pure arithmetic, no I/O) and keeps the two paths guaranteed-consistent —
    caching a plan on session state would just be one more thing to invalidate.

    Out-of-range indices clamp to the last turn, so a session that overruns its
    budget still gets a sensible directive instead of an IndexError.
    """
    plans = plan_interview(profile, max_turns)
    idx = min(max(1, turn_index), len(plans))
    return plans[idx - 1]


def coverage_report(profile: RoleProfile, plans: list[TurnPlan]) -> CoverageReport:
    """Summarise what the plan actually covers.

    Attached to the scorecard as evidence. ``uncovered`` is the important
    field — it names the competencies that got no dedicated turn, which is the
    signal a human reviewer needs to know a score was inferred rather than
    observed. Advisory only: it never suppresses or overrides a score.
    """
    probes = [p for p in plans if p.kind == "probe" and p.competency_id]
    probe_turns = len(probes)

    counts: dict[str, int] = {}
    for plan in probes:
        assert plan.competency_id is not None  # narrowed by the filter above
        counts[plan.competency_id] = counts.get(plan.competency_id, 0) + 1

    rows = [
        CoverageRow(
            competency_id=comp.id,
            competency_name=comp.name,
            weight=comp.weight,
            planned_turns=counts.get(comp.id, 0),
            planned_share=(
                round(counts.get(comp.id, 0) / probe_turns, 4) if probe_turns else 0.0
            ),
        )
        for comp in profile.competencies
    ]

    return CoverageReport(
        total_turns=len(plans),
        probe_turns=probe_turns,
        rows=rows,
        uncovered=[row.competency_id for row in rows if row.planned_turns == 0],
    )
