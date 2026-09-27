"""The smoke runner's local-only guard.

These smokes drop databases, recreate them, and TRUNCATE. It happened once
(2026-09-07: 571 test rows written into the live Neon database and deleted by
hand), so the runner refuses anything but a local, disposable database — and
the refusal itself deserves tests, because the only other way to find out it
is wrong is the way it was found out the first time.

Two things are checked here that the host check alone does not cover.

`SMOKE_DB_NAME` and `SMOKE_DATABASE_URL` are separate variables, and the
runner prepares the first while every smoke connects to the second. So a URL
pointed at `127.0.0.1/intants_dev` passed the host check and the smokes
truncated the developer's own database. "Local" and "not real" are different
claims and only the second one matters here.

And both database names are interpolated unquoted into `psql -c "DROP DATABASE
{name}"`. Anyone who can set the environment can already run code here, so
this is not a privilege boundary — but the statement is a DROP, and a name
that needs quoting is a typo rather than a request worth honouring.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

RUNNER = (
    pathlib.Path(__file__).resolve().parents[1] / "integration" / "run_all.py"
)


def _load(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    """Import run_all.py with a given environment.

    It reads the environment at module scope, so each case needs its own
    import. Guarded by `if __name__ == "__main__"`, so importing runs nothing.
    """
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location(f"run_all_{id(env)}", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LOCAL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432"


def test_a_remote_database_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load(
        monkeypatch,
        SMOKE_DATABASE_URL="postgresql+asyncpg://u:p@ep-cool-1.ap-southeast-1.aws.neon.tech/x_smoke",
        SMOKE_DB_NAME="x_smoke",
    )
    with pytest.raises(SystemExit) as exit_info:
        mod._refuse_unless_local()  # noqa: SLF001
    assert "not this machine" in str(exit_info.value)


def test_a_local_url_naming_a_different_database_than_the_runner_prepares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the host check let through.

    The runner drops and recreates `intants_smoke`; the smokes connect to
    `intants_dev` and truncate it. Both are on this machine, so the host check
    is satisfied and the wrong database is emptied.
    """
    mod = _load(
        monkeypatch,
        SMOKE_DATABASE_URL=f"{LOCAL}/intants_dev",
        SMOKE_DB_NAME="intants_smoke",
    )
    with pytest.raises(SystemExit) as exit_info:
        mod._refuse_unless_local()  # noqa: SLF001
    message = str(exit_info.value)
    assert "intants_dev" in message and "intants_smoke" in message


def test_a_database_not_named_as_disposable_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting both variables to the same real database is the same mistake
    with one fewer inconsistency to notice, so agreeing is not enough — the
    name has to say it is throwaway."""
    mod = _load(
        monkeypatch,
        SMOKE_DATABASE_URL=f"{LOCAL}/intants_dev",
        SMOKE_DB_NAME="intants_dev",
    )
    with pytest.raises(SystemExit) as exit_info:
        mod._refuse_unless_local()  # noqa: SLF001
    assert "disposable" in str(exit_info.value)


@pytest.mark.parametrize(
    "bad",
    [
        "smoke; DROP DATABASE intants_dev",
        'smoke" WITH (FORCE); --',
        "Intants_Smoke",  # would need quoting to mean what it says
        "1_smoke",
    ],
)
def test_a_database_name_that_needs_quoting_is_refused(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    mod = _load(
        monkeypatch, SMOKE_DATABASE_URL=f"{LOCAL}/x_smoke", SMOKE_DB_NAME="x_smoke"
    )
    with pytest.raises(SystemExit) as exit_info:
        mod._refuse_unless_valid_identifier(bad, "SMOKE_DB_NAME")  # noqa: SLF001
    assert "DROP DATABASE" in str(exit_info.value)


def test_the_local_disposable_default_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard has to let the normal case through, or it is just an outage."""
    mod = _load(
        monkeypatch,
        SMOKE_DATABASE_URL=f"{LOCAL}/intants_smoke",
        SMOKE_DB_NAME="intants_smoke",
        SMOKE_PH3_DB="ph3_smoke",
    )
    mod._refuse_unless_local()  # noqa: SLF001 — no SystemExit is the assertion


def test_there_is_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """`conftest.py` honours ALLOW_REMOTE_TEST_DB for pytest fixtures. This
    runner deliberately does not: a smoke that drops databases has no business
    on a remote host, and two guards for one rule with different answers is
    how one of them rots."""
    mod = _load(
        monkeypatch,
        SMOKE_DATABASE_URL="postgresql+asyncpg://u:p@db.example.com/x_smoke",
        SMOKE_DB_NAME="x_smoke",
        ALLOW_REMOTE_TEST_DB="1",
    )
    with pytest.raises(SystemExit):
        mod._refuse_unless_local()  # noqa: SLF001
