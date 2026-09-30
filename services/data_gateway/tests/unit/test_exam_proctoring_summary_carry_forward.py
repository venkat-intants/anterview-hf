"""A recount must never be able to un-say "this record is incomplete".

``exam_attempts.proctoring_summary`` has two writers: ``_note_events_dropped``
sets ``events_dropped`` when the rate limiter refuses an event, and the ingest
endpoint rewrites the summary on every event it accepts. The endpoint rebuilt the
dict from the persisted counts, so the next accepted event erased the flag — and
the HR timeline went back to looking complete.

That is the flag failing in exactly the scenario it was written for: a client
that trips the limiter is, by definition, chatty, so another event follows almost
immediately. The window was usually under a second, which is why nothing caught
it: every test asserted the flag right after the refusal.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from app import exam_camera


def test_a_recount_keeps_the_dropped_flag() -> None:
    """The regression, stated directly."""
    dropped = {"counts": {"tab_blur": 1}, "violations": 1, "camera_in_use": True,
               "events_dropped": True}
    after = exam_camera.rolling_summary(
        dropped, counts={"tab_blur": 2}, camera_in_use=True
    )
    assert after["events_dropped"] is True
    assert after["counts"] == {"tab_blur": 2}, "the counts themselves still recount"


def test_the_flag_is_not_invented_where_nothing_was_dropped() -> None:
    """Carrying forward must not become asserting. A timeline wrongly marked
    incomplete tells HR to discount real signals."""
    after = exam_camera.rolling_summary(
        {"counts": {}, "violations": 0, "camera_in_use": True},
        counts={"copy": 1}, camera_in_use=True,
    )
    assert "events_dropped" not in after


@pytest.mark.parametrize("previous", [None, {}, "not a dict", 7, []])
def test_a_missing_or_malformed_previous_value_is_tolerated(previous: Any) -> None:
    """The column is JSONB and nullable, and the first event of an attempt finds
    it NULL. Raising here would 500 an ingest call, which the client swallows —
    losing the event silently."""
    after = exam_camera.rolling_summary(previous, counts={"copy": 1}, camera_in_use=False)
    assert after == {"counts": {"copy": 1}, "violations": 0, "camera_in_use": False}


def test_violations_count_only_the_violation_vocabulary() -> None:
    """gaze_away and the clipboard events are deliberately not violations — they
    must not reach the auto-submit threshold through this field."""
    after = exam_camera.rolling_summary(
        None,
        counts={"gaze_away": 9, "copy": 4, "paste": 3, "tab_blur": 1, "face_absent": 2},
        camera_in_use=True,
    )
    assert after["violations"] == 3  # tab_blur + face_absent only


def test_the_result_is_a_new_dict_not_an_edit_of_the_stored_value() -> None:
    """SQLAlchemy does not track mutations inside a JSONB value without
    MutableDict, so an in-place edit persists nothing at all."""
    previous = {"counts": {"copy": 1}, "violations": 0, "camera_in_use": True,
                "events_dropped": True}
    snapshot = {**previous, "counts": dict(previous["counts"])}
    after = exam_camera.rolling_summary(previous, counts={"copy": 2}, camera_in_use=True)
    assert previous == snapshot, "the stored value was mutated"
    assert after is not previous


def test_camera_in_use_is_taken_from_the_caller_not_carried_forward() -> None:
    """It is frozen on the attempt at /exam/start and immutable by trigger, so
    the caller's value is authoritative; carrying it forward would let a stale
    summary outvote the row."""
    after = exam_camera.rolling_summary(
        {"camera_in_use": True}, counts={}, camera_in_use=False
    )
    assert after["camera_in_use"] is False


def test_only_collection_facts_are_sticky() -> None:
    """The sticky set is about OUR recording, not about the candidate. Anything
    derived from events must recount, or a stale number would outlive the events
    it described."""
    assert exam_camera.STICKY_SUMMARY_KEYS == ("events_dropped",)
    stale = {"counts": {"tab_blur": 40}, "violations": 40, "integrity_score": 0}
    after = exam_camera.rolling_summary(stale, counts={"tab_blur": 1}, camera_in_use=True)
    assert after["violations"] == 1
    assert "integrity_score" not in after


def test_the_ingest_endpoint_does_not_build_the_summary_by_hand() -> None:
    """The call-site fix and the shared builder do different jobs: this one stops
    a future rewrite of the handler from reintroducing the bug, which is how it
    arrived the first time. Asserted structurally — the handler must assign a
    name it got from rolling_summary, never a dict literal.
    """
    from app.routers import exam_take

    source = inspect.getsource(exam_take.ingest_integrity_event)
    tree = ast.parse(inspect.cleandoc(source))
    assigned_literals = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Dict)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "proctoring_summary"
            for t in node.targets
        )
    ]
    assert not assigned_literals, (
        "proctoring_summary must come from exam_camera.rolling_summary, which "
        "carries events_dropped forward; a dict literal here erases it"
    )
    assert "rolling_summary(" in source
