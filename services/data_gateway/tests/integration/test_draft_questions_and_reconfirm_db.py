"""A saved draft can be finished — audit CA-H1 and CA-M22.

CA-H1: ``submit_draft`` validates the opening's required questions against the
draft's stored answers, but the draft never told the page what the questions
were, so a draft for an opening with a required question could never be
submitted. The draft now carries its opening's questions, and answers saved
through ``PATCH /apply/draft`` are what submission validates.

CA-M22: a confirmation vouches for particular values. Editing a confirmed
detail and saving used to leave ``confirmed_at`` set, so submission sent values
nobody confirmed under a timestamp that said they had. Saving a changed detail
now clears it; saving only answers, or the same values again, does not.

Same harness as ``test_ph5_e3_draft_rediscovery_db.py``: the router functions
called directly against this repo's migrated Postgres, the CV written straight
onto the row so no S3 or PDF parser is involved.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_drafts import hash_token
from app.application_questions import add_question
from app.config import settings
from app.routers.public_apply import (
    DraftFieldsIn,
    DraftStartIn,
    confirm_draft,
    resume_draft,
    start_draft,
    submit_draft,
    update_draft,
)
from tests.integration.test_ph5_e3_draft_rediscovery_db import (
    _FakeRequest,
    _seed_company,
    _seed_open_requisition,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _draft_with_required_question(db: AsyncSession) -> tuple[str, str]:
    """An opening asking one required yes/no question, and a draft against it
    that has a CV on file. Returns ``(token, question_id)``."""
    company_id = uuid.uuid4()
    await _seed_company(db, company_id)
    req_id = await _seed_open_requisition(db, company_id=company_id, title="Welder")
    question_id = await add_question(
        db, company_id=company_id, requisition_id=req_id,
        prompt="Can you work night shifts?", kind="yes_no", required=True,
    )
    await db.commit()
    started = await start_draft(
        requisition_id=req_id,
        body=DraftStartIn(
            email=f"dev-{uuid.uuid4().hex[:10]}@draftq.example", consent_granted=True,
        ),
        request=_FakeRequest(),  # type: ignore[arg-type]
        db=db,
    )
    await db.execute(
        text("UPDATE application_drafts SET resume_s3_key = 'drafts/x.pdf' WHERE token_hash = :h"),
        {"h": hash_token(started.resume_token)},
    )
    await db.commit()
    return started.resume_token, str(question_id)


@pytest.mark.asyncio
async def test_the_draft_carries_its_openings_questions(db: AsyncSession) -> None:
    token, question_id = await _draft_with_required_question(db)
    out = await resume_draft(db=db, token=token)  # type: ignore[arg-type]
    assert [q.id for q in out.questions] == [question_id]
    assert out.questions[0].required is True
    assert out.questions[0].kind == "yes_no"


@pytest.mark.asyncio
async def test_answers_saved_on_the_draft_let_it_be_submitted(db: AsyncSession) -> None:
    token, question_id = await _draft_with_required_question(db)
    await confirm_draft(body=DraftFieldsIn(full_name="Devi P"), db=db, token=token)  # type: ignore[arg-type]

    # Without an answer the required question refuses submission...
    with pytest.raises(HTTPException) as refused:
        await submit_draft(request=_FakeRequest(), db=db, token=token)  # type: ignore[arg-type]
    assert refused.value.status_code == 422

    # ...and with one saved through the draft's own save, it goes through.
    saved = await update_draft(
        body=DraftFieldsIn(answers={question_id: True}), db=db, token=token,  # type: ignore[arg-type]
    )
    assert saved.answers == {question_id: True}
    assert saved.confirmed is True  # answers are not a confirmed detail
    out = await submit_draft(request=_FakeRequest(), db=db, token=token)  # type: ignore[arg-type]
    assert out.already_applied is False


@pytest.mark.asyncio
async def test_editing_a_confirmed_detail_clears_the_confirmation(db: AsyncSession) -> None:
    token, _ = await _draft_with_required_question(db)
    confirmed = await confirm_draft(
        body=DraftFieldsIn(full_name="Devi P", phone="98480 22338"), db=db, token=token,  # type: ignore[arg-type]
    )
    assert confirmed.confirmed is True

    # The same values again (with stray whitespace) are not an edit.
    same = await update_draft(
        body=DraftFieldsIn(full_name=" Devi P ", phone="98480 22338"), db=db, token=token,  # type: ignore[arg-type]
    )
    assert same.confirmed is True

    # A changed name is: it must be confirmed again before it is submitted.
    edited = await update_draft(
        body=DraftFieldsIn(full_name="Devi Prasad"), db=db, token=token,  # type: ignore[arg-type]
    )
    assert edited.full_name == "Devi Prasad"
    assert edited.confirmed is False
    with pytest.raises(HTTPException) as refused:
        await submit_draft(request=_FakeRequest(), db=db, token=token)  # type: ignore[arg-type]
    assert refused.value.status_code == 422
    assert "confirm" in str(refused.value.detail).lower()


@pytest.mark.asyncio
async def test_changing_only_the_email_language_keeps_the_confirmation(
    db: AsyncSession,
) -> None:
    token, _ = await _draft_with_required_question(db)
    await confirm_draft(body=DraftFieldsIn(full_name="Devi P"), db=db, token=token)  # type: ignore[arg-type]
    out = await update_draft(body=DraftFieldsIn(language="te"), db=db, token=token)  # type: ignore[arg-type]
    assert out.language == "te"
    assert out.confirmed is True


@pytest.mark.asyncio
async def test_a_saved_draft_holds_only_bounded_answers_to_its_questions(
    db: AsyncSession,
) -> None:
    """PATCH /apply/draft is the live write path for answers (CA-H1), so it must
    not store keys that are not this opening's questions or values the form
    could never produce (CWE-770)."""
    token, question_id = await _draft_with_required_question(db)
    saved = await update_draft(
        body=DraftFieldsIn(answers={question_id: True, "not-a-question": "x" * 50}),
        db=db, token=token,  # type: ignore[arg-type]
    )
    assert saved.answers == {question_id: True}

    with pytest.raises(HTTPException) as refused:
        await update_draft(
            body=DraftFieldsIn(answers={question_id: {"nested": ["object"]}}),
            db=db, token=token,  # type: ignore[arg-type]
        )
    assert refused.value.status_code == 422
    await db.rollback()
    still = await resume_draft(db=db, token=token)  # type: ignore[arg-type]
    assert still.answers == {question_id: True}
