#!/usr/bin/env python3
"""Fail the build when README.md's Space front matter would be rejected by Hugging Face.

Why this script exists
----------------------
On 2026-09-08 a one-character overrun took the deploy down silently. The README
front matter is not documentation — it is the Space's configuration, validated
by a pre-receive hook on huggingface.co. ``short_description`` was edited to 61
characters against a 60-character limit, and every consequence of that landed
somewhere nobody was looking:

* CI stayed green, because nothing in CI reads this file.
* Both merges to ``main`` after the edit reported success.
* ``sync-to-space`` then failed at the ``git push``, with
  ``rejected during YAML metadata verification``, and the live Space quietly
  went on serving the build from two merges earlier.

The failure is visible only in the deploy job's log, which nobody opens when
the PR is green and the merge is clean. So the check moves to where it is
seen: the same run that decides whether the change is mergeable.

The limits below are Hugging Face's, not ours. They are asserted here rather
than trusted to reviewers because the field is edited by whoever is editing
the README prose around it, and the limit is invisible at the point of editing.

Run locally exactly as CI runs it::

    python ops/ci/check_space_metadata.py

Exit status 0 = the front matter would be accepted. 1 = it would be rejected,
and the reason is printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"

# Hugging Face Spaces card metadata. `short_description`'s 60-character cap is
# the one that has actually bitten; the rest are asserted because a missing
# `sdk` or `app_port` fails the same pre-receive hook the same silent way.
MAX_SHORT_DESCRIPTION = 60
REQUIRED_KEYS = ("title", "sdk", "app_port")
ALLOWED_SDKS = ("docker", "gradio", "streamlit", "static")


def read_front_matter(text: str) -> dict[str, object]:
    """Parse the leading `---` YAML block. Raises ValueError if it is absent."""
    if not text.startswith("---\n"):
        raise ValueError("README.md does not start with a `---` front-matter block")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("README.md's front-matter block is not closed by `---`")
    parsed = yaml.safe_load(text[4:end])
    if not isinstance(parsed, dict):
        raise ValueError("README.md's front matter did not parse as a mapping")
    return parsed


def check(meta: dict[str, object]) -> list[str]:
    problems: list[str] = []

    for key in REQUIRED_KEYS:
        if key not in meta:
            problems.append(f"missing required key `{key}`")

    sdk = meta.get("sdk")
    if sdk is not None and sdk not in ALLOWED_SDKS:
        problems.append(f"`sdk: {sdk}` is not one of {', '.join(ALLOWED_SDKS)}")

    description = meta.get("short_description")
    if description is not None:
        # Characters, not bytes: the hook counts what YAML decoded, and this
        # field carries an em dash.
        length = len(str(description))
        if length > MAX_SHORT_DESCRIPTION:
            problems.append(
                f"`short_description` is {length} characters; Hugging Face rejects "
                f"anything over {MAX_SHORT_DESCRIPTION}. Trim "
                f"{length - MAX_SHORT_DESCRIPTION} character(s):\n"
                f"      {description!r}"
            )

    return problems


def main() -> int:
    try:
        meta = read_front_matter(README.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"README.md front matter could not be read: {exc}", file=sys.stderr)
        return 1

    problems = check(meta)
    if problems:
        print(
            "README.md's Space front matter would be REJECTED by huggingface.co,\n"
            "which means the merge goes green and the deploy silently does not "
            "happen:\n",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("README.md Space front matter OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
