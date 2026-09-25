"""Fake session + storage spy for the PH5-E2 corpus unit tests.

Its own module, on the ``tests/unit/_sandbox_helpers.py`` precedent, because
four test files need the same two objects and copying them would let the copies
drift.

WHAT THE FAKE PROVES, AND WHAT IT CANNOT
It answers the statements ``app/corpus.py`` sends with canned rows, records
every statement WITH ITS BOUND PARAMETERS, and records object-storage calls on
the SAME timeline as the SQL — so a test can assert the ORDER in which a
function touches the database and the object store, which is the only place
several of this module's crash-safety claims live.

It cannot prove anything about SQL SEMANTICS: whether ``d.audience = ANY(...)``
really filters, whether ``ts_rank_cd`` ranks, whether the
``corpus_versions_immutable`` trigger refuses an update, or whether a
half-finished purge really rolls back. Those are
``tests/integration/test_ph5_e2_corpus_db.py``'s job, and tests here assert on
the statement and its parameters rather than pretending otherwise.

``commit()`` raises by default: ``app/corpus.py``'s stated contract is that
every function does at most ``db.flush()`` and the CALLER commits (the router,
which is where the ``CorpusError`` rollback lives). A service function that
started committing on its own would break that rollback, so the fake fails
loudly instead of letting it pass.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

Route = tuple[str, Any]


def norm(sql: Any) -> str:
    """One-line, single-spaced SQL, so a test can match a phrase across the
    line breaks the module's string concatenation happens to use."""
    return " ".join(str(sql).split())


class Result:
    """The slice of SQLAlchemy's ``Result`` that ``app/corpus.py`` actually
    uses: ``.mappings()``, ``.all()``, ``.first()``, ``.one()``, ``.rowcount``."""

    def __init__(self, rows: Sequence[Any], rowcount: int | None = None) -> None:
        self._rows = list(rows)
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    def mappings(self) -> Result:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        if not self._rows:
            raise AssertionError(
                "one() on an empty result — the test's route table has no row for this statement"
            )
        return self._rows[0]


class FakeSession:
    """Answers statements by matching a substring of the normalised SQL.

    ``routes``/``scalars`` are ``(phrase, value)`` pairs, first match wins.
    A value may be:

    * a list of rows (mappings or tuples — whatever the call site reads),
    * an ``int``, meaning "no rows, this ``rowcount``" (for an UPDATE whose
      caller checks ``rowcount``, e.g. ``mark_version_indexed``),
    * an exception instance, which is raised,
    * a zero-argument callable returning any of the above, re-evaluated per
      call, for a statement whose answer must change between calls.

    An unmatched statement returns no rows. That is deliberate: a function
    reaching for a row the test did not plan for shows up as an empty result
    (usually a 404/409 the test then fails on) rather than as a mystery.
    """

    def __init__(
        self,
        routes: Sequence[Route] | None = None,
        *,
        scalars: Sequence[Route] | None = None,
        allow_commit: bool = False,
    ) -> None:
        self.routes: list[Route] = list(routes or [])
        self.scalars: list[Route] = list(scalars or [])
        self.allow_commit = allow_commit
        # Statements and store calls interleaved, in call order. SQL entries are
        # the normalised statement text; store entries are "store.<fn>:<detail>".
        self.timeline: list[str] = []
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    # -- plumbing ----------------------------------------------------------
    def _resolve(self, table: Sequence[Route], sql: str) -> Any:
        for phrase, value in table:
            if phrase in sql:
                return value() if callable(value) else value
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Result:
        sql = norm(stmt)
        self.statements.append((sql, dict(params or {})))
        self.timeline.append(sql)
        value = self._resolve(self.routes, sql)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, int):
            return Result([], rowcount=value)
        return Result(value or [])

    async def scalar(self, stmt: Any, params: Any = None) -> Any:
        sql = norm(stmt)
        self.statements.append((sql, dict(params or {})))
        self.timeline.append(sql)
        value = self._resolve(self.scalars, sql)
        if isinstance(value, BaseException):
            raise value
        return value

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.timeline.append(f"session.add:{type(obj).__name__}")

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        if not self.allow_commit:
            raise AssertionError(
                "app/corpus.py's contract is that the CALLER commits — this function did"
            )
        self.commits += 1
        self.timeline.append("COMMIT")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.timeline.append("ROLLBACK")

    # -- assertions helpers ------------------------------------------------
    def matching(self, phrase: str) -> list[tuple[str, dict[str, Any]]]:
        return [(sql, p) for sql, p in self.statements if phrase in sql]

    def one_statement(self, phrase: str) -> tuple[str, dict[str, Any]]:
        found = self.matching(phrase)
        assert len(found) == 1, (
            f"expected exactly one statement containing {phrase!r}, got {len(found)}:\n"
            + "\n".join(sql for sql, _ in found)
        )
        return found[0]

    def params_for(self, phrase: str) -> dict[str, Any]:
        return self.one_statement(phrase)[1]

    def has(self, phrase: str) -> bool:
        return bool(self.matching(phrase))

    def step(self, phrase: str) -> int:
        """Position of the first timeline entry containing *phrase*."""
        for i, entry in enumerate(self.timeline):
            if phrase in entry:
                return i
        raise AssertionError(f"{phrase!r} never happened. Timeline:\n" + "\n".join(self.timeline))

    def writes(self) -> list[str]:
        """Every statement that could change data, for the "a refusal wrote
        nothing" assertions."""
        return [
            sql
            for sql, _ in self.statements
            if sql.split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}
        ]


class StoreSpy:
    """Stands in for ``app.document_storage``'s three network calls, recording
    them on the session's timeline so ordering against SQL is assertable.

    Installed with ``monkeypatch.setattr`` on the individual attributes of
    ``app.corpus.store`` — ``check_corpus``/``corpus_storage_key`` stay REAL, so
    the content sniffing and the key shape under test are the shipped ones.
    """

    def __init__(
        self,
        session: FakeSession,
        *,
        removed: int | Callable[[list[str]], int] | None = None,
        url: str = "https://signed.example/doc?sig=x",
        fail_put: BaseException | None = None,
    ) -> None:
        self.session = session
        self._removed = removed
        self.url = url
        self.fail_put = fail_put
        self.put: list[tuple[str, bytes, str]] = []
        self.removed_keys: list[list[str]] = []
        self.signed: list[tuple[str, str]] = []

    async def store(self, _settings: Any, key: str, data: bytes, content_type: str) -> None:
        self.put.append((key, data, content_type))
        self.session.timeline.append(f"store.put:{key}")
        if self.fail_put is not None:
            raise self.fail_put

    async def remove(self, _settings: Any, keys: list[str]) -> int:
        self.removed_keys.append(list(keys))
        self.session.timeline.append("store.remove:" + ",".join(keys))
        if callable(self._removed):
            return self._removed(keys)
        return len(keys) if self._removed is None else self._removed

    async def signed_download(self, _settings: Any, key: str, filename: str) -> str:
        self.signed.append((key, filename))
        self.session.timeline.append(f"store.signed_download:{key}")
        return self.url


def install_store(monkeypatch: Any, module: Any, spy: StoreSpy) -> StoreSpy:
    """Point *module*'s ``store`` at *spy* for the three network calls only."""
    monkeypatch.setattr(module.store, "store", spy.store)
    monkeypatch.setattr(module.store, "remove", spy.remove)
    monkeypatch.setattr(module.store, "signed_download", spy.signed_download)
    return spy
