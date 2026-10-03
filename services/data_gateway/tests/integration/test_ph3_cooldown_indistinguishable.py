"""A rejected candidate must be indistinguishable from a live one — PH3-B4b.

WHY THIS FILE EXISTS, AND WHY IT IS AN INTEGRATION TEST.

The public apply endpoints are anonymous and accept any address, and the
requisition id is documented as not a secret. So every difference an outsider
can observe between "this address has a live application" and "this address was
REJECTED" is employment-outcome data about a third party, handed to whoever
typed the address.

SIX separate reviews have now found that difference, each in a new place,
each time after the previous one was "fixed":

  1. a 409 whose body named the date they could reapply;
  2. `awaiting_confirmation: true` plus the victim's own `applicant_id` and
     `enrolment_id` in the body;
  3. the draft row left in a different state, so a LATER call to the same
     endpoint answered 404 for one and 201 for the other;
  4. with every reply finally identical — what the SECOND submission read back.
     A rejected address was answered "accepted" on every submission for ever,
     while an address that had never applied was answered "accepted" once and
     "already applied" from the second time on. Two anonymous requests.

Each fix closed the channel it was pointed at and left the difference intact
somewhere adjacent. And this file did not catch (4), which is the part worth
dwelling on, because it was written to. The version of it that shipped:

  * repeated the probe only on live-vs-in-cooldown, the pair that does NOT
    differ, so the repetition it was proud of could not fail;
  * probed the pair that DOES differ exactly once each;
  * compared three field names rather than the body, and one of those names
    (`awaiting_confirmation`) had already been deleted from the response model,
    so that assertion compared None with None;
  * never sent a single request to the draft door, although finding (3) is a
    finding about the draft door.

So it is now a matrix and not a handful of chosen pairs: all four states, both
doors, repeated up to three times, comparing the status code and the entire
body — plus, on the draft door, what `GET /apply/draft` reads back afterwards.
A difference cannot satisfy this by relocating, and it cannot survive by living
in a request number nobody sent.

WHAT IS NOT ASSERTED HERE, stated plainly because an earlier version of this
paragraph claimed an acceptance that did not exist.

Response TIMING IS now asserted, and this paragraph has been wrong in both
directions — first claiming an acceptance nobody had granted, then saying
nothing in this file detects the difference after a test that detects it was
added to this file. Where it actually stands:

Every reply on both doors is held to a common deadline measured from before
that door's state-dependent work (`_reply`, `apply_reply_floor_ms`, default
400 ms), so a branch that finishes early waits.
`test_the_states_cannot_be_separated_by_how_long_the_reply_takes` runs on both
doors and fails if a reply arrives before the floor — which is what a removed,
disabled or bypassed floor looks like. `test_every_reply_from_both_doors_is_the
_same_object` counts the reply-bearing exits and requires every one of them to
go through `_reply` or `_refuse`.

What is NOT asserted is the spread between states. An earlier version compared
max-minus-min against the floor; it passed alone and failed under load,
because end-to-end time includes the CV upload — the caller-sized term the
floor deliberately excludes. It was measuring the design's declared noise, so
it was deleted rather than retuned: a constant tuned until a test goes green
proves only that the constant was tuned.

And the floor FAILS OPEN. A branch that overruns it logs
`public_apply.floor_exceeded` and answers immediately, so under enough
load the control switches itself off. There is no counter and no alert on that
log line. That residue, and the write-path 201/503 split the write probe does
not reach, are in docs/ACCEPTED-RISKS.md with an owner — not here, because
neither a test docstring nor a commit message can grant an acceptance.

Response HEADERS are not compared either (`_observable` reads status and body),
and neither are emails staged, auth-token rows minted, or behaviour under
concurrent submissions.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from unittest import mock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import settings
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
    # EVERY public-apply bucket, not just the submit one. The draft door goes
    # through `public_apply_draft_start` as well, and a 429 from a bucket this
    # helper forgot looks exactly like a failing assertion.
    keys = [k async for k in redis.scan_iter(match="rl:public_apply*")]
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


async def _set_status(email: str, status: str, *, days_ago: int = 0) -> None:
    """Move this applicant's enrolment, the way HR's own decision would.

    ``days_ago`` backdates the rejection on the ledger. Needed because the
    cooldown is measured from the transition, so "rejected, and the window has
    since elapsed" cannot be built any other way — see `_four_addresses`.
    """
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
                    "        'rejected', NULL, true, 'other', 'Other',"
                    "        now() - make_interval(days => :ago)"
                    "   FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                    "  WHERE lower(btrim(a.email)) = :em"
                ),
                {"em": email.lower(), "ago": days_ago},
            )
        await db.commit()




async def _apply_via_draft(
    client: AsyncClient, req_id: uuid.UUID, email: str, *, expect_submit: int | None = 201
):  # noqa: ANN202
    """The OTHER door: start a draft, attach a CV, submit it.

    This door was never exercised by this file, although the round-3 finding it
    cites — "the draft row left in a different state" — is a finding about this
    door. It carries the same `gate`, so every case below runs through both.
    """
    await _clear_rate_limit()
    started = await client.post(
        f"/apply/{req_id}/draft",
        json={"email": email, "consent_granted": True},
    )
    if started.status_code != 201:
        raise AssertionError(f"draft start failed: {started.status_code} {started.text}")
    token = started.json()["resume_token"]
    hdr = {"X-Draft-Token": token}

    # EVERY step's status is checked, not just the first. This helper used to
    # assert only that the draft was created, and that made the whole
    # draft-door test satisfiable without reaching the code under test: if the
    # CV upload failed for all five cases, `submit_draft` returns a uniform
    # 422 ("Please upload your CV before submitting") BEFORE the applicant
    # lookup and before the gate — five identical replies, five identical
    # readbacks, and a green test over a door whose gate never ran.
    #
    # A uniformity assertion cannot detect a uniform regression. That is why
    # these are equality checks against the status each step must return,
    # rather than a check that the five agree.
    for label, resp in (
        ("patch", await client.patch(
            "/apply/draft", json={"full_name": "Probe Person"}, headers=hdr
        )),
        ("resume-upload", await client.post(
            "/apply/draft/resume-upload",
            files={"resume": ("cv.pdf", _PDF, "application/pdf")},
            headers=hdr,
        )),
        ("confirm", await client.post(
            "/apply/draft/confirm", json={"full_name": "Probe Person"}, headers=hdr
        )),
    ):
        if resp.status_code != 200:
            raise AssertionError(
                f"draft {label} failed: {resp.status_code} {resp.text[:200]}"
            )

    await _clear_rate_limit()
    submitted = await client.post("/apply/draft/submit", headers=hdr)
    # `expect_submit=None` for a test that deliberately breaks the submit — the
    # read-only one, where 503 IS the property under test. Everywhere else the
    # default pins 201, because a uniform failure before the gate is exactly
    # how this helper used to let the draft-door matrix pass without the gate
    # ever running.
    if expect_submit is not None and submitted.status_code != expect_submit:
        raise AssertionError(
            f"draft submit did not reach the gate: {submitted.status_code} "
            f"{submitted.text[:200]}"
        )
    return submitted, token


async def _draft_readback(client: AsyncClient, token: str):  # noqa: ANN202
    """What ``GET /apply/draft`` says about a draft after it was submitted.

    The round-3 channel exactly: one branch consumed the draft and another did
    not, so a later read answered 404 for a live application and 200 for a
    rejected one. A difference in what a request LEAVES BEHIND is as readable
    as a difference in what it says.
    """
    r = await client.get("/apply/draft", headers={"X-Draft-Token": token})
    # 404 is the right answer for every state: `load` filters `status='draft'`
    # and every exit consumes the draft. Asserted as a VALUE by the caller, not
    # merely as "the five agree" — five identical 200s would be a uniform
    # regression that a uniformity check cannot see.
    return r.status_code, (r.json() if r.status_code == 200 else None)


# The four states an address can be in when it reaches an anonymous door. The
# whole point is that no sequence of requests can tell them apart, so they are
# declared once and every test below exercises all four.
_LIVE = "live"            # (a) has an application, still being considered
_IN_COOLDOWN = "cooling"  # (b) rejected, inside the waiting period
_PAST_COOLDOWN = "past"   # (c) rejected, waiting period genuinely elapsed
_UNKNOWN = "unknown"      # (d) never applied here at all
_OVERRIDDEN = "override"  # (e) rejected, window still running, HR let them back

_ALL_CASES = (_LIVE, _IN_COOLDOWN, _PAST_COOLDOWN, _OVERRIDDEN, _UNKNOWN)


async def _address_in_state(client: AsyncClient, req_id: uuid.UUID, case: str) -> str:
    """An address prepared into *case*, ready to be probed."""
    email = f"{case}-{uuid.uuid4().hex[:10]}@example.com"
    if case == _UNKNOWN:
        return email
    r = await _apply(client, req_id, email)
    assert r.status_code == 201, f"seeding {case}: {r.status_code} {r.text}"
    if case == _LIVE:
        await _set_status(email, "shortlisted")
    elif case == _PAST_COOLDOWN:
        # Backdated past a REAL one-day window, so `check` runs its date
        # arithmetic and returns through `until <= now`.
        await _set_status(email, "rejected", days_ago=3)
    else:
        await _set_status(email, "rejected")
    if case == _OVERRIDDEN:
        await _grant_override(email)
    return email


async def _grant_override(email: str) -> None:
    """The exception HR grants, written the way the endpoint writes it."""
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(
            text(
                "UPDATE enrolments SET reapply_override_at = now(),"
                "       reapply_override_reason = 'fixture', updated_at = now()"
                " WHERE applicant_id IN (SELECT id FROM applicants"
                "                         WHERE lower(btrim(email)) = :em)"
            ),
            {"em": email.lower()},
        )
        await db.commit()


async def _seeded_addresses(
    client: AsyncClient,
) -> dict[str, tuple[uuid.UUID, str]]:
    """One address per case, on openings that put each case in its state.

    Three openings, because these states are the same rejection under
    different settings and different clocks. The cases are still compared with
    each other — what is under test is the REPLY, and a reply that differed by
    which opening was asked would be its own leak.

    `_PAST_COOLDOWN` is seeded on a opening with a ONE-DAY window and a
    rejection backdated three days, NOT on an opening with `cooldown_days=0`.
    That was the bug in the first version of this fixture: `check` returns at
    `if not cooldown_days` before it reads the ledger at all, so the case
    labelled "rejected, waiting period elapsed" was really "this opening has
    no waiting period", and the elapsed-window branch — the one an actual
    rejected candidate hits — was never executed by this file.

    `_OVERRIDDEN` is the state that only exists because a person acted: a live
    90-day window with an HR exception granted against it. It reaches the
    staging path through a DIFFERENT branch of `check` from `_PAST_COOLDOWN`,
    and nothing else here covers it.
    """
    _, waiting = await _seed_opening(cooldown_days=90)
    _, elapsed = await _seed_opening(cooldown_days=1)
    _, overridden = await _seed_opening(cooldown_days=90)
    return {
        _LIVE: (waiting, await _address_in_state(client, waiting, _LIVE)),
        _IN_COOLDOWN: (waiting, await _address_in_state(client, waiting, _IN_COOLDOWN)),
        _PAST_COOLDOWN: (
            elapsed,
            await _address_in_state(client, elapsed, _PAST_COOLDOWN),
        ),
        _OVERRIDDEN: (
            overridden,
            await _address_in_state(client, overridden, _OVERRIDDEN),
        ),
        _UNKNOWN: (waiting, await _address_in_state(client, waiting, _UNKNOWN)),
    }


def _observable(response) -> tuple[int, object]:  # noqa: ANN001
    """EVERYTHING a caller can read: the status and the entire body.

    Not a list of field names. The previous version of this file compared
    ``already_applied``, ``awaiting_confirmation`` and ``message`` by name — and
    ``awaiting_confirmation`` had already been deleted from the response model,
    so that assertion compared None with None and passed on a branch that
    leaked. A named-field comparison can only check the fields somebody thought
    of; this one cannot go stale.
    """
    return response.status_code, response.json()


def _assert_one_answer(seen: dict[str, tuple[int, object]], what: str) -> None:
    """All cases produced the same thing, or say exactly how they differ."""
    if len({repr(seen[c]) for c in _ALL_CASES}) == 1:
        return
    detail = "\n".join(f"    {c:9} -> {seen[c]}" for c in _ALL_CASES)
    raise AssertionError(
        f"{what} tells the states apart, so two anonymous requests reveal\n"
        f"that a named person applied here and was rejected:\n{detail}"
    )



_BOTH_DOORS = ("one-shot", "draft")


async def _submit_through(
    door: str,
    client: AsyncClient,
    req_id: uuid.UUID,
    email: str,
    *,
    expect_submit: int | None = 201,
):  # noqa: ANN202
    """One submission through whichever door, so a property can be asserted of both.

    Five of the eight tests in this file used to drive the one-shot door only,
    and the cost was measured rather than guessed: deleting
    `_require_write_capability` from `submit_draft` left 2883 tests passing.
    Every structural property this branch relies on for the draft door was
    pinned by a substring check or by nothing.

    Not every test here can be parametrised honestly. `..._does_the_same_work`
    counts `_upload_to_s3` calls and `..._storage_outage...` makes that call
    fail — but the draft door performs no upload in its handler at all (the CV
    arrived at `/apply/draft/resume-upload`), so both would assert zero of
    something on that door and prove nothing. Those two stay one-shot by
    nature, and say so. The read-only and latency properties are identical on
    both doors, so they run on both.
    """
    if door == "one-shot":
        return await _apply(client, req_id, email)
    submitted, _token = await _apply_via_draft(
        client, req_id, email, expect_submit=expect_submit
    )
    return submitted


@pytest.mark.asyncio
@pytest.mark.parametrize("probes", [1, 2, 3])
async def test_every_state_answers_identically_on_the_one_shot_door(
    client: AsyncClient, probes: int
) -> None:
    """Every case, one reply, however many times you ask.

    REPETITION IS THE POINT, and it is what every earlier version of this file
    left out. Each of them probed the interesting pair exactly once, and the
    branch passed while a rejected address was answered "accepted" on every
    submission for ever, and an address that had never applied was answered
    "accepted" once and "already applied" from the second time onward. Two
    requests, and a stranger knew a named person had been turned down.

    Asking N times and demanding a single answer makes that unreachable: a
    difference cannot hide in the second request, or the third.
    """
    addresses = await _seeded_addresses(client)
    for attempt in range(1, probes + 1):
        seen = {
            case: _observable(await _apply(client, target, email))
            for case, (target, email) in addresses.items()
        }
        _assert_one_answer(seen, f"submission {attempt} of {probes}")


@pytest.mark.asyncio
@pytest.mark.parametrize("probes", [1, 2, 3])
async def test_every_state_answers_identically_on_the_draft_door(
    client: AsyncClient, probes: int
) -> None:
    """The same cases through the save-and-finish-later door.

    It carries the same gate and so the same risk, and it is where the round-3
    finding actually lived — yet no version of this file had ever sent it a
    single request.
    """
    addresses = await _seeded_addresses(client)
    for attempt in range(1, probes + 1):
        replies: dict[str, tuple[int, object]] = {}
        readbacks: dict[str, tuple[int, object]] = {}
        for case, (target, email) in addresses.items():
            submitted, token = await _apply_via_draft(client, target, email)
            replies[case] = _observable(submitted)
            # AND what the draft looks like afterwards. Identical replies
            # followed by a readable draft for one case and a 404 for another
            # is the same disclosure, one step later.
            readbacks[case] = await _draft_readback(client, token)
        _assert_one_answer(replies, f"draft submission {attempt} of {probes}")
        _assert_one_answer(readbacks, f"the draft left behind on submission {attempt}")


@pytest.mark.asyncio
async def test_the_reply_carries_nothing_that_was_stored(
    client: AsyncClient,
) -> None:
    """The body echoes this request, and nothing we hold about the address.

    A previous round shipped the real ``applicant_id`` and ``enrolment_id`` in
    here. They are blank now; this keeps them blank, and keeps the reply from
    growing a new field with an opinion in it — which is how both of the last
    two leaks arrived, each added in good faith by the fix for the one before.
    """
    _, req_id = await _seed_opening(cooldown_days=0)
    email = await _address_in_state(client, req_id, _PAST_COOLDOWN)

    body = (await _apply(client, req_id, email)).json()
    assert body["applicant_id"] == ""
    assert body["enrolment_id"] is None
    # `full_name` is what THIS request typed: the caller's own input coming
    # back, not something disclosed to them.
    assert body["full_name"] == "Probe Person"
    assert set(body) == {"applicant_id", "enrolment_id", "full_name", "message"}


@pytest.mark.asyncio
async def test_every_state_does_the_same_work(client: AsyncClient) -> None:
    """The fifth channel: identical replies, different WORK.

    Every earlier round closed a difference in what the endpoint SAID. Round 5
    found the states still did different amounts of it — a live application
    and a cooldown returned BEFORE the CV upload that a first-time application
    and a reapplication performed. Two requests with a large PDF and a short
    client timeout separated them on latency, and the caller chose the file
    size, so the caller chose the size of the gap.

    The upload now happens before the gate on every submission, and the
    branches that keep nothing delete it afterwards. THE UPLOAD COUNT is the
    thing to assert, not what is left in the bucket: a refused submission
    deletes its object and an accepted one keeps it, so the objects left
    behind differ by design — and they are in our bucket, where no anonymous
    caller can see them. What the caller can measure is whether the work
    happened, which is exactly one upload per submission, in every state.

    (The first version of this test compared objects remaining in the local
    store. It was vacuous — the store it pointed at was empty, so every count
    was zero — and it would have been wrong even had it worked.)

    ONE-SHOT DOOR ONLY, and honestly so: `submit_draft` performs no upload in
    its handler — the CV arrived at `/apply/draft/resume-upload` — so counting
    uploads there would assert zero of something and prove nothing. The draft
    door's equivalent shared work is pinned by the read-only and latency
    tests, which do run on both.
    """
    from app.routers import public_apply

    addresses = await _seeded_addresses(client)
    real_upload = public_apply._upload_to_s3

    uploads: dict[str, int] = {}
    for case, (target, email) in addresses.items():
        count = 0

        async def _counting(raw: bytes, key: str) -> None:
            nonlocal count
            count += 1
            await real_upload(raw, key)

        with mock.patch.object(public_apply, "_upload_to_s3", _counting):
            await _apply(client, target, email)
        uploads[case] = count

    assert set(uploads.values()) == {1}, (
        "the states do different amounts of work, so an anonymous caller can "
        "time two requests and learn which one an address is in:\n"
        + "\n".join(f"    {c:9} -> {n} upload(s)" for c, n in uploads.items())
    )


@pytest.mark.asyncio
async def test_a_storage_outage_answers_the_same_way_for_every_state(
    client: AsyncClient,
) -> None:
    """The non-statistical half of the same channel.

    With the upload after the gate, a live application and a cooldown could
    never reach the storage error — so an outage turned "has this address
    applied?" into a 201-vs-503 read from two requests. Now the upload is the
    first thing every submission does, so every state fails the same way.

    ONE-SHOT DOOR ONLY, for the same reason as the upload-count test above:
    `submit_draft` never calls `_upload_to_s3`, so making that call fail
    cannot change its answer. The draft door's status uniformity under a
    failing dependency is covered by the read-only test, which runs on both.
    """
    from app.local_storage import LocalStorageError
    from app.routers import public_apply

    addresses = await _seeded_addresses(client)

    async def _always_fails(_raw: bytes, _key: str) -> None:
        raise LocalStorageError("storage is down")

    seen: dict[str, int] = {}
    with mock.patch.object(public_apply, "_upload_to_s3", _always_fails):
        for case, (target, email) in addresses.items():
            seen[case] = (await _apply(client, target, email)).status_code

    assert set(seen.values()) == {503}, (
        "a storage outage tells the states apart:\n"
        + "\n".join(f"    {c:9} -> {n}" for c, n in seen.items())
    )


@pytest.mark.asyncio
async def test_a_refused_submission_deletes_the_cv_it_was_made_to_upload(
    client: AsyncClient,
) -> None:
    """The other half of storing the CV before the gate.

    Uploading on every path is what makes the work uniform; deleting it again
    on the paths that keep nothing is what stops that being a bucket full of
    strangers' CVs under a named victim's erasure prefix. The whole trade-off
    rests on the second half, and the only thing guarding it was
    `assert "_release_unadopted" in src` — a substring check against the
    function's source, which passes if the identifier appears in a comment.
    Remove both call sites and leave the helper defined, and every test stayed
    green.

    Asserted as counts per state rather than as uniformity, because these
    legitimately differ: a refused submission deletes what it uploaded, an
    accepted one keeps it. That difference is in our bucket, where no
    anonymous caller can read it.
    """
    from app.routers import public_apply

    addresses = await _seeded_addresses(client)
    deletes: dict[str, int] = {}
    real_delete = public_apply._delete_from_s3

    def _counter(into: list[str]):  # noqa: ANN202
        async def _counting(key: str) -> None:
            into.append(key)
            await real_delete(key)

        return _counting

    for case, (target, email) in addresses.items():
        seen: list[str] = []
        with mock.patch.object(public_apply, "_delete_from_s3", _counter(seen)):
            await _apply(client, target, email)
        deletes[case] = len(seen)

    for refused in (_LIVE, _IN_COOLDOWN):
        assert deletes[refused] == 1, (
            f"{refused} uploaded a CV before the gate and did not delete it, so "
            "every probe leaves a stranger's PDF in the bucket: "
            f"{deletes}"
        )
    for kept in (_PAST_COOLDOWN, _OVERRIDDEN, _UNKNOWN):
        assert deletes[kept] == 0, (
            f"{kept} deleted the CV it was supposed to keep: {deletes}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("door", _BOTH_DOORS)
async def test_the_states_cannot_be_separated_by_how_long_the_reply_takes(
    client: AsyncClient, door: str
) -> None:
    """The channel round 6 found, and the reason the reply is held.

    Every state answers with the same bytes. They do not cost the same: a
    live application is two SELECTs and a return, an address free to apply is
    a dozen writes, two commits and an email staged. Identical replies at
    measurably different times still say which branch ran, and the branch is
    the answer to "did this named person apply here and get turned down".

    The previous attempt at this made the CV upload common to every state and
    declared the work uniform. It was not: only the upload moved, and making
    it common handed the caller the NOISE FLOOR rather than removing the
    signal — a tiny PDF shrinks the shared term and leaves the write
    differential as the whole variance.

    So every reply is now held to a deadline measured from after the shared
    work. What this asserts is the part that is stable under load: no state
    answers before the floor, which is what a missing, disabled, or bypassed
    floor looks like. See the note at the end for what it deliberately does
    NOT assert, and why measuring the spread turned out to be measuring noise.
    """
    floor_s = settings.apply_reply_floor_ms / 1000
    assert floor_s > 0, "the floor is disabled, so the timing channel is open"

    addresses = await _seeded_addresses(client)
    elapsed: dict[str, float] = {}
    for case, (target, email) in addresses.items():
        start = time.monotonic()
        await _submit_through(door, client, target, email)
        elapsed[case] = time.monotonic() - start

    # Nothing may come back before the floor. A branch returning early is a
    # branch whose reply was not held.
    too_fast = {c: round(t, 3) for c, t in elapsed.items() if t < floor_s}
    assert not too_fast, (
        "these states answered faster than the reply floor, so the branch they "
        f"took is visible from outside: {too_fast} (floor {floor_s}s)"
    )

    # NO SPREAD ASSERTION, and why matters more than the test.
    #
    # The first version compared max-minus-min against the floor. It passed
    # alone and failed under full-suite load — because end-to-end time
    # includes the CV upload, the one term the floor deliberately does NOT
    # cover, since the caller sizes it and it is common to every state. The
    # spread it measured was therefore dominated by exactly what the design
    # treats as noise, and tuning the constant would only have moved the flake
    # around. This docstring predicted that and the assertion was written
    # anyway.
    #
    # What remains is the one-directional property above, which is stable: no
    # state answers before the floor. That is what a door bypassing `_reply`
    # looks like, and a refactor of these two near-identical handlers is
    # exactly how that would happen.
    #
    # Uniformity itself is pinned structurally, where it is not a measurement.
    # Stated precisely, because the earlier version of this comment claimed
    # more than the guard delivered and that over-citation is what hid a
    # broken deadline for a whole round:
    #
    # * `test_every_reply_from_both_doors_is_the_same_object` counts the
    #   reply-bearing exits on both doors and requires each to go through
    #   `_reply` or `_refuse`. It checks the CALLEE, not the arguments.
    # * The arguments are pinned by the signature instead: `tail_from` is a
    #   REQUIRED keyword on both, so an exit that omits it fails mypy, which
    #   CI gates on. That is stronger than a test reading call sites.
    # * `test_a_pad_absorbs_the_work_it_covers_to_a_constant` is the one that
    #   proves the deadlines do their job, with a negative control beside it.
    # * `test_every_state_does_the_same_work` pins the shared work above.


@pytest.mark.asyncio
@pytest.mark.parametrize("door", _BOTH_DOORS)
async def test_a_read_only_database_answers_the_same_way_for_every_state(
    client: AsyncClient, door: str
) -> None:
    """The last status-code oracle, and the one the reply floor cannot close.

    Under a partial outage that serves reads and refuses writes — failover to
    a read-only standby, `default_transaction_read_only`, a maintenance
    window — the states used to diverge on the status line:

      * a live application performs only SELECTs and commits nothing -> 201
      * a cooldown's only write is the notice, swallowed in its own
        savepoint, so its commit commits nothing -> 201
      * every state free to apply raises on the insert -> 503

    Two anonymous requests in that window separated "this address already has
    an application here" from "this address is free to apply". Shaping latency
    cannot hide a status code, so this is closed by asking the question before
    anything branches: `_require_write_capability` performs a zero-row DML,
    which Postgres rejects in a read-only transaction at executor start.

    Simulated by making that probe raise rather than by putting the real
    database into recovery, which a test cannot do without taking the whole
    suite's connection with it. What is under test is the ROUTE's behaviour
    when the answer is no — that it refuses every state alike — and the
    probe's own semantics are the thing that was verified against real
    Postgres before this was written.
    """
    from app.routers import public_apply

    addresses = await _seeded_addresses(client)

    async def _write_refused(_db: object) -> None:
        raise HTTPException(
            status_code=503,
            detail="We could not save your application. Please try again.",
        )

    seen: dict[str, tuple[int, object]] = {}
    with mock.patch.object(
        public_apply, "_require_write_capability", _write_refused
    ):
        for case, (target, email) in addresses.items():
            seen[case] = _observable(
                # The status is what this test measures, so it must not be
                # pinned by the helper.
                await _submit_through(
                    door, client, target, email, expect_submit=None
                )
            )

    _assert_one_answer(seen, f"a read-only database ({door} door)")
    # And the answer is the refusal, not a cheerful 201 — telling a real
    # candidate their application landed when it did not would be a worse
    # thing to do than leaking the distinction.
    assert {s for s, _ in seen.values()} == {503}, seen


@pytest.mark.asyncio
async def test_the_two_doors_answer_the_same_state_the_same_way(
    client: AsyncClient,
) -> None:
    """CROSS-DOOR EQUIVALENCE, which nothing in this file asserted.

    Every other test here compares the five states WITHIN one door. Both
    round-9 reviewers landed on the same gap: nothing compared door A's
    observable against door B's, and that is where the damage has repeatedly
    been. The reply bodies are equal by construction — both doors return
    `_received` — but the timing properties, the failure shapes and the work
    were not, and twice a fix was applied to one handler while the other
    silently kept different behaviour. Round 9's own HIGH was exactly that: a
    broken deadline on the one-shot door while the draft door was correct, and
    the suite could not see the difference because no test looked across.

    So this probes the same prepared address through BOTH doors and requires
    the same answer. Not "the five states agree on door A, and separately on
    door B" — the same answer, door to door.

    It cannot catch a timing difference (nothing here measures time; see the
    latency test's note). It catches what it can: status and body, per state,
    across doors.
    """
    addresses = await _seeded_addresses(client)

    mismatched: dict[str, tuple[object, object]] = {}
    for case, (target, email) in addresses.items():
        one = _observable(await _submit_through("one-shot", client, target, email))
        draft = _observable(await _submit_through("draft", client, target, email))
        if repr(one) != repr(draft):
            mismatched[case] = (one, draft)

    assert not mismatched, (
        "the two anonymous doors answer the same address differently, so which "
        "door a caller uses is itself information:\n"
        + "\n".join(
            f"    {c:9}\n        one-shot: {o}\n        draft:    {d}"
            for c, (o, d) in mismatched.items()
        )
    )


@pytest.mark.asyncio
async def test_both_doors_refuse_a_read_only_database_identically(
    client: AsyncClient,
) -> None:
    """The same, for the failure shape rather than the happy path.

    Round 8 found the draft door answering 500 where the one-shot door
    answered 503, and round 9 found the two doors holding different timing
    properties. Both were one-door drifts in a FAILURE path, which is where
    the matrix never looked. This pins that a write-refusing database produces
    the same status and body through either door.
    """
    from app.routers import public_apply

    addresses = await _seeded_addresses(client)

    async def _write_refused(_db: object) -> None:
        raise HTTPException(status_code=503, detail=public_apply._UNAVAILABLE)

    mismatched: dict[str, tuple[object, object]] = {}
    with mock.patch.object(
        public_apply, "_require_write_capability", _write_refused
    ):
        for case, (target, email) in addresses.items():
            one = _observable(
                await _submit_through(
                    "one-shot", client, target, email, expect_submit=None
                )
            )
            draft = _observable(
                await _submit_through(
                    "draft", client, target, email, expect_submit=None
                )
            )
            if repr(one) != repr(draft):
                mismatched[case] = (one, draft)

    assert not mismatched, (
        "the doors fail differently, so a degraded window tells a caller which "
        "door they used and which state the address is in:\n"
        + "\n".join(
            f"    {c:9}\n        one-shot: {o}\n        draft:    {d}"
            for c, (o, d) in mismatched.items()
        )
    )
