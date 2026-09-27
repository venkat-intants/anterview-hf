"""PH5 Wave 1 / C1 — the 90-day hire check-in, and HR's source vocabulary.

The database guarantees (hire-stands gate, one-live-per-enrolment, supersede,
tenant isolation, the employed-timing rule, ``due()``, retention, a
requisition hard-delete cascading through) are
``tests/integration/test_ph5_w1_checkins.py``. This file is the fast layer:
every validation branch, the structural guarantee that a check-in cannot
touch the hiring pipeline, and the source vocabulary HR now writes to.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import uuid
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app import hire_checkins as svc
from app.application_source import INTERNAL, SOURCES, validate_hr_source

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


# ===========================================================================
# _validate_payload — vocabulary and iff rules
# ===========================================================================
def test_employment_must_be_in_vocabulary() -> None:
    with pytest.raises(svc.CheckinError) as exc:
        svc._validate_payload("quit", None, None)
    assert exc.value.status_code == 422


def test_left_requires_a_reason() -> None:
    with pytest.raises(svc.CheckinError) as exc:
        svc._validate_payload("left", None, None)
    assert exc.value.status_code == 422


def test_left_reason_must_be_in_vocabulary() -> None:
    with pytest.raises(svc.CheckinError):
        svc._validate_payload("left", "got_bored", None)


def test_left_refuses_a_performance_value() -> None:
    with pytest.raises(svc.CheckinError):
        svc._validate_payload("left", "voluntary", "meets")


def test_employed_requires_performance() -> None:
    with pytest.raises(svc.CheckinError):
        svc._validate_payload("employed", None, None)


def test_employed_performance_must_be_in_vocabulary() -> None:
    with pytest.raises(svc.CheckinError):
        svc._validate_payload("employed", None, "amazing")


def test_employed_refuses_a_left_reason() -> None:
    with pytest.raises(svc.CheckinError):
        svc._validate_payload("employed", "voluntary", "meets")


def test_valid_left_payload_is_accepted() -> None:
    svc._validate_payload("left", "involuntary", None)  # does not raise


def test_valid_employed_payload_is_accepted() -> None:
    svc._validate_payload("employed", None, "exceeds")  # does not raise


# ===========================================================================
# LOW-1: a double submit is a 409, not a 500 (interviewer_scorecards.assign
# precedent). A real race is exercised at the DB level in
# tests/integration/test_ph5_w1_checkins.py; this is the exception-handling
# path itself, isolated from needing two real concurrent connections.
# ===========================================================================
def _integrity_error(constraint: str) -> IntegrityError:
    return IntegrityError(
        "INSERT INTO hire_checkins ...", {},
        Exception(f'duplicate key value violates unique constraint "{constraint}"'),
    )


async def test_record_double_submit_gives_409_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc, "_assert_writable", AsyncMock(return_value={"applicant_id": uuid.uuid4()})
    )
    monkeypatch.setattr(svc, "_assert_within_window", AsyncMock(return_value=None))

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # the live pre-check sees nothing racing in
    savepoint = AsyncMock()
    db.begin_nested = AsyncMock(return_value=savepoint)
    db.execute = AsyncMock(side_effect=_integrity_error("uq_hire_checkins_live"))

    with pytest.raises(svc.CheckinError) as exc:
        await svc.record(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), actor=uuid.uuid4(),
            employment="left", left_reason="voluntary", performance=None,
            meta=svc.RequestMeta(),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == svc._DOUBLE_SUBMIT_DETAIL
    savepoint.rollback.assert_awaited_once()


async def test_record_reraises_an_unrelated_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the named constraint is swallowed into a 409 — anything else
    (a real bug) must still surface, not be hidden behind the wrong message."""
    monkeypatch.setattr(
        svc, "_assert_writable", AsyncMock(return_value={"applicant_id": uuid.uuid4()})
    )
    monkeypatch.setattr(svc, "_assert_within_window", AsyncMock(return_value=None))

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    savepoint = AsyncMock()
    db.begin_nested = AsyncMock(return_value=savepoint)
    db.execute = AsyncMock(side_effect=_integrity_error("fk_hire_checkins_recorded_by"))

    with pytest.raises(IntegrityError):
        await svc.record(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), actor=uuid.uuid4(),
            employment="left", left_reason="voluntary", performance=None,
            meta=svc.RequestMeta(),
        )


def _row_result(row: dict[str, object] | None) -> MagicMock:
    """A stand-in for ``await db.execute(...)``: ``.mappings().first()`` is a
    plain (synchronous) chained call on the awaited result, not itself
    awaited — the ``test_ph4_wave1.py::_mapping_result`` precedent."""
    res = MagicMock()
    res.mappings.return_value.first.return_value = row
    return res


async def test_correct_double_submit_gives_409_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    live_row = {
        "id": uuid.uuid4(), "enrolment_id": uuid.uuid4(), "kind": svc.KIND_90_DAY,
        "employment": "employed", "left_reason": None, "performance": "meets",
        "recorded_by_user_id": uuid.uuid4(), "recorded_at": None, "supersedes_id": None,
        "superseded_at": None,
    }
    db = AsyncMock()
    # execute() is called for: (1) the FOR UPDATE row read, (2) the supersede
    # UPDATE, (3) the INSERT, which raises. _assert_writable /
    # _assert_within_window are monkeypatched out, so they call execute() 0
    # times.
    db.execute = AsyncMock(
        side_effect=[
            _row_result(live_row), MagicMock(), _integrity_error("uq_hire_checkins_live"),
        ]
    )
    monkeypatch.setattr(svc, "_assert_writable", AsyncMock(return_value={}))
    monkeypatch.setattr(svc, "_assert_within_window", AsyncMock(return_value=None))
    savepoint = AsyncMock()
    db.begin_nested = AsyncMock(return_value=savepoint)

    with pytest.raises(svc.CheckinError) as exc:
        await svc.correct(
            db, company_id=uuid.uuid4(), checkin_id=live_row["id"], actor=uuid.uuid4(),
            employment="left", left_reason="voluntary", performance=None,
            meta=svc.RequestMeta(),
        )
    assert exc.value.status_code == 409
    savepoint.rollback.assert_awaited_once()


# ===========================================================================
# HIRE_STANDS_SQL / REVERSED_OFFER_OUTCOMES — byte-identical wherever written
# ===========================================================================
def test_hire_stands_sql_is_the_agreed_text() -> None:
    assert svc.HIRE_STANDS_SQL == (
        "e.status = 'hired' AND COALESCE(e.offer_outcome, '') NOT IN "
        "('offer_declined', 'offer_expired', 'offer_withdrawn')"
    )


def test_reversed_offer_outcomes_is_the_agreed_three() -> None:
    assert svc.REVERSED_OFFER_OUTCOMES == (
        "offer_declined", "offer_expired", "offer_withdrawn",
    )


def _migration_module() -> ModuleType:
    """The hire_checkins migration, loaded like any other module — not read as
    text — so what is compared is the actual evaluated constants and the
    fully-substituted trigger source, not two string literals a line break
    happens to split in the file."""
    migrations = APP.parents[0] / "alembic" / "versions"
    matches = list(migrations.glob("*8f41cb18a299*hire_checkins*.py"))
    assert len(matches) == 1, matches
    spec = importlib.util.spec_from_file_location("_ph5_w1_hire_checkins_migration", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_documents_the_same_hire_stands_text() -> None:
    """The migration's own ``HIRE_STANDS_SQL`` (documentation, not what the
    trigger itself contains — see below) must never drift from the service's."""
    assert _migration_module().HIRE_STANDS_SQL == svc.HIRE_STANDS_SQL


def test_migration_reversed_offer_outcomes_matches_the_service() -> None:
    assert tuple(_migration_module().REVERSED_OFFER_OUTCOMES) == svc.REVERSED_OFFER_OUTCOMES


def test_migration_trigger_checks_the_same_three_literals() -> None:
    """The trigger spells the rule as PL/pgSQL's ``<> ALL (ARRAY[...])``
    rather than ``NOT IN (...)`` — a different syntax for the same rule, so
    this checks the trigger's own three literals rather than a string match
    against ``HIRE_STANDS_SQL``, which would never be true of a trigger
    written in a different syntax on purpose."""
    trigger_source = _migration_module().HIRE_CHECKINS_LIFECYCLE
    start = trigger_source.index("<> ALL (ARRAY")
    array_literal = trigger_source[start:][: trigger_source[start:].index(")") + 1]
    for outcome in svc.REVERSED_OFFER_OUTCOMES:
        assert f"'{outcome}'" in array_literal, (outcome, array_literal)


# ===========================================================================
# Structural: a check-in never touches the hiring pipeline
# ===========================================================================
@pytest.mark.parametrize("module", ["hire_checkins.py", "routers/hr_checkins.py"])
def test_hire_checkins_never_touches_the_pipeline(module: str) -> None:
    """Evidence is recorded here and decided nowhere near here. If this module
    ever learns to write ``enrolments`` or call a lifecycle/decision writer,
    "a check-in is a signal" has quietly become "a check-in decides"."""
    tree = ast.parse((APP / module).read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    identifiers: set[str] = set()
    sql: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.asname or node.name)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings):
            sql.append(node.value)

    for writer in ("record_transition", "record_result", "record_final_decision",
                   "record_round_move", "release_hold"):
        assert writer not in identifiers, f"{module} references {writer}"
    assert not any("UPDATE enrolments" in s for s in sql), f"{module} updates enrolments"
    assert not any("INSERT INTO enrolments" in s for s in sql), f"{module} inserts enrolments"


def test_no_agent_module_references_hire_checkins() -> None:
    """Security review MEDIUM-4: widened past ``app/agents`` and
    ``shared/agents`` to every place a copilot, the role engine or another
    service could plausibly read this table — none of them has any business
    with a post-hire HR record (this module's own docstring, "TENANCY AND
    DATA CLASS"). ``erasure_executor.py`` is exempt: it is the one place
    outside this module that is SUPPOSED to reference hire_checkins (step
    5j)."""
    repo_root = APP.parents[2]
    bases = (
        APP / "agents",
        repo_root / "shared" / "agents",
        repo_root / "shared" / "intelligence",
        repo_root / "services" / "interview_core",
        repo_root / "services" / "feedback_billing",
        repo_root / "services" / "admin_ops" / "app",
    )
    for base in bases:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if path.name == "erasure_executor.py":
                continue
            text_ = path.read_text(encoding="utf-8").lower()
            assert "hire_checkin" not in text_, path


def test_delete_from_hire_checkins_has_exactly_two_callers() -> None:
    """Security review LOW-4: DELETE against this table is otherwise
    unrestricted (migration ``8f41cb18a299``'s trigger allows it always), on
    the understanding that only retention and erasure ever issue one. Grepped
    across the whole ``services/`` tree, not just this file, so a THIRD DELETE
    added anywhere else — an HR-facing route included — turns this red."""
    repo_root = APP.parents[2]
    needle = "DELETE FROM hire_checkins"
    hits: list[str] = []
    for path in (repo_root / "services").rglob("*.py"):
        # Only production code: test fixtures legitimately exercise the same
        # DELETE directly (both hire_checkins.py's own tests and the ones
        # that reproduce erasure step 5j's exact statement).
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        text_ = path.read_text(encoding="utf-8")
        if needle in text_:
            hits.append(str(path.relative_to(repo_root)))
    allowed_suffixes = (
        "data_gateway/app/hire_checkins.py",
        "admin_ops/app/erasure_executor.py",
    )
    for hit in hits:
        assert hit.replace("\\", "/").endswith(allowed_suffixes), hit
    joined = "/".join(hits).replace("\\", "/")
    assert "data_gateway/app/hire_checkins.py" in joined
    assert "admin_ops/app/erasure_executor.py" in joined


# ===========================================================================
# app.application_source.validate_hr_source — HR's own inputs
# ===========================================================================
def test_hr_source_defaults_to_internal_when_absent() -> None:
    assert validate_hr_source(None) == INTERNAL


def test_hr_source_defaults_to_internal_when_blank() -> None:
    assert validate_hr_source("   ") == INTERNAL


def test_hr_source_accepts_a_governed_value() -> None:
    assert validate_hr_source("referral") == "referral"


def test_hr_source_is_case_and_whitespace_insensitive() -> None:
    assert validate_hr_source("  Campus  ") == "campus"


def test_hr_source_refuses_an_unrecognised_value() -> None:
    with pytest.raises(ValueError, match="source must be one of"):
        validate_hr_source("linkedin_dm")


def test_hr_source_vocabulary_matches_the_governed_set() -> None:
    """A sanity check that this test file's expectations track the real
    vocabulary rather than a copy of it."""
    assert "referral" in SOURCES
    assert "campus" in SOURCES


def test_both_hr_write_paths_validate_their_source() -> None:
    """HR single add and bulk ingest both live in hr_applicants.py; each must
    run its ``source`` form field through ``validate_hr_source`` rather than
    writing whatever a client sends straight to the pipeline."""
    source = (APP / "routers" / "hr_applicants.py").read_text(encoding="utf-8")
    assert source.count("validate_hr_source(source)") >= 2


def test_hr_write_paths_never_accept_a_source_detail_field() -> None:
    """Architecture review: HR's own inputs accept a channel only, never a
    free-text detail (a referrer's or agency contact's name). Checks for a new
    FORM FIELD specifically (the ``Annotated[...]`` shape every Form()
    parameter in this router uses) — ``source_detail`` legitimately appears
    elsewhere, read back on ``ApplicationOut`` from what the public apply path
    already wrote."""
    source = (APP / "routers" / "hr_applicants.py").read_text(encoding="utf-8")
    assert "source_detail: Annotated" not in source
