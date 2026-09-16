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
    # The refusal comes before any identity is minted and before any row is
    # written. _draft_only_guest_user is now the first write in the handler.
    assert src.index("consent_granted") < src.index("_draft_only_guest_user")


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


def test_the_candidate_is_not_asked_for_consent_twice() -> None:
    """It was recorded at the first save and the ledger entry is idempotent per
    person. Asking again would imply the first answer had not counted."""
    from app.routers.public_apply import submit_draft

    assert "consent_granted" not in inspect.getsource(submit_draft)


def test_consent_is_also_recorded_against_the_identity_that_owns_the_application() -> None:
    """The draft's ledger row hangs off the throwaway guest identity that
    created it. Without this, an audit that looks a person up by their real
    user id would not find their consent."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert "_record_apply_consent" in src
    assert "owner_user_id" in src


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


# ===========================================================================
# An email is NOT an authenticator — the security fix
#
# start_draft used to resolve the email in an unauthenticated request body to
# an existing identity, and if that person had a live draft it rotated the
# token and returned the contents. Anyone with the apply link (not a secret)
# and a candidate's address could read their name, phone, employer, answers and
# CV filename, get a working token, alter the draft, replace the CV and submit
# in their name — one request, and the victim's own link died silently.
#
# These assert the shape that makes that impossible, not the symptom.
# ===========================================================================
@pytest.mark.asyncio
async def test_starting_a_draft_never_reads_an_existing_one() -> None:
    """No SELECT against application_drafts at all — there is nothing to find,
    and no code path that could return somebody else's row."""
    from app.application_drafts import start

    db = _db(None)
    await start(
        db, requisition_id=REQ, company_id=COMPANY, email="a@b.test",
        consent_granted=True, user_id=USER, now=NOW,
    )
    for call in db.execute.await_args_list:
        sql = call.args[0].text
        assert "SELECT" not in sql.upper(), sql


@pytest.mark.asyncio
async def test_starting_a_draft_never_rotates_an_existing_token() -> None:
    """Rotating on an unverified request was both the takeover and a denial of
    service against the real owner's link."""
    from app.application_drafts import start

    db = _db(None)
    await start(
        db, requisition_id=REQ, company_id=COMPANY, email="a@b.test",
        consent_granted=True, user_id=USER, now=NOW,
    )
    for call in db.execute.await_args_list:
        assert "UPDATE application_drafts" not in call.args[0].text


def test_identity_is_never_resolved_from_the_request_body() -> None:
    """The handler must not look anybody up by the address it was handed."""
    from app.routers.public_apply import _draft_only_guest_user, start_draft

    handler = inspect.getsource(start_draft)
    assert "SELECT id FROM applicants" not in handler
    assert "_ensure_guest_user" not in handler

    minter = inspect.getsource(_draft_only_guest_user)
    # It does not even accept an email any more, so there is nothing to match on.
    assert "email" not in inspect.signature(_draft_only_guest_user).parameters
    # No read of any table an identity could be recovered from. (It does SELECT
    # from `roles` to grant guest_candidate — reference data, keyed by a literal
    # role name, with nothing of anyone's in it.)
    for table in ("applicants", "application_drafts"):
        assert f"FROM {table}" not in minter, table
    assert "FROM users" not in minter


def test_every_draft_gets_a_fresh_identity() -> None:
    from app.routers.public_apply import _draft_only_guest_user

    src = inspect.getsource(_draft_only_guest_user)
    assert "user_id = uuid.uuid4()" in src
    assert "INSERT INTO users" in src


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


def test_the_draft_routes_are_declared_before_the_parameterised_ones() -> None:
    """FastAPI matches in declaration order and `/apply/{requisition_id}`
    happily matches the literal string "draft".

    With the draft block below it, `GET /apply/draft` was served by get_posting
    with requisition_id="draft" — a 500, and it fired get_posting's rate-limit
    bucket. The module docstring had already warned about exactly this for
    /apply/activate. This asserts the order so the warning is enforced rather
    than repeated.
    """
    from app.routers.public_apply import router

    paths = [r.path for r in router.routes]
    first_parameterised = min(
        i for i, p in enumerate(paths) if "{requisition_id}" in p and p.count("/") == 2
    )
    for i, path in enumerate(paths):
        if path.startswith("/apply/draft"):
            assert i < first_parameterised, (
                f"{path} is declared after /apply/{{requisition_id}} and will be "
                f"shadowed by it"
            )


def test_the_resume_token_is_never_in_the_url() -> None:
    """A live credential to somebody's name, phone, employer, answers and CV
    must not land in uvicorn's access log, the edge logs, browser history or a
    cross-origin Referer. This is the pattern exam_take and interview_take
    already use, and exam_take's docstring says "never the URL path/query".
    """
    from app.routers.public_apply import router

    for route in router.routes:
        assert "{token}" not in route.path, route.path


def test_a_missing_token_header_is_indistinguishable_from_a_bad_one() -> None:
    """Otherwise the difference is an oracle."""
    from app.routers.public_apply import _NO_SUCH_DRAFT, _draft_token

    assert _NO_SUCH_DRAFT.status_code == 404
    assert "_NO_SUCH_DRAFT" in inspect.getsource(_draft_token)


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

    # One row on the first pass, none after — the drain loop stops when a pass
    # comes back short, so the fake must eventually return nothing.
    db = AsyncMock()
    batches = [[{"resume_s3_key": "drafts/x/y.pdf", "user_id": uuid.uuid4(),
                 "status": "draft"}]]

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.all = MagicMock(return_value=batches.pop(0) if batches else [])
        res.mappings = MagicMock(return_value=mapped)
        res.rowcount = 0
        return res

    db.execute = AsyncMock(side_effect=_execute)
    assert await purge_expired(db, now=NOW) == ["drafts/x/y.pdf"]


@pytest.mark.asyncio
async def test_purging_drains_rather_than_stopping_at_the_limit() -> None:
    """A single capped pass could not keep up with the rate start_draft creates
    rows — which turns a retention control into a slowly losing race."""
    from app.application_drafts import purge_expired

    db = AsyncMock()
    full = [
        {"resume_s3_key": f"k{i}.pdf", "user_id": uuid.uuid4(), "status": "draft"}
        for i in range(2)
    ]
    batches = [list(full), list(full), []]

    # Dispatches on the statement: each pass issues TWO deletes (drafts, then
    # the identities they anchored), and a queue shared between them would be
    # drained twice per pass and make the loop look like it stopped early.
    async def _execute(statement: object, *_a: object, **_k: object) -> MagicMock:
        sql = getattr(statement, "text", str(statement))
        # Matched on the statement HEAD: the identity cleanup also mentions
        # application_drafts, in a NOT EXISTS subquery.
        is_draft_delete = sql.lstrip().startswith("DELETE FROM application_drafts")
        rows = (batches.pop(0) if batches else []) if is_draft_delete else []
        res = MagicMock()
        mapped = MagicMock()
        mapped.all = MagicMock(return_value=rows)
        res.mappings = MagicMock(return_value=mapped)
        res.rowcount = 0
        return res

    db.execute = AsyncMock(side_effect=_execute)
    keys = await purge_expired(db, now=NOW, limit=2)
    assert len(keys) == 4, "stopped after one pass instead of draining"


@pytest.mark.asyncio
async def test_purging_also_clears_the_identities_the_drafts_anchored() -> None:
    """Every start_draft mints a guest user and a consent row, unauthenticated
    and unbounded. A guest with no applicant and no draft represents nobody."""
    from app.application_drafts import purge_expired

    db = AsyncMock()
    batches = [[{"resume_s3_key": None, "user_id": uuid.uuid4(), "status": "draft"}]]

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.all = MagicMock(return_value=batches.pop(0) if batches else [])
        res.mappings = MagicMock(return_value=mapped)
        res.rowcount = 1
        return res

    db.execute = AsyncMock(side_effect=_execute)
    await purge_expired(db, now=NOW)
    statements = [c.args[0].text for c in db.execute.await_args_list]
    cleanup = next(s for s in statements if "DELETE FROM users" in s)
    # Only ever a guest placeholder, and only when nothing else needs it.
    assert "guest+%@applicants.invalid" in cleanup
    assert "FROM applicants a WHERE a.user_id = u.id" in cleanup
    assert "erasure_requests" in cleanup


@pytest.mark.asyncio
async def test_submitted_drafts_do_not_live_forever() -> None:
    """The docstring claimed a retention window; no code enforced one, so a
    duplicate of somebody's name, phone and CV key was kept indefinitely."""
    from app.application_drafts import SUBMITTED_RETENTION_DAYS, purge_expired

    assert SUBMITTED_RETENTION_DAYS > 0
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.all = MagicMock(return_value=[])
        res.mappings = MagicMock(return_value=mapped)
        res.rowcount = 0
        return res

    db.execute = AsyncMock(side_effect=_execute)
    await purge_expired(db, now=NOW)
    sql = db.execute.await_args_list[0].args[0].text
    assert "status = 'submitted'" in sql
    assert "submitted_at <= :cutoff" in sql


def test_a_draft_is_erased_not_anonymised() -> None:
    """Unlike `applicants` there is no structural record worth keeping: nobody
    applied, so an anonymised draft is a row that means nothing to anyone."""
    executor = (
        Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    assert "DELETE FROM application_drafts" in executor


def test_erasure_reaches_a_draft_by_more_than_one_route() -> None:
    """user_id alone was not enough. When a guest activates into an account
    they already had, apply_activation re-points the draft — and a draft
    written before that repair existed, or one whose re-point failed, would be
    invisible to an executor that matched on user_id only, survive a completed
    erasure, and keep its CV in the bucket."""
    executor = (
        Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    delete = executor[executor.index("DELETE FROM application_drafts"):][:800]
    assert "d.user_id = :uid" in delete
    assert "lower(btrim(d.email))" in delete


def test_activation_repoints_the_draft_to_the_real_account() -> None:
    """The other half: the repair itself, so the executor's user_id route also
    keeps working."""
    src = (
        Path(__file__).resolve().parents[2] / "app" / "apply_activation.py"
    ).read_text(encoding="utf-8")
    assert "UPDATE application_drafts SET user_id = :new" in src


def test_erasure_collects_the_draft_cv_before_deleting_the_row() -> None:
    executor = (
        Path(__file__).resolve().parents[4]
        / "services" / "admin_ops" / "app" / "erasure_executor.py"
    ).read_text(encoding="utf-8")
    collect = executor.index("SELECT d.resume_s3_key FROM application_drafts")
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


def test_a_returning_applicants_record_is_not_touched_at_all() -> None:
    """The submission is unauthenticated: the only thing tying it to this
    person is an address anybody can type.

    This path used to UPDATE their applicants row — overwriting full_name and
    stamping full_name_source='candidate' plus details_confirmed_at, which
    assert to the reconciler and to HR that the real person confirmed those
    values. So a stranger could rename somebody in a company's ATS, back-fill
    their empty fields and give it false provenance.

    The one-shot path refuses this and its own comment records it as a fixed
    bug; the draft path had re-introduced it. Now neither writes.
    """
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert "UPDATE applicants" not in src


def test_only_a_brand_new_applicant_row_is_ever_written() -> None:
    """The one INSERT is guarded by is_new_person, so there is no branch in
    which an existing person's record is modified."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert "if is_new_person:" in src
    # The confirmed provenance is only ever set on a row this submission
    # created, never applied to one that already existed.
    assert src.index("if is_new_person:") < src.index('full_name_source="candidate"')


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


# ===========================================================================
# The retention pass must not delete a LIVE applicant's CV
#
# submit_draft does not copy the uploaded file: it points
# applicants.resume_s3_key and enrolments.applied_resume_s3_key at the draft's
# own key. So once a draft is submitted that object IS the application's CV.
# The 90-day purge of submitted draft ROWS was queuing that object for hard
# deletion too, which silently destroyed the CV of everybody who used Save &
# Resume, ninety days after they applied, while both rows still pointed at it.
# ===========================================================================
def _purge_db(rows: list[dict]) -> AsyncMock:
    db = AsyncMock()
    batches = [rows]

    async def _execute(statement: object, *_a: object, **_k: object) -> MagicMock:
        sql = getattr(statement, "text", str(statement))
        is_draft_delete = sql.lstrip().startswith("DELETE FROM application_drafts")
        out = (batches.pop(0) if batches else []) if is_draft_delete else []
        res = MagicMock()
        mapped = MagicMock()
        mapped.all = MagicMock(return_value=out)
        res.mappings = MagicMock(return_value=mapped)
        res.rowcount = 0
        return res

    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.mark.asyncio
async def test_an_abandoned_drafts_cv_is_deleted() -> None:
    from app.application_drafts import purge_expired

    db = _purge_db([
        {"resume_s3_key": "drafts/c/abandoned.pdf", "user_id": uuid.uuid4(),
         "status": "draft"},
    ])
    assert await purge_expired(db, now=NOW) == ["drafts/c/abandoned.pdf"]


@pytest.mark.asyncio
async def test_a_submitted_applications_cv_is_never_deleted() -> None:
    """The row is a duplicate. The object is the applicant's live CV."""
    from app.application_drafts import purge_expired

    db = _purge_db([
        {"resume_s3_key": "drafts/c/submitted.pdf", "user_id": uuid.uuid4(),
         "status": "submitted"},
    ])
    assert await purge_expired(db, now=NOW) == []


@pytest.mark.asyncio
async def test_a_mixed_batch_deletes_only_the_abandoned_objects() -> None:
    from app.application_drafts import purge_expired

    db = _purge_db([
        {"resume_s3_key": "keep.pdf", "user_id": uuid.uuid4(), "status": "submitted"},
        {"resume_s3_key": "drop.pdf", "user_id": uuid.uuid4(), "status": "draft"},
    ])
    assert await purge_expired(db, now=NOW) == ["drop.pdf"]


def test_the_purge_asks_for_the_status_it_filters_on() -> None:
    """If RETURNING ever loses `status`, the filter silently keeps nothing —
    or, worse, a later edit drops the filter and restores the data loss."""
    from app.application_drafts import purge_expired

    src = inspect.getsource(purge_expired)
    assert "RETURNING resume_s3_key, user_id, status" in src


def test_submit_reuses_the_drafts_key_rather_than_copying_it() -> None:
    """This is WHY the filter above has to exist. If submission ever starts
    copying the object instead, the filter becomes unnecessary — but until
    then, removing it destroys live CVs."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    assert src.count('resume_s3_key=row["resume_s3_key"]') == 2


# ===========================================================================
# A withdrawal is sticky against everyone but its owner
# ===========================================================================
def _consent_db(prior: bool | None) -> AsyncMock:
    """prior: None = no row ever, True = withdrawn, False = active grant."""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=prior)
    db.execute = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_a_first_time_consent_is_recorded() -> None:
    from app.routers.public_apply import _record_apply_consent

    db = _consent_db(None)
    await _record_apply_consent(
        db, request=MagicMock(headers={}, client=None), user_id=USER,
        applicant_id=uuid.uuid4(), company_id=COMPANY, requisition_id=REQ, now=NOW,
    )
    assert any(
        "INSERT INTO dpdp_consent_ledger" in c.args[0].text
        for c in db.execute.await_args_list
    ), "a first grant was not written"


@pytest.mark.asyncio
async def test_an_active_consent_is_not_recorded_twice() -> None:
    from app.routers.public_apply import _record_apply_consent

    db = _consent_db(False)
    await _record_apply_consent(
        db, request=MagicMock(headers={}, client=None), user_id=USER,
        applicant_id=uuid.uuid4(), company_id=COMPANY, requisition_id=REQ, now=NOW,
    )
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_a_withdrawn_consent_is_not_re_granted_by_a_stranger() -> None:
    """The predicate used to filter `revoked_at IS NULL`, so a withdrawal was
    only sticky against people who had not withdrawn. Anyone could type the
    address into the public form and mint a fresh `granted = TRUE` row — and
    reconciliation gates processing on exactly `granted AND revoked_at IS NULL`.
    """
    from app.routers.public_apply import _record_apply_consent

    db = _consent_db(True)
    await _record_apply_consent(
        db, request=MagicMock(headers={}, client=None), user_id=USER,
        applicant_id=uuid.uuid4(), company_id=COMPANY, requisition_id=REQ, now=NOW,
    )
    assert db.execute.await_count == 0, "a withdrawal was overridden"


@pytest.mark.asyncio
async def test_the_lookup_does_not_filter_out_withdrawn_rows() -> None:
    """The bug was in the WHERE clause, so assert on the WHERE clause."""
    from app.routers.public_apply import _record_apply_consent

    db = _consent_db(None)
    await _record_apply_consent(
        db, request=MagicMock(headers={}, client=None), user_id=USER,
        applicant_id=uuid.uuid4(), company_id=COMPANY, requisition_id=REQ, now=NOW,
    )
    lookup = db.scalar.await_args.args[0].text
    assert "revoked_at IS NULL" not in lookup
    assert "revoked_at IS NOT NULL" in lookup  # it reads the flag, not filters on it


# ===========================================================================
# The one submission that adopts nothing must release what it uploaded
#
# Every other path deliberately KEEPS the draft's CV object, because
# applicants.resume_s3_key and enrolments.applied_resume_s3_key are that same
# key. The "you have already applied" branch creates neither, so nothing ever
# references the file — and once the purge correctly stopped deleting submitted
# drafts' objects, it had no deletion path at all. A CV in the bucket for ever,
# past its purpose, referenced by nobody, is a DPDP §8(7) storage-limitation
# problem, not merely untidy.
# ===========================================================================
@pytest.mark.asyncio
async def test_the_already_applied_branch_clears_the_pointer() -> None:
    from app.application_drafts import mark_submitted

    db = AsyncMock()
    await mark_submitted(db, draft_id=uuid.uuid4(), release_resume=True)
    sql = db.execute.await_args.args[0].text
    assert "resume_s3_key = NULL" in sql


@pytest.mark.asyncio
async def test_every_other_submission_keeps_the_pointer() -> None:
    """Clearing it here would strand applicants.resume_s3_key on a key whose
    draft row no longer admits to owning it."""
    from app.application_drafts import mark_submitted

    db = AsyncMock()
    await mark_submitted(db, draft_id=uuid.uuid4())
    assert "resume_s3_key" not in db.execute.await_args.args[0].text


def test_the_already_applied_branch_deletes_the_object_it_released() -> None:
    """Clearing the pointer without deleting the object just makes the orphan
    unfindable, which is worse than leaving it addressable."""
    from app.routers.public_apply import submit_draft

    src = inspect.getsource(submit_draft)
    branch = src[src.index('existing["enrolment_id"] is not None'):]
    branch = branch[: branch.index("return ApplicationOut")]
    assert "release_resume=True" in branch
    assert "_delete_from_s3" in branch
    # Order matters: commit the cleared pointer BEFORE deleting the object, so a
    # failed delete leaves an orphan rather than a dangling reference.
    assert branch.index("db.commit") < branch.index("_delete_from_s3")


# ===========================================================================
# The drain bounds the transaction, not just the work
# ===========================================================================
@pytest.mark.asyncio
async def test_each_purge_pass_commits() -> None:
    """_MAX_PURGE_PASSES caps the WORK. Without a commit per pass, 40 x 500
    draft deletes plus their cascading user/user_roles/ledger deletes sit in one
    open transaction against Neon all night, and a failure in the last pass
    rolls back the other thirty-nine — a single bad row losing a whole night's
    retention."""
    from app.application_drafts import purge_expired

    full = [{"resume_s3_key": f"k{i}.pdf", "user_id": uuid.uuid4(), "status": "draft"}
            for i in range(2)]
    db = AsyncMock()
    batches = [full, full[:1]]

    async def _execute(statement: object, *_a: object, **_k: object) -> MagicMock:
        sql = getattr(statement, "text", str(statement))
        out = (batches.pop(0) if batches else []) \
            if sql.lstrip().startswith("DELETE FROM application_drafts") else []
        res = MagicMock()
        res.mappings = MagicMock(return_value=MagicMock(all=MagicMock(return_value=out)))
        res.rowcount = 0
        return res

    db.execute = AsyncMock(side_effect=_execute)
    await purge_expired(db, now=NOW, limit=2)
    assert db.commit.await_count == 2, "the drain committed once, not per pass"


def test_the_purge_log_still_carries_the_row_count() -> None:
    """`drafts` is the retention evidence a DPDP audit reads — how many records
    past their purpose were actually deleted. `objects` is a subset (submitted
    rows keep their CV), so it cannot stand in for it."""
    from app.application_drafts import purge_expired

    src = inspect.getsource(purge_expired)
    assert "drafts=purged" in src


# ===========================================================================
# A deletion is claimed only after it has happened
#
# delete_draft used to delete the rows, commit, then attempt the object and
# swallow any failure into a warning nothing consumes. On a storage outage the
# candidate saw "Your saved application has been deleted" while their CV was
# still in the bucket — and the row that pointed at it was already gone, so
# nothing could ever find the object again. A false statement to a data
# principal about erasure, and a permanent orphan.
# ===========================================================================
def test_the_object_is_deleted_before_the_rows() -> None:
    from app.routers.public_apply import delete_draft

    src = inspect.getsource(delete_draft)
    assert src.index("_delete_from_s3") < src.index("DELETE FROM application_drafts"), (
        "the rows go first again, so a storage failure leaves an unfindable "
        "orphan and tells the candidate it was deleted"
    )


def test_a_storage_failure_refuses_rather_than_lying() -> None:
    """503 and nothing removed beats 204 and a false claim."""
    from app.routers.public_apply import delete_draft

    src = inspect.getsource(delete_draft)
    guard = src[src.index("_delete_from_s3"):src.index("DELETE FROM application_drafts")]
    assert "raise HTTPException" in guard
    assert "503" in guard or "SERVICE_UNAVAILABLE" in guard
    # And it must not still be swallowing the error into a warning.
    assert "draft_object_orphaned" not in src
