"""Tests for the routing / header contract gate.

The cases that matter are the ones that actually happened:

  * a router prefix (``/apply``, ``/careers``) present in the code and absent
    from both Caddyfiles, which on the Space fails as **HTTP 200 serving
    index.html** rather than as an error;
  * a custom token header (``X-Draft-Token``) reaching neither the access-log
    redaction list nor the CORS allow-list.

A checker's own failure mode is silence: a parser stops matching, every set
comes back empty, and it prints OK forever. So the first test below asserts the
parser still finds the real prefixes and headers, and the rest assert the gate
can genuinely go red.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "ops" / "ci" / "check_routing_contract.py"

spec = importlib.util.spec_from_file_location("check_routing_contract", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules["check_routing_contract"] = module
spec.loader.exec_module(module)


# A minimal Caddyfile with the same shape as the real ones.
GOOD_CADDY = """
:7860 {
\trewrite @spa_nav /index.html
\thandle /internal/* {
\t\trespond "Forbidden" 403
\t}
\thandle /apply* {
\t\treverse_proxy 127.0.0.1:8002
\t}
\tlog {
\t\tformat filter {
\t\t\tfields {
\t\t\t\trequest>headers>X-Draft-Token delete
\t\t\t}
\t\t}
\t}
}
"""

GOOD_CORS = 'allow_headers=["Authorization", "X-Draft-Token"],\n'


@pytest.fixture()
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A one-prefix, one-header world the tests can break in specific ways."""

    def _build(caddy: str = GOOD_CADDY, cors: str = GOOD_CORS, **kw: object):
        caddyfile = tmp_path / "Caddyfile"
        caddyfile.write_text(caddy, encoding="utf-8")
        corsfile = tmp_path / "main.py"
        corsfile.write_text(cors, encoding="utf-8")
        monkeypatch.setattr(module, "ROOT", tmp_path)
        monkeypatch.setattr(module, "CADDYFILES", (caddyfile,))
        monkeypatch.setattr(module, "CORS_FILE", corsfile)
        monkeypatch.setattr(
            module,
            "router_prefixes",
            lambda: kw.get("prefixes", {"data_gateway": {"/apply"}}),
        )
        monkeypatch.setattr(
            module,
            "token_headers",
            lambda: kw.get("headers", {"X-Draft-Token": "public_apply.py:491"}),
        )
        return module.main()

    return _build


# ===========================================================================
# The gate is not vacuous
# ===========================================================================
def test_the_committed_repo_passes() -> None:
    """The real files, checked as CI checks them — the regression guard."""
    assert module.main() == 0


def test_the_parser_still_finds_the_real_prefixes() -> None:
    """If `APIRouter(prefix=...)` is ever spelled differently, this gate would
    silently check an empty set and pass forever."""
    found = {p for prefixes in module.router_prefixes().values() for p in prefixes}
    for expected in ("/apply", "/careers", "/auth", "/hr", "/admin", "/users"):
        assert expected in found, expected


def test_the_parser_still_finds_the_real_headers() -> None:
    headers = module.token_headers()
    assert {"X-Exam-Token", "X-Interview-Token", "X-Draft-Token"} <= set(headers)


# ===========================================================================
# The failures that actually shipped
# ===========================================================================
def test_a_prefix_with_no_handle_block_is_caught(fake, capsys) -> None:
    """`/careers` in the code, in neither Caddyfile — the PH3 bug."""
    rc = fake(prefixes={"data_gateway": {"/apply", "/careers"}})
    assert rc == 1
    assert "/careers" in capsys.readouterr().err


def test_a_prefix_matched_but_not_proxied_is_caught(fake) -> None:
    """A `handle` that only responds is not routing; catching the block but not
    the proxy would be a false pass."""
    caddy = GOOD_CADDY.replace(
        '\thandle /apply* {\n\t\treverse_proxy 127.0.0.1:8002\n\t}',
        '\thandle /apply* {\n\t\trespond "Not Found" 404\n\t}',
    )
    assert fake(caddy=caddy) == 1


def test_an_unredacted_token_header_is_caught(fake, capsys) -> None:
    """The credential moving from the URL into the access log — CWE-532."""
    assert fake(caddy=GOOD_CADDY.replace("request>headers>X-Draft-Token delete", "")) == 1
    assert "logged verbatim" in capsys.readouterr().err


def test_a_header_missing_from_cors_is_caught(fake, capsys) -> None:
    """Same-origin on the Space, dead on every split-origin deploy."""
    assert fake(cors='allow_headers=["Authorization"],\n') == 1
    assert "preflight" in capsys.readouterr().err


def test_an_exempt_header_is_not_required_to_be_redacted(fake) -> None:
    """X-CSRF-Token is deliberately logged; the exemption must actually work,
    or the next author silences the gate instead of the finding."""
    assert fake(
        caddy=GOOD_CADDY.replace("request>headers>X-Draft-Token delete", ""),
        cors='allow_headers=["X-CSRF-Token"],\n',
        headers={"X-CSRF-Token": "auth.py:611"},
    ) == 0


def test_an_internal_prefix_must_be_refused_not_proxied(fake) -> None:
    """/internal exposed at the edge is the bug, so proxying it must fail."""
    caddy = GOOD_CADDY.replace(
        '\thandle /internal/* {\n\t\trespond "Forbidden" 403\n\t}',
        "\thandle /internal/* {\n\t\treverse_proxy 127.0.0.1:8003\n\t}",
    )
    assert fake(caddy=caddy, prefixes={"feedback_billing": {"/internal"}}) == 1


# ===========================================================================
# Caddy path-matcher semantics
# ===========================================================================
@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/apply*", "/apply/draft", True),
        ("/apply*", "/applyx", True),  # Caddy really is a raw prefix match
        ("/users/*", "/users/me/probe", True),
        ("/users/*", "/usersomething", False),
        ("/health", "/health", True),
        ("/health", "/health/live", False),
    ],
)
def test_path_matching(pattern: str, path: str, expected: bool) -> None:
    assert module.matches(pattern, path) is expected


def test_nested_braces_do_not_truncate_a_handle_body() -> None:
    """The log block nests three levels; a naive scan to the first `}` would
    report the body as empty and every proxy check would falsely fail."""
    blocks = dict(module.handle_blocks(GOOD_CADDY))
    assert "reverse_proxy" in blocks["/apply*"]
