"""HR's per-interview avatar/audio toggle — the writer side.

Three things are worth pinning here, and they are the three ways this feature
could be present and still not work:

1. THE VALUE IS VALIDATED, so a typo cannot reach a column whose CHECK will
   then reject the whole transaction at commit — a 500 where a 422 belongs.

2. THE PATCH ROUTE EXISTS AND IS STATUS-GATED. Most invites are minted by the
   workflow runner with no human choosing anything, so a create-only toggle
   would be unreachable for the majority of interviews. And it must refuse once
   the candidate has started, because the mode is copied onto the session at
   first redeem: a control that silently does nothing is worse than no control.

3. THE COPY HAPPENS. The worker reads ``sessions.avatar_mode``, never the
   invite. If the redeem path does not carry the value across, HR's choice is
   recorded, displayed, audited — and ignored.

Nos. 2 and 3 are asserted against the source rather than a live request,
because both sit behind a database and an authenticated HR session. That is a
weaker test than a round-trip and it is named as such; what it buys is that the
specific regressions above fail loudly instead of silently.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest
from pydantic import ValidationError

from app.routers import hr_interviews


class TestTheValueIsValidated:
    @pytest.mark.parametrize("mode", ["avatar", "audio"])
    def test_both_modes_are_accepted(self, mode: str) -> None:
        body = hr_interviews.InviteCreateIn(
            applicant_id="00000000-0000-0000-0000-000000000001", avatar_mode=mode
        )
        assert body.avatar_mode == mode

    @pytest.mark.parametrize(
        "bad", ["", "AUDIO", "Avatar", "video", "none", "tavus", "simli", "off"]
    )
    def test_anything_else_is_refused_at_the_edge(self, bad: str) -> None:
        """Including the PROVIDER names. 'tavus' is the shape of mistake someone
        makes reaching for this control expecting a vendor picker, and it must
        be a 422 rather than a column value no worker can interpret."""
        with pytest.raises(ValidationError):
            hr_interviews.InviteCreateIn(
                applicant_id="00000000-0000-0000-0000-000000000001", avatar_mode=bad
            )

    def test_omitting_it_keeps_the_old_behaviour(self) -> None:
        """Every caller written before this feature — and the public API
        contract — must be unaffected."""
        body = hr_interviews.InviteCreateIn(
            applicant_id="00000000-0000-0000-0000-000000000001"
        )
        assert body.avatar_mode == "avatar"

    def test_the_patch_body_validates_the_same_way(self) -> None:
        """Two entry points, one rule. A PATCH that accepted values create
        refuses would be the easier of the two to reach, since it is the one
        HR uses on workflow-minted invites."""
        assert hr_interviews.AvatarModeIn(avatar_mode="audio").avatar_mode == "audio"
        with pytest.raises(ValidationError):
            hr_interviews.AvatarModeIn(avatar_mode="tavus")

    def test_the_two_modes_are_exactly_these(self) -> None:
        assert {"avatar", "audio"} == hr_interviews.AVATAR_MODES


class TestThePatchRouteIsStatusGated:
    def test_the_route_is_registered(self) -> None:
        paths = {
            (r.path, m)
            for r in hr_interviews.router.routes
            for m in getattr(r, "methods", set())
        }
        assert ("/hr/interviews/{invite_id}/avatar-mode", "PATCH") in paths, (
            "the per-invite toggle is unreachable; a create-only mode leaves "
            "every workflow-minted interview with no way to change it"
        )

    def test_it_refuses_anything_but_invited(self) -> None:
        """Guarded by source: the handler needs a DB session and an HR context.
        What must not regress is the gate itself — without it the endpoint
        accepts a change for a started interview and returns 200 having
        altered nothing the worker will read."""
        src = inspect.getsource(hr_interviews.set_invite_avatar_mode)
        assert 'inv.status != "invited"' in src, (
            "the status gate is gone — HR can now 'change' the mode of an "
            "interview already in flight and be told it worked"
        )
        assert "409" in src, "the refusal must be a conflict, not a silent no-op"

    def test_the_change_is_audited(self) -> None:
        """Who switched a candidate's interview to audio, and when. The repo
        audits every other invite mutation; an unaudited one is the gap a DPDP
        review finds."""
        src = inspect.getsource(hr_interviews.set_invite_avatar_mode)
        assert "interview_invite.avatar_mode_changed" in src
        assert '"from": previous' in src, "an audit row without the old value cannot show a change"


class TestTheCopyToTheSessionHappens:
    """The worker reads sessions.avatar_mode and never the invite. This is the
    one link in the chain whose absence leaves everything else looking correct.
    """

    def _redeem_source(self) -> str:
        path = (
            pathlib.Path(hr_interviews.__file__).resolve().parent / "interview_take.py"
        )
        return path.read_text(encoding="utf-8")

    def test_the_insert_names_the_column(self) -> None:
        src = self._redeem_source()
        assert "avatar_mode" in src, (
            "interview_take.py never mentions avatar_mode: the session is "
            "created without it, so every interview runs on the instance "
            "default and HR's choice is recorded but ignored"
        )

    def test_the_value_comes_from_the_invite(self) -> None:
        src = self._redeem_source()
        assert '"amode": inv.avatar_mode' in src, (
            "the session's avatar_mode is not being taken from the invite"
        )

    def test_the_insert_is_still_parameterised(self) -> None:
        """Adding a column to a hand-written INSERT is where an f-string creeps
        in. The whole statement must stay bound parameters."""
        src = self._redeem_source()
        start = src.index("INSERT INTO sessions")
        stmt = src[start : start + 400]
        assert ":amode" in stmt, "avatar_mode must be a bound parameter"
        assert "f'" not in stmt and 'f"' not in stmt, (
            "the sessions INSERT gained an f-string — bind it instead"
        )


def test_every_invite_response_carries_the_mode() -> None:
    """HR cannot manage what the list does not show. ``InviteOut`` declares
    ``avatar_mode`` as required, so a construction site that forgets it is a
    TypeError at import-time review rather than a field quietly missing from
    one of the four responses."""
    assert "avatar_mode" in hr_interviews.InviteOut.model_fields
    assert hr_interviews.InviteOut.model_fields["avatar_mode"].is_required()

    source = pathlib.Path(hr_interviews.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    sites = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "InviteOut"
    ]
    assert sites, "no InviteOut construction found — this test stopped checking anything"
    for call in sites:
        kwargs = {k.arg for k in call.keywords}
        assert "avatar_mode" in kwargs, (
            f"InviteOut built at line {call.lineno} without avatar_mode"
        )
