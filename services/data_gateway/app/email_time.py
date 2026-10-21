"""How a date or time is written in an email to a candidate (audit X6).

One event used to reach a candidate in three time zones: the invite and the
exam-assignment emails printed UTC ("10:00 UTC" for a 15:30 slot), the reminder
for the same event printed IST, and the web app printed browser time. A
candidate who read "10:00 UTC" and did not convert it missed the slot.

Every candidate on this platform is in India, so candidate email copy is written
in India Standard Time and always says so. IST has no daylight saving, so the
fixed +05:30 offset is exact for every date, and it needs no tz database on the
host (``zoneinfo`` on Windows needs the separate ``tzdata`` package).

Templates take pre-formatted strings (see ``email_templates``), so the
formatting happens here, at the call site that fills the template context.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

__all__ = ["IST", "format_email_date", "format_email_datetime"]

#: India Standard Time. Named so ``strftime("%Z")`` would also print "IST".
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _as_ist(value: datetime) -> datetime:
    # A naive datetime here came out of the database or a ``datetime.now(UTC)``
    # that lost its zone along the way; every timestamp column is UTC. Reading
    # it as host-local time (what ``astimezone`` does with a naive value) would
    # shift the email by the server's offset, so it is pinned to UTC instead.
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def format_email_datetime(value: datetime) -> str:
    """``21 Oct 2026, 03:30 PM IST``: a date and time for candidate email copy."""
    return _as_ist(value).strftime("%d %b %Y, %I:%M %p IST")


def format_email_date(value: datetime) -> str:
    """``21 Oct 2026``: the IST calendar date, for deadlines stated as a day.

    The date is taken in IST, not UTC: an offer that expires at 20:00 UTC
    expires on the NEXT day in India, and the email must name that day.
    """
    return _as_ist(value).strftime("%d %b %Y")
