"""Whitespace a paste added must never reach a connection string or a header.

Three outages, one root cause, each invisible in the settings UI because the
field keeps whatever the clipboard carried:

* ``DATABASE_URL`` with a trailing newline — ``InvalidCatalogNameError: database
  "neondb<LF>" does not exist``, every route 503, legible only as a line break
  inside the quotes forty lines into a traceback.
* ``GROQ_API_KEY`` split mid-value by a wrapped paste — ``Illegal header
  value``, which also printed the key into a user-facing error.
* ``GROQ_MODEL`` with a trailing newline — ``The model
  `openai/gpt-oss-120b<LF>` does not exist``.

The third is why this is tested against a MODEL-WIDE validator and not a list of
field names: the hand-written list that preceded it did not have GROQ_MODEL on
it, so the same failure came back a third time. An allowlist of the settings
someone remembered is a guarantee of another outage, just later.
"""

# strip_pasted_settings — the model-wide rule that replaced a hand-written list
# of variable names. That list covered GROQ_API_KEY and missed GROQ_MODEL, so
# the same outage returned a third time; these tests pin all three incidents
# together so a future narrowing is caught here.
# ---------------------------------------------------------------------------
from shared.security import strip_pasted_settings

# A Neon-SHAPED url, deliberately not a real one: no live endpoint hostname or
# role name belongs in a tracked test. The only property under test is that the
# last path segment is the database NAME, which is what a trailing newline
# corrupts.
NEON = (
    "postgresql+asyncpg://test-only-owner:test-only-not-a-real-secret@"
    "ep-example-endpoint-pooler.region.aws.neon.test/neondb"
)


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
