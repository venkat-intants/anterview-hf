"""The publish gate has exactly one definition — PH3-B0.

Three queries and four display surfaces each decided for themselves whether an
opening was live to the public, and the answers had drifted: the apply endpoint
required a published workflow and the careers board did not, so the board
advertised roles that 404'd on click. The cross-check test that existed compared
three of the five gates and never saw it.

PH3-B2 (approval) and PH3-B4a (scheduled publish) each add a clause to this same
predicate. The acceptance criterion they have to meet — "an unapproved
requisition cannot accidentally become publicly available" — is exactly the one
that fails on the surface somebody forgot to edit. So the rule is made
executable here rather than requested in a comment: the gate lives in
``app.publishing`` and nowhere else.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# The one module allowed to spell the predicate out.
_HOME = APP / "publishing.py"

# Where a stray re-implementation would do real damage: any module that builds
# SQL against job_requisitions. Tests are excluded — asserting on the predicate
# is what they are for.
_SQL_GATE = re.compile(r"public_apply_enabled\s*(?:AND|\n|$)", re.IGNORECASE)


def _python_sources() -> list[Path]:
    return [p for p in APP.rglob("*.py") if p != _HOME and "__pycache__" not in p.parts]


# ===========================================================================
# Nobody re-implements the gate
# ===========================================================================
def test_no_module_outside_publishing_builds_the_predicate_in_sql() -> None:
    """A WHERE clause that mentions the opt-in flag is a second gate.

    Reading the column to *display* it is fine — that is what
    ``public_gate_open`` is for, and those call sites pass it as a keyword
    argument rather than concatenating it into SQL.
    """
    offenders: list[str] = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            # A SQL fragment, not a Python expression: the flag appears inside a
            # string literal and is followed by more predicate.
            if "public_apply_enabled" not in stripped:
                continue
            if not (stripped.startswith(('"', "'", "f'", 'f"')) or "AND" in stripped.upper()):
                continue
            if _SQL_GATE.search(stripped) and "status" in stripped.lower():
                offenders.append(f"{path.relative_to(APP)}:{lineno}: {stripped}")
    assert not offenders, (
        "These build the publish predicate themselves instead of calling "
        "app.publishing.visible_sql():\n  " + "\n  ".join(offenders)
    )


def test_every_public_surface_imports_the_shared_gate() -> None:
    """The three queries that decide what the open web sees."""
    for module in (
        "routers/careers.py",
        "routers/public_apply.py",
        "routers/candidate_applications.py",
    ):
        source = (APP / module).read_text(encoding="utf-8")
        assert "from app.publishing import" in source, module
        assert "visible_sql" in source, module


def test_the_display_surfaces_use_the_shared_gate_too() -> None:
    """HR's board must not report an opening as open to applications when the
    public surfaces have already stopped showing it."""
    for module in (
        "company_board.py",
        "requisition_dashboard.py",
        "agents/watch_runner.py",
    ):
        source = (APP / module).read_text(encoding="utf-8")
        assert "public_gate_open" in source, module


# ===========================================================================
# The predicate itself
# ===========================================================================
def test_visible_sql_carries_every_named_gate() -> None:
    from app.publishing import GATE_NAMES, visible_sql

    sql = visible_sql("r")
    assert "r.deleted_at IS NULL" in sql
    assert "r.status = 'open'" in sql
    assert "r.public_apply_enabled" in sql
    assert "r.approval_status = 'approved'" in sql  # PH3-B2
    assert "r.closes_at" in sql
    assert "status = 'published'" in sql  # the workflow gate
    # Named rather than counted: a count tells the next author that something
    # changed, a name tells them what.
    assert GATE_NAMES == (
        "not_deleted",
        "status_open",
        "public_apply_enabled",
        "approved",
        "not_past_closing_date",
        "has_published_workflow",
    )


def test_visible_sql_honours_the_alias() -> None:
    from app.publishing import visible_sql

    sql = visible_sql("req")
    assert "req.public_apply_enabled" in sql
    assert " r.public_apply_enabled" not in sql


def test_the_workflow_gate_can_be_dropped_but_nothing_else_can() -> None:
    from app.publishing import visible_sql

    without = visible_sql("r", require_published_workflow=False)
    assert "status = 'published'" not in without
    for gate in ("deleted_at IS NULL", "status = 'open'", "public_apply_enabled", "closes_at"):
        assert gate in without


# ===========================================================================
# The Python mirror agrees with the SQL
# ===========================================================================
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _open(**kw: object) -> dict:
    base = {
        "status": "open",
        "public_apply_enabled": True,
        "approval_status": "approved",
        "closes_at": None,
        "deleted_at": None,
        "now": NOW,
    }
    return {**base, **kw}


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, True),
        ({"status": "paused"}, False),
        ({"status": "closed"}, False),
        ({"public_apply_enabled": False}, False),
        ({"deleted_at": NOW}, False),
        ({"closes_at": NOW - timedelta(days=1)}, False),
        ({"closes_at": NOW + timedelta(days=1)}, True),
        # Exactly at the closing instant is closed: the date is inclusive of
        # its own expiry, matching `closes_at > :now` in SQL.
        ({"closes_at": NOW}, False),
        # PH3-B2: approval is a gate like any other.
        ({"approval_status": "draft"}, False),
        ({"approval_status": "pending_approval"}, False),
        ({"approval_status": "rejected"}, False),
        ({"approval_status": "approved"}, True),
    ],
)
def test_public_gate_open(override: dict, expected: bool) -> None:
    from app.publishing import public_gate_open

    assert public_gate_open(**_open(**override)) is expected


def test_is_publicly_live_also_requires_a_published_workflow() -> None:
    from app.publishing import is_publicly_live

    assert is_publicly_live(**_open(), has_published_workflow=True) is True
    assert is_publicly_live(**_open(), has_published_workflow=False) is False


def test_a_paused_opening_is_not_live_even_with_a_workflow() -> None:
    from app.publishing import is_publicly_live

    assert is_publicly_live(**_open(status="paused"), has_published_workflow=True) is False


def test_an_unapproved_opening_is_not_live_even_with_everything_else_set() -> None:
    """PH3-B2's central acceptance criterion, asserted against the one
    predicate every public surface uses."""
    from app.publishing import is_publicly_live

    assert is_publicly_live(
        **_open(approval_status="pending_approval"), has_published_workflow=True
    ) is False
