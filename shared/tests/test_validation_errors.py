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
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

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


_MODEL_SECRET = "Tr0ub4dor-and-3"


class _Reset(BaseModel):
    """A model-level validator: on failure ``input`` is the whole body."""

    username: str
    password: str
    confirm: str

    @model_validator(mode="after")
    def _matches(self) -> _Reset:
        if self.password != self.confirm:
            # Quotes BOTH submitted secrets back, which no validator should do
            # and a future one might.
            raise ValueError(
                f"{self.password} does not match {self.confirm} for {self.username}"
            )
        return self


def _client() -> TestClient:
    app = FastAPI()
    install_validation_error_handler(app)

    @app.post("/reset")
    def reset(body: _Reset) -> dict[str, bool]:
        return {"ok": True}

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


def test_a_model_validator_never_echoes_a_secret_from_the_body() -> None:
    resp = _client().post(
        "/reset",
        json={"username": "asha.k", "password": _MODEL_SECRET, "confirm": "9142"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body"]
    text = resp.text
    assert _MODEL_SECRET not in text
    # Four characters under a secret-looking key, and a four-digit value.
    assert "9142" not in text
    assert "asha.k" not in text
    assert resp.json()["message"] == (
        "This value does not match this value for this value."
    )


@pytest.mark.parametrize(
    ("loc", "value"),
    [
        (("body", "pin"), "42"),
        (("body", "otp"), "907"),
        (("body", "loginPin"), "7"),
        (("body", "api_key"), "k9"),
        (("body", "refresh_token"), "ab1"),
    ],
)
def test_a_short_secret_is_masked_at_any_length(loc: tuple[str, ...], value: str) -> None:
    raw = [{"type": "value_error", "loc": loc, "msg": f"Value error, {value} is wrong",
            "input": value, "ctx": {"error": f"{value} is wrong"}}]
    clean = sanitise_validation_errors(raw)[0]
    assert clean["msg"] == "Value error, this value is wrong"
    assert clean["ctx"]["error"] == "this value is wrong"


def test_a_short_secret_is_masked_only_where_it_stands_alone() -> None:
    raw = [{"type": "value_error", "loc": ("body", "pin"),
            "msg": "PIN 12 was used in 2012", "input": "12"}]
    assert sanitise_validation_errors(raw)[0]["msg"] == "PIN this value was used in 2012"


def test_a_three_digit_value_is_masked_under_any_field_name() -> None:
    raw = [{"type": "value_error", "loc": ("body", "code"),
            "msg": "Value error, code 381 has expired", "input": "381"}]
    assert sanitise_validation_errors(raw)[0]["msg"] == (
        "Value error, code this value has expired"
    )


def test_small_counts_and_non_secret_short_words_are_kept() -> None:
    raw = [{"type": "value_error", "loc": ("body", "seats"),
            "msg": "Value error, 2 seats and no more", "input": 2},
           {"type": "value_error", "loc": ("body", "answer"),
            "msg": "Value error, and is not an answer", "input": "and"}]
    clean = sanitise_validation_errors(raw)
    assert clean[0]["msg"] == "Value error, 2 seats and no more"
    assert clean[1]["msg"] == "Value error, and is not an answer"


def test_a_field_merely_containing_a_secret_word_is_not_a_secret() -> None:
    raw = [{"type": "value_error", "loc": ("body", "shipping"),
            "msg": "Value error, no is not allowed", "input": "no"}]
    assert sanitise_validation_errors(raw)[0]["msg"] == "Value error, no is not allowed"


def test_an_email_used_as_a_dict_key_is_masked_in_loc() -> None:
    raw = [{"type": "int_parsing", "loc": ("body", "scores", "ravi@example.com"),
            "msg": "Input should be a valid integer", "input": "x"}]
    assert sanitise_validation_errors(raw)[0]["loc"] == ["body", "scores", "[redacted]"]


def test_a_hostile_nested_body_is_walked_within_bounds() -> None:
    deep: object = "leaf-secret-value"
    for _ in range(50):
        deep = {"password": deep}
    raw = [{"type": "value_error", "loc": ("body",), "msg": "bad", "input": deep}]
    # Bounded: returns, and a message without the value is untouched.
    assert sanitise_validation_errors(raw)[0]["msg"] == "bad"
