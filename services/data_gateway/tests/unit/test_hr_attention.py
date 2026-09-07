"""The attention panel.

Thin by design — the detection rules are ``shared.agents.watchers`` and are
tested there. What is worth pinning here is the handful of decisions this
endpoint makes, because each one is invisible and each one would look fine if
it were wrong:

  * it computes live rather than reading delivered notifications, so a
    suppressed-but-persisting condition still shows;
  * it does not consult ``WATCHERS_ENABLED``, which is about background
    delivery, not about answering a direct question;
  * it is scoped to the caller's own company, from the session;
  * a rule that throws costs its own finding, not the whole panel.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock

import pytest
from shared.agents import Citation, WatcherFinding, WatcherInput


def _ctx() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


def _finding(**kw: object) -> WatcherFinding:
    base = {
        "watcher": "stalled_applicants",
        "severity": "warning",
        "title": "7 applicant(s) stalled over 7 days",
        "body": "Nobody has moved them since last week.",
        "link": "/hr/pipeline",
        "dedupe_key": "stalled:a,b,c",
        "citations": [
            Citation(kind="applicant", id="ap-1", label="Asha Rao", href="/hr/applicants/ap-1")
        ],
    }
    return WatcherFinding(**{**base, **kw})


# ===========================================================================
# Live, not replayed
# ===========================================================================
@pytest.mark.asyncio
async def test_the_panel_runs_the_rules_rather_than_reading_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this avoids: notifications are deduped so a condition notifies
    once, which is right for a bell and wrong for a panel — the panel would go
    quiet a day after the alert while the problem was still there."""
    from app.routers import hr_attention

    gathered: list[str] = []

    async def _gather(_db: object, company_id: str) -> WatcherInput:
        gathered.append(company_id)
        return WatcherInput(company_id=company_id)

    monkeypatch.setattr(hr_attention, "gather_company_input", _gather)
    monkeypatch.setattr(hr_attention, "run_watchers", lambda _d: [_finding()])

    out = await hr_attention.get_attention(_ctx(), AsyncMock())
    assert gathered, "the panel must gather fresh data on every read"
    assert out.total == 1


def _code_only(module: object) -> str:
    """Module source with docstrings and comments stripped.

    Scanning raw source for a word is a trap: this module's own docstring
    explains at length why it does NOT read notifications, and a naive scan
    fails on the sentence describing the decision. ast.unparse drops comments
    and lets docstrings be removed, so what is left is what the module does.
    """
    import ast

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_panel_never_reads_delivered_notifications() -> None:
    """Notifications are deduped for a bell. A panel built on them goes quiet a
    day after the alert while the condition is still true."""
    from app.routers import hr_attention

    code = _code_only(hr_attention).lower()
    assert "notification" not in code
    # It has no SQL of its own either — the gathering is the runner's job, and
    # a query added here would be one nobody scoped to the company.
    assert "select " not in code


# ===========================================================================
# Scope
# ===========================================================================
@pytest.mark.asyncio
async def test_the_company_comes_from_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import hr_attention

    seen: list[str] = []

    async def _gather(_db: object, company_id: str) -> WatcherInput:
        seen.append(company_id)
        return WatcherInput(company_id=company_id)

    monkeypatch.setattr(hr_attention, "gather_company_input", _gather)
    monkeypatch.setattr(hr_attention, "run_watchers", lambda _d: [])

    hr_uid, company_id = _ctx()
    await hr_attention.get_attention((hr_uid, company_id), AsyncMock())
    assert seen == [str(company_id)]


def test_the_endpoint_takes_no_company_parameter() -> None:
    """A company_id argument would make the panel pointable at another tenant."""
    from app.routers.hr_attention import get_attention

    params = set(inspect.signature(get_attention).parameters)
    assert not params & {"company_id", "user_id", "slug"}


# ===========================================================================
# The flag
# ===========================================================================
def test_the_panel_does_not_consult_watchers_enabled() -> None:
    """That flag switches off the nightly sweep — unsolicited delivery. Someone
    opening the panel is asking directly, and refusing would be a setting doing
    something nobody meant by it."""
    from app.routers import hr_attention

    code = _code_only(hr_attention).lower()
    assert "watchers_enabled" not in code
    assert "settings" not in code


# ===========================================================================
# Shape
# ===========================================================================
@pytest.mark.asyncio
async def test_a_finding_carries_its_link_and_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every alert must be actionable — clicking one opens the affected record.
    A panel of statements you cannot act on is a second inbox."""
    from app.routers import hr_attention

    monkeypatch.setattr(
        hr_attention, "gather_company_input",
        AsyncMock(return_value=WatcherInput(company_id="c")),
    )
    monkeypatch.setattr(hr_attention, "run_watchers", lambda _d: [_finding()])

    out = await hr_attention.get_attention(_ctx(), AsyncMock())
    item = out.items[0]
    assert item.link == "/hr/pipeline"
    assert item.citations[0].href == "/hr/applicants/ap-1"
    assert item.citations[0].label == "Asha Rao"


@pytest.mark.asyncio
async def test_severity_order_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_watchers sorts worst-first; the panel must not re-sort by anything
    else, or the thing that needs attention stops being at the top."""
    from app.routers import hr_attention

    ordered = [
        _finding(severity="critical", title="c", dedupe_key="1"),
        _finding(severity="warning", title="w", dedupe_key="2"),
        _finding(severity="info", title="i", dedupe_key="3"),
    ]
    monkeypatch.setattr(
        hr_attention, "gather_company_input",
        AsyncMock(return_value=WatcherInput(company_id="c")),
    )
    monkeypatch.setattr(hr_attention, "run_watchers", lambda _d: ordered)

    out = await hr_attention.get_attention(_ctx(), AsyncMock())
    assert [i.severity for i in out.items] == ["critical", "warning", "info"]


@pytest.mark.asyncio
async def test_a_quiet_company_gets_an_empty_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing wrong is the good outcome, not an error."""
    from app.routers import hr_attention

    monkeypatch.setattr(
        hr_attention, "gather_company_input",
        AsyncMock(return_value=WatcherInput(company_id="c")),
    )
    monkeypatch.setattr(hr_attention, "run_watchers", lambda _d: [])

    out = await hr_attention.get_attention(_ctx(), AsyncMock())
    assert out.total == 0
    assert out.items == []
    assert out.generated_at


# ===========================================================================
# What the log may say
# ===========================================================================
@pytest.mark.asyncio
async def test_the_log_line_carries_counts_not_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finding's title names a candidate. Logs are read by people with no
    business seeing who is stalled in someone else's pipeline."""
    from app.routers import hr_attention

    captured: dict = {}
    monkeypatch.setattr(
        hr_attention, "gather_company_input",
        AsyncMock(return_value=WatcherInput(company_id="c")),
    )
    monkeypatch.setattr(hr_attention, "run_watchers", lambda _d: [_finding()])
    monkeypatch.setattr(
        hr_attention.log, "info", lambda _e, **kw: captured.update(kw)
    )

    await hr_attention.get_attention(_ctx(), AsyncMock())
    blob = str(captured)
    assert "Asha Rao" not in blob
    assert "stalled over 7 days" not in blob
    assert captured["findings"] == 1


# ===========================================================================
# Resilience, inherited rather than reimplemented
# ===========================================================================
def test_one_broken_rule_cannot_empty_the_panel() -> None:
    """run_watchers catches per-watcher failures itself. Asserted at the
    library so the endpoint can rely on it without a second try/except that
    would swallow real errors too."""
    from shared.agents import run_watchers
    from shared.agents import watchers as watcher_mod

    def _explode(_data: WatcherInput) -> list[WatcherFinding]:
        raise RuntimeError("boom")

    original = watcher_mod.WATCHERS
    try:
        watcher_mod.WATCHERS = ((("exploding", _explode),) + tuple(original))  # type: ignore[assignment]
        findings = run_watchers(WatcherInput(company_id="c"))
    finally:
        watcher_mod.WATCHERS = original  # type: ignore[assignment]
    assert isinstance(findings, list)


def test_the_endpoint_does_not_wrap_the_rules_in_its_own_except() -> None:
    """A blanket try/except here would hide a genuine database failure behind
    an empty panel, which reads as "nothing needs attention"."""
    from app.routers.hr_attention import get_attention

    assert "except" not in _code_only(get_attention)
