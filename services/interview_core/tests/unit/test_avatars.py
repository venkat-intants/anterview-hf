"""Unit tests for the avatar catalog (app/avatars.py).

Covers:
  - AVATARS list is non-empty and every entry has required fields.
  - AVATARS_BY_ID is a consistent index of AVATARS.
  - valid_avatar_ids() returns the right set.
  - resolve_avatar(known_id) returns the correct avatar.
  - resolve_avatar(None) returns the default ("anna").
  - resolve_avatar(unknown_str) falls back to the default without raising.
  - DEFAULT_AVATAR_ID is present in AVATARS_BY_ID.
  - No duplicate ids in the catalog.
  - Gender values are "male" or "female" only.
  - Voice fields are non-empty strings (Sarvam speaker names).
  - replica_id fields are non-empty strings (Tavus ids).
  - thumbnail_url values look like https:// URLs.
"""

from __future__ import annotations

import pytest

from app.avatars import (
    AVATARS,
    AVATARS_BY_ID,
    DEFAULT_AVATAR_ID,
    Avatar,
    resolve_avatar,
    valid_avatar_ids,
)

# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------


def test_avatars_non_empty() -> None:
    assert len(AVATARS) >= 1, "AVATARS catalog must have at least one entry"


def test_avatars_by_id_consistent() -> None:
    """AVATARS_BY_ID must index every entry in AVATARS by its id."""
    assert len(AVATARS_BY_ID) == len(AVATARS)
    for av in AVATARS:
        assert av.id in AVATARS_BY_ID
        assert AVATARS_BY_ID[av.id] is av


def test_no_duplicate_ids() -> None:
    ids = [av.id for av in AVATARS]
    assert len(ids) == len(set(ids)), "Duplicate avatar ids detected in AVATARS"


def test_default_avatar_id_in_catalog() -> None:
    assert DEFAULT_AVATAR_ID in AVATARS_BY_ID, (
        f"DEFAULT_AVATAR_ID={DEFAULT_AVATAR_ID!r} is not present in AVATARS_BY_ID"
    )


def test_valid_avatar_ids_matches_catalog() -> None:
    assert valid_avatar_ids() == set(AVATARS_BY_ID.keys())


@pytest.mark.parametrize("av", AVATARS)
def test_avatar_fields_non_empty(av: Avatar) -> None:
    """Every catalog field must be a non-empty string."""
    assert av.id and isinstance(av.id, str), f"Empty id in avatar {av!r}"
    assert av.name and isinstance(av.name, str), f"Empty name in avatar {av!r}"
    assert av.gender in ("male", "female"), f"Invalid gender {av.gender!r} in avatar {av!r}"
    assert av.replica_id and isinstance(av.replica_id, str), (
        f"Empty replica_id in avatar {av!r}"
    )
    assert av.voice and isinstance(av.voice, str), f"Empty voice in avatar {av!r}"
    assert av.thumbnail_url.startswith("https://"), (
        f"thumbnail_url must start with https:// in avatar {av!r}"
    )


def test_three_avatars_present() -> None:
    """The spec requires exactly lucas, anna, gloria to be present."""
    ids = valid_avatar_ids()
    assert "lucas" in ids
    assert "anna" in ids
    assert "gloria" in ids


def test_lucas_is_male() -> None:
    assert AVATARS_BY_ID["lucas"].gender == "male"


def test_anna_and_gloria_are_female() -> None:
    assert AVATARS_BY_ID["anna"].gender == "female"
    assert AVATARS_BY_ID["gloria"].gender == "female"


# ---------------------------------------------------------------------------
# resolve_avatar
# ---------------------------------------------------------------------------


def test_resolve_known_id_returns_correct_avatar() -> None:
    for av in AVATARS:
        resolved = resolve_avatar(av.id)
        assert resolved is av, f"resolve_avatar({av.id!r}) did not return the right Avatar"


def test_resolve_none_returns_default() -> None:
    resolved = resolve_avatar(None)
    assert resolved.id == DEFAULT_AVATAR_ID


def test_resolve_unknown_id_returns_default() -> None:
    resolved = resolve_avatar("does-not-exist")
    assert resolved.id == DEFAULT_AVATAR_ID


def test_resolve_empty_string_returns_default() -> None:
    """An empty string is not a valid catalog id — fall back to default."""
    resolved = resolve_avatar("")
    assert resolved.id == DEFAULT_AVATAR_ID


def test_resolve_never_raises() -> None:
    """resolve_avatar must not raise for any string input."""
    for bad_input in (None, "", "xyz", "ANNA", " anna ", "🎭"):
        try:
            result = resolve_avatar(bad_input)  # type: ignore[arg-type]
            assert result.id == DEFAULT_AVATAR_ID or result.id in AVATARS_BY_ID
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"resolve_avatar({bad_input!r}) raised {exc!r}")


# ---------------------------------------------------------------------------
# Per-avatar spec assertions (exact values from the task spec)
# ---------------------------------------------------------------------------


def test_lucas_spec() -> None:
    av = AVATARS_BY_ID["lucas"]
    assert av.name == "Lucas"
    assert av.replica_id == "r5f0577fc829"
    assert av.voice == "rahul"
    assert "40779" in av.thumbnail_url


def test_anna_spec() -> None:
    av = AVATARS_BY_ID["anna"]
    assert av.name == "Anna"
    assert av.replica_id == "rf4e9d9790f0"
    assert av.voice == "kavya"
    assert "39895" in av.thumbnail_url


def test_gloria_spec() -> None:
    av = AVATARS_BY_ID["gloria"]
    assert av.name == "Gloria (Greenscreen)"
    assert av.replica_id == "rb67667672ad"
    assert av.voice == "priya"
    assert "21831" in av.thumbnail_url


def test_gloria_warm_spec() -> None:
    av = AVATARS_BY_ID["gloria_warm"]
    assert av.name == "Gloria (Warm)"
    assert av.gender == "female"
    assert av.replica_id == "r3f427f43c9d"
    assert av.voice == "shreya"
    assert "40031" in av.thumbnail_url


def test_raj_spec() -> None:
    av = AVATARS_BY_ID["raj"]
    assert av.name == "Raj"
    assert av.gender == "male"
    assert av.replica_id == "ra066ab28864"
    assert av.voice == "amit"
    assert "20280" in av.thumbnail_url


def test_benjamin_spec() -> None:
    av = AVATARS_BY_ID["benjamin"]
    assert av.name == "Benjamin"
    assert av.gender == "male"
    assert av.replica_id == "r1a4e22fa0d9"
    assert av.voice == "rohan"
    assert "20269" in av.thumbnail_url


def test_catalog_has_three_male_three_female() -> None:
    """RFP requires 6 avatars — 3 male / 3 female."""
    males = [av for av in AVATARS if av.gender == "male"]
    females = [av for av in AVATARS if av.gender == "female"]
    assert len(males) == 3, f"expected 3 male avatars, got {[a.id for a in males]}"
    assert len(females) == 3, f"expected 3 female avatars, got {[a.id for a in females]}"
