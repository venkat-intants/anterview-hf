"""HR-L6: the applicant list's status filter matches every status it shows.

'interviewed' is derived by the application_progress view, never stored, so
filtering it on ``stored_status`` always came back empty.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.mark.parametrize(
    ("status", "column"),
    [
        ("interviewed", "status"),
        ("new", "stored_status"),
        ("shortlisted", "stored_status"),
        ("held", "stored_status"),
        ("hired", "stored_status"),
        ("rejected", "stored_status"),
    ],
)
def test_each_status_filters_on_the_column_that_holds_it(status: str, column: str) -> None:
    from app.routers.hr_applicants import _status_column

    assert _status_column(status) == column


def test_both_list_modes_use_the_same_column_choice() -> None:
    from app.routers import hr_applicants

    assert "_status_column(status_filter)" in inspect.getsource(hr_applicants._semantic_search)
    assert "_status_column(status_filter)" in inspect.getsource(hr_applicants.list_applicants)
    assert "status" in hr_applicants._PROGRESS.c
