"""The erasure executor's step ORDER is load-bearing, and nothing asserted it.

Two orderings inside `_execute_one_erasure` carry correctness, and both are the kind of
thing a later refactor reorders without noticing, because each step reads fine
on its own:

1. Step 5e (delete application_drafts) matches partly on the erased user's
   ADDRESS. Step 7 overwrites `users.email` with `erased_{uid}@deleted.invalid`.
   Run 7 first and that route matches nothing, so a draft whose `user_id` was
   re-pointed away — or never pointed here — survives a completed erasure with
   the person's name, phone, employer and CV in it. That exact failure already
   happened once, via activation-linking, and is what step 5e was added for.

2. Every object key is collected BEFORE the rows that hold them are deleted.
   The module docstring says so at length, because it was once the other way
   round and orphaned every superseded resume in R2 while stamping
   `status='completed'`.

Asserted against the source order rather than by running the executor: this is a
property of how the function is written, and a test that needed a live Postgres,
an S3 stub and a seeded erasure request to notice a moved block would not be run
often enough to protect it.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1] / "app" / "erasure_executor.py"
).read_text(encoding="utf-8")


def _body() -> str:
    """The executor's per-request function, without the module docstring — which
    describes the ordering and would otherwise satisfy every match below."""
    from app.erasure_executor import _execute_one_erasure

    return inspect.getsource(_execute_one_erasure)


def _at(needle: str, *, where: str | None = None) -> int:
    haystack = where if where is not None else _body()
    assert needle in haystack, f"anchor vanished from the executor: {needle!r}"
    return haystack.index(needle)


# ===========================================================================
# 1. The address route must still work when it runs
# ===========================================================================
def test_drafts_are_deleted_before_the_email_is_anonymised() -> None:
    """Step 5e matches on users.email; step 7 destroys it."""
    body = _body()
    delete_drafts = _at("DELETE FROM application_drafts", where=body)
    anonymise_email = _at("erased_email_sentinel", where=body)
    assert delete_drafts < anonymise_email, (
        "step 5e now runs AFTER users.email is overwritten, so its address "
        "route matches nothing and a re-pointed draft survives erasure"
    )


def test_the_draft_delete_really_does_depend_on_the_address() -> None:
    """If this stops being true the test above is guarding nothing."""
    body = _body()
    delete = body[_at("DELETE FROM application_drafts", where=body):][:1200]
    assert "lower(btrim(d.email))" in delete
    assert "FROM users u" in delete


# ===========================================================================
# 2. Keys before rows, everywhere
# ===========================================================================
@pytest.mark.parametrize(
    ("collect", "delete", "what"),
    [
        ("SELECT d.resume_s3_key FROM application_drafts",
         "DELETE FROM application_drafts", "a draft's CV"),
        ("SELECT resume_s3_key FROM applicants",
         "UPDATE applicants", "an applicant's CV"),
    ],
)
def test_object_keys_are_collected_before_their_rows_go(
    collect: str, delete: str, what: str
) -> None:
    """Deleting the row first orphans the object in the bucket and still stamps
    the request completed — a false §12 completion claim, which the module
    docstring calls worse than an incomplete erasure that reports itself."""
    body = _body()
    assert _at(collect, where=body) < _at(delete, where=body), what


def test_the_three_draft_routes_are_genuinely_different() -> None:
    """One of them used to be `d.user_id IN (SELECT a.user_id FROM applicants
    WHERE a.user_id = :uid)` — algebraically just `d.user_id = :uid`, so the
    inventory claimed three routes and the SQL had two."""
    delete = SOURCE[SOURCE.index("DELETE FROM application_drafts"):][:1600]
    assert "d.user_id = :uid" in delete
    assert "FROM users u" in delete
    assert "FROM applicants a" in delete
    # The tell-tale no-op shape must not come back.
    assert "SELECT a.user_id FROM applicants" not in delete


# ===========================================================================
# 3. Human interview evidence (step 5f, PH4-A1 / PH4-A5)
# ===========================================================================
def test_open_interview_assignments_are_closed_before_prose_is_redacted() -> None:
    """Withdraw first: an open scorecard is a place new prose about the person
    could be written after the erasure reports "completed"."""
    body = _body()
    withdraw = _at("SET status = 'withdrawn'", where=body)
    redact = _at("UPDATE interviewer_scorecards SET summary = NULL", where=body)
    assert withdraw < redact


def test_interview_evidence_is_reached_before_applicants_lose_their_user_id() -> None:
    """Every 5f join goes through applicants.user_id, which step 6 nulls."""
    body = _body()
    anonymise = _at("UPDATE applicants", where=body)
    for needle in ("SET status = 'withdrawn'",
                   "UPDATE interviewer_scorecard_scores SET evidence = NULL",
                   "UPDATE interviewer_scorecards SET summary = NULL",
                   "DELETE FROM interviewer_notes"):
        assert _at(needle, where=body) < anonymise, needle


def test_every_free_text_column_on_a_scorecard_is_redacted() -> None:
    body = _body()
    redact = body[_at("UPDATE interviewer_scorecards SET summary = NULL", where=body):][:700]
    assert "correction_reason = CASE" in redact and "withdrawn_reason = CASE" in redact
    assert "'[redacted]'" in redact


def test_the_steps_read_in_order() -> None:
    """Code review: 5f used to sit between 5c and 5d."""
    body = _body()
    positions = [_at(f"# Step {step}:", where=body)
                 for step in ("5b", "5c", "5d", "5e", "5f", "5g", "5k", "6")]
    assert positions == sorted(positions)


def test_step_5f_counts_reach_the_completion_record() -> None:
    body = _body()
    for key in ("interview_assignments_withdrawn", "interview_evidence_redacted",
                "interview_scorecards_redacted", "interviewer_notes_deleted",
                "stage_exceptions_redacted", "interview_sessions_cancelled",
                "interview_loops_redacted", "offers_redacted",
                "preboarding_documents_redacted"):
        assert body.count(f'"{key}": {key}') == 2, key  # artifacts AND audit row


def test_stage_exception_prose_is_redacted_before_applicants_lose_their_user_id() -> None:
    """PH4-O1: an exception's reason is prose about the candidate."""
    body = _body()
    redact = _at("UPDATE stage_exceptions SET reason = '[redacted]'", where=body)
    assert redact < _at("UPDATE applicants", where=body)


def test_interview_sessions_are_cancelled_before_applicants_lose_their_user_id() -> None:
    """PH4-A2: sessions and loops are found through applicants.user_id, which
    step 6 does not clear, but ordering them inside 5f keeps the step whole."""
    body = _body()
    cancel = _at("UPDATE interview_sessions SET status = 'cancelled'", where=body)
    loops = _at("UPDATE interview_loops SET", where=body)
    assert cancel < loops < _at("UPDATE applicants", where=body)


# ===========================================================================
# 4. Candidate accommodations (step 5g, PH4-D2)
# ===========================================================================
def test_step_5g_counts_reach_the_completion_record() -> None:
    body = _body()
    for key in ("accommodations_revoked", "accommodations_redacted"):
        assert body.count(f'"{key}": {key}') == 2, key  # artifacts AND audit row


def test_accommodations_are_revoked_before_applicants_lose_their_user_id() -> None:
    """5g's join goes through applicants.user_id, which step 6 nulls."""
    body = _body()
    revoke = _at("UPDATE candidate_accommodations SET status = 'revoked'", where=body)
    redact = _at("interviewer_note = NULL, internal_note = NULL, redacted_at = now()",
                where=body)
    anonymise = _at("UPDATE applicants", where=body)
    assert revoke < redact < anonymise


# ===========================================================================
# 5. 90-day hire check-ins (step 5j, PH5-D5-2) — deleted outright, not redacted
# ===========================================================================
def test_hire_checkins_are_deleted_before_applicants_lose_their_user_id() -> None:
    """5j's join reaches hire_checkins through enrolments.applicant_id ->
    applicants.user_id; hire_checkins itself has no applicant_id column."""
    body = _body()
    delete_checkins = _at("DELETE FROM hire_checkins", where=body)
    anonymise = _at("UPDATE applicants", where=body)
    assert delete_checkins < anonymise


def test_hire_checkins_delete_matches_through_enrolments() -> None:
    """There is no hire_checkins.applicant_id; the match must go through the
    enrolment, not a column this table does not have."""
    delete = SOURCE[SOURCE.index("DELETE FROM hire_checkins"):][:400]
    assert "FROM enrolments e" in delete
    assert "JOIN applicants a ON a.id = e.applicant_id" in delete
    assert "a.user_id = :uid" in delete


def test_hire_checkins_count_reaches_the_completion_record() -> None:
    body = _body()
    assert body.count('"hire_checkins_deleted": hire_checkins_deleted') == 1


# ===========================================================================
# 6. Talent pool memberships (step 5k, PH5-E3) — deleted outright, before the
#    applicants row it is keyed on is anonymised
# ===========================================================================
def test_talent_pool_members_are_deleted_before_applicants_lose_their_user_id() -> None:
    """5k keys on applicants.user_id directly; step 6 NULLs it — the same
    ordering hazard application_drafts records for users.email above."""
    body = _body()
    delete_members = _at("DELETE FROM talent_pool_members", where=body)
    anonymise = _at("UPDATE applicants", where=body)
    assert delete_members < anonymise


def test_talent_pool_members_delete_matches_through_applicants_user_id() -> None:
    """There is no talent_pool_members.user_id column of its own — the match
    must go through applicants, on the applicant_id foreign key."""
    delete = SOURCE[SOURCE.index("DELETE FROM talent_pool_members"):][:400]
    assert "applicant_id IN" in delete
    assert "FROM applicants WHERE user_id = :uid" in delete


def test_talent_pool_members_count_reaches_the_completion_record() -> None:
    body = _body()
    assert body.count(
        '"talent_pool_members_deleted": talent_pool_members_deleted'
    ) == 1


def test_talent_pool_members_deleted_before_the_hire_checkins_that_precede_it() -> None:
    """Step 5k sits immediately after step 5j in the docstring's own step
    list (§ the module ordering) — a code-review regression that moved it
    ahead of 5j would still be correct (order among 5-lettered steps does not
    matter to each other, only to step 6), but moving it BEFORE step 5 proper
    (turns/resumes/scorecards/sessions, which do not touch applicants at all)
    would be harmless too. What must never happen is 5k landing after step 6,
    which the first test in this section already guards; this one additionally
    pins 5k to sit after 5j specifically, matching the docstring's own
    numbering, so a reader of the code and a reader of the docstring agree."""
    body = _body()
    hire_checkins = _at("DELETE FROM hire_checkins", where=body)
    pool_members = _at("DELETE FROM talent_pool_members", where=body)
    assert hire_checkins < pool_members
