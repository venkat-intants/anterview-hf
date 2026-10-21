"""Behaviour pins for the shared request-validation handler (audit X1).

What the people on the other end depend on:

* a rejected form says what is wrong in a sentence (``message``) instead of the
  web client's "HTTP 422", and names the field the way the label does;
* ``detail`` is still FastAPI's array, so every caller and test that indexes
  ``detail[0]["loc"]`` keeps working;
* nothing the caller submitted comes back in the body: ``input`` is dropped and
  a validator that quoted the value back has it masked (a weak password on the
  register form used to be returned verbatim).
"""

from __future__ import annotations

from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, EmailStr, Field, field_validator

from shared.http_observability import (
    humanise_field,
    install_validation_error_handler,
    sanitise_validation_errors,
    validation_error_message,
)

_SECRET = "hunter2-correct-horse"


class _Signup(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)
    max_candidates: int = Field(ge=1, le=500)
    kind: Literal["exam", "interview"] = "exam"
    title: str = Field(default="x", min_length=1)

    @field_validator("password")
    @classmethod
    def _no_secret(cls, value: str) -> str:
        # An application validator that quotes the value back, which is exactly
        # the case the masking exists for.
        if value == _SECRET:
            raise ValueError(f"password {value} appears in a breach list")
        return value


def _client() -> TestClient:
    app = FastAPI()
    install_validation_error_handler(app)

    @app.post("/signup")
    def signup(body: _Signup) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/items")
    def items(limit: int = 10) -> dict[str, int]:
        return {"limit": limit}

    return TestClient(app)


def _valid() -> dict[str, object]:
    return {"email": "a@example.com", "password": "longenough", "max_candidates": 5}


def test_missing_field_gets_a_sentence_and_keeps_the_detail_array() -> None:
    body = _valid()
    del body["max_candidates"]
    resp = _client().post("/signup", json=body)

    assert resp.status_code == 422
    payload = resp.json()
    assert payload["message"] == "Max candidates is required."
    # The FastAPI shape survives for existing callers.
    assert isinstance(payload["detail"], list)
    assert payload["detail"][0]["loc"] == ["body", "max_candidates"]
    assert payload["detail"][0]["type"] == "missing"
    assert payload["detail"][0]["msg"] == "Field required"


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"max_candidates": 0}, "Max candidates must be 1 or more."),
        ({"max_candidates": 501}, "Max candidates must be 500 or less."),
        ({"max_candidates": "lots"}, "Max candidates must be a whole number."),
        ({"password": "short"}, "Password must be at least 8 characters."),
        ({"password": "x" * 65}, "Password must be at most 64 characters."),
        ({"title": ""}, "Title cannot be empty."),
        ({"kind": "chat"}, "Kind must be one of 'exam' or 'interview'."),
        ({"email": "not-an-address"}, "Email must be a valid email address."),
    ],
)
def test_common_errors_read_as_plain_sentences(patch: dict[str, object], expected: str) -> None:
    resp = _client().post("/signup", json={**_valid(), **patch})
    assert resp.status_code == 422
    assert resp.json()["message"] == expected


def test_the_submitted_value_never_comes_back() -> None:
    resp = _client().post(
        "/signup", json={**_valid(), "password": _SECRET, "email": "ravi@example.com"}
    )
    assert resp.status_code == 422
    text = resp.text
    assert _SECRET not in text
    assert "input" not in resp.json()["detail"][0]
    # The validator's own sentence still reads, with the value masked.
    assert resp.json()["message"] == "Password: password this value appears in a breach list."


def test_an_email_never_comes_back_even_when_valid_input_was_elsewhere() -> None:
    resp = _client().post("/signup", json={**_valid(), "email": "ravi.kumar@example"})
    assert resp.status_code == 422
    assert "ravi.kumar" not in resp.text


def test_query_parameters_are_named_too() -> None:
    resp = _client().get("/items", params={"limit": "many"})
    assert resp.status_code == 422
    assert resp.json()["message"] == "Limit must be a whole number."


def test_a_body_that_is_not_json_says_so_without_a_field() -> None:
    resp = _client().post(
        "/signup", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422
    assert resp.json()["message"] == (
        "The request could not be read. Refresh the page and try again."
    )


def test_a_valid_request_is_untouched() -> None:
    resp = _client().post("/signup", json=_valid())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (("body", "max_candidates"), "Max candidates"),
        (("body", "items", 2, "unitPrice"), "Unit price"),
        (("body", "jd_url"), "JD URL"),
        (("query", "candidate_id"), "Candidate ID"),
        (("body", "email", "str"), "Email"),
        (("body", "list"), "List"),
        (("body",), None),
        ((), None),
    ],
)
def test_humanise_field(loc: tuple[object, ...], expected: str | None) -> None:
    assert humanise_field(loc) == expected


def test_sanitise_drops_input_and_url_and_stringifies_ctx_errors() -> None:
    raw = [
        {
            "type": "value_error",
            "loc": ("body", "note"),
            "msg": "Value error, note 9876543210 is not allowed",
            "input": "9876543210",
            "ctx": {"error": ValueError("note 9876543210 is not allowed"), "limit": 3},
            "url": "https://errors.pydantic.dev/2/v/value_error",
        }
    ]
    clean = sanitise_validation_errors(raw)
    assert clean == [
        {
            "type": "value_error",
            "loc": ["body", "note"],
            "msg": "Value error, note this value is not allowed",
            "ctx": {"error": "note this value is not allowed", "limit": 3},
        }
    ]
    assert validation_error_message(clean) == "Note: note this value is not allowed."


def test_short_inputs_are_not_masked_out_of_ordinary_words() -> None:
    raw = [{"type": "x", "loc": ("body", "a"), "msg": "a value is needed", "input": "a"}]
    assert sanitise_validation_errors(raw)[0]["msg"] == "a value is needed"


def test_an_empty_error_list_still_gives_a_sentence() -> None:
    assert validation_error_message([]) == (
        "The request could not be read. Refresh the page and try again."
    )
