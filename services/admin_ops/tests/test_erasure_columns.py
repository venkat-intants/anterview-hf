"""DPDP: erasure must reach every personal column, not only every table.

test_erasure_inventory holds the TABLE list against the schema. This is the
same guard one level down, and it exists because the same defect happened
there: migration d5f7b9c1e3a6 added a candidate's phone, employer, profile
links and the name read from their CV to ``applicants``; later work added
location, employment status and goals to ``users``. The anonymising UPDATEs
were written before those columns existed and nobody extended them, so an
erased candidate's phone number and LinkedIn simply stayed.

So: every column the ORM declares on ``applicants`` and ``users`` must either
be written by the erasure executor's UPDATE for that table, or be listed below
as deliberately kept, with the reason. A new column turns this red until
someone decides which.

Columns are discovered from data_gateway's models.py by ``ast`` (see
test_erasure_inventory for why not import); the UPDATEs are captured by running
the real executor step against a stub session.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.erasure_executor import _execute_one_erasure
from app.models import ErasureRequest

_MODELS_PY = Path(__file__).resolve().parents[3] / "services" / "data_gateway" / "app" / "models.py"

# Kept on purpose. Each is structural, not about the person, or is the
# company's assessment record — the last group is what the pending
# security-auditor review of erasure exclusions covers.
_KEPT: dict[str, dict[str, str]] = {
    "applicants": {
        "id": "row identity",
        "company_id": "tenant",
        "created_by_user_id": "the HR user who uploaded them, not the candidate",
        "status": "pipeline state",
        "pending_enrichment": "processing flag",
        "upload_batch_id": "processing batch",
        "full_name_source": "which kind of source the (redacted) name came from",
        "created_at": "timestamps",
        "updated_at": "timestamps",
        "deleted_at": "timestamps",
        "target_job_title": "the company's opening, not the person",
        "target_level": "the company's opening, not the person",
        "target_jd_text": "the company's job description",
        "ats_overall": "assessment record (kept by design; pending security review)",
        "ats_breakdown": "assessment record (kept by design; pending security review)",
        "ats_strengths": "assessment record (kept by design; pending security review)",
        "ats_concerns": "assessment record (kept by design; pending security review)",
        "ats_recommendation": "assessment record (kept by design; pending security review)",
        "ats_summary": "assessment record (kept by design; pending security review)",
    },
    "users": {
        "id": "row identity; erasure_requests points at it",
        "preferred_language": "a UI setting, not identifying",
        "is_active": "account state",
        "email_verified_at": "account state",
        "notify_login_email": "a UI setting",
        "onboarding_completed_at": "account state",
        "onboarding_skipped_at": "account state",
        "target_level": "entry / mid / senior — not identifying",
        "company_id": "tenant",
        "must_change_password": "account state",
        "created_at": "timestamps",
        "updated_at": "timestamps",
        "deleted_at": "timestamps",
    },
}

_CLASS_FOR = {"applicants": "Applicant", "users": "User"}


def _columns(class_name: str) -> set[str]:
    """Attributes of ``class_name`` assigned from mapped_column(...)."""
    tree = ast.parse(_MODELS_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            cols = set()
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", None) == "mapped_column"
                ):
                    cols.add(stmt.target.id)
            return cols
    raise AssertionError(f"class {class_name} not found in {_MODELS_PY}")


async def _updates() -> dict[str, str]:
    """The UPDATE statement the executor issues for each table."""
    seen: list[str] = []

    async def _execute(stmt: Any, *_: Any, **__: Any) -> MagicMock:
        seen.append(str(stmt))
        result = MagicMock()
        result.rowcount = 1
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.add = MagicMock()
    db.execute = _execute
    request = ErasureRequest(
        request_id=uuid.uuid4(), user_id=uuid.uuid4(), requested_by=uuid.uuid4(),
        reason="test", status="pending",
        scheduled_for=datetime.now(tz=UTC) - timedelta(days=31),
        completed_at=None, artifacts=None,
    )
    await _execute_one_erasure(db=db, request=request, system_actor_id=uuid.uuid4())
    out = {}
    for table in _CLASS_FOR:
        stmt = next((s for s in seen if s.strip().startswith(f"UPDATE {table}")), None)
        assert stmt is not None, f"no UPDATE {table} was issued"
        out[table] = stmt
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("table", sorted(_CLASS_FOR))
async def test_every_personal_column_is_erased_or_kept_on_purpose(table: str) -> None:
    update = (await _updates())[table]
    missing = sorted(
        c for c in _columns(_CLASS_FOR[table])
        if c not in _KEPT[table] and f"{c} =" not in update
    )
    assert not missing, (
        f"{table} columns neither erased nor listed as kept: {missing}. Add each to the "
        f"executor's UPDATE {table}, or to _KEPT here with the reason it is not personal."
    )


@pytest.mark.parametrize("table", sorted(_CLASS_FOR))
def test_the_kept_list_names_only_real_columns(table: str) -> None:
    """A renamed column must not leave a stale 'kept' entry that looks like a decision."""
    stale = sorted(set(_KEPT[table]) - _columns(_CLASS_FOR[table]))
    assert not stale, f"_KEPT[{table!r}] names columns that no longer exist: {stale}"
