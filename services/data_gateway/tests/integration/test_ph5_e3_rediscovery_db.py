"""PH5-E3 DB-level tests — consent, eligibility and the rediscovery search
against a real, migrated Postgres with pgvector.

Service functions are called directly, never through HTTP, on the
``test_ph5_e2_corpus_db.py`` precedent: one transaction per test, always rolled
back. ``AI_FAKE_MODE`` gives deterministic embeddings (equal text, equal
vector), which is the only reason a RANKING assertion can be written at all —
the tests below set a candidate's stored vector to the query's own fake vector,
so that candidate's cosine similarity is 1 and everybody else's is noise.

What is deliberately NOT here: the pools service and erasure step 5k (another
agent), and the erasure EXECUTOR run. Erasure at REQUEST time is here, because
it is part of the eligibility CTE this wave owns.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import rediscovery as svc
from app.apply_activation import _link_to_existing
from app.config import settings
from app.embedding_client import EmbeddingError, to_pgvector_literal
from app.fake_ai import fake_embeddings

pytestmark = pytest.mark.integration

QUERY = "hydraulics preventive maintenance"
RESUME_MATCH = "Maintenance fitter: hydraulics, preventive maintenance of presses, safety."
RESUME_OTHER = "Accounts payable clerk: ledgers, reconciliation, invoice processing."
META = svc.OptInMeta(ip_address="127.0.0.1", user_agent="pytest")


@pytest.fixture(autouse=True)
def deterministic_query_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the ONE embedding call a search makes, rather than switching
    ``AI_FAKE_MODE`` on.

    That setting is process-wide, and four unrelated suites (``test_config.py``,
    ``test_security_fixes.py``, ``test_e2e_test_switches.py``,
    ``test_resume_search.py``) assert it is OFF by default — so a file that
    needed it globally true would be a file that makes the suite unrunnable, and
    CI sets it nowhere. Same isolation shape as
    ``test_ph5_e2_corpus_db.py``'s reconciler test.

    The fake IS ``fake_embeddings``: equal text, equal vector. That is what lets
    a test seed a candidate's stored vector as the query's own vector and then
    assert a ranking. The degradation test overrides this with a raiser.
    """

    async def _fake_embed_one_remote(
        *, text: str, task_type: str, acting_user_id: str
    ) -> list[float]:
        return fake_embeddings([text])[0]

    monkeypatch.setattr(svc, "embed_one_remote", _fake_embed_one_remote)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A rolled-back transaction per test."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


class F:
    """One company A with two candidates, plus a second company B for tenancy."""

    def __init__(self) -> None:
        self.company_a = uuid.uuid4()
        self.company_b = uuid.uuid4()
        self.hr_a = uuid.uuid4()
        self.hr_b = uuid.uuid4()
        self.peer = uuid.uuid4()          # an interviewer who is not the viewer
        self.cand1 = uuid.uuid4()         # user ids
        self.cand2 = uuid.uuid4()
        self.applicant1 = uuid.uuid4()    # both at company A
        self.applicant2 = uuid.uuid4()
        self.tag = self.company_a.hex[:8]


async def _seed(db: AsyncSession) -> F:
    f = F()
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:ca,'E3 A',:sa),(:cb,'E3 B',:sb)"),
        {"ca": f.company_a, "cb": f.company_b, "sa": f"e3a-{f.tag}", "sb": f"e3b-{f.tag}"},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, email, company_id) VALUES"
            " (:ha,:eha,:ca), (:hb,:ehb,:cb), (:pe,:epe,:ca), (:c1,:ec1,:ca), (:c2,:ec2,:ca)"
        ),
        {
            "ha": f.hr_a, "eha": f"hr-a-{f.tag}@e3.test", "ca": f.company_a,
            "hb": f.hr_b, "ehb": f"hr-b-{f.tag}@e3.test", "cb": f.company_b,
            "pe": f.peer, "epe": f"peer-{f.tag}@e3.test",
            "c1": f.cand1, "ec1": f"cand1-{f.tag}@e3.test",
            "c2": f.cand2, "ec2": f"cand2-{f.tag}@e3.test",
        },
    )
    for aid, uid, name, resume in (
        (f.applicant1, f.cand1, "Asha K", RESUME_MATCH),
        (f.applicant2, f.cand2, "Bala R", RESUME_OTHER),
    ):
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
                " target_job_title, resume_text, years_experience, current_title,"
                " current_company, created_at, updated_at)"
                " VALUES (:i,:c,:u,:n,:e,'Maintenance Fitter',:r,6,'Fitter','Acme',:t,:t)"
            ),
            {"i": aid, "c": f.company_a, "u": uid, "n": name,
             "e": f"{name.split()[0].lower()}-{f.tag}@e3.test", "r": resume, "t": now},
        )
    # The deterministic trick: applicant 1's stored vector IS the query's vector.
    await db.execute(
        text("UPDATE applicants SET embedding = CAST(:v AS halfvec) WHERE id = :i"),
        {"v": to_pgvector_literal(fake_embeddings([QUERY])[0]), "i": f.applicant1},
    )
    await db.execute(
        text("UPDATE applicants SET embedding = CAST(:v AS halfvec) WHERE id = :i"),
        {"v": to_pgvector_literal(fake_embeddings(["unrelated ledger work"])[0]),
         "i": f.applicant2},
    )
    return f


async def _opt_in(
    db: AsyncSession, f: F, *, user_id: uuid.UUID, company_id: uuid.UUID,
    applicant_id: uuid.UUID, source: str = "public_apply_form",
) -> dict[str, Any]:
    return await svc.record_opt_in(
        db, user_id=user_id, company_id=company_id, applicant_id=applicant_id,
        requisition_id=None, source=source, meta=META,
    )


async def _search(db: AsyncSession, f: F, **kw: Any) -> dict[str, Any]:
    return await svc.search(
        db, company_id=kw.pop("company_id", f.company_a),
        hr_user_id=kw.pop("hr_user_id", f.hr_a), query=kw.pop("query", QUERY), **kw
    )


# ===========================================================================
# The empty universe, the opt-in, the expiry, the withdrawal
# ===========================================================================
@pytest.mark.asyncio
async def test_with_no_opt_ins_the_universe_is_empty_and_the_search_returns_nothing(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    payload = await _search(db, f)
    assert payload["universe"] == {"eligible": 0, "total": 2}
    assert payload["results"] == []
    assert payload["matched"] == 0


@pytest.mark.asyncio
async def test_one_opt_in_makes_one_candidate_findable_and_ranked_first(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    await _opt_in(db, f, user_id=f.cand2, company_id=f.company_a, applicant_id=f.applicant2)
    payload = await _search(db, f)
    assert payload["universe"]["eligible"] == 2
    names = [r["full_name"] for r in payload["results"]]
    assert names[0] == "Asha K", names
    top = payload["results"][0]
    assert top["breakdown"]["semantic"] == pytest.approx(1.0, abs=1e-3)
    assert top["explained"] is True
    assert "hydraulics" in top["why"][1]["terms_matched"]


@pytest.mark.asyncio
async def test_an_expired_opt_in_disappears_with_no_job_run(db: AsyncSession) -> None:
    """The 12-month window is IN the query, bounded on the ledger row's own
    granted_at — so expiry needs no nightly job to take effect. The job only
    tidies the record up afterwards."""
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    assert (await _search(db, f))["universe"]["eligible"] == 1

    await db.execute(
        text(
            "UPDATE dpdp_consent_ledger SET granted_at = :t"
            " WHERE user_id = :u AND consent_type = :ct AND revoked_at IS NULL"
        ),
        {"t": datetime.now(tz=UTC) - timedelta(days=400), "u": f.cand1,
         "ct": svc.REDISCOVERY_CONSENT_TYPE},
    )
    payload = await _search(db, f)
    assert payload["universe"]["eligible"] == 0
    assert payload["results"] == []
    # And the row still reads as granted — which is exactly why the nightly
    # block exists, and exactly why the query does not trust it.
    still_granted = await db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger WHERE user_id = :u"
            "  AND consent_type = :ct AND granted AND revoked_at IS NULL"
        ),
        {"u": f.cand1, "ct": svc.REDISCOVERY_CONSENT_TYPE},
    )
    assert still_granted == 1


@pytest.mark.asyncio
async def test_a_withdrawn_opt_in_disappears_on_the_next_query(db: AsyncSession) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    assert (await _search(db, f))["universe"]["eligible"] == 1
    revoked = await svc.revoke_opt_ins(db, user_id=f.cand1)
    assert len(revoked) == 1
    assert (await _search(db, f))["universe"]["eligible"] == 0
    assert (await _search(db, f))["results"] == []


@pytest.mark.asyncio
async def test_the_nightly_block_stamps_expired_rows_and_honours_dry_run(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    await db.execute(
        text(
            "UPDATE dpdp_consent_ledger SET granted_at = :t"
            " WHERE user_id = :u AND consent_type = :ct"
        ),
        {"t": datetime.now(tz=UTC) - timedelta(days=400), "u": f.cand1,
         "ct": svc.REDISCOVERY_CONSENT_TYPE},
    )
    # A dry run counts and writes nothing.
    assert await svc.expire_stale_opt_ins(db, dry_run=True) == 1
    assert await db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger WHERE user_id = :u"
            "  AND consent_type = :ct AND revoked_at IS NULL"
        ),
        {"u": f.cand1, "ct": svc.REDISCOVERY_CONSENT_TYPE},
    ) == 1

    assert await svc.expire_stale_opt_ins(db, dry_run=False) == 1
    await db.flush()  # the audit row is staged through the ORM
    row = (
        await db.execute(
            text(
                "SELECT revoked_at, evidence ->> 'expiry' AS expiry"
                "  FROM dpdp_consent_ledger WHERE user_id = :u AND consent_type = :ct"
            ),
            {"u": f.cand1, "ct": svc.REDISCOVERY_CONSENT_TYPE},
        )
    ).mappings().one()
    assert row["revoked_at"] is not None
    assert row["expiry"] == "auto"
    audit = (
        await db.execute(
            text(
                "SELECT actor_type, details FROM audit_log"
                " WHERE action = 'rediscovery.consent_expired'"
            )
        )
    ).mappings().all()
    assert len(audit) == 1
    assert audit[0]["actor_type"] == "system"
    assert audit[0]["details"]["revoked"] == 1
    # A second run has nothing left to do.
    assert await svc.expire_stale_opt_ins(db, dry_run=False) == 0


@pytest.mark.asyncio
async def test_a_live_opt_in_is_not_expired_by_the_nightly_block(db: AsyncSession) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    assert await svc.expire_stale_opt_ins(db, dry_run=False) == 0
    assert (await _search(db, f))["universe"]["eligible"] == 1


# ===========================================================================
# record_opt_in — the only writer
# ===========================================================================
@pytest.mark.asyncio
async def test_the_row_record_opt_in_writes_satisfies_the_eligibility_predicate(
    db: AsyncSession,
) -> None:
    """The one thing that makes ``evidence ->> 'company_id'`` safe: a round trip
    through the ONLY writer, proving the string it stores matches the string the
    CTE compares against (``applicants.company_id::text``)."""
    f = await _seed(db)
    out = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    assert out["state"] == "granted"
    matched = await db.scalar(
        text(svc.ELIGIBLE_CTE + " SELECT count(*) FROM eligible WHERE id = :a"),
        {"company_id": f.company_a, "months": settings.rediscovery_consent_months,
         "a": f.applicant1,
         "ct": svc.REDISCOVERY_CONSENT_TYPE, "pu": svc.REDISCOVERY_PURPOSE},
    )
    assert matched == 1
    stored = await db.scalar(
        text("SELECT evidence ->> 'company_id' FROM dpdp_consent_ledger WHERE id = :i"),
        {"i": uuid.UUID(out["consent_id"])},
    )
    assert stored == str(f.company_a)


@pytest.mark.asyncio
async def test_re_ticking_is_idempotent_and_does_not_extend_the_window(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    first = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                         applicant_id=f.applicant1)
    again = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                          applicant_id=f.applicant1)
    assert again["state"] == "already_active"
    assert again["granted_at"] == first["granted_at"]
    assert again["consent_id"] == first["consent_id"]
    rows = await db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger WHERE user_id = :u AND consent_type = :ct"
        ),
        {"u": f.cand1, "ct": svc.REDISCOVERY_CONSENT_TYPE},
    )
    assert rows == 1


@pytest.mark.asyncio
async def test_renewing_revokes_and_re_grants_leaving_two_visible_rows(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    first = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                          applicant_id=f.applicant1)
    renewed = await svc.record_opt_in(
        db, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1,
        requisition_id=None, source="my_applications", meta=META, renew=True,
    )
    assert renewed["state"] == "renewed"
    assert renewed["consent_id"] != first["consent_id"]
    rows = (
        await db.execute(
            text(
                "SELECT revoked_at, evidence ->> 'revoked_reason' AS reason,"
                "       evidence ->> 'replaces' AS replaces"
                "  FROM dpdp_consent_ledger WHERE user_id = :u AND consent_type = :ct"
                " ORDER BY granted_at"
            ),
            {"u": f.cand1, "ct": svc.REDISCOVERY_CONSENT_TYPE},
        )
    ).mappings().all()
    assert len(rows) == 2
    assert rows[0]["revoked_at"] is not None
    assert rows[0]["reason"] == "renewed"
    assert rows[1]["revoked_at"] is None
    assert rows[1]["replaces"] == first["consent_id"]
    # Exactly one active row, so the partial unique index is satisfied.
    assert (await _search(db, f))["universe"]["eligible"] == 1


@pytest.mark.asyncio
async def test_the_unauthenticated_apply_form_cannot_re_grant_a_withdrawn_opt_in(
    db: AsyncSession,
) -> None:
    """Anybody can type anybody's email into a public application form. A
    withdrawal has to be sticky against everyone but its owner — the same
    reasoning ``public_apply.py::_record_apply_consent`` had to apply to the
    same door."""
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    await svc.revoke_opt_ins(db, user_id=f.cand1)

    refused = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                            applicant_id=f.applicant1, source="public_apply_form")
    assert refused["state"] == "not_regranted_after_withdrawal"
    assert refused["consent_id"] is None
    assert (await _search(db, f))["universe"]["eligible"] == 0

    # The owner, signed in on their own page, may opt back in.
    regranted = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                              applicant_id=f.applicant1, source="my_applications")
    assert regranted["state"] == "granted"
    assert (await _search(db, f))["universe"]["eligible"] == 1


@pytest.mark.asyncio
async def test_an_unknown_opt_in_source_is_refused(db: AsyncSession) -> None:
    f = await _seed(db)
    with pytest.raises(svc.RediscoveryError) as caught:
        await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                      applicant_id=f.applicant1, source="somewhere_else")
    assert caught.value.code == "unknown_opt_in_source"


@pytest.mark.asyncio
async def test_the_displayed_expiry_matches_what_postgres_enforces(
    db: AsyncSession,
) -> None:
    """``add_months`` is calendar arithmetic because the query's bound is
    ``make_interval(months => …)``. Pinned against the database rather than
    against another Python implementation of the same idea."""
    f = await _seed(db)
    out = await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a,
                        applicant_id=f.applicant1)
    from_db = await db.scalar(
        text(
            "SELECT granted_at + make_interval(months => :m) FROM dpdp_consent_ledger"
            " WHERE id = :i"
        ),
        {"m": settings.rediscovery_consent_months, "i": uuid.UUID(out["consent_id"])},
    )
    assert from_db is not None
    assert datetime.fromisoformat(out["expires_at"]) == from_db


# ===========================================================================
# Tenancy (criterion 15) — the disclosure this design has to refuse
# ===========================================================================
@pytest.mark.asyncio
async def test_company_b_never_sees_a_candidate_whose_consent_names_company_a(
    db: AsyncSession,
) -> None:
    """A consent that names company A must not make an applicant row at company
    B findable. Without ``evidence ->> 'company_id' = a.company_id::text`` in the
    JOIN, ``l.user_id = a.user_id`` alone matches the two.

    A NOTE ON THE SCENARIO, because the design predicted a different one. The
    design reasoned from "a candidate may hold applicant rows at several
    companies under one user_id after activation" — but
    ``uq_applicants_user_id`` (migration ``a7b8c9d0e1f2``, "one guest user per
    applicant") is a PLATFORM-WIDE unique index on ``applicants.user_id``, so
    that state is currently unreachable: one account, at most one applicant row,
    anywhere. The disclosure is reachable the other way round — one identity
    whose applicant row is at B and whose rediscovery consent names A — which is
    what this test builds. The predicate is required either way, and it stops
    being merely defensive the moment that index is relaxed (it is about
    redemption idempotency, not about tenancy, so nothing guarantees it stays).
    """
    f = await _seed(db)
    now = datetime.now(tz=UTC)
    stranger = uuid.uuid4()
    applicant_at_b = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u,:e,:cb)"),
        {"u": stranger, "e": f"stranger-{f.tag}@e3.test", "cb": f.company_b},
    )
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
            " target_job_title, resume_text, created_at, updated_at)"
            " VALUES (:i,:c,:u,'Chandra M',:e,'Maintenance Fitter',:r,:t,:t)"
        ),
        {"i": applicant_at_b, "c": f.company_b, "u": stranger,
         "e": f"chandra-{f.tag}@e3.test", "r": RESUME_MATCH, "t": now},
    )
    await db.execute(
        text("UPDATE applicants SET embedding = CAST(:v AS halfvec) WHERE id = :i"),
        {"v": to_pgvector_literal(fake_embeddings([QUERY])[0]), "i": applicant_at_b},
    )
    # Chandra's only opt-in names company A; Asha's names A too and she has an
    # applicant row there.
    await _opt_in(db, f, user_id=stranger, company_id=f.company_a,
                  applicant_id=applicant_at_b)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)

    at_a = await _search(db, f, company_id=f.company_a, hr_user_id=f.hr_a)
    at_b = await _search(db, f, company_id=f.company_b, hr_user_id=f.hr_b)
    assert [r["applicant_id"] for r in at_a["results"]] == [str(f.applicant1)]
    assert at_b["results"] == []
    assert at_b["universe"] == {"eligible": 0, "total": 1}

    # Then B gets its own opt-in for its own applicant, and sees exactly that.
    await _opt_in(db, f, user_id=stranger, company_id=f.company_b,
                  applicant_id=applicant_at_b)
    at_b = await _search(db, f, company_id=f.company_b, hr_user_id=f.hr_b)
    assert [r["applicant_id"] for r in at_b["results"]] == [str(applicant_at_b)]
    # A's view is unchanged: one consent per (person, company), and B's grant is
    # not A's.
    at_a = await _search(db, f, company_id=f.company_a, hr_user_id=f.hr_a)
    assert [r["applicant_id"] for r in at_a["results"]] == [str(f.applicant1)]


@pytest.mark.asyncio
async def test_the_company_filter_is_in_the_executed_sql_not_in_python(
    db: AsyncSession,
) -> None:
    """Asserted on the STATEMENT, so the filter cannot be moved into Python
    without a red test. A post-filter would mean a row the caller may not use
    was fetched, and therefore could be counted, logged or cited."""
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    seen: list[str] = []

    original = db.execute

    async def _spy(statement: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(str(statement))
        return await original(statement, *args, **kwargs)

    db.execute = _spy  # type: ignore[method-assign]
    try:
        await _search(db, f)
    finally:
        db.execute = original  # type: ignore[method-assign]

    ranking = [s for s in seen if "eligible" in s and "ORDER BY" in s]
    assert ranking, seen
    for statement in ranking:
        assert "l.evidence ->> 'company_id' = a.company_id::text" in statement
        assert "a.company_id = :company_id" in statement
        assert "erasure_requests" in statement
        assert "l.granted_at > now() - make_interval(months => :months)" in statement


@pytest.mark.asyncio
async def test_an_erasure_request_alone_removes_a_candidate_from_the_search(
    db: AsyncSession,
) -> None:
    """No executor run. The request is enough: the CTE's anti-join is one path,
    and admin_ops' request handler revoking every ledger row is the other."""
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    assert (await _search(db, f))["universe"]["eligible"] == 1
    await db.execute(
        text(
            "INSERT INTO erasure_requests (user_id, requested_by, scheduled_for)"
            " VALUES (:u, :u, now())"
        ),
        {"u": f.cand1},
    )
    payload = await _search(db, f)
    assert payload["universe"]["eligible"] == 0
    assert payload["results"] == []


@pytest.mark.asyncio
async def test_a_bulk_uploaded_cv_with_no_identity_is_not_rediscoverable(
    db: AsyncSession,
) -> None:
    """``applicants.user_id IS NULL`` drops out of the JOIN for free — the
    honest consequence of D5-1 being opt-in, and the reason the screen has to
    say how few candidates it can see."""
    f = await _seed(db)
    await db.execute(
        text("UPDATE applicants SET user_id = NULL WHERE id = :a"), {"a": f.applicant1}
    )
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    assert (await _search(db, f))["universe"]["eligible"] == 0


# ===========================================================================
# Activation moves BOTH companies' opt-ins (§2.1 / NEW-10, one shape along)
# ===========================================================================
@pytest.mark.asyncio
async def test_activating_an_account_moves_every_companys_opt_in(
    db: AsyncSession,
) -> None:
    """``_link_to_existing`` called directly: the token, bcrypt and email path
    around it is covered by the activation tests, and this is about the ledger
    move alone. Before the fix, the second company's row looked like a duplicate
    of the first (same type, same purpose) and stayed on the guest identity that
    the next statement tombstones."""
    f = await _seed(db)
    guest = uuid.uuid4()
    target = uuid.uuid4()
    applicant_at_b = uuid.uuid4()
    now = datetime.now(tz=UTC)
    # The target account holds no applicant row of its own: `uq_applicants_user_id`
    # is a platform-wide unique index, so the guest's applicant row could not
    # move onto an account that already had one. (That is a real limit of
    # activation, not of this test — noted in the wave report.)
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:g,:eg,:cb),(:t,:et,NULL)"),
        {"g": guest, "eg": f"guest-{f.tag}@applicants.invalid", "cb": f.company_b,
         "t": target, "et": f"target-{f.tag}@e3.test"},
    )
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
            " target_job_title, resume_text, created_at, updated_at)"
            " VALUES (:i,:c,:u,'Asha K',:e,'Fitter',:r,:t,:t)"
        ),
        {"i": applicant_at_b, "c": f.company_b, "u": guest,
         "e": f"asha-b2-{f.tag}@e3.test", "r": RESUME_MATCH, "t": now},
    )
    await db.execute(
        text("UPDATE applicants SET embedding = CAST(:v AS halfvec) WHERE id = :i"),
        {"v": to_pgvector_literal(fake_embeddings([QUERY])[0]), "i": applicant_at_b},
    )
    # The TARGET already holds an opt-in at company A; the guest holds one at
    # company B. Same consent_type, same purpose, different company — which is
    # exactly what used to make the guest's row look like a duplicate and leave
    # it on an identity the next statement tombstones.
    await _opt_in(db, f, user_id=target, company_id=f.company_a, applicant_id=f.applicant1)
    await _opt_in(db, f, user_id=guest, company_id=f.company_b, applicant_id=applicant_at_b)

    await _link_to_existing(db, guest_user_id=guest, target_user_id=target, now=now)

    owned = (
        await db.execute(
            text(
                "SELECT evidence ->> 'company_id' AS cid FROM dpdp_consent_ledger"
                " WHERE user_id = :u AND consent_type = :ct AND granted"
                "   AND revoked_at IS NULL ORDER BY granted_at"
            ),
            {"u": target, "ct": svc.REDISCOVERY_CONSENT_TYPE},
        )
    ).mappings().all()
    assert {row["cid"] for row in owned} == {str(f.company_a), str(f.company_b)}
    left_behind = await db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger WHERE user_id = :g"
            "   AND consent_type = :ct AND granted AND revoked_at IS NULL"
        ),
        {"g": guest, "ct": svc.REDISCOVERY_CONSENT_TYPE},
    )
    assert left_behind == 0
    # The moved consent still satisfies the CTE at its own company, against the
    # applicant row that moved with it.
    at_b = await _search(db, f, company_id=f.company_b, hr_user_id=f.hr_b)
    assert [r["applicant_id"] for r in at_b["results"]] == [str(applicant_at_b)]


@pytest.mark.asyncio
async def test_a_duplicate_opt_in_left_on_a_tombstoned_guest_is_revoked_not_left_granted(
    db: AsyncSession,
) -> None:
    """The one row that genuinely cannot move: the target already holds an
    active opt-in for the SAME company, so the unique index would refuse a
    second. It is revoked rather than left reading `granted = TRUE` on an
    identity nothing can reach again."""
    f = await _seed(db)
    guest = uuid.uuid4()
    target = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:g,:eg,:ca),(:t,:et,NULL)"),
        {"g": guest, "eg": f"guest2-{f.tag}@applicants.invalid", "ca": f.company_a,
         "t": target, "et": f"target2-{f.tag}@e3.test"},
    )
    await _opt_in(db, f, user_id=target, company_id=f.company_a, applicant_id=f.applicant1)
    await _opt_in(db, f, user_id=guest, company_id=f.company_a, applicant_id=f.applicant1)

    await _link_to_existing(db, guest_user_id=guest, target_user_id=target, now=now)

    stranded = (
        await db.execute(
            text(
                "SELECT revoked_at, evidence ->> 'revoked_reason' AS reason"
                "  FROM dpdp_consent_ledger WHERE user_id = :g AND consent_type = :ct"
            ),
            {"g": guest, "ct": svc.REDISCOVERY_CONSENT_TYPE},
        )
    ).mappings().all()
    assert len(stranded) == 1
    assert stranded[0]["revoked_at"] is not None
    assert stranded[0]["reason"] == "superseded_by_activation"


# ===========================================================================
# Evidence hydration: independence, redaction, purged AI interviews, staleness
# ===========================================================================
async def _seed_round(
    db: AsyncSession, f: F, *, kind: str = "human_review", competency: str = "problem_solving",
    title: str = "Maintenance Fitter",
) -> dict[str, uuid.UUID]:
    """A requisition, a published workflow, one round with one frozen criterion,
    and an enrolment for applicant 1.

    ``title`` is a parameter because ``uq_job_requisitions_company_title`` means
    a company cannot hold two live openings with the same name — a test that
    seeds two rounds needs two openings.
    """
    ids = {k: uuid.uuid4() for k in ("req", "workflow", "round", "criterion", "enrolment")}
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, level, status,"
            " created_at, updated_at) VALUES (:i,:c,:ti,'mid','open',:t,:t)"
        ),
        {"i": ids["req"], "c": f.company_a, "ti": title, "t": now},
    )
    # A workflow cannot be INSERTed published (PH4-O6: every version starts as
    # an unreviewed draft and walks the review lifecycle), and its rounds freeze
    # once it is. So: draft, rounds, criteria, then submit → approve → publish,
    # with a reviewer who is neither the author nor the submitter.
    await db.execute(
        text(
            "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
            " created_at, updated_at) VALUES (:i,:c,:r,1,'draft',:t,:t)"
        ),
        {"i": ids["workflow"], "c": f.company_a, "r": ids["req"], "t": now},
    )
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " created_at, updated_at) VALUES (:i,:c,:w,1,'Round 2',:k,:t,:t)"
        ),
        {"i": ids["round"], "c": f.company_a, "w": ids["workflow"], "k": kind, "t": now},
    )
    await db.execute(
        text(
            "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
            " competency_name, weight, created_at)"
            " VALUES (:i,:c,:r,:cid,'Fault Diagnosis',1.0,:t)"
        ),
        {"i": ids["criterion"], "c": f.company_a, "r": ids["round"], "cid": competency, "t": now},
    )
    for sql in (
        "UPDATE workflows SET review_status='in_review', submitted_by_user_id=:sub,"
        " review_fingerprint='fp', submitted_for_review_at=:t, updated_at=:t WHERE id=:w",
        "UPDATE workflows SET review_status='approved', reviewed_by_user_id=:rev,"
        " reviewed_at=:t, updated_at=:t WHERE id=:w",
        "UPDATE workflows SET status='published', published_at=:t, updated_at=:t WHERE id=:w",
    ):
        await db.execute(
            text(sql), {"w": ids["workflow"], "sub": f.hr_a, "rev": f.peer, "t": now}
        )
    await db.execute(
        text(
            "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
            " target_job_title, target_level, workflow_id, created_at, updated_at)"
            " VALUES (:i,:c,:r,:a,'new','Maintenance Fitter','mid',:w,:t,:t)"
        ),
        {"i": ids["enrolment"], "c": f.company_a, "r": ids["req"], "a": f.applicant1,
         "w": ids["workflow"], "t": now},
    )
    return ids


async def _seed_scorecard(
    db: AsyncSession, f: F, ids: dict[str, uuid.UUID], *, interviewer: uuid.UUID,
    status: str = "submitted", score: int | None = 4, age_days: int = 10,
    redacted: bool = False, superseded: bool = False, competency: str = "problem_solving",
) -> uuid.UUID:
    card = uuid.uuid4()
    when = datetime.now(tz=UTC) - timedelta(days=age_days)
    # Scores are frozen once a scorecard is submitted (PH4-A1's own trigger), so
    # the seed walks the same path the app does: assigned → scores → submitted.
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, created_at, updated_at)"
            " VALUES (:i,:c,:e,:r,:u,'assigned',:t,:t)"
        ),
        {"i": card, "c": f.company_a, "e": ids["enrolment"], "r": ids["round"],
         "u": interviewer, "t": when},
    )
    if score is not None:
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
                " competency_id, score, evidence)"
                " VALUES (:s,:c,:r,:cid,:sc,'He explained the fault clearly.')"
            ),
            {"s": card, "c": f.company_a, "r": ids["round"], "cid": competency, "sc": score},
        )
    if status != "assigned":
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET status = :s, submitted_at = :sub,"
                " updated_at = :t WHERE id = :i"
            ),
            {"i": card, "s": status, "sub": when if status == "submitted" else None, "t": when},
        )
    if redacted:
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET redacted_at = :red, updated_at = :t"
                " WHERE id = :i"
            ),
            {"i": card, "red": when, "t": when},
        )
    if superseded:
        # The correction dance, in the order the app has to use it: the old row
        # is marked superseded FIRST (uq_interviewer_scorecards_live allows only
        # one live card per round/enrolment/interviewer), naming a successor that
        # does not exist yet — which is why that FK is DEFERRED — and the
        # successor is inserted second. ck_..._superseded_pair and
        # ck_..._correction_pair both travel in pairs.
        successor = uuid.uuid4()
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET superseded_at = :sup,"
                " superseded_by_id = :by, updated_at = :t WHERE id = :i"
            ),
            {"i": card, "sup": when, "by": successor, "t": when},
        )
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
                " interviewer_user_id, status, corrects_id, correction_reason,"
                " created_at, updated_at)"
                " VALUES (:i,:c,:e,:r,:u,'assigned',:old,'Re-scored after review',:t,:t)"
            ),
            {"i": successor, "c": f.company_a, "e": ids["enrolment"], "r": ids["round"],
             "u": interviewer, "old": card, "t": when},
        )
    return card


@pytest.mark.asyncio
async def test_a_human_scorecard_appears_as_evidence_with_its_score_and_citation(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.peer)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)

    payload = await _search(db, f, requisition_id=ids["req"])
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    scored = next(w for w in top["why"] if w["signal"] == "interviewer_scorecard")
    assert scored["score"] == 4
    assert scored["competency_id"] == "problem_solving"
    assert scored["competency"] == "Fault Diagnosis"
    assert scored["produced_by"] == "human"
    assert scored["citation"]["href"] == f"/hr/enrolments/{ids['enrolment']}/evidence"
    # The prose is never fetched, let alone returned.
    assert "He explained the fault clearly." not in str(payload)
    # With a target opening, the covered competency lifts the score.
    assert payload["target"]["competencies"] == 1
    assert top["breakdown"]["coverage"] == pytest.approx(1.0)
    assert top["breakdown"]["evidence_boost"] > 0
    assert top["explained"] is True


@pytest.mark.asyncio
async def test_a_rediscovery_result_withholds_a_peers_score_for_a_round_the_viewer_still_owes(
    db: AsyncSession,
) -> None:
    """PH4-A1 independence, in the rediscovery SQL. An hr_manager can also sit
    on a panel, so without this predicate rediscovery is a side door around the
    blinding rule the evidence screen enforces."""
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.peer)          # peer submitted
    await _seed_scorecard(db, f, ids, interviewer=f.hr_a, status="assigned", score=None)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)

    # hr_a still owes their own scorecard for this round.
    owing = await _search(db, f, hr_user_id=f.hr_a, requisition_id=ids["req"])
    top = next(r for r in owing["results"] if r["applicant_id"] == str(f.applicant1))
    assert not [w for w in top["why"] if w["signal"] == "interviewer_scorecard"]
    assert top["breakdown"]["evidence_boost"] == 0.0

    # A viewer who owes nothing for that round sees it.
    other_hr = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u,:e,:c)"),
        {"u": other_hr, "e": f"hr-c-{f.tag}@e3.test", "c": f.company_a},
    )
    free = await _search(db, f, hr_user_id=other_hr, requisition_id=ids["req"])
    top2 = next(r for r in free["results"] if r["applicant_id"] == str(f.applicant1))
    assert [w["score"] for w in top2["why"] if w["signal"] == "interviewer_scorecard"] == [4]


@pytest.mark.asyncio
async def test_the_viewer_still_sees_their_own_submitted_scorecard(
    db: AsyncSession,
) -> None:
    """Blinding is about PEERS' content, not one's own: the rule is "hidden
    until you submit", and the viewer's own submitted card is theirs to see."""
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.hr_a)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f, hr_user_id=f.hr_a, requisition_id=ids["req"])
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    assert [w["score"] for w in top["why"] if w["signal"] == "interviewer_scorecard"] == [4]


@pytest.mark.asyncio
async def test_a_redacted_scorecard_keeps_its_score_and_a_superseded_one_contributes_nothing(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.peer, redacted=True)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f, requisition_id=ids["req"])
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    item = next(w for w in top["why"] if w["signal"] == "interviewer_scorecard")
    assert item["score"] == 4
    assert item["lifecycle"] == "redacted"
    assert item["freshness"] == "unverifiable"
    # Redacted evidence cannot prove a competency, so it cannot lift the rank.
    assert top["breakdown"]["evidence_boost"] == 0.0
    assert top["evidence_freshness"] == "unverifiable"
    assert "evidence_unverifiable" in top["review_reasons"]


@pytest.mark.asyncio
async def test_a_superseded_scorecard_is_absent_entirely(db: AsyncSession) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.peer, superseded=True)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f, requisition_id=ids["req"])
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    assert not [w for w in top["why"] if w["signal"] == "interviewer_scorecard"]
    assert top["evidence_freshness"] == "none"


@pytest.mark.asyncio
async def test_a_draft_scorecard_is_never_evidence(db: AsyncSession) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.peer, status="in_progress", score=None)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f, requisition_id=ids["req"])
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    assert not [w for w in top["why"] if w["signal"] == "interviewer_scorecard"]


@pytest.mark.asyncio
async def test_evidence_bands_are_per_item_and_the_header_takes_the_worse(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _seed_scorecard(db, f, ids, interviewer=f.peer, age_days=330)   # ageing
    ids2 = await _seed_round(db, f, title="Senior Fitter")
    await _seed_scorecard(db, f, ids2, interviewer=f.peer, age_days=740)  # stale
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)

    payload = await _search(db, f)
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    bands = sorted(
        w["freshness"] for w in top["why"] if w["signal"] == "interviewer_scorecard"
    )
    assert bands == ["ageing", "stale"]
    assert top["evidence_freshness"] == "stale"
    assert "evidence_stale" in top["review_reasons"]
    assert top["requires_review"] is True


@pytest.mark.asyncio
async def test_a_purged_ai_interview_contributes_the_fact_and_no_score_anywhere(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    job = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO jobs (id, title, description, level) VALUES"
            " (:i,'Fitter','A maintenance role','mid')"
        ),
        {"i": job},
    )
    await db.execute(
        text(
            "INSERT INTO interview_invites (id, company_id, applicant_id, job_id, token_hash,"
            " expires_at, status, session_id, created_at, updated_at)"
            " VALUES (:i,:c,:a,:j,'x',:exp,'completed',NULL,:t,:t)"
        ),
        {"i": uuid.uuid4(), "c": f.company_a, "a": f.applicant1, "j": job,
         "exp": datetime.now(tz=UTC) - timedelta(days=100),
         "t": datetime.now(tz=UTC) - timedelta(days=120)},
    )
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)

    payload = await _search(db, f)
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    item = next(w for w in top["why"] if w["signal"] == "ai_interview")
    assert item["lifecycle"] == "purged"
    assert item["freshness"] == "unverifiable"
    assert item["contribution"] == 0
    assert "purged under the 90-day retention rule" in item["content_hidden_reason"]
    assert item.get("score") is None
    assert item.get("percent") is None
    assert item["citation"]["kind"] == "interview"


@pytest.mark.asyncio
async def test_a_round_result_and_an_exam_attempt_contribute_numbers_only(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO round_results (id, company_id, enrolment_id, round_id, percent,"
            " passed, criterion_scores, graded_by, evidence, created_at)"
            " VALUES (:i,:c,:e,:r,72,true,CAST(:cs AS jsonb),'human','private rationale',:t)"
        ),
        {"i": uuid.uuid4(), "c": f.company_a, "e": ids["enrolment"], "r": ids["round"],
         "cs": '{"problem_solving": 4}', "t": now - timedelta(days=30)},
    )
    exam, exam_round = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO exams (id, company_id, title, status)"
            " VALUES (:i,:c,'Trade test','published')"
        ),
        {"i": exam, "c": f.company_a},
    )
    await db.execute(
        text(
            "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, position)"
            " VALUES (:i,:x,:c,1,'Paper 1',1)"
        ),
        {"i": exam_round, "x": exam, "c": f.company_a},
    )
    await db.execute(
        text(
            "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
            " score_percent, passed, status, started_at, submitted_at, answers)"
            " VALUES (:i,:c,:x,:r,:a,81,true,'submitted',:t,:t,CAST(:ans AS jsonb))"
        ),
        {"i": uuid.uuid4(), "c": f.company_a, "x": exam, "r": exam_round,
         "a": f.applicant1, "t": now - timedelta(days=40),
         "ans": '{"coding": [{"source": "print(secret)"}]}'},
    )
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)

    payload = await _search(db, f, requisition_id=ids["req"])
    top = next(r for r in payload["results"] if r["applicant_id"] == str(f.applicant1))
    signals = {w["signal"]: w for w in top["why"]}
    assert signals["round_result"]["percent"] == pytest.approx(72.0)
    assert signals["round_result"]["passed"] is True
    assert signals["round_result"]["competency_ids"] == ["problem_solving"]
    assert signals["exam_attempt"]["percent"] == pytest.approx(81.0)
    # Neither the round's rationale nor the candidate's code ever leaves the DB.
    blob = str(payload)
    assert "private rationale" not in blob
    assert "print(secret)" not in blob
    # A round result's criterion key covers the target competency.
    assert top["breakdown"]["coverage"] == pytest.approx(1.0)


# ===========================================================================
# Degradation, targets and the audit row
# ===========================================================================
@pytest.mark.asyncio
async def test_the_search_degrades_to_keyword_only_and_says_so(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    await _opt_in(db, f, user_id=f.cand2, company_id=f.company_a, applicant_id=f.applicant2)

    async def _boom(**_kw: Any) -> list[float]:
        raise EmbeddingError("embedding service down")

    monkeypatch.setattr(svc, "embed_one_remote", _boom)
    payload = await _search(db, f)
    assert payload["semantic"] is False
    # Only the candidate with an actual keyword hit comes back.
    assert [r["full_name"] for r in payload["results"]] == ["Asha K"]
    top = payload["results"][0]
    assert top["breakdown"]["semantic_available"] is False
    assert [w["signal"] for w in top["why"]] == ["resume_terms"]
    assert top["explained"] is True


@pytest.mark.asyncio
async def test_an_opening_from_another_company_is_a_404_never_a_403(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    ids = await _seed_round(db, f)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    with pytest.raises(svc.RediscoveryError) as caught:
        await _search(db, f, company_id=f.company_b, hr_user_id=f.hr_b,
                      requisition_id=ids["req"])
    assert caught.value.status_code == 404
    assert caught.value.code == "opening_not_found"


@pytest.mark.asyncio
async def test_an_opening_with_no_frozen_criteria_gives_no_boost_and_says_why(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    req = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, level, status,"
            " created_at, updated_at) VALUES (:i,:c,'Fitter','mid','open',:t,:t)"
        ),
        {"i": req, "c": f.company_a, "t": now},
    )
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f, requisition_id=req)
    assert payload["target"]["source"] == "none"
    assert payload["target"]["competencies"] == 0
    assert "no published workflow criteria" in payload["target"]["reason"]
    assert all(r["breakdown"]["evidence_boost"] == 0.0 for r in payload["results"])


@pytest.mark.asyncio
async def test_a_similarity_only_match_sorts_below_an_explained_one_at_equal_score(
    db: AsyncSession,
) -> None:
    """Two candidates whose vectors are identical, one of whom also matches a
    keyword: the explained row must come first, and the other must be labelled
    rather than narrated."""
    f = await _seed(db)
    await db.execute(
        text(
            "UPDATE applicants SET resume_text = 'Unrelated prose about nothing in"
            " particular', embedding = CAST(:v AS halfvec) WHERE id = :i"
        ),
        {"v": to_pgvector_literal(fake_embeddings([QUERY])[0]), "i": f.applicant2},
    )
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    await _opt_in(db, f, user_id=f.cand2, company_id=f.company_a, applicant_id=f.applicant2)

    payload = await _search(db, f)
    assert [r["full_name"] for r in payload["results"]] == ["Asha K", "Bala R"]
    unexplained = payload["results"][1]
    assert unexplained["explained"] is False
    assert unexplained["unexplained_note"] == svc.UNEXPLAINED_NOTE
    assert "unexplained_match" in unexplained["review_reasons"]
    similarity = next(w for w in unexplained["why"] if w["signal"] == "resume_similarity")
    assert similarity["explainable"] is False
    assert similarity["note"] == svc.SIMILARITY_NOTE


@pytest.mark.asyncio
async def test_the_search_audit_row_carries_no_query_and_no_candidate_name(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f, query="Asha hydraulics")
    svc.record_search_audit(
        db, actor_id=f.hr_a, company_id=f.company_a, payload=payload,
        query_length=len("Asha hydraulics"),
    )
    await db.flush()
    row = (
        await db.execute(
            text(
                "SELECT details FROM audit_log WHERE action = 'rediscovery.searched'"
                "   AND resource_id = :c"
            ),
            {"c": f.company_a},
        )
    ).mappings().one()
    blob = str(row["details"])
    assert "Asha" not in blob
    assert "hydraulics" not in blob
    assert row["details"]["query_length"] == 15
    assert row["details"]["results"] == 1


@pytest.mark.asyncio
async def test_the_snippet_ships_bracket_markers_and_never_html(
    db: AsyncSession,
) -> None:
    """``ts_headline`` is asked for bracket delimiters, so no consumer of this
    response needs ``dangerouslySetInnerHTML`` to show a highlight."""
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1, company_id=f.company_a, applicant_id=f.applicant1)
    payload = await _search(db, f)
    terms = next(
        w for w in payload["results"][0]["why"] if w["signal"] == "resume_terms"
    )
    assert "[[" in terms["snippet"]
    assert "<b>" not in terms["snippet"]
    assert len(terms["snippet"]) <= svc.SNIPPET_CHARS
