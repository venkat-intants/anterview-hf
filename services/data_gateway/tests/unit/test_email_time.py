"""Candidate emails state times in IST, and say so (audit X6).

The interview invite and the exam-assignment emails printed UTC ("10:00 UTC"
for a 15:30 slot) while the reminder for the same event printed IST. These pin
the shared formatter, the one call site that is cheap to drive end to end (the
invite and reschedule email), and a source guard over every module that sends
email, so a new ``strftime(... UTC)`` in a template context fails here.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app import reminders
from app.email_time import IST, format_email_date, format_email_datetime
from app.routers import hr_interviews

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"


def test_a_utc_instant_is_written_in_ist() -> None:
    assert format_email_datetime(datetime(2026, 10, 21, 10, 0, tzinfo=UTC)) == (
        "21 Oct 2026, 03:30 PM IST"
    )


def test_an_instant_in_another_zone_is_converted_not_relabelled() -> None:
    new_york = timezone(timedelta(hours=-4))
    assert format_email_datetime(datetime(2026, 10, 21, 6, 0, tzinfo=new_york)) == (
        "21 Oct 2026, 03:30 PM IST"
    )


def test_a_naive_value_is_read_as_utc_never_as_host_local_time() -> None:
    assert format_email_datetime(datetime(2026, 10, 21, 10, 0)) == "21 Oct 2026, 03:30 PM IST"


def test_the_date_is_the_indian_calendar_day() -> None:
    # 20:00 UTC on the 21st is 01:30 on the 22nd in India.
    late = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    assert format_email_date(late) == "22 Oct 2026"
    assert late.astimezone(IST).tzname() == "IST"


def test_reminders_use_the_same_format() -> None:
    when = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    assert reminders._fmt(when) == format_email_datetime(when)
    assert reminders._fmt(None) is None


@pytest.mark.parametrize("rescheduled", [False, True])
async def test_the_interview_invite_email_and_notice_say_ist(
    monkeypatch: pytest.MonkeyPatch, rescheduled: bool
) -> None:
    emails: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []

    async def _enqueue(_db: object, **kwargs: Any) -> None:
        emails.append(kwargs)

    async def _notify(_db: object, **kwargs: Any) -> None:
        notices.append(kwargs)

    monkeypatch.setattr(hr_interviews, "enqueue_email", _enqueue)
    monkeypatch.setattr(hr_interviews, "create_notification", _notify)

    applicant = SimpleNamespace(user_id=uuid.uuid4(), email="c@example.com", full_name="Asha")
    await hr_interviews._tell_candidate_about_invite(
        object(),  # type: ignore[arg-type]
        applicant=applicant,  # type: ignore[arg-type]
        job_title="Electrician",
        magic_link=None if rescheduled else "https://x/interview#t",
        scheduled_at=datetime(2026, 10, 21, 10, 0, tzinfo=UTC),
        language="en",
        company_id=uuid.uuid4(),
        rescheduled=rescheduled,
    )

    assert emails[0]["ctx"]["when"] == "21 Oct 2026, 03:30 PM IST"
    assert "03:30 PM IST" in notices[0]["body"]
    assert "UTC" not in notices[0]["body"]


# A human date written with strftime/format codes ("%d %b", as in "21 Oct").
# Machine stamps ("%Y%m%d%H%M%S" in a dedupe key) do not match. The guard is over
# every module that sends email, because that is where a template context is
# filled; app.email_time is the one place allowed to hold the format.
_DATE_FORMAT = re.compile(r"%d %b")


def _email_senders() -> list[pathlib.Path]:
    return sorted(
        p
        for p in APP_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
        and p.name != "email_time.py"
        and "enqueue_email" in p.read_text(encoding="utf-8")
    )


def test_the_guard_sees_the_email_senders() -> None:
    names = {p.name for p in _email_senders()}
    # Guard the guard: if the scan stops finding these, it passes vacuously.
    assert {"hr_interviews.py", "hr_exams.py", "offers.py", "reminders.py"} <= names


def test_no_email_sender_formats_a_date_itself() -> None:
    offenders = [
        f"{p.relative_to(APP_DIR)}:{n}"
        for p in _email_senders()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _DATE_FORMAT.search(line)
    ]
    assert not offenders, (
        "format dates for email copy with app.email_time "
        f"(format_email_datetime / format_email_date): {offenders}"
    )
