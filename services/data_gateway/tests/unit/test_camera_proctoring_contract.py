"""A cited section must exist, and a defined section must be cited.

53 comments across 22 files cited a "camera-proctoring contract" by section
number for a year, and no such document was ever committed. Nothing caught it,
because a dangling reference in a comment costs nothing at runtime — every test
passed, every deploy worked, and the guarantees those comments deferred to had no
written authority behind them at all.

These tests are the mechanism, not the document. They fail when:

  * code cites a section the contract does not define (a dangling reference —
    the original failure, now impossible to reintroduce silently);
  * the contract defines a section nothing cites (a rule with no enforcement
    point, or a renumbering that orphaned its citations);
  * §6's honest "not reconstructable" marker is quietly replaced by content
    someone invented to close the numbering gap.

Lives under data_gateway/tests because that is a suite CI already runs and the
contract governs code in both services and web. It reads files from the repo
root, not from this service.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

#: Repo root: .../services/data_gateway/tests/unit/this_file.py -> up four.
ROOT = pathlib.Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "docs" / "CAMERA-PROCTORING-CONTRACT.md"

#: "contract §3", "contract §7 item 1", and the compound "contract §3/§8".
CITATION = re.compile(r"contract §([\d/§]+)", re.IGNORECASE)
#: "## §3 — Consent: ..." / "## §6 — [not reconstructable]"
HEADING = re.compile(r"^##\s*§(\d+)\s*—", re.MULTILINE)

#: The one file that says "contract §3" meaning a DIFFERENT contract (PH5-E3
#: rediscovery). It names which one, so it is excluded by path rather than by
#: hoping the wording stays unambiguous.
OTHER_CONTRACTS = {"web/src/api/rediscovery.ts"}


def _tracked_sources() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py", "*.ts", "*.tsx"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [ROOT / rel for rel in out if rel not in OTHER_CONTRACTS]


def _cited_sections() -> dict[str, list[str]]:
    """{section: [file:line, ...]} across every tracked source file."""
    found: dict[str, list[str]] = {}
    for path in _tracked_sources():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(lines, 1):
            m = CITATION.search(line)
            if not m:
                continue
            for sec in re.findall(r"\d+", m.group(1)):
                where = f"{path.relative_to(ROOT).as_posix()}:{n}"
                found.setdefault(sec, []).append(where)
    return found


def _defined_sections() -> set[str]:
    return set(HEADING.findall(CONTRACT.read_text(encoding="utf-8")))


def test_the_contract_exists() -> None:
    """The failure this whole file is about. Asserted first and on its own, so a
    missing document reports as exactly that rather than as eight confusing
    downstream failures."""
    assert CONTRACT.is_file(), (
        f"{CONTRACT.relative_to(ROOT)} is missing, and 50+ code comments cite it "
        "by section number"
    )


def test_every_cited_section_is_defined() -> None:
    cited = _cited_sections()
    assert cited, "no citations found at all — has the wording changed?"
    defined = _defined_sections()
    dangling = {s: v for s, v in cited.items() if s not in defined}
    assert not dangling, (
        "code cites contract sections the document does not define: "
        + "; ".join(f"§{s} (e.g. {v[0]})" for s, v in sorted(dangling.items()))
    )


def test_every_defined_section_is_cited_or_declared_uncited() -> None:
    """A section nothing cites is either a rule with no enforcement point or the
    wreckage of a renumbering. §6 is the one legitimate case and must say so in
    its own heading."""
    cited = set(_cited_sections())
    text = CONTRACT.read_text(encoding="utf-8")
    for section in sorted(_defined_sections(), key=int):
        if section in cited:
            continue
        heading = re.search(rf"^##\s*§{section}\s*—\s*(.+)$", text, re.MULTILINE)
        assert heading, f"§{section} has no heading?"
        assert "not reconstructable" in heading.group(1).lower(), (
            f"§{section} is defined but nothing cites it, and its heading does not "
            f"declare it unreconstructable: {heading.group(1)!r}"
        )


def test_section_six_stays_honestly_empty() -> None:
    """The load-bearing bit of this change. §6 is uncited, so its subject is
    unknown; filling it with something plausible would put an unagreed rule into a
    document quoted in compliance answers and bid responses, which is the exact
    failure CLAUDE.md's documentation rule names. If the original spec resurfaces,
    a citation will appear and the test above stops requiring this marker.
    """
    text = CONTRACT.read_text(encoding="utf-8")
    body = text.split("## §6")[1].split("\n## ")[0]
    assert "not reconstructable" in body.lower()
    assert "deliberately not guessed" in body.lower()
    # And the gap must not have been closed by renumbering, which would silently
    # invalidate every §7 and §8 citation.
    assert {"7", "8"} <= _defined_sections()


def test_the_numbering_has_no_accidental_gap() -> None:
    """Every number from 1 to the highest defined section is present, so a gap is
    always a deliberate, documented one rather than a section someone dropped."""
    defined = {int(s) for s in _defined_sections()}
    assert defined, "no sections defined"
    missing = set(range(1, max(defined) + 1)) - defined
    assert not missing, f"the contract skips §{sorted(missing)} with no heading at all"


@pytest.mark.parametrize(
    ("section", "must_mention"),
    [
        # One anchor per section: the guarantee that section exists to state.
        # Not prose-matching for its own sake — each of these is a claim the code
        # enforces and a reviewer may be asked to substantiate, so the document
        # losing it is a defect in the document.
        ("1", "KNOWN_EVENT_TYPES"),
        ("2", "gaze_away"),
        ("3", "video_capture"),
        ("4", "no runtime CDN"),
        ("5", "auto_submit_relaxed"),
        ("7", "events_dropped"),
        ("8", "fallbackLng"),
    ],
)
def test_each_section_still_states_its_guarantee(section: str, must_mention: str) -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    body = text.split(f"## §{section}")[1].split("\n## ")[0]
    assert must_mention in body, (
        f"§{section} no longer mentions {must_mention!r} — either the guarantee moved "
        "or the document lost it"
    )
