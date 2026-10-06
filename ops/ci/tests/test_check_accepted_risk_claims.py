"""Tests for the accepted-risk claim checker.

The failure mode this guards is the one ops/ci/tests exists for: a matcher stops
recognising what it was looking for, every set comes back empty, and the script
prints OK for ever. That is worse here than elsewhere — a silent pass would
report a CRITICAL acceptance's justification as holding while nobody is checking
it.

So the AST matcher is tested against the shapes it must catch AND the shapes it
must not, rather than only against the repo as it stands today.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import check_accepted_risk_claims as claims  # noqa: E402


def _write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp_path / "mod.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_a_decode_with_algorithms_is_seen_as_restricted(tmp_path) -> None:
    p = _write(tmp_path, "from jose import jwt\nx = jwt.decode(t, k, algorithms=['HS256'])\n")
    assert claims._decode_calls(p) == [(2, True)]


def test_a_decode_without_algorithms_is_seen_as_unrestricted(tmp_path) -> None:
    """The whole python-jose acceptance turns on catching exactly this."""
    p = _write(tmp_path, "from jose import jwt\nx = jwt.decode(t, k)\n")
    assert claims._decode_calls(p) == [(2, False)]


def test_the_keyword_may_sit_on_another_line(tmp_path) -> None:
    """Why this is an AST walk and not `'algorithms=' in line`: a substring
    search on the call's own line misses this, and a search of the file catches
    a comment three functions away."""
    p = _write(
        tmp_path,
        "from jose import jwt\nx = jwt.decode(\n    t,\n    k,\n    algorithms=[a],\n)\n",
    )
    assert claims._decode_calls(p) == [(2, True)]


def test_a_mention_in_a_comment_does_not_count_as_restriction(tmp_path) -> None:
    p = _write(
        tmp_path,
        "from jose import jwt\n# algorithms=['HS256'] would be safer\nx = jwt.decode(t, k)\n",
    )
    assert claims._decode_calls(p) == [(3, False)]


def test_bytes_decode_is_not_a_jwt_decode(tmp_path) -> None:
    """`payload.decode()` is not a JWT call. Counting it would make the script
    cry wolf on every file that touches bytes."""
    p = _write(tmp_path, "raw = b'x'\ns = raw.decode('utf-8')\n")
    assert claims._decode_calls(p) == []


def test_an_aliased_jwt_module_is_still_matched(tmp_path) -> None:
    p = _write(
        tmp_path, "from jose import jwt as jose_jwt\nx = jose_jwt.decode(t, k)\n"
    )
    assert claims._decode_calls(p) == [(2, False)]


def test_the_repo_as_it_stands_passes(capsys) -> None:
    """The real check, against the real tree. If this ever fails, an accepted
    risk's justification has stopped holding — read the stderr, not this test."""
    assert claims.main() == 0
    assert "every accepted-risk justification still holds" in capsys.readouterr().out


def test_an_empty_match_set_is_a_failure_not_a_pass(monkeypatch, capsys) -> None:
    """The silent-rot case, asserted directly.

    If the matcher stops finding any decode at all — a refactor, a library
    change, a bug in this script — the script must FAIL rather than report that
    all decodes are safe, because "no decodes found" and "every decode is safe"
    are indistinguishable from the outside and only one of them is good news.
    """
    monkeypatch.setattr(claims, "_decode_calls", lambda _path: [])
    assert claims.main() == 1
    assert "vacuously" in capsys.readouterr().err


@pytest.mark.parametrize("pkg", ["python-jose", "langgraph", "langgraph-sdk"])
def test_each_reasoned_pin_is_named(pkg: str) -> None:
    """A bump must be noticed, so the versions the reasoning was checked against
    have to be recorded here rather than inferred from the tree."""
    assert pkg in claims.PINNED
