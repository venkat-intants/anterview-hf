"""``POST /exam/integrity-event`` rate limiting — code review FIX 2, 2026-09-29.

The finding: the ingest endpoint carried no rate limit at all — 150
consecutive posts from one token all returned 200 against a live instance,
while the submit and run-code endpoints on ``routers/exam_take.py`` already
carried ``dependencies=[rate_limit(...)]``.

(An earlier version of this docstring said "every OTHER write endpoint"
already carried one. That was false when written — ``/exam/camera-consent``
and ``/exam/start`` did not — and security review MEDIUM-2 caught it. Both
now do, and the claim is corrected here rather than left to mislead the next
reader, per CLAUDE.md's documentation rule.)

The follow-up review the same day found that the FIRST version of that limit
was keyed on client IP alone, which is the wrong boundary for this endpoint:
a college computer lab — CLAUDE.md's #1 target market — NATs every seat behind
one address, so three ordinary candidates would exhaust a 120/min cap and the
rest of the room would have real camera events dropped silently. The route now
uses ``rate_limit_link`` — per exam link, with a far looser per-IP backstop.

This file therefore has three, deliberately separate, concerns:

  * the plain, IP-keyed ``app.rate_limit.rate_limit`` mechanism itself has NO
    dedicated unit coverage anywhere in this service (only the company-keyed
    variant does, in ``test_rate_limit_company.py``) — exercised here directly
    with a fake in-memory Redis and a fake Request, on that file's own
    precedent, rather than by round-tripping the real HTTP surface hundreds of
    times to reach a real ceiling. It still guards the sibling exam routes;
  * that ``rate_limit_link``'s keying actually behaves as the fix claims —
    a shared address does not merge two candidates' budgets, while a flood
    spread across rotating tokens is still caught;
  * that the ROUTE is wired with it, at the names and settings this fix claims,
    so a future edit that quietly drops the dependency OR reverts it to the
    IP-keyed factory is caught here rather than in a college computer lab.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.config import settings
from app.rate_limit import _token_fingerprint, rate_limit, rate_limit_link
from app.routers import exam_take


class _Headers(dict):  # type: ignore[type-arg]
    """Starlette headers are case-insensitive — mirrors test_request_ip_utils.py."""

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def _request(host: str = "203.0.113.9", token: str | None = None) -> Any:
    headers = _Headers({"X-Exam-Token": token} if token else {})
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


class _FakeRedis:
    """Just enough of the redis-py async interface for ``rate_limit``: ``incr``
    and ``expire``, backed by a plain dict — the test_rate_limit_company.py
    precedent."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        pass


# ---------------------------------------------------------------------------
# The generic mechanism, at a bucket/IP scoped to this test — mutation-checked
# by construction: change `per_minute` below and the assertion boundary moves
# with it, so this cannot pass by accident.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rate_limit_dependency_429s_past_the_configured_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = rate_limit("exam_integrity_event", 3).dependency
    request = _request()

    for _ in range(3):
        await dep(request=request)  # must not raise for the first 3
    with pytest.raises(HTTPException) as exc_info:
        await dep(request=request)
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_dependency_gives_each_ip_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auditor's flood came from ONE token/IP — a genuinely different
    candidate, elsewhere, must not be punished for it."""
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = rate_limit("exam_integrity_event", 1).dependency

    await dep(request=_request("203.0.113.9"))  # this IP now at its cap
    await dep(request=_request("198.51.100.4"))  # a different IP — untouched budget
    with pytest.raises(HTTPException):
        await dep(request=_request("203.0.113.9"))


@pytest.mark.asyncio
async def test_rate_limit_dependency_fails_open_when_redis_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fail-open posture as every other rate limit in this service — see
    app/rate_limit.py's module docstring; a Redis blip must not block a
    proctored exam."""

    def _broken_get_redis() -> None:
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr("app.rate_limit.get_redis", _broken_get_redis)
    dep = rate_limit("exam_integrity_event", 1).dependency
    await dep(request=_request())  # must not raise
    await dep(request=_request())  # still must not raise — no counter to trip


# ---------------------------------------------------------------------------
# The ROUTE itself: this fix's actual claim, checked at the name/value it
# makes it at. Reads the module source rather than the FastAPI route object —
# the closure created by rate_limit() does not expose its captured `bucket`/
# `per_minute` through any public attribute, so asserting on the decorator's
# OWN source text is the direct way to pin what it says, on the
# test_camera_proctoring_never_touches_a_hiring_decision precedent
# (test_exam_camera_proctoring.py) of asserting on source for a property no
# object attribute exposes.
# ---------------------------------------------------------------------------
def _route_decorator_source() -> str:
    """The decorator block immediately above ``ingest_integrity_event``.

    Anchored BACKWARD from the route function's own name so a rename, or a
    similar limiter on some other route in the same module, cannot fake any of
    the assertions below green.
    """
    import inspect

    source = inspect.getsource(exam_take)
    idx = source.index("async def ingest_integrity_event")
    return source[max(0, idx - 800) : idx]


def test_the_route_level_guard_is_the_per_ip_volumetric_one() -> None:
    """The route dependency bounds a flood before any DB work, per IP only.

    The per-TOKEN budgets moved into the handler (security re-audit MEDIUM-1)
    so a refusal can be recorded on the attempt before the 429 is raised — a
    dependency raises too early to write anything, which left the outer cap as
    a path that dropped events with no trace, the exact defect HIGH-2 blocked
    on.
    """
    decorator = _route_decorator_source()
    assert 'rate_limit("exam_integrity_event"' in decorator
    assert "exam_integrity_event_per_ip_per_minute" in decorator
    # The per-token caps must NOT be here any more, or a refusal is unflaggable.
    assert "per_token=" not in decorator


def test_both_token_budgets_are_charged_where_a_refusal_can_be_recorded() -> None:
    """The per-candidate caps, and that EVERY refusal path flags the attempt.

    A college computer lab — CLAUDE.md's #1 target market — NATs every seat
    behind one address, so the per-candidate budget has to key on the link and
    not the IP. And both budgets sit inside one try/except whose handler calls
    `_note_events_dropped`, so neither the outer nor the inner refusal can drop
    an event silently.
    """
    import inspect

    source = inspect.getsource(exam_take.ingest_integrity_event)
    assert source.count("enforce_token_budget(") == 2
    assert 'bucket="exam_integrity_event"' in source
    assert 'bucket="exam_integrity_nonviolation"' in source
    assert '"X-Exam-Token"' in source
    # One except clause covering both, flagging before it re-raises.
    assert "except HTTPException:" in source
    flag_at = source.index("_note_events_dropped")
    raise_at = source.index("raise", flag_at)
    assert flag_at < raise_at, "the attempt must be flagged BEFORE the 429 is re-raised"


@pytest.mark.asyncio
async def test_each_exam_link_gets_its_own_budget_behind_one_shared_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lab case, end to end through the dependency: two candidates on one
    NAT address, each at their own per-token ceiling, neither affecting the
    other. Under the old IP keying the second candidate's very first post
    would already have been rejected."""
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = rate_limit_link(
        "exam_integrity_event_test", "X-Exam-Token", per_token=2, per_ip=100
    ).dependency

    lab_ip = "198.51.100.7"
    for _ in range(2):
        await dep(request=_request(lab_ip, token="candidate-one-link"))
    # A different candidate, same room, same address — unaffected.
    for _ in range(2):
        await dep(request=_request(lab_ip, token="candidate-two-link"))

    # Each is now at its OWN ceiling, and only the one that exceeded it trips.
    with pytest.raises(HTTPException) as exc:
        await dep(request=_request(lab_ip, token="candidate-one-link"))
    assert exc.value.status_code == 429
    with pytest.raises(HTTPException):
        await dep(request=_request(lab_ip, token="candidate-two-link"))
    # ...while a third candidate who has posted nothing is still served.
    await dep(request=_request(lab_ip, token="candidate-three-link"))


@pytest.mark.asyncio
async def test_the_ip_backstop_still_bounds_a_flood_spread_across_many_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-token keying must not become a way to post without limit by
    rotating the header: the looser per-IP ceiling still catches volumetric
    abuse from one source, and says so in a distinct message."""
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = rate_limit_link(
        "exam_integrity_event_test", "X-Exam-Token", per_token=50, per_ip=3
    ).dependency

    for i in range(3):
        await dep(request=_request("203.0.113.44", token=f"link-{i}"))
    with pytest.raises(HTTPException) as exc:
        await dep(request=_request("203.0.113.44", token="link-fresh"))
    assert exc.value.status_code == 429
    assert "network" in str(exc.value.detail).lower()

    # Security review MEDIUM-1: the per-IP ceiling must bound the number of
    # Redis KEYS CREATED, not merely the number of requests served. With the
    # token charged first, every rejected request still minted a fresh
    # `rl:...:tok:<sha256>` key, so key growth was bounded only by network
    # throughput — and this Redis also carries the JWT revocation epoch, with
    # which rate limiting fails open. The rejected token above must therefore
    # have no key at all.
    # (The raw token never appears in a key — it is hashed — so this checks
    # the actual fingerprint, not a substring that could never match.)
    rejected_key = f"rl:exam_integrity_event_test:tok:{_token_fingerprint('link-fresh')}"
    assert rejected_key not in fake.counts
    token_keys = [k for k in fake.counts if ":tok:" in k]
    assert len(token_keys) == 3, f"one key per SERVED request, got {token_keys}"


@pytest.mark.asyncio
async def test_a_request_with_no_exam_token_falls_back_to_the_ip_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tokenless request cannot get a free pass by simply omitting the
    header — it has no per-token budget to charge, so the IP ceiling is the
    only thing standing in front of it and must still apply."""
    fake = _FakeRedis()
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    dep = rate_limit_link(
        "exam_integrity_event_test", "X-Exam-Token", per_token=1, per_ip=2
    ).dependency

    await dep(request=_request("203.0.113.55"))
    await dep(request=_request("203.0.113.55"))
    with pytest.raises(HTTPException) as exc:
        await dep(request=_request("203.0.113.55"))
    assert exc.value.status_code == 429


def test_the_outer_cap_sits_above_the_worst_case_a_real_client_can_produce() -> None:
    """Security review HIGH-2: the outer per-token cap must exceed the
    PATHOLOGICAL client rate, not sit under it.

    The previous value (120) was below the 150/min a flapping camera can
    produce — ranged events debounced at 1.2s across 3 conditions — so the
    candidates most likely to be throttled were those with a cheap camera or
    poor lighting, and the HR panel would have shown them a short, clean
    timeline with their real evidence missing. The outer cap is a volumetric
    guard; per-type fairness is the inner budget's job.
    """
    flapping_camera_worst_case = 150
    assert settings.exam_integrity_event_per_minute > flapping_camera_worst_case


def test_violations_and_chatter_do_not_share_a_budget() -> None:
    """The anti-suppression property, pinned as an invariant.

    A 429 on a camera event loses the row, the server's violation count and
    the auto-submit trigger together, and the client swallows it. So if
    clipboard events and violations shared one counter, a candidate could
    spend the budget on `copy` in the first seconds of a minute and have their
    own `multiple_faces` refused for the rest of it. The inner budget must
    therefore stay strictly below the outer one (or it could never bind) AND
    must never be charged to a violation type — the latter is asserted
    directly against the route's source below.
    """
    assert (
        settings.exam_integrity_nonviolation_per_minute
        < settings.exam_integrity_event_per_minute
    )


def test_the_inner_budget_is_never_charged_to_a_violation_event() -> None:
    import inspect

    source = inspect.getsource(exam_take.ingest_integrity_event)
    assert "not in exam_camera.VIOLATION_EVENT_TYPES" in source
    assert "exam_integrity_nonviolation" in source
    # And a refusal must be recorded on the attempt, not swallowed — an
    # incomplete timeline has to say so.
    assert "_note_events_dropped" in source


def test_the_ip_backstop_clears_a_full_computer_lab() -> None:
    """The per-IP ceiling is an abuse backstop, NOT the normal-use limit, so it
    has to clear the busiest room we actually sell to. A 60-seat lab with every
    candidate at the realistic 40/min ceiling is 2400 requests/minute from one
    address; anything at or below that would throttle a legitimate exam hall.
    """
    seats, realistic_per_candidate = 60, 40
    assert settings.exam_integrity_event_per_ip_per_minute > seats * realistic_per_candidate
    # And comfortably above the per-token cap, or it would become the binding
    # limit for a single candidate and defeat the point of per-token keying.
    assert (
        settings.exam_integrity_event_per_ip_per_minute
        > settings.exam_integrity_event_per_minute
    )
