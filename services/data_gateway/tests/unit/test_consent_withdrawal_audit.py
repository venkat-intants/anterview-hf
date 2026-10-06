"""A DPDP §11 withdrawal must leave evidence, and for three of four types it did not.

THE GAP. ``DELETE /consent`` revokes voice-recording, video-capture,
preboarding-documents and talent-pool-rediscovery in one action, and stamps every
in-flight session ``consent_withdrawn`` — which moves that data into the next nightly
purge window instead of waiting out 90 days. The only durable traces were the ledger
mutation itself and a ``log.info``. Exactly one ``audit_log`` row got written, and
only when a ``preboarding_documents`` consent happened to be among the revoked set
(``preboarding.documents_consent_withdrawn_elsewhere``). So for a candidate who had
granted only interview consents, the platform could show that the ledger row IS
revoked and could not evidence who did it, when, or through which door — and the
ledger's own ``revoked_at`` is an UPDATE in place, so the ledger itself cannot say.

WHY IT MATTERS MORE HERE THAN ON MOST ROUTES, and both reasons are deliberate design
rather than oversights:

  * The route is reachable with a GUEST token — an interview magic link, which
    ``interview_take._issue_guest_token`` mints with no company claim at all.
    ``test_candidate_route_authz.py::test_consent_withdrawal_stays_reachable_by_a_guest``
    keeps it that way on purpose: "a guest token is an unactivated applicant's only
    credential — they have no password — so gating /consent would leave the people
    whose data was collected with no way to withdraw it."
  * Withdrawal is GLOBAL across every company. ``revoke_opt_ins`` is called with
    ``company_id=None``, and ``test_withdrawal_revokes_every_companys_rediscovery_opt_in``
    asserts the ABSENCE of a company filter, because §11 says "without restriction".

Those two together mean one forwarded link can clear a candidate's consent state
everywhere. Scoping the route per company is therefore not the fix — it would break
a documented DPDP right and two tests. Evidencing it is.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.models import AuditLog
from app.routers import consent as consent_router
from app.routers.consent import CONSENT_WITHDRAWN_AUDIT_ACTION, _audit_withdrawal
from app.schemas.consent import RevokedConsentItem


class _Recorder:
    """Just the one method ``_audit_withdrawal`` uses."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _items(*types: str) -> list[RevokedConsentItem]:
    return [
        RevokedConsentItem(
            consent_type=t,
            consent_id=str(uuid.uuid4()),
            revoked_at="2026-10-06T12:00:00+00:00",
        )
        for t in types
    ]


def _run(*types: str, sessions: int = 0) -> tuple[_Recorder, list[RevokedConsentItem]]:
    db = _Recorder()
    items = _items(*types)
    _audit_withdrawal(db, user_id=uuid.uuid4(), items=items, sessions_withdrawn=sessions)  # type: ignore[arg-type]
    return db, items


# ---------------------------------------------------------------------------
# The rows get written at all
# ---------------------------------------------------------------------------
class TestEveryRevokedConsentIsEvidenced:
    def test_an_interview_only_withdrawal_is_now_audited(self) -> None:
        """THE DEFECT, stated as a test. This exact case — voice and video, no
        documents — produced no audit row whatsoever, because the only writer was
        the preboarding-documents branch."""
        db, items = _run("interview_voice_recording", "video_capture")

        assert len(db.added) == 2
        assert all(isinstance(row, AuditLog) for row in db.added)
        assert {row.details["consent_type"] for row in db.added} == {
            "interview_voice_recording",
            "video_capture",
        }

    def test_one_row_per_revoked_ledger_entry(self) -> None:
        """Per entry rather than one summary row, so "when was video_capture
        withdrawn for this candidate" is answerable on its own — and so
        ``resource_id`` can point at the exact ledger row each revoked."""
        db, items = _run(
            "interview_voice_recording", "video_capture", "preboarding_documents"
        )

        assert len(db.added) == 3
        assert {str(row.resource_id) for row in db.added} == {i.consent_id for i in items}

    def test_the_resource_is_the_ledger_row_it_revoked(self) -> None:
        """The shape ``rediscovery.py`` already uses for its expiry sweep."""
        db, items = _run("video_capture")

        row = db.added[0]
        assert row.resource_type == "dpdp_consent_ledger"
        assert str(row.resource_id) == items[0].consent_id

    def test_nothing_revoked_writes_nothing(self) -> None:
        """The route 404s before reaching this, but an empty list must not produce a
        row claiming a withdrawal happened."""
        db, _ = _run()

        assert db.added == []


# ---------------------------------------------------------------------------
# What each row says
# ---------------------------------------------------------------------------
class TestTheRowAnswersTheQuestionsAnAuditorAsks:
    def test_the_candidate_is_the_actor_not_the_system(self) -> None:
        """The data principal exercised a right. Recording it as ``system`` would
        lose the only fact that distinguishes a withdrawal from the nightly expiry
        sweep, which genuinely is the system."""
        db, _ = _run("video_capture")

        assert db.added[0].actor_type == "candidate"
        assert db.added[0].actor_id is not None

    def test_the_action_has_one_definition(self) -> None:
        """A literal typed at the call site drifts from the one a query uses."""
        assert CONSENT_WITHDRAWN_AUDIT_ACTION == "dpdp_consent.withdrawn"
        db, _ = _run("video_capture")

        assert db.added[0].action == CONSENT_WITHDRAWN_AUDIT_ACTION

    def test_the_door_is_named(self) -> None:
        """Three other routes can withdraw a narrower consent
        (``/task/consent/withdraw``, ``/offer/documents/consent/withdraw``, and the
        exam camera path's own grant), so "which one was this" has to be answerable
        from the row. ``via`` follows ``preboarding.py``'s convention."""
        db, _ = _run("video_capture")

        assert db.added[0].details["via"] == "DELETE /consent"

    def test_the_global_scope_is_recorded_because_it_is_surprising(self) -> None:
        """This is every company's consent of that type, not one company's. An
        auditor reading a single row should not have to know that."""
        db, _ = _run("talent_pool_rediscovery")

        assert db.added[0].details["scope"] == "all_companies"

    def test_the_sessions_it_stopped_are_counted(self) -> None:
        """Those sessions become purgeable at the next nightly window rather than at
        90 days (``retention._PURGEABLE_STATUSES`` includes ``consent_withdrawn``),
        so the number is part of what the withdrawal DID, not context."""
        db, _ = _run("video_capture", sessions=3)

        assert db.added[0].details["sessions_withdrawn"] == 3

    def test_zero_sessions_is_recorded_as_zero_not_omitted(self) -> None:
        """"No sessions were stopped" and "nobody recorded whether any were" are
        different facts, and a missing key cannot tell them apart."""
        db, _ = _run("video_capture", sessions=0)

        assert db.added[0].details["sessions_withdrawn"] == 0

    def test_the_breadth_of_the_action_is_on_every_row(self) -> None:
        """Each row is one consent, but the ACT was one withdrawal of several. Any
        single row should say how many went with it."""
        db, _ = _run("interview_voice_recording", "video_capture")

        assert all(row.details["consents_revoked"] == 2 for row in db.added)


# ---------------------------------------------------------------------------
# DPDP minimisation
# ---------------------------------------------------------------------------
class TestNoRequestMetadataIsStored:
    """``audit_log`` rows survive DPDP erasure — that is the documented exclusion —
    so an IP or user-agent stored here is un-erasable PII about the data principal.
    ``exam_camera._audit_notice_accepted`` makes the same call under §6(1) and has a
    test enforcing it.

    The sibling row in ``preboarding.py`` DOES carry ``meta``. That inconsistency is
    real and this change follows the stricter side of it rather than widening the
    looser one; the choice is written down in ``_audit_withdrawal``'s docstring.
    """

    def test_no_ip_address(self) -> None:
        db, _ = _run("video_capture")

        assert db.added[0].ip_address is None

    def test_no_user_agent(self) -> None:
        db, _ = _run("video_capture")

        assert db.added[0].user_agent is None

    def test_details_carries_no_hashed_substitute_either(self) -> None:
        """A hash of an IP is still a per-device identifier. Blocking the obvious
        workaround as well as the obvious field."""
        db, _ = _run("video_capture")

        keys = set(db.added[0].details)
        assert not {k for k in keys if "ip" in k or "agent" in k or "ua" in k}, (
            f"request metadata reached details: {sorted(keys)}"
        )

    def test_details_carries_exactly_the_agreed_keys(self) -> None:
        """An EXACT key set, not a length bound. The first version of this allowed
        any string under 64 characters, and a mutation that added
        "the candidate asked us to stop processing their data entirely" (58) walked
        straight through it.

        Exact is also the right contract on its own terms: these rows survive DPDP
        erasure, so adding a field here should be a deliberate edit to this test and
        not something that happens by accident in a details dict.
        """
        db, _ = _run("video_capture", sessions=2)

        assert set(db.added[0].details) == {
            "consent_type",
            "via",
            "scope",
            "sessions_withdrawn",
            "consents_revoked",
        }

    def test_every_value_is_a_fact_rather_than_prose(self) -> None:
        """Ids, counts, flags and short enums only. Kept alongside the key-set
        assertion because the two fail differently: this one catches an existing key
        being repurposed to carry a sentence."""
        db, _ = _run("video_capture", sessions=2)

        for key, value in db.added[0].details.items():
            assert isinstance(value, str | int | bool), f"{key} is {type(value)}"
            if isinstance(value, str):
                assert len(value.split()) <= 3, f"{key} reads like prose: {value!r}"


# ---------------------------------------------------------------------------
# It is actually wired, on the right transaction
# ---------------------------------------------------------------------------
class TestItIsWiredIntoTheRoute:
    def test_the_route_calls_it(self) -> None:
        """A helper nothing calls is not evidence. Read from the source because the
        route needs a database, a user and a request to invoke."""
        source = inspect.getsource(consent_router.revoke_consent)

        assert "_audit_withdrawal(" in source

    def test_it_is_called_before_the_commit(self) -> None:
        """Staging the evidence after ``commit()`` would put it on a different
        transaction, so a crash between the two could leave a revoked consent with no
        record of the revocation — the worse of the two half-states. The session
        stamp is already on this transaction for the same reason."""
        source = inspect.getsource(consent_router.revoke_consent)

        assert source.index("_audit_withdrawal(") < source.index("await db.commit()")

    def test_it_is_called_after_the_session_stamp_so_the_count_is_real(self) -> None:
        """``sessions_withdrawn`` is the stamp's rowcount. Called before it, the row
        would record 0 every time and look like a withdrawal that stopped nothing."""
        source = inspect.getsource(consent_router.revoke_consent)

        assert source.index("_mark_sessions_consent_withdrawn(") < source.index(
            "_audit_withdrawal("
        )

    def test_the_guest_route_is_still_reachable(self) -> None:
        """The invariant this change must not touch. Adding evidence is not a reason
        to gate the route — a guest token is an unactivated applicant's only
        credential, so gating it would remove a DPDP right from exactly the people
        whose data was collected without an account."""
        from app.main import app

        routes = [r for r in app.routes if getattr(r, "path", "") == "/consent"]

        assert routes, "no /consent route found — this test would pass vacuously"
        for route in routes:
            deps = getattr(route, "dependencies", [])
            names = [getattr(d.dependency, "__qualname__", "") for d in deps]
            assert not any("reject_role" in n for n in names), (
                "withdrawal must stay reachable without an account"
            )


def test_the_fake_session_models_add_rather_than_swallowing_it() -> None:
    """Guards the guard. Every assertion above reads ``db.added``; a recorder whose
    ``add`` did nothing would make all of them pass against a deleted control."""
    db = _Recorder()
    sentinel = MagicMock()

    db.add(sentinel)

    assert db.added == [sentinel]


@pytest.mark.parametrize(
    "consent_type",
    [
        "interview_voice_recording",
        "video_capture",
        "preboarding_documents",
        "talent_pool_rediscovery",
    ],
)
def test_all_four_types_this_route_revokes_are_evidenced(consent_type: str) -> None:
    """Named individually because the defect was precisely that three of the four
    were not — the single writer was the preboarding-documents branch."""
    db, _ = _run(consent_type)

    assert len(db.added) == 1
    assert db.added[0].details["consent_type"] == consent_type
