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
\t\trequest_body {
\t\t\tmax_size 6MB
\t\t}
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
        # Check 4 reads real service files under ROOT, and ROOT is a tmp_path here,
        # so the real table would report every row as unreadable. Empty by default;
        # the body-cap tests below pass their own.
        monkeypatch.setattr(module, "UPLOAD_LIMITS", kw.get("upload_limits", ()))
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
        '\thandle /apply* {\n\t\trequest_body {\n\t\t\tmax_size 6MB\n\t\t}\n'
        '\t\treverse_proxy 127.0.0.1:8002\n\t}',
        '\thandle /apply* {\n\t\trequest_body {\n\t\t\tmax_size 6MB\n\t\t}\n'
        '\t\trespond "Not Found" 404\n\t}',
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


def test_a_body_cap_that_disagrees_with_the_table_is_caught(fake, capsys) -> None:
    """The cap and BODY_CAPS must agree, or AR-10's enumeration rots again.

    That is not hypothetical. AR-10 described these caps in prose, which went
    stale; the prose became an enumeration on 2026-10-04 and the enumeration was
    wrong the NEXT DAY, when a VAPT pass retuned /apply* from 8MB to 6MB. This
    check is why the table is now the only statement of them.
    """
    assert fake(caddy=GOOD_CADDY.replace("max_size 6MB", "max_size 9MB")) == 1
    assert "BODY_CAPS says" in capsys.readouterr().err


def test_a_capped_prefix_losing_its_cap_is_caught(fake, capsys) -> None:
    """Deleting the block is the likelier regression than mistyping the number —
    it looks like tidying up, and it uncaps an anonymous upload prefix."""
    assert (
        fake(caddy=GOOD_CADDY.replace("\t\trequest_body {\n\t\t\tmax_size 6MB\n\t\t}\n", ""))
        == 1
    )
    assert "has no request_body block" in capsys.readouterr().err


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


# ===========================================================================
# Check 4 — an edge body cap below the handler's own limit
# ===========================================================================
UPLOAD_CADDY = """
:7860 {
\thandle /apply* {
\t\trequest_body {
\t\t\tmax_size 6MB
\t\t}
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


def _service_file(root: Path, body: str) -> None:
    """Write services/data_gateway/app/routers/probe.py under *root*."""
    target = root / "services" / "data_gateway" / "app" / "routers"
    target.mkdir(parents=True, exist_ok=True)
    (target / "probe.py").write_text(body, encoding="utf-8")


ROW = ("/apply/probe/resume", "data_gateway/app/routers/probe.py", "_MAX", "probe upload")


def test_a_cap_below_the_handlers_limit_is_caught(fake, tmp_path: Path, capsys) -> None:
    """The defect this check exists for, and the one I shipped into review:
    ``/jobs*`` at 1MB against a 10 MB JD limit. Caddy answers 413 at the edge, so
    the service never sees the request and its log says nothing."""
    _service_file(tmp_path, "_MAX = 10 * 1024 * 1024\n")

    assert fake(caddy=UPLOAD_CADDY, upload_limits=(ROW,)) == 1

    err = capsys.readouterr().err
    assert "below the 10,485,760 the handler allows" in err
    assert "413" in err


def test_a_cap_at_exactly_the_limit_passes() -> None:
    """``>=``, not ``>``. A cap equal to the handler's limit rejects nothing the
    handler would have accepted, and off-by-one here would make every correctly
    sized cap a failure."""
    assert module.body_cap("\n\t\tmax_size 6MB\n") == 6 * 1024 * 1024


def test_a_cap_above_the_limit_passes(fake, tmp_path: Path) -> None:
    """Multipart framing and a filename push the body above the file's own size,
    which is why the real blocks sit a megabyte over."""
    _service_file(tmp_path, "_MAX = 5 * 1024 * 1024\n")

    assert fake(caddy=UPLOAD_CADDY, upload_limits=(ROW,)) == 0


def test_a_block_with_no_cap_at_all_is_not_reported(
    fake, tmp_path: Path, monkeypatch
) -> None:
    """Deliberate asymmetry, stated in the script's scope note: this check only
    says a cap is not too SMALL. Whether a route needs one is a judgement the
    checker cannot make, and reporting every uncapped prefix would turn a precise
    gate into a list nobody reads.

    BODY_CAPS IS DROPPED FOR THIS ONE TEST, and that is the point rather than a
    workaround. ``BODY_CAPS`` is the other, stricter contract: for the prefixes it
    ENUMERATES it demands the cap exists and agrees, because AR-10 rests on
    ``/apply*`` being capped at the edge. The fixture strips the cap off
    ``/apply*``, so leaving the enumeration in place would have this test
    exercising that contract instead of the asymmetry it is named for — it failed
    exactly that way when the two checks first met, on the 2026-10-07 merge.
    """
    _service_file(tmp_path, "_MAX = 999 * 1024 * 1024\n")
    monkeypatch.setattr(
        module, "BODY_CAPS", {k: v for k, v in module.BODY_CAPS.items() if k != "/apply*"}
    )
    no_cap = UPLOAD_CADDY.replace(
        "\t\trequest_body {\n\t\t\tmax_size 6MB\n\t\t}\n", ""
    )

    assert fake(caddy=no_cap, upload_limits=(ROW,)) == 0


def test_an_enumerated_cap_that_vanishes_IS_reported(fake, tmp_path: Path, capsys) -> None:
    """The other side of the test above, so the asymmetry is bounded rather than
    general: a prefix listed in BODY_CAPS whose ``request_body`` block has gone is
    a finding. Without this, the monkeypatch above could be loosened to "never
    report a missing cap" and nothing would notice."""
    _service_file(tmp_path, "_MAX = 999 * 1024 * 1024\n")
    no_cap = UPLOAD_CADDY.replace(
        "\t\trequest_body {\n\t\t\tmax_size 6MB\n\t\t}\n", ""
    )

    assert fake(caddy=no_cap, upload_limits=(ROW,)) == 1
    assert "should cap bodies at 6MB" in capsys.readouterr().err


def test_a_symbol_that_stopped_being_a_literal_is_reported_not_skipped(
    fake, tmp_path: Path, capsys
) -> None:
    """A checker's own failure mode is silence. If someone makes the limit a
    computed value, this must say so rather than quietly pass the row."""
    _service_file(tmp_path, "_MAX = int(os.environ['X'])\n")

    assert fake(caddy=UPLOAD_CADDY, upload_limits=(ROW,)) == 1
    assert "no longer has a literal value" in capsys.readouterr().err


def test_an_unrouted_probe_is_left_to_check_one(fake, tmp_path: Path, capsys) -> None:
    """One defect, one message. A prefix that reaches no backend is check 1's
    finding, and reporting it twice with different wording sends the reader
    looking for two problems."""
    _service_file(tmp_path, "_MAX = 10 * 1024 * 1024\n")
    row = ("/nowhere/probe", "data_gateway/app/routers/probe.py", "_MAX", "probe")

    fake(caddy=UPLOAD_CADDY, upload_limits=(row,))

    assert "caps bodies at" not in capsys.readouterr().err


# ===========================================================================
# The parsers behind check 4
# ===========================================================================
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("max_size 1MB", 1024**2),
        ("max_size 260MB", 260 * 1024**2),
        ("max_size 512KB", 512 * 1024),
        ("max_size 2GB", 2 * 1024**3),
        ("max_size 1024B", 1024),
        ("reverse_proxy x:1", None),
    ],
)
def test_body_cap_units(text: str, expected: int | None) -> None:
    """Caddy's own suffixes. ``256KB`` is already used on two real blocks, so a
    parser that only understood MB would read it as nothing and pass anything."""
    assert module.body_cap(f"\n\t\t{text}\n") == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("_MAX = 5242880\n", 5242880),
        ("_MAX = 5 * 1024 * 1024\n", 5 * 1024 * 1024),
        ("_MAX: int = 10 * 1024 * 1024\n", 10 * 1024 * 1024),
        ("_MAX = 2 * 1024 * 1024  # 2 MB\n", 2 * 1024 * 1024),
        ("_MAX = some_call()\n", None),
    ],
)
def test_handler_limit_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, expected: int | None
) -> None:
    """Both shapes the repo uses: a module constant and a Settings field with an
    annotation. The last row is the one that must return None rather than guess."""
    _service_file(tmp_path, source)
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.handler_limit("data_gateway/app/routers/probe.py", "_MAX") == expected


def test_every_upload_limits_row_resolves_against_the_real_tree() -> None:
    """The table is maintained by hand, so a renamed constant or a moved module
    must fail here rather than silently stop checking its route. This is the same
    anti-rot rule the camera-proctoring contract test applies to its citations."""
    unresolved = [
        f"{symbol} in {mod}"
        for _probe, mod, symbol, _what in module.UPLOAD_LIMITS
        if module.handler_limit(mod, symbol) is None
    ]

    assert not unresolved, f"UPLOAD_LIMITS rows that no longer resolve: {unresolved}"


def test_the_real_caddyfiles_cap_the_bulk_route_above_its_handler_limit() -> None:
    """Named explicitly because it is the row most likely to be "simplified" away:
    /hr/applicants/bulk needs its OWN handle block, since /hr/* at 12MB would 413
    a legitimate 250 MB batch. The generic check above covers it, but a reader
    deleting the block deserves a test that says why it exists."""
    limit = module.handler_limit(
        "data_gateway/app/routers/hr_applicants.py", "_MAX_BULK_TOTAL_BYTES"
    )
    assert limit is not None

    for path in module.CADDYFILES:
        text = path.read_text(encoding="utf-8")
        block = next(
            b
            for pat, b in module.handle_blocks(text)
            if module.matches(pat, "/hr/applicants/bulk")
        )
        cap = module.body_cap(block)
        assert cap is not None and cap >= limit, f"{path.name}: bulk cap {cap} < {limit}"
