"""Tests for the Space front-matter gate.

The case that matters is the one that actually happened: a `short_description`
of 61 characters against a 60-character limit, which passed every check the
repo had and then failed at the deploy's `git push`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "ops" / "ci" / "check_space_metadata.py"

spec = importlib.util.spec_from_file_location("check_space_metadata", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules["check_space_metadata"] = module
spec.loader.exec_module(module)


def _meta(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "AntHire AI Interview",
        "sdk": "docker",
        "app_port": 7860,
        "short_description": "AntHire — voice-first AI interview platform (demo)",
    }
    base.update(overrides)
    return base


def test_committed_readme_would_be_accepted() -> None:
    """The real file, checked as CI checks it — this is the regression guard."""
    assert module.main() == 0


def test_valid_front_matter_has_no_problems() -> None:
    assert module.check(_meta()) == []


def test_short_description_one_char_over_is_rejected() -> None:
    """61 characters. The exact string that broke the 2026-09-08 deploy."""
    over = "AntHire — voice-first AI interview platform (demo deployment)"
    assert len(over) == 61
    problems = module.check(_meta(short_description=over))
    assert len(problems) == 1
    assert "61 characters" in problems[0]
    assert "Trim 1 character(s)" in problems[0]


def test_length_is_counted_in_characters_not_bytes() -> None:
    """The em dash is 3 bytes and 1 character; counting bytes would false-fail."""
    exactly_60 = "—" * 60
    assert module.check(_meta(short_description=exactly_60)) == []
    assert module.check(_meta(short_description="—" * 61)) != []


@pytest.mark.parametrize("key", ["title", "sdk", "app_port"])
def test_missing_required_key_is_rejected(key: str) -> None:
    meta = _meta()
    del meta[key]
    problems = module.check(meta)
    assert any(f"missing required key `{key}`" in p for p in problems)


def test_unknown_sdk_is_rejected() -> None:
    problems = module.check(_meta(sdk="rust"))
    assert any("is not one of" in p for p in problems)


def test_front_matter_must_open_the_file() -> None:
    with pytest.raises(ValueError, match="does not start with"):
        module.read_front_matter("# AntHire\n\nno front matter here\n")


def test_unclosed_front_matter_is_an_error() -> None:
    with pytest.raises(ValueError, match="not closed"):
        module.read_front_matter("---\ntitle: AntHire\n")
