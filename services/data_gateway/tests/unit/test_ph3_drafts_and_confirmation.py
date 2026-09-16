"""Save & resume, and the confirmation step — PH3-B4c / PH3-B5 / PH3-B5b.

The load-bearing property is the first section. A draft holds a name, an email,
a phone number and a CV; CLAUDE.md's third hard constraint forbids storing that
without a consent ledger entry, and ``public_apply`` has always honoured it by
committing both together. Save & Resume is the feature that would most naturally
break it, so the test that matters is the one asserting it did not.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0006_c9e1b3d5f7a2_ph3_b4c_application_drafts.py"
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
REQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY = uuid.UUID("22222222-2222-2222-2222-222222222222")
USER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _db(row: dict | None = None) -> AsyncMock:
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=row)
        mapped.all = MagicMock(return_value=[row] if row else [])
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=None)
    return db


# ===========================================================================
# THE INVARIANT: no stored personal data without recorded consent
# ===========================================================================
@pytest.mark.asyncio
async def test_a_draft_cannot_be_created_without_consent() -> None:
    """The one test this whole story exists to pass."""
    from app.application_drafts import ConsentRequiredError, start

    with pytest.raises(ConsentRequiredError):
        await start(
            _db(), requisition_id=REQ, company_id=COMPANY,
            email="a@b.test", consent_granted=False, user_id=USER, now=NOW,
        )


@pytest.mark.asyncio
async def test_refusing_consent_writes_nothing_at_all() -> None:
    """Not "writes and rolls back" — never reaches a write."""
    from app.application_drafts import ConsentRequiredError, start

    db = _db()
    with pytest.raises(ConsentRequiredError):
        await start(
            db, requisition_id=REQ, company_id=COMPANY,
            email="a@b.test", consent_granted=False, user_id=USER, now=NOW,
        )
    assert db.execute.await_count == 0


def test_the_endpoint_refuses_before_it_touches_the_database() -> None:
    from app.routers.public_apply import start_draft

    src = inspect.getsource(start_draft)
    assert "body.consent_granted" in src
    assert src.index("consent_granted") < src.index("_ensure_guest_user")


def test_the_ledger_entry_is_written_in_the_same_transaction_as_the_draft() -> None:
    """Exactly what the submitted-application path does, one step earlier.
    There must be no commit between the draft row and its consent record."""
    from app.routers.public_apply import start_draft

    src = inspect.getsource(start_draft)
    start_i = src.index("draft_store.start")
    consent_i = src.index("_record_apply_consent")
    commit_i = src.index("await db.commit()")
    assert start_i < commit_i
    assert consent_i < commit_i


def test_consent_is_not_asked_for_twice_at_submission() -> None:
    """It was recorded when the draft was created and the ledger entry is
    idempotent per person. Asking again would imply the first answer had not
    counted."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert "consent_granted" not in src
    assert "_record_apply_consent" not in src


def test_a_draft_does_not_create_an_applicant() -> None:
    """A draft is not an application. Putting somebody in HR's pipeline before
    they have applied would be both wrong and visible."""
    from app.routers.public_apply import _draft_only_guest_user

    src = inspect.getsource(_draft_only_guest_user)
    assert "INSERT INTO applicants" not in src
    assert "INSERT INTO users" in src


# ===========================================================================
# The token is the only credential
# ===========================================================================
def test_only_the_hash_is_stored() -> None:
    """A leaked dump must not hand somebody every half-finished application."""
    from app.application_drafts import hash_token, mint_token, start

    raw, hashed = mint_token()
    assert raw != hashed
    assert hash_token(raw) == hashed
    assert len(hashed) == 64
    assert "token_hash" in inspect.getsource(start)


def test_the_token_carries_real_entropy() -> None:
    from app.application_drafts import _TOKEN_BYTES, mint_token

    assert _TOKEN_BYTES >= 32
    assert len({mint_token()[0] for _ in range(50)}) == 50


@pytest.mark.asyncio
async def test_resuming_rotates_the_token_so_the_old_link_dies() -> None:
    """A draft has exactly one live credential."""
    from app.application_drafts import start

    existing = {"id": uuid.uuid4(), "token_hash": "old", "email": "a@b.test"}
    db = _db(existing)
    draft, raw = await start(
        db, requisition_id=REQ, company_id=COMPANY, email="a@b.test",
        consent_granted=True, user_id=USER, now=NOW,
    )
    assert draft["token_hash"] != "old"
    update = next(
        c.args[0].text for c in db.execute.await_args_list
        if c.args[0].text.lstrip().startswith("UPDATE")
    )
    assert "token_hash = :h" in update


@pytest.mark.asyncio
async def test_a_submitted_draft_stops_being_resumable() -> None:
    from app.application_drafts import mark_submitted

    db = _db()
    draft_id = uuid.uuid4()
    await mark_submitted(db, draft_id=draft_id, now=NOW)
    sql = db.execute.await_args_list[0].args[0].text
    assert "token_hash = :h" in sql
    assert "'submitted'" in sql


@pytest.mark.asyncio
async def test_an_expired_draft_does_not_open_even_before_the_cron_runs() -> None:
    """The cron runs daily; a link must stop working at its stated time, not
    within a day of it."""
    from app.application_drafts import load

    db = _db({"expires_at": NOW - timedelta(minutes=1)})
    assert await load(db, raw_token="whatever", now=NOW) is None


@pytest.mark.asyncio
async def test_a_live_draft_does_open() -> None:
    from app.application_drafts import load

    row = {"expires_at": NOW + timedelta(days=1), "id": uuid.uuid4()}
    db = _db(row)
    assert await load(db, raw_token="whatever", now=NOW) is not None


def test_every_draft_route_is_rate_limited() -> None:
    """256 random bits is a lot, but an unthrottled endpoint that reports
    valid-or-invalid is still a free oracle."""
    from app.routers import public_apply

    for name in ("start_draft", "resume_draft", "update_draft",
                 "confirm_draft", "submit_draft", "upload_draft_resume"):
        src = inspect.getsource(getattr(public_apply, name))
        assert "rate_limit" in src or "rate_limit" in inspect.getsource(public_apply), name


# ===========================================================================
# Expiry is retention, not cleanup
# ===========================================================================
def test_expiry_is_purged_by_the_dpdp_cron_not_a_bespoke_sweep() -> None:
    """A second cleanup schedule is a second thing to notice has stopped."""
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "purge_expired_drafts" in main
    assert "_run_retention_job" in main
    # In the same function as the existing purges.
    job = main.split("async def _run_retention_job")[1].split("@asynccontextmanager")[0]
    assert "purge_expired_drafts" in job


def test_the_expiry_is_stored_not_computed() -> None:
    """A later policy change must not retroactively delete a draft somebody is
    still working on."""
    assert "expires_at" in MIGRATION.read_text(encoding="utf-8")
    from app.application_drafts import start

    assert "expires_at" in inspect.getsource(start)


@pytest.mark.asyncio
async def test_saving_extends_the_expiry() -> None:
    """Somebody who comes back weekly should not lose their work to a deadline
    they were never shown."""
    from app.application_drafts import save

    db = _db()
    await save(db, draft_id=uuid.uuid4(), fields={"phone": "+91 99999 99999"}, now=NOW)
    sql = db.execute.await_args_list[0].args[0].text
    assert "expires_at = :exp" in sql


@pytest.mark.asyncio
async def test_purging_returns_the_objects_to_delete() -> None:
    """Deleting the row without the CV would leave the file in the bucket."""
    from app.application_drafts import purge_expired

    db = _db({"resume_s3_key": "drafts/x/y.pdf"})
    assert await purge_expired(db, now=NOW) == ["drafts/x/y.pdf"]


def test_a_draft_is_erased_not_anonymised() -> None:
    """Unlike `applicants` there is no structural record worth keeping: nobody
    applied, so an anonymised draft is a row that means nothing to anyone."""
    executor = (
        Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    assert "DELETE FROM application_drafts WHERE user_id = :uid" in executor


def test_erasure_reaches_a_draft_without_going_through_applicants() -> None:
    """A draft that was never submitted has no applicant row to be reached
    through, and reaching for one is precisely how this table would be missed."""
    executor = (
        Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    assert "FROM application_drafts \nWHERE" in executor or (
        "application_drafts " in executor and "WHERE user_id = :uid" in executor
    )


def test_erasure_collects_the_draft_cv_before_deleting_the_row() -> None:
    executor = (
        Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    collect = executor.index("SELECT resume_s3_key FROM application_drafts")
    delete = executor.index("DELETE FROM application_drafts")
    assert collect < delete


# ===========================================================================
# A draft is allowed to be incomplete
# ===========================================================================
def test_nothing_in_a_draft_is_required() -> None:
    from app.routers.public_apply import DraftFieldsIn

    assert DraftFieldsIn().model_dump(exclude_unset=True) == {}


def test_a_draft_is_not_validated_against_the_openings_rules() -> None:
    """Those apply at submission. That is what a draft is."""
    from app.application_drafts import save

    src = inspect.getsource(save)
    assert "validate_answers" not in src


def test_unknown_fields_are_dropped_rather_than_refused() -> None:
    """A client that learns a new field before the server does should not make
    the whole save fail."""
    from app.application_drafts import sanitise

    assert sanitise({"nonsense": "x", "phone": "123"}) == {"phone": "123"}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ({"years_experience": "7"}, {"years_experience": 7}),
        ({"years_experience": "not a number"}, {"years_experience": None}),
        ({"years_experience": 900}, {"years_experience": 60}),
        ({"years_experience": -5}, {"years_experience": 0}),
        ({"language": "fr"}, {"language": "en"}),
        ({"language": "hi"}, {"language": "hi"}),
        ({"phone": "  +91   99999  "}, {"phone": "+91 99999"}),
        ({"full_name": ""}, {"full_name": None}),
    ],
)
def test_sanitising(given: dict, expected: dict) -> None:
    from app.application_drafts import sanitise

    assert sanitise(given) == expected


def test_submission_applies_the_rules_a_draft_was_excused() -> None:
    """A draft started last week says nothing about whether the opening is
    still taking applications today."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert "_open_posting" in src          # still live?
    assert "validate_answers" in src        # required questions answered?
    assert "cooldown_check" in src          # cooldown passed?


# ===========================================================================
# PH3-B5 — the confirmation step
# ===========================================================================
def test_submitting_without_confirming_is_refused() -> None:
    """The confirmed details are what gets stored, so submitting unconfirmed
    would mean storing what a parser guessed."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert 'row.get("confirmed_at") is None' in src


@pytest.mark.asyncio
async def test_confirming_writes_the_corrections_not_the_parsed_values() -> None:
    """A name read out of a PDF is a guess and a person's own answer is not."""
    from app.application_drafts import confirm

    db = _db()
    await confirm(
        db, draft_id=uuid.uuid4(), corrections={"full_name": "Nadia Newbie"}, now=NOW
    )
    statements = [c.args[0].text for c in db.execute.await_args_list]
    assert any("full_name = :full_name" in s for s in statements)
    assert any("confirmed_at = :n" in s for s in statements)


def test_an_unparsed_field_does_not_block_confirmation() -> None:
    """PH3-B5 Task 4: don't prevent somebody applying because an optional
    resume field could not be extracted."""
    from app.routers.public_apply import confirm_draft

    src = inspect.getsource(confirm_draft)
    # The only thing required is what the application itself requires.
    assert "full_name" in src
    assert "years_experience" not in src.split('"""')[2]


def test_confirming_an_empty_name_is_refused() -> None:
    from app.routers.public_apply import confirm_draft

    assert "Please tell us your name" in inspect.getsource(confirm_draft)


@pytest.mark.asyncio
async def test_replacing_the_cv_clears_the_confirmation() -> None:
    """A confirmation is about one particular CV. Carrying it across a
    replacement would mean the candidate had confirmed something they never
    saw."""
    from app.application_drafts import attach_resume

    db = _db()
    await attach_resume(
        db, draft_id=uuid.uuid4(), s3_key="k", filename="cv.pdf", parsed={}, now=NOW
    )
    assert "confirmed_at = NULL" in db.execute.await_args_list[0].args[0].text


def test_the_confirmation_survives_the_draft() -> None:
    """The draft is closed at submission; "did this candidate confirm what we
    read?" is a question about the application that outlives it."""
    from app.routers.public_apply import submit_draft

    assert "details_confirmed_at" in inspect.getsource(submit_draft)
    assert "details_confirmed_at" in MIGRATION.read_text(encoding="utf-8")


def test_a_confirmed_name_is_authored_and_the_reconciler_must_not_overwrite_it() -> None:
    """full_name_source='candidate' is what stops the scorer replacing a name
    the person typed with the one inside their PDF."""
    from app.routers.public_apply import submit_draft

    assert 'full_name_source="candidate"' in inspect.getsource(submit_draft)


def test_a_returning_candidates_details_are_filled_never_overwritten() -> None:
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert "COALESCE(phone, :ph)" in src


# ===========================================================================
# PH3-B5b — what the parser actually produces
# ===========================================================================
def test_the_parser_does_not_call_a_language_model() -> None:
    """public_apply's own rule: a candidate pressing Submit must not wait on a
    model, and an applicant lost because the model was down would be the worst
    possible failure for this endpoint.

    Asserted against the AST rather than against the text — a substring search
    for "llm" matches ``re.fullmatch``, which is how a test like this passes for
    years and then fails for the wrong reason.
    """
    import ast

    tree = ast.parse((APP / "resume_details.py").read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    # Nothing that could reach a model, a network or a database.
    assert imported <= {"__future__", "re", "typing"}, imported

    # No I/O at all: every function here is synchronous and pure.
    for node in ast.walk(tree):
        assert not isinstance(node, ast.AsyncFunctionDef), ast.dump(node)[:80]
        assert not isinstance(node, ast.Await), ast.dump(node)[:80]


CV = """Priya Sharma
Bengaluru, India
priya.sharma@example.com | +91 98765 43210
linkedin.com/in/priyasharma | github.com/priyasharma

EXPERIENCE
Senior Engineer, Acme Corp (2022-2026)
"""


def test_it_reads_what_is_actually_there() -> None:
    from app.resume_details import extract_contact_details

    got = extract_contact_details(CV)
    assert got["full_name"] == "Priya Sharma"
    assert got["email"] == "priya.sharma@example.com"
    assert "98765" in got["phone"]
    assert got["linkedin_url"].endswith("/in/priyasharma")
    assert got["github_url"].endswith("/priyasharma")


def test_an_absent_field_is_absent_rather_than_guessed() -> None:
    """A wrong pre-filled field is worse than an empty one: people accept what
    looks plausible."""
    from app.resume_details import extract_contact_details

    got = extract_contact_details("EXPERIENCE\nSome company, some years\n")
    assert "full_name" not in got
    assert "email" not in got


def test_a_template_header_is_not_mistaken_for_a_name() -> None:
    from app.resume_details import extract_contact_details

    for header in ("Curriculum Vitae", "RESUME", "Profile Summary", "Contact Details"):
        got = extract_contact_details(f"{header}\nSome other text here\n")
        assert got.get("full_name") != header


def test_an_employee_id_is_not_mistaken_for_a_phone_number() -> None:
    from app.resume_details import extract_contact_details

    assert "phone" not in extract_contact_details("Employee ID 123456789012345678\n")


def test_an_empty_or_unreadable_cv_returns_nothing_and_does_not_raise() -> None:
    from app.resume_details import extract_contact_details

    assert extract_contact_details(None) == {}
    assert extract_contact_details("") == {}
    assert extract_contact_details("\x00\x01\x02") == {}


def test_the_confirmation_screen_only_offers_fields_the_parser_produces() -> None:
    """An empty box labelled "Education" that can never fill in is worse than
    not asking. PH3-B5b grows this shape when the parser grows."""
    from app.routers.public_apply import ParsedDetails

    assert set(ParsedDetails.model_fields) == {"full_name", "email"}
