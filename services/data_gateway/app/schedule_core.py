"""Pure scheduling arithmetic — PH4-A2. No database, no clock of its own.

Kept apart from the endpoints so the parts most easily got wrong — free time,
timezones, the calendar file — are tested exhaustively on plain values. The
database's exclusion constraints are what finally stop a double booking; this
is what finds a time that will not be refused.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

GRID_MINUTES = 15
MAX_SLOTS = 200
MIN_LEAD = timedelta(hours=2)
HORIZON = timedelta(days=21)

Interval = tuple[datetime, datetime]


class ScheduleError(ValueError):
    """A scheduling input that cannot be right, said in words."""


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------
_ZONES: frozenset[str] | None = None


def valid_timezone(name: str | None) -> str:
    """The IANA zone name, or ScheduleError. 'UTC' is accepted as itself."""
    global _ZONES
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > 64:
        raise ScheduleError("Choose a timezone.")
    if _ZONES is None:
        _ZONES = frozenset(available_timezones())
    if cleaned not in _ZONES:
        raise ScheduleError(f"{cleaned!r} is not a timezone this system knows.")
    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover — listed but unloadable
        raise ScheduleError(f"{cleaned!r} is not a timezone this system knows.") from exc
    return cleaned


def aware(value: datetime, *, field: str = "time") -> datetime:
    """Refuse a naive datetime: a wall time with no zone is a guess."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleError(f"The {field} needs a timezone offset.")
    return value.astimezone(UTC)


def local_label(instant: datetime, tz: str) -> str:
    """'Tue 22 Sep 2026, 10:30 IST' — how a person reads a time, in their zone."""
    local = instant.astimezone(ZoneInfo(tz))
    return local.strftime("%a %d %b %Y, %H:%M ") + (local.tzname() or tz)


# ---------------------------------------------------------------------------
# Free time
# ---------------------------------------------------------------------------
def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Union of half-open intervals, sorted. Touching intervals join."""
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def subtract(free: Sequence[Interval], busy: Iterable[Interval]) -> list[Interval]:
    """``free`` minus every ``busy`` interval."""
    result = list(merge(free))
    for b0, b1 in merge(busy):
        nxt: list[Interval] = []
        for f0, f1 in result:
            if b1 <= f0 or b0 >= f1:
                nxt.append((f0, f1))
                continue
            if f0 < b0:
                nxt.append((f0, b0))
            if b1 < f1:
                nxt.append((b1, f1))
        result = nxt
    return result


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> list[Interval]:
    """Times in both."""
    out: list[Interval] = []
    i = j = 0
    a, b = merge(a), merge(b)
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo < hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _ceil_grid(t: datetime) -> datetime:
    t = t.astimezone(UTC).replace(second=0, microsecond=0)
    rem = t.minute % GRID_MINUTES
    return t if rem == 0 else t + timedelta(minutes=GRID_MINUTES - rem)


@dataclass(frozen=True)
class SlotQuery:
    duration_minutes: int
    buffer_minutes: int
    now: datetime
    lead: timedelta = MIN_LEAD
    horizon: timedelta = HORIZON


def slots(
    q: SlotQuery,
    *,
    interviewer_free: Sequence[Sequence[Interval]],
    candidate_busy: Iterable[Interval],
) -> list[datetime]:
    """Start times, on a 15-minute grid, when EVERY interviewer is free and the
    candidate is not already in (or within a buffer of) another session.

    ``interviewer_free`` is each interviewer's availability minus their live
    bookings; ``candidate_busy`` is the candidate's other scheduled sessions,
    already widened by their buffer at the end. The candidate side is widened
    here by this session's buffer too, so a slot never lands inside the gap
    either side of another session.
    """
    if not interviewer_free:
        return []
    duration = timedelta(minutes=q.duration_minutes)
    buffer = timedelta(minutes=q.buffer_minutes)
    window: list[Interval] = [(q.now + q.lead, q.now + q.horizon)]
    common = window
    for free in interviewer_free:
        common = intersect(common, free)
    # A slot [s, s+d) plus its trailing buffer must not touch another session,
    # and no other session may sit within the buffer before it.
    busy = [(b0 - buffer, b1) for b0, b1 in candidate_busy]
    common = subtract(common, busy)
    out: list[datetime] = []
    for f0, f1 in common:
        s = _ceil_grid(f0)
        while s + duration <= f1:
            out.append(s)
            if len(out) >= MAX_SLOTS:
                return out
            s += timedelta(minutes=GRID_MINUTES)
    return out


def covered(start: datetime, end: datetime, windows: Iterable[Interval]) -> bool:
    """Whether [start, end) lies inside one merged availability window."""
    return any(w0 <= start and end <= w1 for w0, w1 in merge(windows))


# ---------------------------------------------------------------------------
# ICS (RFC 5545)
# ---------------------------------------------------------------------------
def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """Fold at 75 OCTETS, never inside a UTF-8 character (RFC 5545 §3.1)."""
    out: list[str] = []
    buf = b""
    limit = 75
    for ch in line:
        enc = ch.encode("utf-8")
        if len(buf) + len(enc) > limit:
            out.append(buf.decode("utf-8"))
            buf = b" "  # continuation lines start with one space
            limit = 75
        buf += enc
    out.append(buf.decode("utf-8"))
    return "\r\n".join(out)


def _utc(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    sequence: int
    starts_at: datetime
    ends_at: datetime
    summary: str
    description: str = ""
    location: str = ""
    cancelled: bool = False


def ics(events: Sequence[IcsEvent], *, now: datetime, product: str = "AntHire") -> str:
    """A VCALENDAR with one VEVENT per session. Times in UTC ('Z'), so every
    calendar shows them in its owner's zone without a VTIMEZONE block."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{_ics_escape(product)}//Interview schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL" if events and all(e.cancelled for e in events) else "METHOD:PUBLISH",
    ]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e.uid}",
            f"SEQUENCE:{max(0, e.sequence)}",
            f"DTSTAMP:{_utc(now)}",
            f"DTSTART:{_utc(e.starts_at)}",
            f"DTEND:{_utc(e.ends_at)}",
            f"SUMMARY:{_ics_escape(e.summary)}",
        ]
        if e.description:
            lines.append(f"DESCRIPTION:{_ics_escape(e.description)}")
        if e.location:
            lines.append(f"LOCATION:{_ics_escape(e.location)}")
        lines.append("STATUS:CANCELLED" if e.cancelled else "STATUS:CONFIRMED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
