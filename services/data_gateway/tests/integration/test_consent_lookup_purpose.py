"""The generic consent route finds each consent type under the purpose it is
RECORDED with — against a real, migrated Postgres.

``_find_active_consent`` was fixed on purpose 'interview'. The PH4-A4
documents consent is recorded for 'onboarding' at offer acceptance, so
``DELETE /consent`` never found it and left it standing while the router's
own comment said it revoked it. Found by the Wave 5 acceptance evidence pass.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.routers.consent import _VALID_CONSENT_TYPES, _find_active_consent

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """One transaction per test, always rolled back — nothing is left behind."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
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


async def _user_with_consent(db: AsyncSession, consent_type: str, purpose: str) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (:i, :e)"),
        {"i": uid, "e": f"consent-{uid.hex[:12]}@lookup.test"},
    )
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger (id, user_id, consent_type, granted, granted_at,"
            " purpose, evidence) VALUES (gen_random_uuid(), :u, :t, true, now(), :p,"
            " '{}'::jsonb)"
        ),
        {"u": uid, "t": consent_type, "p": purpose},
    )
    return uid


@pytest.mark.asyncio
async def test_the_documents_consent_is_found_under_its_onboarding_purpose(
    db: AsyncSession,
) -> None:
    uid = await _user_with_consent(db, "preboarding_documents", "onboarding")
    row = await _find_active_consent(db, str(uid), "preboarding_documents")
    assert row is not None
    assert row.purpose == "onboarding"


@pytest.mark.asyncio
async def test_the_interview_consents_are_still_found_under_interview(
    db: AsyncSession,
) -> None:
    uid = await _user_with_consent(db, "interview_voice_recording", "interview")
    assert await _find_active_consent(db, str(uid), "interview_voice_recording") is not None


def test_the_revoke_route_still_covers_the_documents_consent() -> None:
    """``DELETE /consent`` revokes every type in this set; the documents
    consent must stay in it now that the lookup can actually find it."""
    assert "preboarding_documents" in _VALID_CONSENT_TYPES
    # PH4-D4's task consent is withdrawn on the task link, never here.
    assert "assessment_submission" not in _VALID_CONSENT_TYPES
