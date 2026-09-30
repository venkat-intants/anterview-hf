"""The exam's camera notice leaves audit evidence, once per round.

THE GAP (security review LOW-1). ``dpdp_consent_ledger`` holds ONE ACTIVE row
per (user, consent_type, purpose) — ``ix_dpdp_consent_active_unique`` enforces
it. So a candidate who already consented during the AI interview, or on an
earlier round, produces no new ledger row when the exam asks again. The consent
STATE was recorded correctly; the INTERACTION existed only as a ``log.info``
line, which is not the immutable trail and rotates away. That is a disclosed
claim: ``docs/DATA-FLOW.md`` tells bid readers "granting it once covers both
doors".

HOW THE BOUND IS ENFORCED, and why these tests look the way they do. The first
version deduplicated with a SELECT probe. Security review rejected it twice
over — ``audit_log``'s only index is on ``event_ts``, so the probe sequentially
scanned a three-year, permanently-growing table on every FIRST acceptance from
an UNAUTHENTICATED route; and SELECT-then-INSERT is TOCTOU, so parallel POSTs
all saw "absent" and all inserted. It is now one
``INSERT ... ON CONFLICT DO NOTHING`` against the partial unique index
``ix_audit_log_camera_notice_round``.

Uniqueness is therefore the DATABASE's property, and the integration test is
what proves it. These tests cover what the unit level genuinely can: that every
path writes, what the row contains, and what it deliberately does not.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app import exam_camera


class _Recorder:
    """Captures each execute()'s parameters, without a database."""

    def __init__(self) -> None:
        self.executed: list[tuple[Any, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.executed.append((stmt, params or {}))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))

    def add(self, obj: Any) -> None:  # pragma: no cover
        raise AssertionError("the audit row must be INSERTed, not staged via add()")


def _applicant() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), company_id=uuid.uuid4(), user_id=uuid.uuid4())


def _write(*, already_granted: bool) -> tuple[dict[str, Any], Any, uuid.UUID, uuid.UUID]:
    db = _Recorder()
    applicant = _applicant()
    consent_id, round_id = uuid.uuid4(), uuid.uuid4()
    asyncio.run(
        exam_camera._audit_notice_accepted(
            db,  # type: ignore[arg-type]
            user_id=applicant.user_id,
            applicant=applicant,
            exam_round_id=round_id,
            now=datetime(2026, 9, 30, 12, 0, tzinfo=UTC),
            consent_id=consent_id,
            already_granted=already_granted,
        )
    )
    assert len(db.executed) == 1, "exactly one statement: the conflict-safe INSERT"
    return db.executed[0][1], applicant, consent_id, round_id


def test_the_write_is_conflict_safe_rather_than_read_then_write() -> None:
    """The structural fix. A SELECT probe scanned a permanently-growing table
    from an unauthenticated route AND was TOCTOU. Uniqueness now belongs to the
    partial unique index, and the conflict resolves at INSERT time so it cannot
    surface at commit and roll the consent ledger row back with it.
    """
    sql = str(exam_camera._INSERT_NOTICE_AUDIT_SQL)
    assert "INSERT INTO audit_log" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    # DO UPDATE would hit audit_log_no_mutation (BEFORE UPDATE OR DELETE).
    assert "DO UPDATE" not in sql


def test_the_acceptance_is_recorded_even_when_the_ledger_writes_nothing() -> None:
    """The defect itself: an already-active grant left no durable trace that
    the exam had asked at all."""
    params, applicant, consent_id, round_id = _write(already_granted=True)
    details = json.loads(params["det"])
    assert params["act"] == exam_camera.CONSENT_NOTICE_AUDIT_ACTION
    assert details["already_granted"] is True
    assert details["applicant_id"] == str(applicant.id)
    assert details["exam_round_id"] == str(round_id)
    assert details["accepted_at_iso"] == "2026-09-30T12:00:00+00:00"
    assert params["cid"] == consent_id


def test_the_notice_version_is_recorded_so_we_know_what_they_agreed_to() -> None:
    """"They consented" is not evidence on its own — the question is which
    wording they were shown."""
    params, _a, _c, _r = _write(already_granted=True)
    assert json.loads(params["det"])["notice_version"] == exam_camera.CONSENT_NOTICE_VERSION


def test_the_grant_is_part_of_the_key_not_merely_a_pointer() -> None:
    """Security review M3. Without ``consent_id`` in the key, a candidate who
    withdraws and accepts again is suppressed by the row written for the REVOKED
    grant — leaving the grant actually in force with no evidence, which is the
    exact question this feature exists to answer.
    """
    params, _a, consent_id, _r = _write(already_granted=True)
    assert json.loads(params["det"])["consent_id"] == str(consent_id), (
        "consent_id must be in details, because the unique index keys on it"
    )


def test_the_candidate_is_the_actor_not_the_system() -> None:
    """Consent is something a person gave; attributing it to "system" would
    make the trail describe the wrong event."""
    params, applicant, _c, _r = _write(already_granted=True)
    assert params["uid"] == applicant.user_id
    assert "'candidate'" in str(exam_camera._INSERT_NOTICE_AUDIT_SQL)


def test_a_fresh_grant_is_recorded_too_and_says_so() -> None:
    params, _a, _c, _r = _write(already_granted=False)
    assert json.loads(params["det"])["already_granted"] is False


def test_no_request_metadata_is_stored_at_all() -> None:
    """Security review L1. An ``ip_hash`` here would be pseudonymous personal
    data — one global salt over a 2^32 address space is enumerable and linkable
    — kept forever in a table erasure cannot reach, serving no purpose this row
    has. The ledger already carries it once per user. DPDP §6(1).
    """
    params, _a, _c, _r = _write(already_granted=True)
    details = json.loads(params["det"])
    for banned in ("ip_hash", "ua_hash", "ip_address", "user_agent"):
        assert banned not in details, (
            f"{banned} must not sit in a permanent, erasure-exempt row"
        )
    sql = str(exam_camera._INSERT_NOTICE_AUDIT_SQL)
    assert "ip_address" not in sql and "user_agent" not in sql


def test_every_return_path_writes_one() -> None:
    """A later early-return that skips the audit call is how this was missed the
    first time. Uniqueness itself is the index's job — see the integration
    test; this only pins that no path is silent."""
    import inspect

    source = inspect.getsource(exam_camera.record_camera_consent)
    assert source.count("await _audit_notice_accepted(") == 3, (
        "fresh grant, already-granted and raced paths must each record the "
        "acceptance — the ledger only writes on one of them"
    )


def test_the_round_is_required_so_the_bound_cannot_be_opted_out_of() -> None:
    """Security review L2. ``exam_round_id`` is half the uniqueness key; a
    default would let a future caller silently write unbounded rows."""
    import inspect

    p = inspect.signature(exam_camera._audit_notice_accepted).parameters["exam_round_id"]
    assert p.default is inspect.Parameter.empty, "exam_round_id must be required"


def test_the_raced_path_records_against_the_winning_grant() -> None:
    """The path with no coverage at all before this test. When a concurrent
    request wins the ledger INSERT, this one must still evidence the acceptance
    — and name the grant that exists, not the id it failed to write."""
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import IntegrityError

    winner_id = uuid.uuid4()
    applicant = _applicant()

    class _Raced(_Recorder):
        def __init__(self) -> None:
            super().__init__()
            self.n = 0

        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
            self.n += 1
            if self.n == 2:  # the ledger INSERT, inside the savepoint
                raise IntegrityError("insert", {}, Exception("duplicate key"))
            self.executed.append((stmt, params or {}))
            found = (
                {"id": winner_id, "granted_at": datetime(2026, 9, 30, 9, 0, tzinfo=UTC)}
                if self.n == 3
                else None
            )
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: found))

        def begin_nested(self) -> Any:
            @asynccontextmanager
            async def _cm() -> Any:
                yield None

            return _cm()

    db = _Raced()
    out = asyncio.run(
        exam_camera.record_camera_consent(
            db,  # type: ignore[arg-type]
            applicant=applicant,
            exam_round_id=uuid.uuid4(),
            meta=exam_camera.CameraConsentMeta(),
        )
    )
    assert out["already_granted"] is True
    audit = [
        p for _s, p in db.executed
        if p.get("act") == exam_camera.CONSENT_NOTICE_AUDIT_ACTION
    ]
    assert len(audit) == 1, "the raced path must still evidence the acceptance"
    assert audit[0]["cid"] == winner_id, "must name the grant that won, not the failed id"
    assert json.loads(audit[0]["det"])["already_granted"] is True
