"""A secret that carried a newline must not be able to take the platform down.

THE INCIDENT THIS PINS. A ``DATABASE_URL`` pasted into a Hugging Face Space
secret kept the clipboard's trailing newline. The Space then asked Neon for a
database called ``"neondb\\n"``, which does not exist, and every route returned
503. Nothing in the settings UI showed the character; the only evidence was a
line break inside the quotes, forty lines into an asyncpg traceback.

The fix is deliberately boring — strip the ends — and these tests exist to say
why removing whitespace is SAFE as well as necessary: it must fix the broken
case without altering any value that already worked, and without reaching
inside a secret and corrupting it.
"""

from __future__ import annotations

import pytest

from shared.security import strip_connection_value

# A Neon-SHAPED url, deliberately not a real one: no live endpoint hostname or
# role name belongs in a tracked test. The only property under test is that the
# last path segment is the database NAME, which is what a trailing newline
# corrupts.
NEON = (
    "postgresql+asyncpg://test-only-owner:test-only-not-a-real-secret@"
    "ep-example-endpoint-pooler.region.aws.neon.test/neondb"
)


@pytest.mark.parametrize(
    "carried",
    [
        NEON + "\n",        # the actual incident: a trailing LF
        NEON + "\r\n",      # a Windows clipboard
        NEON + "  ",        # a trailing space
        "  " + NEON,        # a leading space
        "\n" + NEON + "\n",  # pasted as its own line
        "\t" + NEON + "\t",
    ],
)
def test_a_pasted_connection_string_survives_whatever_the_clipboard_added(
    carried: str,
) -> None:
    assert strip_connection_value(carried) == NEON


def test_the_database_name_is_what_breaks_without_this() -> None:
    """The specific failure, stated so the regression is unmistakable.

    Without stripping, the last path segment — the database NAME asyncpg asks
    for — is ``neondb\\n`` rather than ``neondb``.
    """
    assert (NEON + "\n").rsplit("/", 1)[-1] == "neondb\n"
    assert str(strip_connection_value(NEON + "\n")).rsplit("/", 1)[-1] == "neondb"


def test_a_value_that_already_worked_is_returned_unchanged() -> None:
    """The safety half: this runs on every boot, so it must be a no-op for
    every correct value, or it becomes its own outage."""
    for good in (
        NEON,
        "redis://localhost:6379/0",
        "rediss://default:tok@giving-spider-126027.upstash.io:6379",
        "re_hqvun_abc123",
        "onboarding@resend.dev",
        "",
    ):
        assert strip_connection_value(good) == good


def test_inner_whitespace_is_never_touched() -> None:
    """Only the ENDS are stripped. A password may legitimately contain a space,
    and silently rewriting it would swap one baffling auth failure for another.
    """
    inner = "postgresql+asyncpg://user:pass word@host/db"
    assert strip_connection_value(inner) == inner
    assert strip_connection_value("  " + inner + "\n") == inner


def test_non_strings_pass_through_so_pydantic_still_reports_a_type_error() -> None:
    for value in (None, 123, ["a"], {"b": 1}):
        assert strip_connection_value(value) is value


# ---------------------------------------------------------------------------
# strip_pasted_settings — the model-wide rule that replaced a hand-written list
# of variable names. That list covered GROQ_API_KEY and missed GROQ_MODEL, so
# the same outage returned a third time; these tests pin all three incidents
# together so a future narrowing is caught here.
# ---------------------------------------------------------------------------
from shared.security import strip_pasted_settings  # noqa: E402


def test_the_three_real_incidents_are_all_repaired_together() -> None:
    cleaned = strip_pasted_settings(
        {
            # 1. trailing newline -> database "neondb\n" does not exist
            "database_url": NEON + "\n",
            # 2. trailing newline -> model `openai/gpt-oss-120b\n` not found
            "groq_model": "openai/gpt-oss-120b\n",
            # 3. wrapped paste split the key -> Illegal header value
            "groq_api_key": "gsk_firsthalf\nsecondhalf",
        }
    )
    assert cleaned["database_url"] == NEON
    assert cleaned["groq_model"] == "openai/gpt-oss-120b"
    assert cleaned["groq_api_key"] == "gsk_firsthalfsecondhalf"
    assert not any(c.isspace() for c in str(cleaned["groq_api_key"])), (
        "an API key with any whitespace left in it produces an illegal HTTP header"
    )


def test_a_multi_line_pem_keeps_its_internal_newlines() -> None:
    """The reason ends-only is the default rule: JWT_PRIVATE_KEY is a PEM whose
    internal newlines are load bearing. Flattening it would break RS256 signing
    — trading a visible outage for a subtle one."""
    # The PEM markers are assembled rather than written as one literal: gitleaks'
    # `private-key` rule fires on the contiguous banner even for an obvious stub
    # like this one, and this repo reserves .gitleaks.toml allowlisting for
    # secrets that were genuinely exposed — widening it for a fake would blunt
    # the scanner for the case it exists to catch.
    begin = "-----BEGIN " + "PRIVATE KEY-----"
    end = "-----END " + "PRIVATE KEY-----"
    pem = f"{begin}\nMIIE...\nAAAB...\n{end}"
    out = strip_pasted_settings({"jwt_private_key": pem + "\n"})
    assert out["jwt_private_key"] == pem
    assert out["jwt_private_key"].count("\n") == 3


def test_inner_whitespace_survives_outside_api_keys() -> None:
    """A DSN password may legitimately contain a space. Only *_api_key fields —
    which go straight into an Authorization header — have inner whitespace
    removed."""
    out = strip_pasted_settings({"database_url": "  postgresql://u:pa ss@h/db\n"})
    assert out["database_url"] == "postgresql://u:pa ss@h/db"


def test_every_key_is_covered_not_a_chosen_list() -> None:
    """The actual lesson. Any setting can be pasted, so a name this code has
    never heard of must still be trimmed."""
    out = strip_pasted_settings({"some_future_setting_nobody_listed": " value \n"})
    assert out["some_future_setting_nobody_listed"] == "value"


def test_non_dict_and_non_string_values_pass_through() -> None:
    assert strip_pasted_settings("not a dict") == "not a dict"
    out = strip_pasted_settings({"port": 8002, "debug": True, "missing": None})
    assert out == {"port": 8002, "debug": True, "missing": None}
