"""E4 — the public application link: what it refuses, reveals and rewrites.

The endpoint is exercised end to end against Postgres in
``smoke_group_e_intake``. These pin the properties that would quietly regress.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import re
import time

import pytest


def _submit() -> str:
    from app.routers.public_apply import submit_application

    return inspect.getsource(submit_application)


def test_an_opening_without_a_published_workflow_takes_no_applications() -> None:
    """Since PH3-B0 the gate is one shared predicate, so this asserts against
    that rather than against this endpoint's own copy of it — the copy is what
    the careers board drifted away from."""
    from app.publishing import visible_sql
    from app.routers.public_apply import _open_posting

    gate = visible_sql("r")
    assert "w.status = 'published'" in gate
    assert "r.public_apply_enabled" in gate
    assert "visible_sql('r')" in inspect.getsource(_open_posting)


def test_the_reply_model_carries_nothing_that_depends_on_stored_state() -> None:
    """Four fields, and no room for a fifth with an opinion in it.

    Both of the last two disclosures were a FIELD: `awaiting_confirmation`,
    then `already_applied`. Each was added in good faith, each was true, and
    each let anyone who typed an address into an anonymous form learn that a
    named person had applied here and been turned down. The shape of the reply
    is therefore the invariant, not the spelling of any one leak.
    """
    from app.routers.public_apply import ApplicationOut

    assert set(ApplicationOut.model_fields) == {
        "applicant_id",
        "enrolment_id",
        "full_name",
        "message",
    }


def test_every_reply_from_both_doors_is_the_same_object() -> None:
    """No call site builds its own.

    The four-case indistinguishability property is held down properly by
    `tests/integration/test_ph3_cooldown_indistinguishable.py`, which drives
    the real app and compares whole bodies. This is the cheap structural
    companion: a reply assembled inline is how a branch grows its own opinion
    again, and it is the one thing a source read genuinely can see.
    """
    from app.routers import public_apply

    src = inspect.getsource(public_apply)
    # Two: the `class ApplicationOut(BaseModel)` statement and the single
    # construction inside `_received`. Spelled out because a bare `== 2` with
    # a comment claiming "one construction" reads as off-by-one.
    assert src.count("ApplicationOut(") == 2, (
        "ApplicationOut is constructed somewhere other than `_received` — "
        "every door must answer with the one reply"
    )

    for door in (public_apply.submit_application, public_apply.submit_draft):
        body = inspect.getsource(door)
        assert "ApplicationOut(" not in body, f"{door.__name__} builds its own reply"
        assert "_received(name)" not in body, (
            f"{door.__name__} answers without holding the reply floor"
        )

        # EVERY reply-bearing exit, counted — not "at least one of them".
        #
        # This was `assert "_reply(name, floor_from=floor_from)" in body`, a
        # single-substring test satisfied by one matching return out of ten.
        # It could not see a new exit that bypassed the floor, three already
        # did, and the integration file cited it as the backstop that
        # "requires every exit on both doors to return through `_reply`". It
        # did not. That is the same defect class as the binding guard whose
        # `f"{field}=" in body` missed `status=` inside `stored_status=` —
        # written in the round that found it.
        #
        # A reply-bearing exit is one that returns a value. `raise
        # HTTPException` exits are input errors and genuine failures, which
        # are allowed to answer immediately.
        returns = re.findall(r"^\s*return (?:await )?(\S+)", body, re.M)
        floored = [r for r in returns if r.startswith(("_reply(", "_refuse("))]
        assert returns and len(floored) == len(returns), (
            f"{door.__name__} has {len(returns) - len(floored)} reply-bearing "
            f"exit(s) that do not go through the floor: "
            f"{[r for r in returns if r not in floored]}"
        )

        # And the write probe, on BOTH doors. Deleting it from one of them
        # left 2883 tests passing, which is how six rounds of one-door drift
        # kept happening.
        assert "_require_write_capability(db)" in body, (
            f"{door.__name__} answers without checking the database can be "
            "written to, so a read-only window splits the states 201/503"
        )


def test_nothing_stored_is_echoed_to_a_repeat_or_racing_submission() -> None:
    src = _submit()
    assert 'existing["full_name"]' not in src
    assert 'existing["enrolment_id"])' not in src


def test_a_returning_applicants_record_is_not_rewritten_by_the_form() -> None:
    """Anyone who knew an email address could replace that person's CV, role,
    contact details and scores."""
    src = " ".join(_submit().split())
    assert "UPDATE applicants SET resume_text" not in src
    assert "previous_key" not in src
    # Their CV is pinned to the new application instead.
    assert "resume_s3_key=s3_key," in src


def test_the_upload_read_is_bounded() -> None:
    assert "resume.read(_MAX_RESUME_BYTES + 1)" in _submit()


def test_the_applicant_chooses_the_language_of_their_emails() -> None:
    from pydantic import TypeAdapter, ValidationError

    from app.routers.public_apply import _ensure_guest_user, submit_application

    param = inspect.signature(submit_application).parameters["language"]
    assert param.default == "en"
    assert ":lang" in inspect.getsource(_ensure_guest_user)

    import typing

    hint = typing.get_type_hints(submit_application, include_extras=True)["language"]
    adapter = TypeAdapter(hint)
    for ok in ("en", "hi", "te"):
        assert adapter.validate_python(ok) == ok
    try:
        adapter.validate_python("fr")
    except ValidationError:
        pass
    else:  # pragma: no cover — the assertion is the point
        raise AssertionError("an unsupported language was accepted")


# ===========================================================================
# ORDER, which no substring guard in this repo can see
# ===========================================================================
def _door_bodies() -> dict[str, ast.AST]:
    """Both handlers as parsed trees, not as text."""
    import app.routers.public_apply as mod

    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in (
            "submit_application",
            "submit_draft",
        ):
            out[node.name] = node
    assert set(out) == {"submit_application", "submit_draft"}, out.keys()
    return out


def _first_line(node: ast.AST, pred) -> int | None:  # noqa: ANN001
    """The line of the first statement matching *pred*, or None."""
    hits = [n.lineno for n in ast.walk(node) if pred(n)]
    return min(hits) if hits else None


def _call_line(node: ast.AST, fn: str) -> int | None:
    """The line of the first call to *fn* inside *node*."""
    return _first_line(
        node,
        lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == fn,
    )


def test_the_reply_floor_starts_before_anything_state_dependent() -> None:
    """THE property seven rounds bought, and the only one with no guard.

    Round 7's fix was the POSITION of `floor_from = time.monotonic()` — above
    the identity lookup, so the LEFT JOIN that returns a row for four states
    and nothing for the fifth is inside the deadline. Round 8's reviewers both
    found the same hole: that position is pinned by nothing. Move the
    assignment back below `_identify` and every reply stays byte-identical,
    every reply still takes at least the floor, the counting guard still sees
    the same floored exits, and 2900 tests stay green while the channel is
    open.

    No substring or regex guard can see this; it is a statement ORDER
    property, so it is asserted against the parsed tree.
    """
    for name, node in _door_bodies().items():
        floor = _first_line(
            node,
            lambda n: isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "floor_from" for t in n.targets
            ),
        )
        identify = _first_line(
            node,
            lambda n: isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_identify",
        )
        gate = _first_line(
            node,
            lambda n: isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "reapplication_gate",
        )
        assert floor is not None, f"{name} never starts the reply floor"
        assert identify is not None and gate is not None, name
        assert floor < identify, (
            f"{name}: the reply floor starts at line {floor}, AFTER the identity "
            f"lookup at {identify}. The lookup returns a row for four states and "
            "nothing for the fifth, so it is outside the deadline and additively "
            "measurable with a small PDF."
        )
        assert floor < gate, (
            f"{name}: the reply floor starts after the gate, so the branch it "
            "exists to mask is outside it"
        )


def test_the_one_shot_door_takes_a_second_deadline_after_the_caller_sized_work() -> None:
    """The other half, and the regression round 8 measured.

    One deadline cannot cover both a caller-sized term and the tail below it.
    Round 7 moved the single clock to the top of the handler to cover the
    lookup — which put the PDF parse inside the window. Measured on this
    repo's own extractor: a dense 60-page CV parses in ~555 ms against a
    400 ms floor, so the floor was spent before the branch began and an
    attacker could guarantee it never engaged.

    So the one-shot door takes a SECOND clock after the parse and upload. This
    pins that it exists and that it sits after the upload and before the gate.
    The draft door needs none — its CV arrived at a different endpoint, so
    there is no caller-sized term in its handler at all.
    """
    doors = _door_bodies()

    one = doors["submit_application"]
    tail = _first_line(
        one,
        lambda n: isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "tail_from" for t in n.targets),
    )
    upload = _first_line(
        one,
        lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_upload_to_s3",
    )
    gate = _first_line(
        one,
        lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "reapplication_gate",
    )
    assert tail is not None, (
        "submit_application has no second deadline, so the caller-sized PDF "
        "parse is inside the only window and a large CV switches the floor off"
    )
    assert upload is not None and gate is not None
    assert upload < tail < gate, (
        f"the second deadline must sit after the upload ({upload}) and before "
        f"the gate ({gate}); it is at {tail}"
    )

    assert (
        _first_line(
            doors["submit_draft"],
            lambda n: isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_upload_to_s3",
        )
        is None
    ), (
        "submit_draft now uploads in its handler, so it needs a second "
        "deadline too — see submit_application"
    )


def test_every_degraded_reply_uses_the_one_sentence() -> None:
    """Two sentences meant the states were told apart by wording.

    The draft door's accept path said "We could not submit your application
    just now" while its refusal path and every one-shot exit said "We could
    not save your application. Please try again." In a write-failure window
    that is the refusing and accepting states answering with different bodies
    — on the one channel this feature exists to close, by a difference nobody
    had compared and no test induces.
    """
    import app.routers.public_apply as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    # SCOPED TO THE SUBMIT PATH. The module raises 503 from other endpoints
    # too — saving a draft, deleting a CV, setting up an account, following a
    # confirmation link — and those sentences differ for good reason: they are
    # different operations, and none of them branches on what is stored about
    # an address. What must be identical is every 503 a SUBMISSION can get,
    # because that is where the five states are.
    submit_path = {
        "submit_application",
        "submit_draft",
        "_refuse",
        "_refuse_work",
        "_require_write_capability",
    }
    scoped = [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        and n.name in submit_path
    ]
    assert {n.name for n in scoped} == submit_path, {n.name for n in scoped}

    # The `detail=` of every 503 raised in this module, read off the parsed
    # call rather than by scanning for words — a text scan picks up docstrings
    # that happen to contain the same phrases.
    literals: set[str] = set()
    for node in (n for fn in scoped for n in ast.walk(fn)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "HTTPException"
        ):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        status = kw.get("status_code")
        is_503 = (
            isinstance(status, ast.Constant) and status.value == 503
        ) or (
            isinstance(status, ast.Attribute)
            and status.attr == "HTTP_503_SERVICE_UNAVAILABLE"
        )
        if not is_503:
            continue
        detail = kw.get("detail")
        if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
            literals.add(detail.value)
        elif isinstance(detail, ast.Name):
            literals.add(getattr(mod, detail.id))
    assert literals == {mod._UNAVAILABLE}, (
        "more than one degraded-reply sentence exists, so a write failure can "
        f"tell the states apart by wording: {sorted(literals)}"
    )


def test_the_draft_door_wires_the_release_decision_into_mark_submitted() -> None:
    """The DECISION is tested; this is the WIRING, which was not.

    `_stage_reapplication` returning `release_draft_pointer=True` has three
    behavioural tests. What had nothing was the handler's use of it:
    `release_staged_draft_cv = staged.release_draft_pointer` feeding
    `mark_submitted(..., release_resume=release_staged_draft_cv)`. Change that
    argument to a literal `False` and the draft keeps a pointer to the object
    the tail delete has just removed — and every test stays green, because the
    readback is 404 either way and the decision test never reaches the handler.

    That is round 5's defect moved from a function body to a call site, which
    is what extracting a body without extracting its call buys you. Asserted
    on the parsed tree because what matters is that the ARGUMENT is the value
    the helper returned, not a constant.
    """
    import app.routers.public_apply as mod

    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
    door = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "submit_draft"
    )

    calls = [
        n
        for n in ast.walk(door)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "mark_submitted"
    ]
    assert calls, "submit_draft no longer consumes its draft"

    wired = []
    for call in calls:
        arg = next(
            (k.value for k in call.keywords if k.arg == "release_resume"), None
        )
        # A Name, not a Constant: the decision must come from
        # `_stage_reapplication`, never be hard-coded at the call site.
        wired.append(isinstance(arg, ast.Name))

    assert any(wired), (
        "every `mark_submitted` in submit_draft passes a constant or nothing "
        "for `release_resume`, so the release decision "
        "`_stage_reapplication` makes is thrown away — the draft keeps a "
        "pointer to an object the tail delete removes"
    )


# ===========================================================================
# The pad ABSORBS its term — asserted by behaviour, not by position
# ===========================================================================
@pytest.mark.asyncio
async def test_a_pad_absorbs_the_work_it_covers_to_a_constant() -> None:
    """The property every placement guard in this file assumes and none proves.

    Round 9 found the deadline arithmetic broken while every structural guard
    stayed green: `_reply` computed
        max(floor - (now - floor_from), floor - (now - tail_from))
    and called it "the later of two deadlines". It is — and `time.monotonic`
    is monotonic, so `tail_from >= floor_from` always, so the tail term always
    won and the `floor_from` term was unreachable arithmetic. The reply
    released at `tail_from + floor`, with `tail_from` taken AFTER the identity
    lookup, so a 1.30 ms difference in that lookup produced a 1.30 ms
    difference in reply time. The guards checked where the clocks were
    ASSIGNED; nothing checked that a clock bounded anything.

    So this measures the actual property: given a deadline taken BEFORE some
    work, two runs whose work differs in duration must finish at the same
    time. If the deadline is computed from a clock taken after the work — the
    bug — the difference passes straight through and the two runs differ by
    however much the work differed.
    """
    from app.routers.public_apply import _hold_until

    budget = 0.25

    async def release_after(work: float) -> float:
        started = time.monotonic()
        await asyncio.sleep(work)
        await _hold_until(started + budget, what="test")
        return time.monotonic() - started

    quick = await release_after(0.005)
    slow = await release_after(0.060)

    # Both land on the deadline, so the 55 ms difference in work is gone.
    assert quick >= budget * 0.95, quick
    assert slow >= budget * 0.95, slow
    assert abs(slow - quick) < 0.040, (
        "the pad did not absorb the work it covers: runs differing by 55 ms of "
        f"work finished {abs(slow - quick) * 1000:.0f} ms apart, so the work is "
        "still observable from outside"
    )


@pytest.mark.asyncio
async def test_a_deadline_taken_after_the_work_absorbs_nothing() -> None:
    """The negative control, so the test above cannot pass vacuously.

    This is the shape round 9 found in `_reply`: the deadline measured from a
    clock taken after the state-dependent work. It must NOT absorb — and if
    this ever starts absorbing, the test above is measuring something other
    than what it claims.
    """
    from app.routers.public_apply import _hold_until

    budget = 0.25

    async def release_after(work: float) -> float:
        started = time.monotonic()
        await asyncio.sleep(work)
        after_work = time.monotonic()  # the mistake: clock taken AFTER
        await _hold_until(after_work + budget, what="test")
        return time.monotonic() - started

    quick = await release_after(0.005)
    slow = await release_after(0.060)

    assert slow - quick > 0.030, (
        "a deadline taken after the work appears to absorb it, which cannot be "
        "true — the positive test above is therefore not measuring absorption"
    )


def test_the_reply_pad_is_not_a_max_of_two_clocks() -> None:
    """`max()` of two remaining-times is just the later deadline.

    Kept as a cheap structural companion to the behavioural tests above,
    because this exact expression shipped and was described as covering both
    terms. It covers the later one, and the later one is the one that has
    already let the lookup through.
    """
    import app.routers.public_apply as mod

    # PARSED, not scanned. The first version of this test asserted
    # `"max(" not in inspect.getsource(_reply)` and failed immediately — on the
    # word "max()" inside the docstring explaining why max() is wrong. That is
    # the same defect it exists to catch (an index into source text matching
    # prose rather than code), reproduced in the guard against it.
    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
    reply = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_reply"
    )

    calls = {
        n.func.id
        for n in ast.walk(reply)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "max" not in calls, (
        "`_reply` combines deadlines with max() again — that selects the LATER "
        "deadline, and the later clock is taken after the identity lookup, so "
        "the lookup passes straight through"
    )

    names = {n.id for n in ast.walk(reply) if isinstance(n, ast.Name)}
    assert "floor_from" not in names, (
        "`_reply` reads floor_from again; the lookup is absorbed by its own pad "
        "in the handler, and a second reference here is the broken shape"
    )


def _hold_calls(node: ast.AST, what: str) -> list[ast.Call]:
    """Every `_hold_until(..., what=<what>)` call inside *node*."""
    out = []
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_hold_until"
        ):
            for kw in n.keywords:
                if (
                    kw.arg == "what"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == what
                ):
                    out.append(n)
    return out


def _enclosing_ifs(node: ast.AST, target: ast.Call) -> list[ast.If]:
    """EVERY `if` whose body contains *target*, outermost first.

    Round 12 defeated the single-`If` version: `ast.walk` is breadth-first, so
    it returned the OUTERMOST condition, and nesting a second test under the
    unchanged `apply_lookup_floor_ms > 0` switched the pad off in production
    with the guard green:

        if settings.apply_lookup_floor_ms > 0:
            if not settings.app_env.startswith("prod"):
                await _hold_until(...)

    Returning the whole chain lets the caller require that the budget test is
    present AND that nothing else gates the pad.
    """
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.If) and any(
            c is target for stmt in n.body for c in ast.walk(stmt)
        ):
            out.append(n)
    return out


def test_the_lookup_pad_exists_and_bounds_the_identity_lookup() -> None:
    """ROUND 10 added this pad's only guard. ROUND 11 defeated it four ways.

    `apply_lookup_floor_ms` is the whole of round 7's fix and the reason round
    9 existed: it absorbs the identity lookup, a LEFT JOIN that returns a row
    for four states and nothing for the fifth. Before round 10 it appeared
    only in the config default, the two call sites and one line of the risk
    register — asserted nowhere, removable three ways with the suite green.

    Round 10's guard required a `_hold_until(..., what="lookup")` call whose
    deadline mentioned `floor_from`, positioned before `tail_from` and before
    a hard-coded list of caller-sized callees. Round 11 walked through it:

    * `max(floor_from, time.monotonic()) + ...` — round 9's own bug, verbatim,
      moved from `_reply` to the handlers. `floor_from` still appears as a
      Name, so the guard passed, while the deadline became `now + 50 ms` and
      absorbed nothing. The guard written for exactly this shape
      (`test_the_reply_pad_is_not_a_max_of_two_clocks`) is scoped to `_reply`.
    * `if settings.apply_lookup_floor_ms > 0:` → `< 0:`. One character, both
      pads dead, 17/17 green. The guard inspected the call and never its
      enclosing condition.
    * Rename `_extract_pdf_text` to a one-line helper and the "caller-sized"
      ceiling silently drops out of a name-keyed list, after which the pad can
      be relocated below the parse again. The ceiling set was the weak part: a
      list of three callee names is a guard against those three names.

    So this now asserts STRUCTURE rather than mentions:

    1. The deadline is `floor_from + <something>` — `floor_from` as the BinOp's
       own left operand, not a Name buried anywhere inside it.
    2. The enclosing condition is `settings.apply_lookup_floor_ms > 0`.
    3. The ONLY awaited work between `floor_from` and the pad is `_identify`.
       That is the property the pad exists for, it names no caller-sized
       callee, and it cannot be defeated by renaming one — anything moved into
       that window is work a 50 ms budget was never sized for.
    4. The pad still closes before the second deadline.
    """
    for name, node in _door_bodies().items():
        holds = _hold_calls(node, "lookup")
        assert len(holds) == 1, (
            f"{name}: expected exactly one lookup pad, found {len(holds)}. "
            "Removing it reopens round 7's channel."
        )
        call = holds[0]

        # (1) shape, not mention.
        deadline = call.args[0] if call.args else None
        assert (
            isinstance(deadline, ast.BinOp)
            and isinstance(deadline.op, ast.Add)
            and isinstance(deadline.left, ast.Name)
            and deadline.left.id == "floor_from"
        ), (
            f"{name}: the lookup deadline is not `floor_from + <budget>`. It must "
            "be measured FROM the clock taken before the lookup — "
            "`max(floor_from, time.monotonic()) + budget` mentions floor_from and "
            "absorbs nothing, which is round 9's bug moved into the handler."
        )

        # (2) the switch that turns it off — and NOTHING ELSE gating it.
        guards = _enclosing_ifs(node, call)
        assert len(guards) == 1, (
            f"{name}: the lookup pad sits under {len(guards)} conditions, not 1. "
            "A second test nested under the budget check — on `app_env`, on a "
            "feature flag — turns the pad off wherever that test is false, "
            "while the budget check it is nested in keeps this guard green."
        )
        test_src = ast.dump(guards[0].test)
        assert (
            "apply_lookup_floor_ms" in test_src
            and "Gt()" in test_src
            and "value=0" in test_src
        ), (
            f"{name}: the pad's condition is not "
            "`settings.apply_lookup_floor_ms > 0`. Flipping the comparison is a "
            f"one-character way to disable both pads. Found: {test_src[:120]}"
        )

        # (3) nothing but the lookup inside the padded window.
        floor = _first_line(
            node,
            lambda n: isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "floor_from" for t in n.targets
            ),
        )
        assert floor is not None, f"{name}: never starts the lookup clock"
        assert floor < call.lineno, (
            f"{name}: the pad at {call.lineno} is above `floor_from` at {floor}"
        )

        inside = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Await) and floor < n.lineno < call.lineno
        ]
        # ATTRIBUTE CALLS COUNT. Round 12's fourth defeat was
        # `raw = await resume.read(_MAX_RESUME_BYTES + 1)` moved above
        # `_identify` — a 5 MB read inside the 50 ms pad, invisible because the
        # callee is an Attribute and not a Name. `await db.scalar(text(...))`
        # for a new flag is the realistic instance.
        callees = {c for c in (_callee_name(n.value) for n in inside) if c}
        assert callees == {"_identify"}, (
            f"{name}: the work between `floor_from` and the lookup pad is "
            f"{sorted(callees) or 'nothing'}, not just the identity lookup. A "
            "50 ms budget was sized for one indexed point read; anything else in "
            "that window is absorbed by the same deadline or pushes it over. "
            "Relocating the pad below the CV parse lands here, whatever the "
            "parse helper is called."
        )

        # (4) and it closes before the second clock.
        tail = _first_line(
            node,
            lambda n: isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "tail_from" for t in n.targets),
        )
        assert tail is not None, f"{name}: no `tail_from`, the two-pad structure is gone"
        assert call.lineno < tail, (
            f"{name}: the lookup pad at {call.lineno} is below `tail_from` at {tail}"
        )


def test_the_lookup_pad_is_not_configured_off() -> None:
    """The cheapest defeat of all: no code change, one environment variable.

    `config.py`'s own comment says "Setting it to 0 disables the pad and
    reopens the channel", and before this nothing enforced it — so
    `APPLY_LOOKUP_FLOOR_MS=0` in a deployment's environment silently switched
    off half the control with 2882 unit tests still green. The reply floor has
    had this assertion since round 8 (in the integration matrix); the lookup
    floor never got one, which is exactly the one-of-two pattern this branch
    keeps finding.
    """
    import pydantic
    import pytest

    from app.config import Settings, settings

    assert settings.apply_lookup_floor_ms > 0, (
        "the lookup pad is disabled, so the identity lookup's cost is "
        "measurable again: ~1.3 ms on this repo, additive, outside every "
        "deadline, and it separates 'never applied here' from the other four "
        "states in roughly 200 requests"
    )

    # AND A DEPLOYMENT CANNOT TURN IT OFF EITHER. The assertion above pins the
    # DEFAULT, which is all a unit test can see — round 11 pointed out that the
    # docstring and the alert runbook both claimed this test stopped
    # `APPLY_LOOKUP_FLOOR_MS=0` in a real environment, and it could not.
    #
    # ROUND 12 then pointed out that `Field(gt=0)` is one character wide: `=1`
    # booted with the control effectively off, and `=999999999` booted with an
    # 11.6-day reply, neither red nor alerted. Both pads now carry a floor AND
    # a ceiling, so the useless settings are unreachable from a deployment's
    # environment rather than merely discouraged in a comment.
    for field, too_small, too_big in (
        ("apply_lookup_floor_ms", 9, 1_001),
        ("apply_reply_floor_ms", 99, 5_001),
    ):
        for bad in (0, -1, too_small, too_big):
            with pytest.raises(pydantic.ValidationError):
                Settings(**{field: bad})


def test_the_stored_resume_text_is_bounded_on_both_doors() -> None:
    """ROUND 10's HIGH, and the one channel the two pads could not absorb.

    `resume_text` is written twice inside the reply pad — `applicants` and
    `users` — and both writes live only on the branch that CREATES an
    applicant. States (a) and (b) take `_refuse`; (c) and (e) have
    `is_new_person=False` and an already-linked user, so `_ensure_guest_user`
    returns early. Only "this address has never applied here" writes it.

    While the text was unbounded, the caller chose how far that one state
    overran a 400 ms budget whose own comment justifies itself as covering
    "tens of milliseconds" of fixed work. `_MAX_RESUME_BYTES` is not a bound on
    it: pypdf extraction AMPLIFIES, and a 0.81 MB PDF of repetitive text
    measured 14.19 MB of extracted characters.

    The bound is applied where the text is PRODUCED, not where it is written,
    so a third write site cannot reintroduce the channel. This asserts that is
    still true on both doors — the draft door included, even though only the
    one-shot door pads a region containing the write, because a bound that
    holds on one door only is the shape of defect this branch has found five
    times.
    """
    import app.routers.public_apply as mod

    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
    funcs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name in ("submit_application", "upload_draft_resume")
    }
    assert set(funcs) == {"submit_application", "upload_draft_resume"}, funcs.keys()

    for name, node in funcs.items():
        bounded = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Subscript)
            and any(
                isinstance(x, ast.Name) and x.id == "_MAX_RESUME_TEXT_CHARS"
                for x in ast.walk(n.slice)
            )
        ]
        assert bounded, (
            f"{name}: nothing truncates the extracted CV text by "
            "_MAX_RESUME_TEXT_CHARS. Unbounded, it is a caller-sized term "
            "inside the reply pad on the one door and an unbounded column on "
            "the other."
        )

        # AND IT MUST WRAP THE PARSE, not sit at a write site. Round 11 found
        # the draft door truncating at its `attach_resume` call instead, which
        # left an unbounded local in scope for the rest of the function — so
        # "every write site inherits the bound, and a third one cannot
        # reintroduce the channel" was true on one door only. Requiring the
        # subscript to wrap `await _extract_pdf_text(...)` is what makes that
        # sentence checkable rather than aspirational.
        at_producer = [
            n
            for n in bounded
            if isinstance(n.value, ast.Await)
            and isinstance(n.value.value, ast.Call)
            and isinstance(n.value.value.func, ast.Name)
            and n.value.value.func.id == "_extract_pdf_text"
        ]
        assert at_producer, (
            f"{name}: the CV text is truncated somewhere, but not where it is "
            "PRODUCED. Applied at a write site, the unbounded value stays in "
            "scope and the next consumer added to this function inherits "
            "nothing. Wrap the `_extract_pdf_text` await instead."
        )

    assert 0 < mod._MAX_RESUME_TEXT_CHARS <= 200_000, (
        f"_MAX_RESUME_TEXT_CHARS is {mod._MAX_RESUME_TEXT_CHARS}. The point of "
        "the bound is that two writes of it stay far below the reply pad; "
        "raising it past ~200k chars needs the pad re-measured first."
    )


_DELETERS = frozenset({"_delete_from_s3", "_release_unadopted", "_best_effort_delete"})


def _callee_name(node: ast.AST) -> str | None:
    """The called name, whether it is `f(...)` or `mod.f(...)`.

    BOTH FORMS, because round 12 defeated the first version of this guard and
    of the lookup-pad guard the same way: they collected callees only from
    `ast.Call` whose `func` is an `ast.Name`, so `await resume_mod._delete_from_s3(k)`
    or `await db.scalar(...)` was invisible. "A list of three callee names is a
    guard against those three names" was round 11's criticism of round 10's
    guard; matching only bare names reintroduced it one layer down.
    """
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _handlers_that_reply(tree: ast.AST) -> list[ast.ExceptHandler]:
    """Every `except` handler that RETURNS rather than raising.

    The point of the distinction: an awaited S3 delete is fine on a path that
    raises a 503, because the response is already decided and no pad is
    covering it. It is NOT fine on a path that returns `_reply`, because that
    path is padded like any other.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and any(
            isinstance(inner, ast.Return)
            for stmt in node.body
            for inner in ast.walk(stmt)
        ):
            out.append(node)
    return out


def test_no_object_release_is_awaited_on_a_reply_path() -> None:
    """ROUND 11 wrote this guard. ROUND 12 walked through it in one edit.

    `_refuse` used to commit, then AWAIT an S3 `DeleteObject`, then call
    `_reply` — a state-dependent term inside the reply pad, since only the
    branches that release an unadopted CV reach it. Measured at 347-651 ms
    against a 400 ms pad, dominated by 180-450 ms of SYNCHRONOUS botocore
    client construction.

    THE RULE: an object release on a path that returns a reply must be
    SCHEDULED, not awaited. Awaiting is correct inside a handler that RAISES —
    there the response is already decided and no pad covers it.

    Round 11's version excused every line inside any `except` block, on the
    stated premise that "those paths raise a 503 rather than returning
    `_reply`". Two of the five sites it counted are inside handlers that
    `return await _reply(...)`: `submit_draft`'s `except Exception` race
    cleanup and `submit_application`'s `except IntegrityError`. So adding
    `await _release_unadopted(s3_key)` next to the existing `add_task` on
    either of them put the delete back inside the pad with all eighteen
    structural tests green — demonstrated, not theorised.

    This version excuses a handler only when it does not return, and matches
    attribute calls as well as bare names.
    """
    import app.routers.public_apply as mod

    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

    # Lines inside a handler that RAISES rather than returning. Those may await.
    replying = {id(h) for h in _handlers_that_reply(tree)}
    excused: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and id(node) not in replying:
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    if hasattr(inner, "lineno"):
                        excused.add(inner.lineno)

    scrutinised = ("_refuse", "submit_application", "submit_draft")
    funcs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name in scrutinised
    }
    assert set(funcs) == set(scrutinised), funcs.keys()

    offenders: list[str] = []
    for name, node in funcs.items():
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Await):
                continue
            callee = _callee_name(inner.value)
            if callee in _DELETERS and inner.lineno not in excused:
                offenders.append(f"{name}:{inner.lineno} awaits {callee}")

    assert not offenders, (
        "an object release is awaited on a path that returns a reply, which "
        "puts a state-dependent S3 round trip inside the pad. Schedule it with "
        "`background.add_task` instead; awaiting is only correct inside an "
        "`except` handler that RAISES. Offenders: " + "; ".join(offenders)
    )

    # And the scheduling route must be in use, or the rule is satisfied
    # vacuously by a handler that stopped cleaning up. Counted on the RELEASE
    # callee, not on `add_task`: round 11's version counted any `add_task` in
    # the module, so wrapping an unrelated call (`background.add_task(
    # wake_reconciler)`) restored the count while a release went back to being
    # awaited.
    scheduled = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and _callee_name(n) == "add_task"
        and n.args
        and isinstance(n.args[0], ast.Name)
        and n.args[0].id in _DELETERS
    ]
    assert len(scheduled) == 5, (
        f"{len(scheduled)} release(s) are scheduled after the reply, expected 5. "
        "The five sites that release an object on a 201 path are `_refuse`, "
        "both doors' superseded-reapplication CV, and both doors' lost-race "
        "exit. A LOWER count is the vacuous escape: a handler that stops "
        "cleaning up awaits nothing and leaks the object instead, which is an "
        "un-consented CV left in storage with no erasure anchor. A HIGHER count "
        "means a release site exists that this guard has not been reasoned "
        "about — add it here deliberately."
    )
