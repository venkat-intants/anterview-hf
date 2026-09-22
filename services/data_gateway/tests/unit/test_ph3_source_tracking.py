"""Where an application came from — PH3-B1.

The value lands in a PH5-C1 ``GROUP BY``, and it arrives on a URL anyone can
construct. Most of what matters here is therefore what the normaliser refuses to
put in the column, and what it refuses to throw away.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0001_c3e5a7b9d1f4_ph3_b1_application_source.py"
)


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
    """The company's attribution of a person is the company's, not theirs."""
    app = Path(__file__).resolve().parents[2] / "app"
    src = (app / "routers" / "candidate_applications.py").read_text(encoding="utf-8")
    assert not re.search(r"\bsource\b\s*[:=]", src)
