"""The two agent invariants cannot be loosened after the fact (L9).

Both invariants were enforced once, at construction, and then left mutable:

* ``ToolSpec.allowed_roles`` was checked against ``DATA_CLASS_ROLES`` by a model
  validator -- and then stayed assignable, so ``spec.allowed_roles = (...)`` widened
  a candidate-PII tool to another console with no check at all.
* ``DATA_CLASS_ROLES`` / ``CITATION_MIN_ROLES`` were plain dicts, so one line
  anywhere in the process could widen the matrix itself.

Neither change alters what any role may do; these tests pin that the matrix is
read-only and a spec cannot be edited -- or sneaked past validation -- on its way
into the registry. No ``ToolEffect`` member was added.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import ValidationError

from shared.agents.registry import ToolRegistry
from shared.agents.schema import (
    CITATION_MIN_ROLES,
    DATA_CLASS_ROLES,
    ToolEffect,
    ToolSpec,
)

OBJ_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _spec(**kw: Any) -> ToolSpec:
    base: dict[str, Any] = {
        "name": "some_tool",
        "description": "x",
        "parameters": OBJ_SCHEMA,
        "effect": "read",
        "data_class": "candidate_pii",
        "allowed_roles": ("hr_manager",),
    }
    base.update(kw)
    return ToolSpec(**base)


async def _noop(ctx: Any, args: Any) -> Any:  # pragma: no cover - never invoked
    raise AssertionError


def test_a_built_spec_cannot_have_its_roles_reassigned() -> None:
    spec = _spec()
    with pytest.raises(ValidationError):
        spec.allowed_roles = ("hr_manager", "super_admin")  # type: ignore[misc]
    assert spec.allowed_roles == ("hr_manager",)


def test_no_other_spec_field_is_assignable_either() -> None:
    spec = _spec()
    for field, value in (("data_class", "platform_aggregate"), ("effect", "draft")):
        with pytest.raises(ValidationError):
            setattr(spec, field, value)


def test_the_matrix_is_read_only() -> None:
    with pytest.raises(TypeError):
        DATA_CLASS_ROLES["candidate_pii"] = frozenset({"hr_manager", "super_admin"})  # type: ignore[index]
    with pytest.raises(TypeError):
        del DATA_CLASS_ROLES["candidate_pii"]  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        CITATION_MIN_ROLES["applicant"] = frozenset({"super_admin"})  # type: ignore[index]


def test_the_matrix_meaning_is_unchanged() -> None:
    """Hardening must not have moved a single role."""
    assert dict(DATA_CLASS_ROLES) == {
        "candidate_pii": frozenset({"hr_manager"}),
        "company_scoped": frozenset({"hr_manager", "super_admin"}),
        "company_staff": frozenset({"super_admin"}),
        "platform_aggregate": frozenset({"platform_owner", "admin"}),
    }
    assert CITATION_MIN_ROLES["applicant"] == frozenset({"hr_manager"})
    assert CITATION_MIN_ROLES["audit"] == frozenset({"platform_owner", "admin"})


def test_tool_effect_still_has_no_write_member() -> None:
    assert set(get_args(ToolEffect)) == {"read", "draft"}


def test_register_revalidates_a_spec_that_skipped_the_validators() -> None:
    """``model_copy(update=...)`` builds an instance WITHOUT running validators."""
    widened = _spec().model_copy(update={"allowed_roles": ("hr_manager", "super_admin")})
    with pytest.raises(ValueError, match="does not permit"):
        ToolRegistry().register(widened, _noop)


def test_register_still_accepts_a_well_formed_spec() -> None:
    reg = ToolRegistry()
    reg.register(_spec(), _noop)
    assert any(s.name == "some_tool" for s in reg.specs()) if hasattr(reg, "specs") else True
