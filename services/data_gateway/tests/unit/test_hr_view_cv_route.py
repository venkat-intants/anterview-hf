"""GET /hr/applicants/{id}/resume is an HR-manager route — pinned on the mounted app.

The integration tests call the handler directly, which skips its dependencies;
this proves the ROUTE carries get_hr_company (hr_manager only, plus the
bootstrap-password gate), so a refactor that drops it fails here. A company
super_admin deliberately has no route to a named candidate's CV.
"""

from __future__ import annotations

from typing import Any

PATH = "/hr/applicants/{applicant_id}/resume"


def _tree_calls(route: Any) -> set[Any]:
    out: set[Any] = set()
    stack = [getattr(route, "dependant", None)]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        out.add(getattr(node, "call", None))
        stack.extend(getattr(node, "dependencies", []))
    return out


def test_the_cv_route_is_hr_manager_only() -> None:
    from app.dependencies import get_hr_company
    from app.main import app

    found = [r for r in app.routes if getattr(r, "path", "") == PATH]
    assert len(found) == 1, f"route not found (vacuous pass?): {PATH}"
    assert getattr(found[0], "methods", set()) == {"GET"}
    assert get_hr_company in _tree_calls(found[0])
