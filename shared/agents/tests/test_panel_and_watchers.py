"""Panel + watcher tests.

Contradiction detection is deterministic precisely so it can be tested like
this — a hiring conversation may rest on it, and a model asked "do these
disagree?" is not reproducible run to run.
"""

from __future__ import annotations

import json

import pytest

from shared.agents.panel import (
    CONTRADICTION_THRESHOLD,
    CandidateEvidence,
    SignalEvidence,
    assess_candidate,
    detect_contradictions,
)
from shared.agents.schema import Citation, SignalAssessment, WatcherFinding
from shared.agents.watchers import (
    DECISION_WAIT_DAYS,
    ErasureRequest,
    FunnelRow,
    OpeningHealth,
    QuestionStat,
    StalledApplicant,
    WatcherInput,
    digest,
    run_watchers,
    watch_decision_backlog,
    watch_openings_without_workflow,
)


def _sig(name: str, score: float | None, available: bool = True) -> SignalAssessment:
    return SignalAssessment(
        signal=name,  # type: ignore[arg-type]
        available=available,
        score_0_100=score,
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


def test_the_headline_case_is_caught() -> None:
    """Coding 91 vs technical interview 52 — the case the panel exists for."""
    found = detect_contradictions([_sig("coding", 91), _sig("interview", 52)])
    assert len(found) == 1
    assert set(found[0].between) == {"coding", "interview"}
    assert found[0].severity == "high"
    assert "walk through their submitted code" in found[0].suggested_check


def test_agreeing_signals_produce_nothing() -> None:
    assert detect_contradictions([_sig("coding", 78), _sig("interview", 72)]) == []


@pytest.mark.parametrize("gap", [CONTRADICTION_THRESHOLD - 1, CONTRADICTION_THRESHOLD])
def test_gaps_at_or_below_threshold_are_noise(gap: float) -> None:
    assert detect_contradictions([_sig("exam", 50 + gap), _sig("interview", 50)]) == []


def test_absent_signals_are_never_compared() -> None:
    """'Has not sat the exam' must not read as 'scored zero on the exam'."""
    found = detect_contradictions(
        [_sig("exam", None, available=False), _sig("interview", 85)]
    )
    assert found == []


def test_resume_disagreeing_with_a_measured_result_is_not_flagged() -> None:
    """A self-authored claim differing from a measurement is the normal case.

    Flagging it every time would train recruiters to ignore the panel.
    """
    assert detect_contradictions([_sig("resume", 90), _sig("exam", 40)]) == []
    assert detect_contradictions([_sig("resume", 90), _sig("coding", 35)]) == []


def test_resume_vs_interview_is_still_compared() -> None:
    """A resume claim contradicted in conversation IS worth surfacing."""
    found = detect_contradictions([_sig("resume", 92), _sig("interview", 41)])
    assert len(found) == 1
    assert "Verify the claimed experience" in found[0].suggested_check


def test_contradictions_are_ordered_worst_first() -> None:
    found = detect_contradictions(
        [_sig("exam", 80), _sig("interview", 50), _sig("coding", 95)]
    )
    severities = [c.severity for c in found]
    assert severities == sorted(severities, key=lambda s: {"high": 0, "medium": 1}[s])


# ---------------------------------------------------------------------------
# The panel end to end
# ---------------------------------------------------------------------------


def _evidence(**overrides: object) -> CandidateEvidence:
    return CandidateEvidence(
        applicant_id="a-1",
        applicant_label="Candidate A",
        job_title="CNC Machine Operator",
        signals=[
            SignalEvidence(
                signal="resume",
                available=True,
                score_0_100=72,
                detail="Diploma in mechanical engineering. " * 30,
                citations=[Citation(kind="applicant", id="a-1", label="Candidate A")],
            ),
            SignalEvidence(
                signal="exam", available=True, score_0_100=64, detail="x" * 500
            ),
            SignalEvidence(
                signal="coding", available=False
            ),
            SignalEvidence(
                signal="interview", available=True, score_0_100=88, detail="y" * 900
            ),
        ],
        **overrides,  # type: ignore[arg-type]
    )


async def test_panel_runs_without_a_model() -> None:
    """No LLM configured must still produce a usable, honest brief."""
    verdict = await assess_candidate(_evidence(), llm=None)

    assert verdict.applicant_id == "a-1"
    assert verdict.decision_authority == "human_only"
    assert len(verdict.signals) == 4
    # The deterministic fallback still reports the numbers and the gaps.
    assert "64/100" in verdict.summary
    assert "A human reviewer decides" in verdict.summary


async def test_absent_signal_is_reported_as_absent_not_as_zero() -> None:
    verdict = await assess_candidate(_evidence(), llm=None)
    coding = next(s for s in verdict.signals if s.signal == "coding")

    assert coding.available is False
    assert coding.score_0_100 is None
    assert coding.confidence == 0.0
    assert any("Coding test" in gap for gap in verdict.coverage_gaps)


async def test_uncovered_competencies_surface_as_gaps() -> None:
    """A competency no round probed must be stated, never silently scored."""
    verdict = await assess_candidate(
        _evidence(uncovered_competencies=["Shop-floor Safety"]), llm=None
    )
    assert any("Shop-floor Safety" in gap for gap in verdict.coverage_gaps)


async def test_specialists_see_only_their_own_signal() -> None:
    """The blindness property — a specialist must not be shown other signals."""
    seen: list[str] = []

    async def llm(system: str, user: str) -> str:
        seen.append(user)
        return json.dumps({"confidence": 0.8, "strengths": ["s"], "concerns": [], "evidence": ["e"]})

    evidence = CandidateEvidence(
        applicant_id="a-1",
        applicant_label="A",
        job_title="Welder",
        signals=[
            SignalEvidence(signal="exam", available=True, score_0_100=40, detail="EXAM_MARKER " * 60),
            SignalEvidence(
                signal="interview", available=True, score_0_100=90, detail="INTERVIEW_MARKER " * 60
            ),
        ],
    )
    await assess_candidate(evidence, llm=llm)

    exam_prompt = next(p for p in seen if "EXAM_MARKER" in p)
    assert "INTERVIEW_MARKER" not in exam_prompt
    # ...and the score of the other signal must not leak either.
    assert "90/100" not in exam_prompt


async def test_specialist_confidence_is_capped_by_signal_type() -> None:
    """A model cannot talk itself into trusting a resume like a measurement."""

    async def overconfident(system: str, user: str) -> str:
        return json.dumps({"confidence": 1.0, "strengths": [], "concerns": [], "evidence": []})

    evidence = CandidateEvidence(
        applicant_id="a-1",
        applicant_label="A",
        job_title="Welder",
        signals=[
            SignalEvidence(signal="resume", available=True, score_0_100=95, detail="x" * 900)
        ],
    )
    verdict = await assess_candidate(evidence, llm=overconfident)
    # Base ceiling for a self-authored resume is 0.45.
    assert verdict.signals[0].confidence <= 0.45


async def test_a_failing_specialist_does_not_lose_the_panel() -> None:
    async def broken(system: str, user: str) -> str:
        raise TimeoutError("provider down")

    verdict = await assess_candidate(_evidence(), llm=broken)
    assert len(verdict.signals) == 4
    # Scores come from the database, so they survive a model outage.
    assert next(s for s in verdict.signals if s.signal == "exam").score_0_100 == 64
    assert verdict.summary


async def test_malformed_specialist_json_degrades_gracefully() -> None:
    async def garbage(system: str, user: str) -> str:
        return "not json"

    verdict = await assess_candidate(_evidence(), llm=garbage)
    assert all(0.0 <= s.confidence <= 1.0 for s in verdict.signals)


async def test_confidence_rises_with_more_independent_signals() -> None:
    async def ok(system: str, user: str) -> str:
        return json.dumps({"confidence": 0.9, "strengths": [], "concerns": [], "evidence": []})

    one = CandidateEvidence(
        applicant_id="a", applicant_label="A", job_title="Welder",
        signals=[SignalEvidence(signal="interview", available=True, score_0_100=70, detail="x" * 900)],
    )
    three = CandidateEvidence(
        applicant_id="a", applicant_label="A", job_title="Welder",
        signals=[
            SignalEvidence(signal="interview", available=True, score_0_100=70, detail="x" * 900),
            SignalEvidence(signal="exam", available=True, score_0_100=72, detail="x" * 900),
            SignalEvidence(signal="coding", available=True, score_0_100=68, detail="x" * 900),
        ],
    )
    assert (await assess_candidate(three, llm=ok)).confidence > (
        await assess_candidate(one, llm=ok)
    ).confidence


async def test_contradictions_reduce_overall_confidence() -> None:
    async def ok(system: str, user: str) -> str:
        return json.dumps({"confidence": 0.9, "strengths": [], "concerns": [], "evidence": []})

    def _ev(interview_score: float) -> CandidateEvidence:
        return CandidateEvidence(
            applicant_id="a", applicant_label="A", job_title="Welder",
            signals=[
                SignalEvidence(signal="coding", available=True, score_0_100=90, detail="x" * 900),
                SignalEvidence(
                    signal="interview", available=True, score_0_100=interview_score, detail="x" * 900
                ),
            ],
        )

    agreeing = await assess_candidate(_ev(88), llm=ok)
    disagreeing = await assess_candidate(_ev(40), llm=ok)
    assert disagreeing.confidence < agreeing.confidence
    assert disagreeing.contradictions


async def test_panel_has_no_way_to_express_a_hiring_verdict() -> None:
    """Advisory-only, enforced by the type rather than by a prompt."""
    verdict = await assess_candidate(_evidence(), llm=None)
    dumped = verdict.model_dump()
    assert dumped["decision_authority"] == "human_only"
    assert "recommendation" not in dumped
    assert "decision" not in dumped


# ---------------------------------------------------------------------------
# Watchers
# ---------------------------------------------------------------------------


def test_stalled_applicants_are_flagged_with_the_worst_named() -> None:
    data = WatcherInput(
        company_id="co-1",
        stalled=[
            StalledApplicant("a-1", "Asha", "shortlisted", 12),
            StalledApplicant("a-2", "Bala", "shortlisted", 31),
            StalledApplicant("a-3", "Chandra", "new", 3),  # under threshold
        ],
    )
    findings = [f for f in run_watchers(data) if f.watcher == "stalled_applicants"]
    assert len(findings) == 1
    assert "2 applicant(s) stalled" in findings[0].title
    assert "Bala" in findings[0].body  # longest-stalled leads
    assert "Chandra" not in findings[0].body


def test_stalled_dedupe_key_is_stable_across_runs_but_changes_with_the_set() -> None:
    """Nightly re-runs must not re-notify about the same people."""
    a = WatcherInput(company_id="c", stalled=[StalledApplicant("a-1", "A", "new", 12)])
    b = WatcherInput(company_id="c", stalled=[StalledApplicant("a-1", "A", "new", 19)])
    c = WatcherInput(
        company_id="c",
        stalled=[StalledApplicant("a-1", "A", "new", 12), StalledApplicant("a-2", "B", "new", 12)],
    )
    def key(d: WatcherInput) -> str:
        return run_watchers(d)[0].dedupe_key
    assert key(a) == key(b)  # same people, more days → same alert
    assert key(a) != key(c)  # a new person → a new alert


def test_low_volume_funnels_are_not_flagged() -> None:
    """Below the volume floor, a bad ratio is noise."""
    data = WatcherInput(
        company_id="c", funnels=[FunnelRow("j-1", "Welder", applicants=5, interviewed=0)]
    )
    assert [f for f in run_watchers(data) if f.watcher == "funnel_health"] == []


def test_lossy_funnel_at_volume_is_flagged() -> None:
    data = WatcherInput(
        company_id="c", funnels=[FunnelRow("j-1", "Welder", applicants=40, interviewed=1)]
    )
    findings = [f for f in run_watchers(data) if f.watcher == "funnel_health"]
    assert len(findings) == 1
    assert "40 applicants, 1 interviewed" in findings[0].body


def test_broken_and_trivial_exam_questions_are_distinguished() -> None:
    data = WatcherInput(
        company_id="c",
        question_stats=[
            QuestionStat("e-1", "CNC Basics", "q-7", 7, attempts=14, correct=1),
            QuestionStat("e-1", "CNC Basics", "q-2", 2, attempts=14, correct=14),
            QuestionStat("e-1", "CNC Basics", "q-9", 9, attempts=3, correct=0),  # too few
        ],
    )
    findings = [f for f in run_watchers(data) if f.watcher == "exam_quality"]
    assert len(findings) == 2
    assert any("almost nobody passes it" in f.title for f in findings)
    assert any("separates nobody" in f.title for f in findings)


def test_dpdp_deadline_is_always_critical_and_sorts_first() -> None:
    """A statutory breach must not sit at the same weight as a stalled candidate."""
    data = WatcherInput(
        company_id="c",
        stalled=[StalledApplicant("a-1", "A", "new", 40)],
        erasure_requests=[ErasureRequest("r-1", hours_remaining=6)],
    )
    findings = run_watchers(data)
    assert findings[0].watcher == "dpdp_deadlines"
    assert findings[0].severity == "critical"


def test_erasure_requests_outside_the_window_are_quiet() -> None:
    data = WatcherInput(
        company_id="c", erasure_requests=[ErasureRequest("r-1", hours_remaining=200)]
    )
    assert run_watchers(data) == []


def test_a_broken_watcher_does_not_take_down_the_sweep() -> None:
    """One bad rule must not cost every company every other alert."""
    from shared.agents import watchers as watchers_mod

    def _explode(data: WatcherInput) -> list[WatcherFinding]:
        raise RuntimeError("bad rule")

    original = watchers_mod.WATCHERS
    watchers_mod.WATCHERS = (("boom", _explode),) + original
    try:
        data = WatcherInput(
            company_id="c", stalled=[StalledApplicant("a-1", "A", "new", 20)]
        )
        findings = run_watchers(data)
        assert len(findings) == 1
        assert findings[0].watcher == "stalled_applicants"
    finally:
        watchers_mod.WATCHERS = original


def test_digest_handles_the_quiet_case() -> None:
    assert "Nothing needs attention" in digest([])


def test_digest_lists_findings_worst_first() -> None:
    data = WatcherInput(
        company_id="c",
        stalled=[StalledApplicant("a-1", "A", "new", 20)],
        erasure_requests=[ErasureRequest("r-1", hours_remaining=6)],
    )
    text = digest(run_watchers(data))
    assert text.index("DPDP") < text.index("stalled")


# ---------------------------------------------------------------------------
# Per-opening watchers — E6
# ---------------------------------------------------------------------------


def _opening(**kw: object) -> OpeningHealth:
    base = {
        "requisition_id": "req-1",
        "title": "Backend Engineer",
        "awaiting_decision": 0,
        "longest_wait_days": 0.0,
        "held": 0,
        "live_enrolments": 0,
        "has_published_workflow": True,
        "accepting_public_applications": False,
    }
    base.update(kw)
    return OpeningHealth(**base)  # type: ignore[arg-type]


def test_a_decision_queue_nobody_opens_raises_a_finding() -> None:
    """The rule this guards: D-05 reserves every outcome for a human, which is
    only a protection while a human is actually looking. An unread queue turns
    "a person decides" into "nobody decides" — worse for the candidate than a
    rejection, because they are not turned down, they are left."""
    data = WatcherInput(
        company_id="c",
        openings=[_opening(awaiting_decision=3, longest_wait_days=5.0, held=1)],
    )
    findings = watch_decision_backlog(data)
    assert len(findings) == 1
    assert findings[0].watcher == "decision_backlog"
    assert "Backend Engineer" in findings[0].title
    # Names the screen, not just the problem.
    assert findings[0].link == "/hr/requisitions/req-1/decisions"


def test_a_queue_being_worked_is_not_a_backlog() -> None:
    data = WatcherInput(
        company_id="c",
        openings=[_opening(awaiting_decision=4, longest_wait_days=DECISION_WAIT_DAYS - 1)],
    )
    assert watch_decision_backlog(data) == []


def test_an_empty_queue_raises_nothing() -> None:
    data = WatcherInput(
        company_id="c", openings=[_opening(awaiting_decision=0, longest_wait_days=99.0)]
    )
    assert watch_decision_backlog(data) == []


def test_a_fortnight_of_silence_is_critical_not_a_warning() -> None:
    """By two weeks the candidate has concluded they were rejected, so the
    decision is being made by default — which is exactly what D-05 forbids."""
    short = WatcherInput(
        company_id="c", openings=[_opening(awaiting_decision=1, longest_wait_days=4.0)]
    )
    long = WatcherInput(
        company_id="c", openings=[_opening(awaiting_decision=1, longest_wait_days=15.0)]
    )
    assert watch_decision_backlog(short)[0].severity == "warning"
    assert watch_decision_backlog(long)[0].severity == "critical"


def test_the_backlog_alert_mentions_holds_only_when_there_are_some() -> None:
    """A hold is not a rejection, and someone skimming a notification should
    not be able to read it as one."""
    with_held = watch_decision_backlog(
        WatcherInput(
            company_id="c",
            openings=[_opening(awaiting_decision=2, longest_wait_days=5.0, held=2)],
        )
    )[0]
    without = watch_decision_backlog(
        WatcherInput(
            company_id="c",
            openings=[_opening(awaiting_decision=2, longest_wait_days=5.0, held=0)],
        )
    )[0]
    assert "below a round threshold" in with_held.body
    assert "below a round threshold" not in without.body


def test_the_backlog_dedupe_key_tracks_the_queue_size() -> None:
    """Re-fires when the backlog grows, stays quiet while it merely sits — the
    difference between a useful alert and one people learn to ignore."""
    same = _opening(awaiting_decision=3, longest_wait_days=5.0)
    grown = _opening(awaiting_decision=7, longest_wait_days=5.0)
    a = watch_decision_backlog(WatcherInput(company_id="c", openings=[same]))[0]
    b = watch_decision_backlog(WatcherInput(company_id="c", openings=[same]))[0]
    c = watch_decision_backlog(WatcherInput(company_id="c", openings=[grown]))[0]
    assert a.dedupe_key == b.dedupe_key
    assert a.dedupe_key != c.dedupe_key


def test_each_opening_gets_its_own_finding() -> None:
    """"You have decisions waiting" across six roles tells a manager nothing
    about where to start."""
    data = WatcherInput(
        company_id="c",
        openings=[
            _opening(requisition_id="r1", title="Backend", awaiting_decision=2,
                     longest_wait_days=5.0),
            _opening(requisition_id="r2", title="Data", awaiting_decision=9,
                     longest_wait_days=8.0),
        ],
    )
    findings = watch_decision_backlog(data)
    assert {f.citations[0].id for f in findings} == {"r1", "r2"}


def test_candidates_with_no_workflow_to_walk_are_flagged() -> None:
    """The failure mode: an opening published to the apply form before its
    workflow was. Applications pile up and nothing moves any of them, which
    from the outside is indistinguishable from being ignored."""
    data = WatcherInput(
        company_id="c",
        openings=[_opening(live_enrolments=12, has_published_workflow=False)],
    )
    findings = watch_openings_without_workflow(data)
    assert len(findings) == 1
    assert findings[0].link == "/hr/requisitions/req-1/workflow"


def test_an_empty_opening_with_no_workflow_is_fine() -> None:
    """A role nobody has applied to yet does not need a process built for it."""
    data = WatcherInput(
        company_id="c",
        openings=[_opening(live_enrolments=0, has_published_workflow=False)],
    )
    assert watch_openings_without_workflow(data) == []


def test_a_published_workflow_silences_it() -> None:
    data = WatcherInput(
        company_id="c",
        openings=[_opening(live_enrolments=30, has_published_workflow=True)],
    )
    assert watch_openings_without_workflow(data) == []


def test_a_public_opening_with_no_workflow_says_more_are_coming() -> None:
    data = WatcherInput(
        company_id="c",
        openings=[
            _opening(
                live_enrolments=4,
                has_published_workflow=False,
                accepting_public_applications=True,
            )
        ],
    )
    assert "more are arriving" in watch_openings_without_workflow(data)[0].body


def test_the_new_watchers_are_wired_into_the_sweep() -> None:
    """Registered, not just defined — a watcher missing from WATCHERS is a
    function nothing ever calls."""
    data = WatcherInput(
        company_id="c",
        openings=[
            _opening(awaiting_decision=5, longest_wait_days=9.0, live_enrolments=5,
                     has_published_workflow=False)
        ],
    )
    fired = {f.watcher for f in run_watchers(data)}
    assert {"decision_backlog", "openings_without_workflow"} <= fired
