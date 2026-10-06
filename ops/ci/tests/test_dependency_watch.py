"""The nightly dependency watch must not drift from the merge gate.

CI scans dependencies on every push, which is the right gate for a CHANGE and
says nothing about the days between changes. On 2026-10-06 three advisories —
multidict CVE-2026-104874, langgraph-sdk CVE-2026-104873 (HIGH) and python-jose
CVE-2026-85394 — were live on `main` and were found only because somebody happened
to push a branch. `dependency-watch.yml` closes that, and the repo had no
`schedule:` trigger of any kind before it.

A scheduled workflow fails quietly in a way a PR check cannot: there is no PR to
block and no reviewer to tell. So the things that would silently stop it working
are what this file tests — a second inline copy of the scan that stops covering a
new service, a renamed ignore file, a lost cron, or a lost ability to open the
issue that is the only notification anybody receives.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WATCH = REPO_ROOT / ".github" / "workflows" / "dependency-watch.yml"
SCRIPT = REPO_ROOT / "scripts" / "audit_python_deps.sh"
IGNORE = REPO_ROOT / "scripts" / "pip-audit-ignore.txt"

SERVICES = ("data_gateway", "interview_core", "feedback_billing", "admin_ops")


def _text(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The files exist where the rest of this file assumes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [CI, WATCH, SCRIPT, IGNORE])
def test_the_pieces_are_where_we_think_they_are(path: pathlib.Path) -> None:
    """Guards the guard. Every assertion below reads one of these as text, and a
    moved file would make them all pass vacuously."""
    assert path.is_file(), f"{path.relative_to(REPO_ROOT).as_posix()} is missing"


# ---------------------------------------------------------------------------
# One scan, two callers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("workflow", [CI, WATCH], ids=["ci", "dependency-watch"])
def test_both_workflows_run_the_shared_script(workflow: pathlib.Path) -> None:
    """The loop lived inline in ci.yml. A second inline copy in the nightly
    workflow would drift the moment a fifth service was added — and the nightly
    copy is the one nobody would notice had stopped covering a service, because it
    would go on reporting a clean scan of three."""
    assert "scripts/audit_python_deps.sh" in _text(workflow)


def test_neither_workflow_keeps_its_own_copy_of_the_loop() -> None:
    """Calling the script is not enough if the old loop is still there too: the
    one that runs is whichever the step reaches first."""
    for workflow in (CI, WATCH):
        body = _text(workflow)
        assert "pip-audit -r" not in body, (
            f"{workflow.name} invokes pip-audit directly — that is the copy that "
            "drifts. Call scripts/audit_python_deps.sh instead."
        )


def test_the_script_names_every_service_that_has_a_requirements_file() -> None:
    """The reverse direction: a fifth service with pinned dependencies that the
    script does not list is a service nobody scans, nightly or on push."""
    on_disk = {
        p.parent.name
        for p in (REPO_ROOT / "services").glob("*/requirements.txt")
    }
    declared = set(re.findall(r"^SERVICES=\((.*?)\)", _text(SCRIPT), re.MULTILINE | re.DOTALL)[0].split())

    assert on_disk == declared, (
        "scripts/audit_python_deps.sh's SERVICES list and services/*/requirements.txt "
        f"disagree — only in the script: {sorted(declared - on_disk)}; "
        f"only on disk (UNSCANNED): {sorted(on_disk - declared)}"
    )


def test_the_script_reads_the_accepted_risk_list_that_exists() -> None:
    """A renamed ignore file would make the script exit 1 on every entry rather
    than silently pass — but only because of the explicit guard, so pin it."""
    body = _text(SCRIPT)
    assert "scripts/pip-audit-ignore.txt" in body
    assert "accepted-risk list not found" in body, (
        "a missing ignore file must be an error, not an empty --ignore-vuln list "
        "that fails every accepted risk and teaches people to ignore the job"
    )


def test_a_missing_scanner_is_an_error_rather_than_a_clean_run() -> None:
    """The worst outcome for a nightly scan: the tool is absent, nothing is
    scanned, and the log looks like four services with no findings."""
    body = _text(SCRIPT)
    assert "import pip_audit" in body
    assert "nothing was scanned" in body


def test_the_script_uses_the_module_not_the_console_script() -> None:
    """`python -m pip_audit` is what `pip install pip-audit` guarantees. The
    `pip-audit` entry point depends on the install location being on PATH, which
    it is not under Git Bash on Windows — so a developer running this by hand got
    four "command not found" lines.

    Matched on the INVOCATION, not on the string appearing anywhere: a mutation
    test caught the first version of this passing against the comment that
    explains the choice while the real call had been reverted."""
    body = _text(SCRIPT)

    invocations = re.findall(r'^\s*"\$\{PYTHON\}" -m pip_audit -r .*$', body, re.MULTILINE)

    assert invocations, (
        "no `\"${PYTHON}\" -m pip_audit -r ...` call found — a bare `pip-audit` "
        "depends on PATH, which it is not under Git Bash on Windows"
    )


# ---------------------------------------------------------------------------
# The schedule, and the notification that makes it worth having
# ---------------------------------------------------------------------------
def test_the_watch_is_actually_scheduled() -> None:
    """Without this the workflow is a file nobody runs. Before it existed,
    `grep -n "schedule:" .github/workflows/*.yml` returned nothing at all."""
    body = _text(WATCH)
    assert re.search(r"^\s*schedule:", body, re.MULTILINE), "no schedule: trigger"
    assert re.search(r"cron:\s*'[^']+'", body), "no cron expression"


def test_the_watch_can_be_run_on_demand() -> None:
    """A scheduled-only workflow cannot be tested without waiting a day."""
    assert "workflow_dispatch:" in _text(WATCH)


def test_a_finding_opens_an_issue() -> None:
    """The whole point. A scheduled run that only turns the Actions tab red is a
    notification nobody receives — there is no PR to block and no reviewer to
    tell. Closing the issue is the signal that the work is done."""
    body = _text(WATCH)
    assert "issues.create" in body, "nothing opens an issue"
    assert "issues.update" in body, (
        "an issue that is created but never updated becomes one thread per night; "
        "updating in place keeps a week of unfixed advisories readable"
    )
    assert "dependency-watch" in body, "no marker to find the existing issue by"


def test_only_the_reporting_job_can_write_issues() -> None:
    """`issues: write` is a real escalation, and the job that needs it is not the
    job that installs third-party packages. ci.yml is read-only throughout for
    exactly this reason; the scan half of this workflow must stay that way.

    Read from the PARSED workflow, because the permission a job ends up with is
    the job's own block or, failing that, the workflow default — and a mutation
    test caught the first version of this slicing from ``jobs:`` onward, which
    missed `issues: write` added at the top level where it reaches every job.
    """
    doc = yaml.safe_load(_text(WATCH))
    top = doc.get("permissions") or {}
    jobs = doc["jobs"]

    def effective(name: str) -> dict[str, str]:
        return {**top, **(jobs[name].get("permissions") or {})}

    assert effective("report").get("issues") == "write", (
        "the reporting job cannot open the issue that is the only notification "
        "anybody receives from a scheduled run"
    )
    assert effective("scan").get("issues") != "write", (
        "the scanning job must not hold a write token while it runs pip install "
        "and npm ci — a compromised transitive dependency would inherit it"
    )
    assert top.get("issues") != "write", (
        "a top-level `issues: write` grants it to every job, including the one "
        "running installs; scope it to the reporting job"
    )


def test_one_scanner_failing_does_not_hide_the_others() -> None:
    """Three scanners run (pip, npm, base image). Without continue-on-error the
    first red one ends the job and the other two are never reported, so a week of
    nights could each reveal one finding at a time."""
    body = _text(WATCH)
    assert body.count("continue-on-error: true") >= 3


def test_the_image_scanner_is_pinned() -> None:
    """An unpinned scanner means last night's clean run and tonight's red one can
    differ by the tool rather than by the code — ci.yml pins it for the same
    reason."""
    body = _text(WATCH)
    assert re.search(r"TRIVY_VERSION:\s*'[\d.]+'", body), "trivy version not pinned"
    assert "aquasec/trivy:${TRIVY_VERSION}" in body


def test_the_image_scan_honours_the_same_accepted_risk_file_as_ci() -> None:
    """Two ignore lists would mean the nightly reports risks the merge gate has
    already accepted, which is how a job gets switched off.

    Anchored so `.trivyignore.nightly` does not satisfy it — the first version of
    this assertion was a prefix match and a mutation test walked straight through
    it. The file must also be the one ci.yml uses, so the two cannot diverge.
    """
    watch_files = re.findall(r"--ignorefile\s+(\S+)", _text(WATCH))
    ci_files = re.findall(r"--ignorefile\s+(\S+)", _text(CI))

    assert watch_files, "the nightly image scan applies no accepted-risk list"
    assert set(watch_files) == {".trivyignore"}, (
        f"the nightly scan uses {sorted(set(watch_files))}; it must use the same "
        ".trivyignore the merge gate does, or it will report risks already accepted"
    )
    assert set(watch_files) <= set(ci_files), (
        f"ci.yml applies {sorted(set(ci_files))} — the two have diverged"
    )


def test_the_watch_does_not_build_the_service_images() -> None:
    """Deliberate scope, asserted so it is not quietly widened: a nightly
    five-image build is the reason this workflow would get disabled for being
    slow. pip-audit covers the Python layer; the shared base covers the OS one."""
    body = _text(WATCH)
    assert "docker build" not in body
    assert "BASE_IMAGE:" in body


def test_the_base_image_matches_what_the_dockerfiles_actually_use() -> None:
    """Scanning a base nothing is built on would be a clean report about an image
    we do not ship."""
    base = re.search(r"BASE_IMAGE:\s*'([^']+)'", _text(WATCH))
    assert base is not None
    tag = base.group(1)

    dockerfiles = [
        *(REPO_ROOT / "services").glob("*/Dockerfile"),
        REPO_ROOT / "Dockerfile",
    ]
    users = [p for p in dockerfiles if p.is_file() and f"FROM {tag}" in _text(p)]

    assert len(users) >= 4, (
        f"{tag} is the scanned base but only {len(users)} Dockerfile(s) build on it"
    )
