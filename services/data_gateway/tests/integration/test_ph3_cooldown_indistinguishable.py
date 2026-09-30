"""A rejected candidate must be indistinguishable from a live one — PH3-B4b.

WHY THIS FILE EXISTS, AND WHY IT IS AN INTEGRATION TEST.

The public apply endpoints are anonymous and accept any address, and the
requisition id is documented as not a secret. So every difference an outsider
can observe between "this address has a live application" and "this address was
REJECTED" is employment-outcome data about a third party, handed to whoever
typed the address.

Three separate reviews have now found that difference, in three different
places, each time after the previous one was "fixed":

  1. a 409 whose body named the date they could reapply;
  2. `awaiting_confirmation: true` plus the victim's own `applicant_id` and
     `enrolment_id` in the body;
  3. the draft row left in a different state, so a LATER call to the same
     endpoint answered 404 for one and 201 for the other.

Each fix closed the channel it was pointed at and left the difference intact
somewhere adjacent. The test that was supposed to prevent that —
`assert "HTTP_409_CONFLICT" not in inspect.getsource(fn)` — passed on a branch
where the reply was still distinguishable three ways, because reading the
source for one spelling cannot see a difference that has moved.

So this asserts the WHOLE observable output, through the real ASGI app against
a real database: status code, every byte of the body, and the post-conditions a
second request can read back. A difference cannot satisfy this test by
relocating; it has to actually not exist.

What is deliberately NOT asserted: response timing. The cooldown path does more
work than the already-applied path and always will, so a timing difference is
real and is recorded as an accepted risk rather than pretended away here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import get_session_factory
from app.main import app
from tests.integration.seed_helpers import approve_for_publish

pytestmark = pytest.mark.integration

def _pdf(lines: list[str]) -> bytes:
    """One page of selectable Helvetica text, with a correct xref table.

    The endpoint refuses a PDF it cannot read ("if it is a scan, please upload
    a text-based version"), which is right, and which a byte-string stub does
    not satisfy. Same shape as the e2e suite's makeCvPdf, in Python.
    """
    body = (
        "BT /F1 11 Tf 72 780 Td 16 TL\n"
        + "\n".join(f"({ln}) Tj T*" for ln in lines)
        + "\nET\n"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(body.encode('latin-1'))} >>\nstream\n{body}endstream",
    ]
    out = "%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(objects):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i + 1} 0 obj\n{obj}\nendobj\n"
    xref_at = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n"
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    )
    return out.encode("latin-1")


_PDF = _pdf(["Probe Person", "probe@example.com", "Backend engineer"])


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(  # noqa: SIM117
        transport=ASGITransport(app=app), base_url="http://test", timeout=60.0
    ) as ac:
        async with app.router.lifespan_context(app):
            yield ac


async def _seed_opening(cooldown_days: int) -> tuple[uuid.UUID, uuid.UUID]:
    """A published opening anyone may apply to, with a cooldown configured.

    Built with SQL rather than through the HR API because what is under test is
    the PUBLIC door: the fewer authenticated calls between the fixture and the
    assertion, the less there is to mistake for the thing being measured.
    """
    factory = get_session_factory()
    company_id, req_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO companies (id, name, slug, created_at, updated_at)"
                " VALUES (:i, :n, :s, now(), now())"
            ),
            {"i": company_id, "n": f"Oracle Co {company_id.hex[:8]}",
             "s": f"oracle-{company_id.hex[:8]}"},
        )
        await db.execute(
            text(
                "INSERT INTO job_requisitions"
                " (id, company_id, title, level, status, public_apply_enabled,"
                "  approval_status, approval_decided_at, reapply_cooldown_days,"
                "  target_hires, created_at, updated_at)"
                " VALUES (:i, :c, :t, 'mid', 'open', true, 'approved', now(), :cd,"
                "         1, now(), now())"
            ),
            {"i": req_id, "c": company_id, "t": f"Oracle Role {req_id.hex[:8]}",
             "cd": cooldown_days},
        )
        # The shared publish gate (PH3-B0) requires a PUBLISHED workflow, so
        # an opening without one 404s — which is correct, and is exactly the
        # drift that gate was created to stop.
        wf_id = uuid.uuid4()
        # Born an unreviewed draft, then reviewed, then published — a trigger
        # enforces that order (PH4-O6's two-person rule), so a fixture cannot
        # insert a published workflow directly and should not want to.
        await db.execute(
            text(
                "INSERT INTO workflows (id, company_id, requisition_id, version,"
                "  status, auto_score_on_apply, auto_assign_first_round,"
                "  auto_advance_rounds, created_at, updated_at)"
                " VALUES (:w, :c, :r, 1, 'draft', false, false, false, now(), now())"
            ),
            {"w": wf_id, "c": company_id, "r": req_id},
        )
        # draft -> in_review -> approved, by two distinct people, which is what
        # the trigger checks. The helper the smokes already use, rather than a
        # second hand-rolled copy of PH4-O6's rules.
        await approve_for_publish(db, workflow_id=wf_id, company_id=company_id)
        await db.execute(
            text(
                "UPDATE workflows SET status = 'published', published_at = now(),"
                "       updated_at = now() WHERE id = :w"
            ),
            {"w": wf_id},
        )
        await db.commit()
    return company_id, req_id


async def _clear_rate_limit() -> None:
    """Reset the apply bucket between probes.

    The route allows 6/min per client IP and the ASGI transport has no socket,
    so every request in this file arrives from one synthetic host. Clearing the
    counter keeps the limiter's real production value intact — what is under
    test is what the endpoint SAYS, and a 429 is neither of the two answers
    being compared.
    """
    from app.redis_client import get_redis  # noqa: PLC0415

    redis = get_redis()
    # By pattern rather than by a guessed host: the ASGI transport has no
    # socket, and which string `extract_client_ip` settles on is an
    # implementation detail this test has no business encoding.
    keys = [k async for k in redis.scan_iter(match="rl:public_apply_submit:*")]
    if keys:
        await redis.delete(*keys)


async def _apply(client: AsyncClient, req_id: uuid.UUID, email: str):  # noqa: ANN202
    await _clear_rate_limit()
    r = await client.post(
        f"/apply/{req_id}",
        data={"full_name": "Probe Person", "email": email, "consent_granted": "true"},
        files={"resume": ("cv.pdf", _PDF, "application/pdf")},
    )
    if r.status_code == 422:  # a fixture fault, not a finding — say which field
        raise AssertionError(f"apply rejected the fixture payload: {r.json()}")
    return r


async def _set_status(email: str, status: str) -> None:
    """Move this applicant's enrolment, the way HR's own decision would."""
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text(
                "UPDATE enrolments SET status = :s, updated_at = now()"
                " WHERE applicant_id IN (SELECT id FROM applicants"
                "                         WHERE lower(btrim(email)) = :em)"
            ),
            {"s": status, "em": email.lower()},
        )
        if status == "rejected":
            # The cooldown is measured from the REJECTION on the append-only
            # ledger, not from enrolments.updated_at, so it has to be there.
            await db.execute(
                text(
                    # reason_code is required on a move into rejected
                    # (PH4-O4) — a rejection with no recorded reason is not a
                    # decision this product lets anyone make.
                    "INSERT INTO stage_transitions"
                    " (company_id, enrolment_id, from_status, to_status,"
                    "  actor_user_id, automated, reason_code, reason_label,"
                    "  occurred_at)"
                    " SELECT e.company_id, e.id, 'new',"
                    "        'rejected', NULL, true, 'other', 'Other', now()"
                    "   FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                    "  WHERE lower(btrim(a.email)) = :em"
                ),
                {"em": email.lower()},
            )
        await db.commit()


@pytest.mark.asyncio
async def test_a_rejected_applicant_is_indistinguishable_from_a_live_one(
    client: AsyncClient,
) -> None:
    """The whole observable reply, byte for byte, on the one-shot door."""
    _, req_id = await _seed_opening(cooldown_days=90)

    live = f"live-{uuid.uuid4().hex[:10]}@example.com"
    gone = f"gone-{uuid.uuid4().hex[:10]}@example.com"
    assert (await _apply(client, req_id, live)).status_code == 201
    assert (await _apply(client, req_id, gone)).status_code == 201

    await _set_status(live, "shortlisted")
    await _set_status(gone, "rejected")

    second_live = await _apply(client, req_id, live)
    second_gone = await _apply(client, req_id, gone)

    assert second_live.status_code == second_gone.status_code, (
        "the status code alone must not say which of them was rejected"
    )
    assert second_live.json() == second_gone.json(), (
        "every field of the body: a flag, an id or a different sentence is the "
        "same disclosure the 409's date was"
    )
    body = second_gone.json()
    assert body["applicant_id"] == "", "never echo a stored id back to a stranger"
    assert body["enrolment_id"] is None


@pytest.mark.asyncio
async def test_the_reply_is_the_same_on_a_second_and_third_attempt(
    client: AsyncClient,
) -> None:
    """Post-conditions, not just the first reply.

    The draft door leaked this way: both branches returned the same body, but
    one consumed the draft and the other did not, so the NEXT call answered 404
    for a live application and 201 for a rejected one. A difference in what the
    endpoint leaves behind is as readable as a difference in what it says.
    """
    _, req_id = await _seed_opening(cooldown_days=90)

    live = f"live2-{uuid.uuid4().hex[:10]}@example.com"
    gone = f"gone2-{uuid.uuid4().hex[:10]}@example.com"
    await _apply(client, req_id, live)
    await _apply(client, req_id, gone)
    await _set_status(live, "shortlisted")
    await _set_status(gone, "rejected")

    for attempt in range(2):
        again_live = await _apply(client, req_id, live)
        again_gone = await _apply(client, req_id, gone)
        assert again_live.status_code == again_gone.status_code, f"attempt {attempt + 2}"
        assert again_live.json() == again_gone.json(), f"attempt {attempt + 2}"


@pytest.mark.asyncio
async def test_a_stranger_cannot_tell_a_rejection_from_never_having_applied(
    client: AsyncClient,
) -> None:
    """The third state, and the one an enumeration attack actually uses.

    Distinguishing "rejected" from "never applied here" is enough to confirm
    somebody applied to this company at all, which is the fact they would least
    want a stranger to hold.
    """
    _, req_id = await _seed_opening(cooldown_days=0)

    gone = f"gone3-{uuid.uuid4().hex[:10]}@example.com"
    await _apply(client, req_id, gone)
    await _set_status(gone, "rejected")

    unknown = f"nobody-{uuid.uuid4().hex[:10]}@example.com"
    reapplied = await _apply(client, req_id, gone)
    first_time = await _apply(client, req_id, unknown)

    assert reapplied.status_code == first_time.status_code
    for field in ("already_applied", "awaiting_confirmation", "message"):
        assert reapplied.json().get(field) == first_time.json().get(field), (
            f"{field} tells a stranger this address had applied before"
        )
