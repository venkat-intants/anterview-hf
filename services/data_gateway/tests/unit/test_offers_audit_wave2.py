"""Offers audit, wave 2: the parts that need no database.

- A cleared field that cannot be cleared is refused in words (never a 500),
  and every field that can be cleared accepts null.
- A sent offer past its deadline counts as expired however the sweep is doing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.offers import OfferError, _past_expiry, clean_fields


@pytest.mark.parametrize(
    ("field", "words"),
    [
        ("valid_days", "1 to 60 days"),
        ("employment_type", "full time, part time"),
        ("pay_period", "annual, monthly or hourly"),
        ("currency", "three-letter"),
        ("job_title", "job title"),
    ],
)
def test_a_field_that_cannot_be_cleared_is_refused_in_words(field: str, words: str) -> None:
    with pytest.raises(OfferError, match=words) as err:
        clean_fields({field: None}, partial=True)
    assert err.value.status_code == 422


@pytest.mark.parametrize(
    "field",
    ["location", "bonus", "equity", "benefits", "terms", "start_date", "probation_months",
     "notice_period_days"],
)
def test_a_clearable_field_accepts_null(field: str) -> None:
    assert clean_fields({field: None}, partial=True) == {field: None}


def _offer(status: str, expires_in: timedelta | None) -> dict[str, object]:
    return {"status": status,
            "expires_at": None if expires_in is None else datetime.now(tz=UTC) + expires_in}


@pytest.mark.parametrize(
    ("status", "expires_in", "lapsed"),
    [
        ("sent", timedelta(seconds=-1), True),
        ("sent", timedelta(hours=1), False),
        ("sent", None, False),
        ("accepted", timedelta(days=-3), False),  # answered in time; the deadline no longer matters
        ("approved", None, False),
    ],
)
def test_only_a_sent_offer_past_its_deadline_counts_as_lapsed(
    status: str, expires_in: timedelta | None, lapsed: bool,
) -> None:
    assert _past_expiry(_offer(status, expires_in)) is lapsed
