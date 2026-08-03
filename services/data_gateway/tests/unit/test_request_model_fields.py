"""Every ``body.<attr>`` a router reads must be a declared field on its model.

Written after a live 500: ``hr_coding.generate_coding_questions`` passed
``body.job_title`` and ``body.experience_level`` to the generator, but
``GenerateCodingQuestionsIn`` never declared either. The class docstring says it
"mirrors the MCQ generate flow" and the *code* was copied from
``GenerateQuestionsIn`` — the two field declarations were not. Pydantic raises
AttributeError for an undeclared attribute, so every call to that endpoint was a
guaranteed 500. It had no test, so nothing caught it.

This test is deliberately GENERIC rather than a single assertion about that one
model. The defect is a copy-paste class, not a one-off, and the same mistake in
any other router would be just as invisible: a request model is only exercised
end-to-end when something actually posts to the endpoint.

Mechanism: parse each router with ``ast`` (no imports, no DB, no network), find
handler parameters annotated with a BaseModel subclass defined in that module,
then assert every attribute read off such a parameter is a declared field.
Static analysis is the point — it covers endpoints that have no test at all,
which is exactly where this bug lived.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest
from pydantic import BaseModel

ROUTERS_DIR = Path(__file__).resolve().parents[2] / "app" / "routers"

# Attributes that exist on every BaseModel and are never declared fields.
_PYDANTIC_API = {
    "dict",
    "json",
    "copy",
    "schema",
    "construct",
    "validate",
}


def _router_module_names() -> list[str]:
    return sorted(
        name for _, name, ispkg in pkgutil.iter_modules([str(ROUTERS_DIR)]) if not ispkg
    )


def _annotation_name(node: ast.expr | None) -> str | None:
    """Bare class name from an annotation, unwrapping Optional/Annotated/etc."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _body_param_models(
    func: ast.FunctionDef | ast.AsyncFunctionDef, module: object
) -> dict[str, type[BaseModel]]:
    """Map parameter name -> request model, for params annotated with a
    BaseModel subclass that lives in this module."""
    found: dict[str, type[BaseModel]] = {}
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        name = _annotation_name(arg.annotation)
        if not name:
            continue
        candidate = getattr(module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            found[arg.arg] = candidate
    return found


@pytest.mark.parametrize("module_name", _router_module_names())
def test_router_reads_only_declared_request_fields(module_name: str) -> None:
    module = importlib.import_module(f"app.routers.{module_name}")
    source = (ROUTERS_DIR / f"{module_name}.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []

    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        param_models = _body_param_models(func, module)
        if not param_models:
            continue

        # Only attribute READS (ast.Load). A write would be a different bug and
        # pydantic would reject it at assignment time anyway.
        for node in ast.walk(func):
            if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            model = param_models.get(node.value.id)
            if model is None:
                continue
            attr = node.attr
            if attr.startswith(("model_", "_")) or attr in _PYDANTIC_API:
                continue
            if attr in model.model_fields:
                continue
            # A property or method on the model itself is legitimate.
            if hasattr(model, attr):
                continue
            violations.append(
                f"{module_name}.{func.name}:{node.lineno} reads "
                f"{node.value.id}.{attr}, but {model.__name__} declares no such "
                f"field (fields: {sorted(model.model_fields)})"
            )

    assert not violations, "Undeclared request-model attribute read:\n" + "\n".join(violations)


def test_coding_generate_model_carries_role_context() -> None:
    """The specific regression: the coding generator's role context.

    ``generate_coding_questions`` forwards both to
    ``generate_coding_questions_remote``, whose signature defaults them to
    ``""`` / ``"mid"``. Keep the model's defaults identical so omitting them
    client-side behaves the same as not having them at all.
    """
    from app.routers.hr_coding import GenerateCodingQuestionsIn
    from app.routers.hr_exams import GenerateQuestionsIn

    for field in ("job_title", "experience_level"):
        assert field in GenerateCodingQuestionsIn.model_fields, (
            f"GenerateCodingQuestionsIn lost {field} — the endpoint 500s without it"
        )
        assert (
            GenerateCodingQuestionsIn.model_fields[field].default
            == GenerateQuestionsIn.model_fields[field].default
        ), f"{field} default drifted from the MCQ flow it mirrors"

    # Omitting them must be valid — the frontend's GenerateCodingParams does.
    model = GenerateCodingQuestionsIn(topic="binary search", allowed_languages=["python"])
    assert model.job_title == ""
    assert model.experience_level == "mid"
