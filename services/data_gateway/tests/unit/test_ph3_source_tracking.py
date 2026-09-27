"""Where an application came from — PH3-B1.

The value lands in a PH5-C1 ``GROUP BY``, and it arrives on a URL anyone can
construct. Most of what matters here is therefore what the normaliser refuses to
put in the column, and what it refuses to throw away.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0001_c3e5a7b9d1f4_ph3_b1_application_source.py"
)
_DATA_GATEWAY = Path(__file__).resolve().parents[2]
_REPO_ROOT = _DATA_GATEWAY.parents[1]


# ===========================================================================
# The vocabulary has one definition
# ===========================================================================
def test_the_migration_allows_exactly_what_the_code_knows_about() -> None:
    """A CHECK constraint wider than the vocabulary admits values nothing can
    read; narrower, and a legitimate write becomes a 500."""
    from app.application_source import SOURCES

    module: dict = {}
    exec(  # noqa: S102 - reading a constant out of a migration, not running it
        compile(MIGRATION.read_text(encoding="utf-8").split("def upgrade")[0], "m", "exec"),
        module,
    )
    assert set(module["_SOURCES"]) == set(SOURCES)


def test_the_detail_pattern_is_the_same_in_both_places() -> None:
    from app.application_source import _DETAIL_OK

    sql = MIGRATION.read_text(encoding="utf-8")
    assert "^[a-z0-9][a-z0-9_-]{0,63}$" in sql
    assert _DETAIL_OK.pattern == "^[a-z0-9][a-z0-9_-]{0,63}$"


def test_untracked_and_direct_are_different_values() -> None:
    """Collapsing them would make the day this shipped read as a surge in
    direct applications."""
    from app.application_source import DIRECT, UNTRACKED

    assert DIRECT != UNTRACKED
    assert {DIRECT, UNTRACKED} <= set(__import__(
        "app.application_source", fromlist=["SOURCES"]
    ).SOURCES)


# ===========================================================================
# Normalisation
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("careers_site", ("careers_site", None)),
        ("CAREERS_SITE", ("careers_site", None)),
        ("  careers_site  ", ("careers_site", None)),
        ("careers-site", ("careers_site", None)),
        ("careers site", ("careers_site", None)),
        ("referral", ("referral", None)),
        # Aliases a recruiter will actually type.
        ("linkedin", ("job_board", "linkedin")),
        ("LinkedIn", ("job_board", "linkedin")),
        ("naukri", ("job_board", "naukri")),
        ("careers", ("careers_site", None)),
        ("qr", ("qr_code", None)),
    ],
)
def test_known_channels_normalise(raw: str, expected: tuple[str, str | None]) -> None:
    from app.application_source import normalise_source

    assert normalise_source(raw) == expected


def test_an_untracked_application_is_direct_not_unknown() -> None:
    from app.application_source import normalise_source

    assert normalise_source(None) == ("direct", None)
    assert normalise_source("") == ("direct", None)
    assert normalise_source("   ") == ("direct", None)


def test_the_caller_chooses_what_absent_means() -> None:
    """An HR import with no channel is internal; a public application with no
    channel is direct. Same function, different default."""
    from app.application_source import normalise_source

    assert normalise_source(None, default="internal") == ("internal", None)


def test_a_url_cannot_claim_to_be_untracked() -> None:
    """'unknown' is ours to assign. A link that says it is untracked would
    otherwise hide a tracked arrival inside the historical bucket."""
    from app.application_source import normalise_source

    assert normalise_source("unknown") == ("direct", None)
    assert normalise_source("unknown", default="internal") == ("internal", None)


def test_an_unrecognised_channel_is_kept_not_dropped() -> None:
    from app.application_source import normalise_source

    assert normalise_source("diwali_campaign_2026") == ("other", "diwali_campaign_2026")


@pytest.mark.parametrize(
    "hostile",
    [
        "'; DROP TABLE enrolments; --",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "a" * 200,
        "%00null",
        "emoji-🙂-channel",
        "chan;nel",
        "a|b",
    ],
)
def test_a_hostile_value_never_reaches_the_column(hostile: str) -> None:
    """Refused, not recorded as `other`: filling the `other` bucket with noise
    is itself the attack on an analytics dimension."""
    from app.application_source import normalise_source

    source, detail = normalise_source(hostile)
    assert source == "direct"
    assert detail is None


def test_whitespace_inside_a_tag_is_folded_rather_than_refused() -> None:
    """A newline gets exactly the treatment a space gets — the folding that
    makes "careers site" work. What survives is a slug that satisfies the
    column's own CHECK, so there is nothing left to refuse, and refusing would
    silently lose a campaign to a stray line break in a spreadsheet."""
    from app.application_source import _DETAIL_OK, normalise_source

    source, detail = normalise_source("channel\nwith\nnewlines")
    assert source == "other"
    assert detail == "channel_with_newlines"
    assert _DETAIL_OK.match(detail)


def test_an_over_long_value_is_not_even_normalised() -> None:
    from app.application_source import normalise_source

    assert normalise_source("x" * 5000) == ("direct", None)


def test_every_result_satisfies_the_database_constraints() -> None:
    """Whatever the normaliser returns must be insertable, or a tracked link
    becomes a 500 on the apply endpoint."""
    from app.application_source import _DETAIL_OK, SOURCES, normalise_source

    probes = [
        None, "", "linkedin", "referral", "unknown", "other", "a-b-c",
        "'; DROP TABLE x; --", "x" * 500, "Campaign.Name-2026", "  ",
    ]
    for probe in probes:
        source, detail = normalise_source(probe)
        assert source in SOURCES, probe
        assert detail is None or _DETAIL_OK.match(detail), probe


def test_normalise_detail_sanitises_a_supplied_sub_channel() -> None:
    from app.application_source import normalise_detail

    assert normalise_detail("LinkedIn") == "linkedin"
    assert normalise_detail("<script>") is None
    assert normalise_detail(None) is None
    assert normalise_detail("x" * 500) is None


# ===========================================================================
# The write path
# ===========================================================================
def test_only_one_place_creates_an_enrolment_and_it_writes_the_source() -> None:
    from app.workflow_runner import enrol_applicant

    src = inspect.getsource(enrol_applicant)
    assert "source, source_detail" in src.replace("\n", " ") or "source," in src
    assert '"src": source if source in SOURCES else UNTRACKED' in src
    assert '"srcd": normalise_detail(source_detail)' in src


def test_a_caller_that_says_nothing_gets_unknown_not_a_guess() -> None:
    """Defaulting to the most likely channel would put invented attribution in
    a funnel chart somebody makes a decision from."""
    from app.workflow_runner import enrol_applicant

    assert inspect.signature(enrol_applicant).parameters["source"].default == "unknown"


# ===========================================================================
# C1-2 ("sources are retained throughout the lifecycle"): a grep shows
# nothing UPDATES enrolments.source once written — this asserts nothing else
# is even ABLE to WRITE it in the first place. Same technique as
# ``test_ph5_w1_checkins.py::test_delete_from_hire_checkins_has_exactly_two_callers``:
# a text/AST scan of the whole ``services/`` tree, not just this one file, so
# a second writer added anywhere else (an HR-facing route included) turns
# this red.
# ===========================================================================
def _sql_string_literals(path: Path) -> list[str]:
    """Every string literal in ``path`` that is not a docstring — adjacent
    literals (``"a" "b"``) are already ONE ``ast.Constant`` by the time the
    parser sees them, so a statement built across several lines is not split
    across several list entries."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    literals: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            literals.append(node.value)
    return literals


def _writes_enrolments_source(literal: str) -> bool:
    """True if ``literal`` is a write statement against ``enrolments`` that
    touches the ``source`` column specifically — never ``source_detail``
    (``\\bsource\\b`` does not match inside it: there is no boundary between
    ``e`` and the following ``_``)."""
    low = literal.lower()
    is_write = "insert into enrolments" in low or "update enrolments" in low
    return is_write and re.search(r"\bsource\b", low) is not None


def test_only_enrol_applicant_writes_enrolments_source() -> None:
    """Scans every ``.py`` file under ``services/`` (production code only —
    tests legitimately seed ``enrolments.source`` directly) for a write
    statement against ``enrolments`` that touches ``source``. The one
    permitted writer is ``app.workflow_runner.enrol_applicant`` — the only
    place an enrolment is ever created (its own docstring says so) — via a
    single INSERT. A bare grep for the column name would also flag every
    SELECT that reads it (``app.metrics.compute``, ``hr_requisitions.py``,
    the copilot); this only flags a WRITE."""
    hits: list[str] = []
    for path in (_REPO_ROOT / "services").rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        for literal in _sql_string_literals(path):
            if _writes_enrolments_source(literal):
                hits.append(str(path.relative_to(_REPO_ROOT)).replace("\\", "/"))
                break
    assert hits == ["services/data_gateway/app/workflow_runner.py"], hits


def test_hr_created_applicants_are_internal_not_direct() -> None:
    """PH5-C1: HR may now say which channel a candidate came through, so
    neither write path hardcodes 'internal' any more — but 'internal' is
    still what either one falls back to when HR does not say."""
    app = Path(__file__).resolve().parents[2] / "app"
    hr_applicants = (app / "routers" / "hr_applicants.py").read_text(encoding="utf-8")
    bulk_ingest = (app / "bulk_ingest.py").read_text(encoding="utf-8")
    # Single add: the form field is validated (default 'internal') before
    # ever reaching _file_under/enrol_applicant.
    assert "validate_hr_source(source)" in hr_applicants
    assert "source=validated_source" in hr_applicants
    # _file_under's own default, for its one caller that never got a form
    # value at all (_ingest_resume, exercised only by tests today).
    assert "source: str = INTERNAL" in hr_applicants
    # Bulk ingest: the batch's own channel, falling back to 'internal'.
    assert "source=it[\"source\"] or INTERNAL" in bulk_ingest


def test_the_public_apply_endpoint_normalises_before_it_writes() -> None:
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert "normalise_source(src, default=DIRECT)" in src
    assert "source=source" in src
    # Normalised before anything is stored, not after.
    assert src.index("normalise_source") < src.index("enrol_applicant")


def test_a_bad_src_does_not_refuse_the_application() -> None:
    """A malformed campaign tag is the recruiter's mistake, never the
    candidate's. FastAPI must not validate it into a 422."""
    from app.routers.public_apply import submit_application

    param = inspect.signature(submit_application).parameters["src"]
    assert param.default is not inspect.Parameter.empty
    # Bounded, but not pattern-constrained: the normaliser decides.
    assert "pattern" not in str(param.annotation)


# ===========================================================================
# Exposure
# ===========================================================================
def test_the_candidate_facing_posting_echoes_the_channel_but_stores_nothing() -> None:
    """A page view is not an application."""
    from app.routers.public_apply import get_posting

    src = inspect.getsource(get_posting)
    assert "normalise_source" in src
    assert "INSERT" not in src.upper()


def test_source_reaches_the_hr_view() -> None:
    from app.routers.hr_requisitions import EnrolmentOut

    assert "source" in EnrolmentOut.model_fields
    assert "source_detail" in EnrolmentOut.model_fields


def test_source_is_not_exposed_on_the_candidates_own_application_view() -> None:
    """The company's attribution of a person is the company's, not theirs.

    Matches the ACQUISITION-CHANNEL CONCEPT (PH3-B1's ``enrolments.source`` /
    ``source_detail`` pair, and the governed vocabulary in
    ``app/application_source.py`` that fills it), not the bare English word
    ``source``. The old ``r"\\bsource\\b\\s*[:=]"`` pattern matched the WORD,
    and this file has a second, legitimately-spelled-the-same field:
    ``rediscovery.record_opt_in``'s ``source`` parameter — where a
    REDISCOVERY OPT-IN came from (``rediscovery.OPT_IN_SOURCES`` =
    ``{"my_applications", "public_apply_form"}``), a candidate-own-account
    concept this router is right to use. PH5-E3 could not tell the two apart
    either and paid for it: ``rediscovery.opt_in_from_my_applications`` exists
    ONLY as a same-named wrapper so this file need not spell ``source=``
    itself and trip the old check — a contortion of production code around a
    test that was matching the wrong thing.

    Three checks, each aimed at the concept rather than the word:

      1. ``source_detail`` — verified (2026-09) to mean nothing else anywhere
         in this codebase: every reader/writer is PH3-B1 acquisition-channel
         code (``app/models.py``'s ``Enrolment``, ``app/application_source.py``,
         ``app/workflow_runner.py``, ``app/application_drafts.py``, and the
         ``hr_requisitions.py`` / ``hr_applicants.py`` / ``public_apply.py``
         routers). This candidate-facing router needs it for nothing.
      2. ``application_source`` — the governed-vocabulary module itself
         (``SOURCES``, ``normalise_source``, ``validate_hr_source``, ...).
         A candidate's own view has no legitimate reason to import or
         reference it.
      3. ``.source`` / ``["source"]`` / ``['source']`` — an ATTRIBUTE or
         SUBSCRIPT read, the shape the real leak takes in
         ``hr_requisitions.py`` (``source=r["source"]``): you cannot hand a
         company's attribution data to a response without reading it off a
         row first. Deliberately NOT the bare keyword ``source=``, which is
         also how ``rediscovery.record_opt_in(..., source="my_applications")``
         would read if ever inlined here — a literal opt-in-origin constant,
         never a column read off an enrolment/application_drafts row.

    Each of these is confirmed absent from the file's current, correct
    content (so none is a latent false positive), and the whole test is
    mutation-checked in the sibling test immediately below: injecting the
    real hr_requisitions.py leak shape into this router turns it red.
    """
    app = Path(__file__).resolve().parents[2] / "app"
    src = (app / "routers" / "candidate_applications.py").read_text(encoding="utf-8")
    assert "source_detail" not in src
    assert "application_source" not in src
    assert not re.search(r"\.source\b", src)
    assert not re.search(r"\[[\"']source[\"']\]", src)
