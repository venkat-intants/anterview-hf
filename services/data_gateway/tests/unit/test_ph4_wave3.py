"""PH4 Wave 3 — interview scheduling and loops (A2), panel workload and
calibration (O5): the arithmetic, and the promises that hold by construction.

The database half (no double booking, the buffer, the interviewer-row sync)
is in tests/integration/test_ph4_wave3_guarantees.py.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _t(h: int, m: int = 0, d: int = 21) -> datetime:
    return datetime(2026, 9, d, h, m, tzinfo=UTC)


def _calls(module: Any) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return names


def _sql(module: Any) -> str:
    tree = ast.parse(inspect.getsource(module))
    return " ".join(
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ).upper()


# ===========================================================================
# Free time
# ===========================================================================
def test_intervals_merge_subtract_and_intersect() -> None:
    from app.schedule_core import intersect, merge, subtract

    assert merge([(_t(9), _t(10)), (_t(10), _t(11)), (_t(13), _t(14))]) == [
        (_t(9), _t(11)), (_t(13), _t(14))]
    assert subtract([(_t(9), _t(17))], [(_t(10), _t(11)), (_t(12), _t(13))]) == [
        (_t(9), _t(10)), (_t(11), _t(12)), (_t(13), _t(17))]
    assert intersect([(_t(9), _t(12))], [(_t(11), _t(14))]) == [(_t(11), _t(12))]


def test_a_slot_needs_every_interviewer_free() -> None:
    from app.schedule_core import SlotQuery, slots

    q = SlotQuery(duration_minutes=60, buffer_minutes=0, now=_t(0))
    found = slots(q, interviewer_free=[[(_t(6), _t(9))], [(_t(7), _t(10))]], candidate_busy=[])
    assert [s.strftime("%H:%M") for s in found] == ["07:00", "07:15", "07:30", "07:45", "08:00"]


def test_the_buffer_keeps_a_gap_on_both_sides_of_the_candidate_s_other_sessions() -> None:
    from app.schedule_core import SlotQuery, slots

    q = SlotQuery(duration_minutes=60, buffer_minutes=15, now=_t(0))
    # Another session 08:30–09:30, already widened by its own buffer to 09:45.
    found = slots(q, interviewer_free=[[(_t(6), _t(12))]], candidate_busy=[(_t(8, 30), _t(9, 45))])
    starts = [s.strftime("%H:%M") for s in found]
    assert "07:15" in starts and "07:30" not in starts  # 07:30+60+15 would reach 08:45
    assert "09:45" in starts and "09:30" not in starts


def test_no_slot_sooner_than_the_lead_time_or_off_the_grid() -> None:
    from app.schedule_core import SlotQuery, slots

    now = _t(6, 7)
    found = slots(SlotQuery(duration_minutes=30, buffer_minutes=0, now=now),
                  interviewer_free=[[(_t(6), _t(12))]], candidate_busy=[])
    assert found[0] == _t(8, 15)  # 06:07 + 2h lead = 08:07, rounded up to the grid
    assert all(s.minute % 15 == 0 for s in found)


def test_an_interviewer_with_no_availability_means_no_slots() -> None:
    from app.schedule_core import SlotQuery, slots

    q = SlotQuery(duration_minutes=30, buffer_minutes=0, now=_t(0))
    assert slots(q, interviewer_free=[[(_t(6), _t(12))], []], candidate_busy=[]) == []
    assert slots(q, interviewer_free=[], candidate_busy=[]) == []


def test_the_slot_list_is_capped() -> None:
    from app.schedule_core import MAX_SLOTS, SlotQuery, slots

    q = SlotQuery(duration_minutes=15, buffer_minutes=0, now=_t(0))
    many = slots(q, interviewer_free=[[(_t(2), _t(2) + timedelta(days=20))]], candidate_busy=[])
    assert len(many) == MAX_SLOTS


def test_covered_means_inside_one_window() -> None:
    from app.schedule_core import covered

    ws = [(_t(9), _t(10)), (_t(10), _t(12))]  # touching windows join
    assert covered(_t(9, 30), _t(11), ws)
    assert not covered(_t(8, 30), _t(9, 30), ws)


# ===========================================================================
# Time zones
# ===========================================================================
def test_only_real_timezones_are_accepted() -> None:
    from app.schedule_core import ScheduleError, valid_timezone

    assert valid_timezone(" Asia/Kolkata ") == "Asia/Kolkata"
    for bad in ("", "Mars/Olympus", "IST+5:30", "x" * 70):
        with pytest.raises(ScheduleError):
            valid_timezone(bad)


def test_a_naive_time_is_refused_not_guessed() -> None:
    from app.schedule_core import ScheduleError, aware

    with pytest.raises(ScheduleError):
        aware(datetime(2026, 9, 21, 10, 0))
    ist = datetime.fromisoformat("2026-09-21T10:00:00+05:30")
    assert aware(ist) == _t(4, 30)


def test_times_are_shown_in_the_candidate_s_zone_across_dst() -> None:
    from app.schedule_core import local_label

    assert local_label(_t(5), "Asia/Kolkata") == "Mon 21 Sep 2026, 10:30 IST"
    # New York is on EDT in September and EST in December.
    assert local_label(_t(14), "America/New_York").endswith("10:00 EDT")
    assert local_label(datetime(2026, 12, 1, 15, 0, tzinfo=UTC), "America/New_York").endswith(
        "10:00 EST")


# ===========================================================================
# Calendar files
# ===========================================================================
def test_the_calendar_file_is_rfc_5545() -> None:
    from app.schedule_core import IcsEvent, ics

    cal = ics([IcsEvent("s1@x", 2, _t(7), _t(8), "Panel; round 2, the long one — " + "x" * 80,
                        "Line one\nLine two", "Room 4")], now=_t(0))
    lines = cal.split("\r\n")
    assert cal.endswith("\r\n") and lines[0] == "BEGIN:VCALENDAR"
    assert "DTSTART:20260921T070000Z" in lines and "SEQUENCE:2" in lines
    assert all(len(line.encode()) <= 75 for line in lines)
    unfolded = cal.replace("\r\n ", "")
    assert "SUMMARY:Panel\\; round 2\\, the long one — " in unfolded
    assert "DESCRIPTION:Line one\\nLine two" in unfolded
    assert "METHOD:PUBLISH" in lines and "STATUS:CONFIRMED" in lines


def test_folding_never_splits_a_multibyte_character() -> None:
    from app.schedule_core import IcsEvent, ics

    cal = ics([IcsEvent("s@x", 0, _t(7), _t(8), "ఇంటర్వ్యూ " * 12)], now=_t(0))
    for line in cal.split("\r\n"):
        line.encode("utf-8").decode("utf-8")  # would raise on a split character
        assert len(line.encode()) <= 75


def test_a_cancelled_schedule_says_cancel() -> None:
    from app.schedule_core import IcsEvent, ics

    cal = ics([IcsEvent("s@x", 3, _t(7), _t(8), "Panel", cancelled=True)], now=_t(0))
    assert "METHOD:CANCEL" in cal and "STATUS:CANCELLED" in cal


# ===========================================================================
# Calibration
# ===========================================================================
def _rows(pattern: dict[str, int], n: int, comp: str = "c1") -> list[Any]:
    from app.calibration_core import ScoreRow

    return [ScoreRow(f"{who}-{i}", who, f"e{i}", "r1", comp, score)
            for i in range(n) for who, score in pattern.items()]


def test_the_outlier_is_flagged_and_the_panel_is_not() -> None:
    from app.calibration_core import calibrate

    out = {c.interviewer_id: c for c in calibrate(_rows({"gen": 5, "p1": 3, "p2": 3}, 6))}
    assert out["gen"].flag == "higher" and out["gen"].mean_delta == pytest.approx(1.333, abs=1e-3)
    assert out["p1"].flag is None and out["p2"].flag is None


def test_a_difference_must_repeat_before_it_is_called_meaningful() -> None:
    from app.calibration_core import MIN_PAIRS, calibrate

    few = {c.interviewer_id: c for c in calibrate(_rows({"gen": 5, "p1": 1}, MIN_PAIRS - 1))}
    assert few["gen"].flag is None and few["gen"].pairs == MIN_PAIRS - 1


def test_scores_nobody_else_gave_are_not_compared() -> None:
    from app.calibration_core import ScoreRow, calibrate

    rows = [ScoreRow(f"s{i}", "solo", f"e{i}", "r1", "c1", 5) for i in range(10)]
    (c,) = calibrate(rows)
    assert c.pairs == 0 and c.mean_delta is None and c.flag is None and c.mean == 5.0


def test_not_assessed_is_a_rate_not_a_score() -> None:
    from app.calibration_core import ScoreRow, calibrate

    rows = [r for i in range(5) for r in (ScoreRow(f"a{i}", "iv", f"e{i}", "r1", "c1", 4),
                                          ScoreRow(f"a{i}", "iv", f"e{i}", "r1", "c2", None))]
    (c,) = calibrate(rows)
    assert c.not_assessed_rate == 0.5 and c.mean == 4.0
    assert c.distribution is not None and c.distribution[4] == 5


def test_a_single_candidate_s_scores_are_never_published() -> None:
    """Security review M1: an average over one candidate IS their score."""
    from app.calibration_core import calibrate

    for c in calibrate(_rows({"gen": 5, "p1": 3}, 1)):
        assert c.suppressed and c.candidates == 1
        assert c.mean is None and c.mean_delta is None and c.flag is None
        assert c.by_competency == {} and c.distribution is None and c.not_assessed_rate is None


def test_a_competency_cell_resting_on_few_candidates_is_withheld() -> None:
    from app.calibration_core import ScoreRow, calibrate

    rows = [ScoreRow(f"s{i}", "iv", f"e{i}", "r1", "c1", 4) for i in range(5)]
    rows += [ScoreRow(f"s{i}", "iv", f"e{i}", "r1", "c2", 2) for i in range(2)]
    (c,) = calibrate(rows)
    assert not c.suppressed and c.by_competency == {"c1": 4.0}


def test_calibration_hides_what_the_caller_has_not_yet_scored() -> None:
    """Security review M1: the PH4-A1 independence rule holds in calibration too."""
    import app.panel_workload as pw

    sql = " ".join(pw._SCORES_SQL.split())
    assert "mine.interviewer_user_id = :me" in sql
    assert "mine.status IN ('assigned', 'in_progress')" in sql
    assert '"me": actor' in inspect.getsource(pw.calibration)
    assert timedelta(days=7) == pw.MIN_CALIBRATION_SPAN


def test_calibration_names_interviewers_never_candidates() -> None:
    from app.calibration_core import calibrate

    for c in calibrate(_rows({"a": 4, "b": 2}, 6)):
        assert "e0" not in repr(c) and "enrolment" not in repr(vars(c))


# ===========================================================================
# Workload
# ===========================================================================
def _session(uid: str, start: datetime, minutes: int = 60, loop: str = "L1") -> dict[str, Any]:
    return {"user_id": uid, "starts_at": start, "ends_at": start + timedelta(minutes=minutes),
            "loop_id": loop}


def test_workload_counts_days_in_india_and_flags_over_allocation() -> None:
    from app.panel_workload import workload_rows

    people = [{"user_id": "u1", "full_name": "Asha", "role": "interviewer"}]
    # 20:00 UTC is 01:30 the NEXT day in India: it counts on the 22nd.
    sessions = [_session("u1", _t(4)), _session("u1", _t(6)), _session("u1", _t(20))]
    (row,) = workload_rows(people, sessions, [], {"u1": [(_t(0), _t(0) + timedelta(days=3))]},
                           {"u1": {"max_sessions_per_day": 1, "max_sessions_per_week": None}},
                           now=_t(0))
    assert row["by_day"] == {"2026-09-21": 2, "2026-09-22": 1}
    assert row["over_allocated_days"] == ["2026-09-21"] and "over_allocated" in row["flags"]
    assert row["hours"] == 3.0 and row["outside_availability"] == 0


def test_workload_flags_sessions_outside_availability_and_overdue_scorecards() -> None:
    from app.panel_workload import workload_rows

    people = [{"user_id": "u1", "full_name": "Asha", "role": "interviewer"}]
    cards = [{"user_id": "u1", "status": "assigned", "due_at": _t(1)},
             {"user_id": "u1", "status": "in_progress", "due_at": _t(23)}]
    (row,) = workload_rows(people, [_session("u1", _t(9))], cards, {}, {}, now=_t(12))
    assert row["outside_availability"] == 1 and row["overdue_scorecards"] == 1
    assert row["open_scorecards"] == 2
    assert set(row["flags"]) >= {"outside_availability", "overdue_scorecards"}


def test_a_multi_interviewer_session_counts_once_for_each_of_them() -> None:
    from app.panel_workload import workload_rows

    people = [{"user_id": u, "full_name": u, "role": "interviewer"} for u in ("a", "b")]
    rows = workload_rows(people, [_session("a", _t(9)), _session("b", _t(9))], [], {}, {},
                         now=_t(0))
    assert [r["sessions"] for r in rows] == [1, 1]


def test_the_conflict_detector_sees_an_overlap_the_database_should_prevent() -> None:
    from app.panel_workload import workload_rows

    people = [{"user_id": "a", "full_name": "A", "role": "interviewer"}]
    (row,) = workload_rows(people, [_session("a", _t(9)), _session("a", _t(9, 30))], [], {}, {},
                           now=_t(0))
    assert row["conflicts"] == 1 and "conflict" in row["flags"]


# ===========================================================================
# What holds by construction
# ===========================================================================
def test_scheduling_and_panel_views_cannot_move_a_candidate() -> None:
    import app.interview_scheduling as sch
    import app.panel_workload as pw

    for module in (sch, pw):
        assert not (_calls(module) & {"record_transition", "record_round_move", "record_result",
                                      "record_final_decision", "release_hold", "_hold"})
        sql = _sql(module)
        assert "UPDATE ENROLMENTS" not in sql and "INSERT INTO STAGE_TRANSITIONS" not in sql


def test_calibration_writes_nothing_but_its_audit_row() -> None:
    import app.calibration_core as core
    import app.panel_workload as pw

    sql = _sql(pw.calibration) + " " + pw._SCORES_SQL.upper()
    assert "UPDATE " not in sql and "INSERT " not in sql and "DELETE " not in sql
    assert "SC.STATUS = 'SUBMITTED' AND SC.SUPERSEDED_AT IS NULL" in pw._SCORES_SQL.upper()
    # No model, no recommendation: plain arithmetic.
    src = inspect.getsource(core) + inspect.getsource(pw)
    for word in ("llm", "gemini", "groq", "recommend", "decision_authority"):
        assert word not in src.lower(), word


def test_a_candidate_can_book_but_never_move_or_cancel() -> None:
    """A2 #23: candidate-initiated rescheduling is not supported."""
    from app.routers.interview_scheduling import me_router

    routes = {(m, r.path) for r in me_router.routes for m in getattr(r, "methods", set())}
    writes = {(m, p) for m, p in routes if m not in {"GET", "HEAD"}}
    assert writes == {("POST", "/users/me/interview-loops/{loop_id}/book")}


def test_every_route_sits_behind_its_audience_gate() -> None:
    source = (APP / "routers/interview_scheduling.py").read_text(encoding="utf-8")
    gate = {"hr_router": "HrCtxDep", "interviewer_router": "InterviewerCtxDep",
            "me_router": "CurrentUserDep"}
    tree = ast.parse(source)
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                router = getattr(d.func.value, "id", "")
                ann = " ".join(ast.unparse(a.annotation) for a in fn.args.args if a.annotation)
                assert gate[router] in ann, fn.name
                seen += 1
    assert seen >= 20


def test_a_booking_is_checked_against_the_slots_actually_free() -> None:
    import app.interview_scheduling as sch

    src = inspect.getsource(sch.candidate_book)
    assert "free_slots(" in src and "status = 'awaiting_slot'" in src
    assert "This interview already has a time" in src


def test_candidate_views_never_carry_scorecards_or_interviewer_ids() -> None:
    import app.interview_scheduling as sch

    out = sch._candidate_session({
        "id": "s", "title": "Panel", "duration_minutes": 45, "starts_at": None, "ends_at": None,
        "location": None, "status": "awaiting_slot",
        "interviewers": [{"user_id": "u", "name": "Asha", "scorecard_id": "c",
                          "scorecard_status": "assigned"}],
    })
    assert out["interviewers"] == ["Asha"] and "scorecard" not in repr(out)


def test_new_tables_are_in_the_erasure_inventory_and_5f_cancels_sessions() -> None:
    inv = (APP.parents[1] / "admin_ops" / "app" / "erasure_executor.py").read_text(encoding="utf-8")
    for table in ("interviewer_availability", "interviewer_capacity", "interview_loops",
                  "interview_sessions", "interview_session_interviewers"):
        assert f'"{table}"' in inv, table
    assert "UPDATE interview_sessions SET status = 'cancelled'" in inv


@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_every_scheduling_email_speaks_the_candidate_s_language(lang: str) -> None:
    from app.email_templates import render

    session = {"title": "Panel <b>", "when": "Mon 21 Sep 2026, 10:30 IST", "duration_minutes": 45}
    for template, ctx in (
        ("interview_itinerary", {"sessions": [session], "timezone": "Asia/Kolkata"}),
        ("interview_slot_request", {"count": 2}),
        ("interview_session_update", {"session": session}),
        ("interview_session_update", {"session": session, "cancelled": True}),
        ("interview_session_reminder", {"session": session, "window": "1h"}),
    ):
        mail = render(template, lang, {"name": "Asha", "job_title": "Engineer",
                                       "cta_url": "https://x/applications", **ctx})
        assert mail.subject and "Panel <b>" not in mail.html, (template, lang)
        if lang != "en":
            assert mail.subject != render(template, "en", {"job_title": "Engineer", **ctx}).subject


def test_session_reminders_are_a_sweep_stage_keyed_by_start_time() -> None:
    import app.reminders as rem

    assert "_session_reminders" in inspect.getsource(rem.run_once)
    k1 = rem.session_reminder_key("s", "24h", _t(9))
    assert k1 != rem.session_reminder_key("s", "24h", _t(10))  # a moved session re-reminds
    assert "COALESCE(wf.reminders_enabled, true)" in rem._SESSION_DUE_SQL


def test_candidate_routes_read_the_auth_user_not_the_orm_model() -> None:
    """Security review F1: `user.id` on the auth user raised AttributeError — a
    500 on every candidate route, hidden by a test override of the wrong type."""
    from shared.auth.base import User

    import app.routers.interview_scheduling as r

    assert r.User is User
    assert "user.id" not in inspect.getsource(r)
    import uuid as _uuid

    who = _uuid.uuid4()
    assert r._me(User(user_id=str(who), full_name="C", email="c@x.test", roles=[])) == who


def test_a_final_decision_closes_the_schedule() -> None:
    import app.final_decision as fd
    import app.interview_scheduling as sch

    assert "close_for_decision(" in inspect.getsource(fd.record_final_decision)
    src = inspect.getsource(sch.close_for_decision)
    assert "status = 'awaiting_slot' OR (status = 'scheduled' AND starts_at > :n)" in src


def test_candidates_cannot_book_or_see_slots_once_decided() -> None:
    import app.interview_scheduling as sch
    import app.reminders as rem

    assert "_still_open(" in inspect.getsource(sch.candidate_book)
    assert "_still_open(" in inspect.getsource(sch.candidate_slots)
    assert "e.status NOT IN ('hired', 'rejected')" in rem._SESSION_DUE_SQL


def test_eligibility_is_checked_before_anyone_s_availability_is_read() -> None:
    """Security review L1."""
    import app.interview_scheduling as sch

    src = inspect.getsource(sch.add_session)
    assert src.index("list_assignable_interviewers") < src.index("_outside_availability(")
    for fn in (sch._availability_windows, sch._bookings):
        assert "company_id = :c" in inspect.getsource(fn)


def test_calibration_looks_back_a_year_while_booking_windows_stay_short() -> None:
    """The panel offers a 180-day calibration period; it used to be refused by
    the 92-day cap that suits look-ahead windows (found in the checklist pass)."""
    from datetime import UTC, datetime, timedelta

    from fastapi import HTTPException

    import app.routers.interview_scheduling as r

    now = datetime.now(tz=UTC)
    s, e = r._window(now - timedelta(days=180), now, days=90, max_days=r.CALIBRATION_MAX_DAYS)
    assert e - s == timedelta(days=180)
    with pytest.raises(HTTPException):
        r._window(now, now + timedelta(days=180), days=28)
    assert "max_days=CALIBRATION_MAX_DAYS" in inspect.getsource(r.hr_calibration)


def test_a_cancelled_interview_releases_only_the_scorecards_it_alone_held() -> None:
    """Found in the checklist pass: cancelling left scorecards open to turn late."""
    import app.interview_scheduling as sch

    sql = sch._RELEASABLE_SQL
    assert "sc.status = 'assigned'" in sql          # never one the interviewer began
    assert "sc.corrects_id IS NULL" in sql          # never an open correction
    assert "os.id <> ALL(:s)" in sql and "'completed', 'no_show'" in sql  # still needed elsewhere
    assert "sc.company_id = :c" in sql
    assert "cards.withdraw(" in inspect.getsource(sch._release_scorecards)  # A1's rules and audit
    for fn in (sch.set_session_outcome, sch.cancel_loop, sch.close_for_decision):
        assert "_release_scorecards(" in inspect.getsource(fn), fn.__name__


def test_every_change_to_a_sent_schedule_raises_the_calendar_sequence() -> None:
    import app.interview_scheduling as sch

    for fn in (sch.reschedule_session, sch.set_session_outcome, sch.cancel_loop,
               sch.close_for_decision):
        assert "_bump_itinerary(" in inspect.getsource(fn), fn.__name__
    assert "sent_at IS NOT NULL" in inspect.getsource(sch._bump_itinerary)
