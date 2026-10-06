#!/usr/bin/env bash
#
# pip-audit every service's pinned dependency set, honouring the accepted-risk
# list in scripts/pip-audit-ignore.txt.
#
# WHY THIS IS A SCRIPT AND NOT TWO COPIES OF A YAML `run:` BLOCK. It is invoked
# from two workflows — `security (deps + secrets)` in ci.yml, which gates merges,
# and `dependency-watch.yml`, which runs nightly because an advisory published
# today fails a push made next week and nobody learns in between. That is exactly
# what happened on 2026-10-06: three advisories (multidict CVE-2026-104874,
# langgraph-sdk CVE-2026-104873, python-jose CVE-2026-85394) had been live on
# `main` for days and were discovered only because somebody happened to push.
#
# Two inline copies of this loop would drift the moment a fifth service is added
# or the ignore file moves, and the nightly copy is the one nobody would notice
# had stopped covering a service. `ops/ci/tests/test_dependency_watch.py` asserts both
# workflows call THIS file.
#
# Exit 0 = every service clean or accepted. Exit 1 = at least one finding that is
# not on the accepted list, with the table printed per service.
set -euo pipefail

IGNORE_FILE="${IGNORE_FILE:-scripts/pip-audit-ignore.txt}"
PYTHON="${PYTHON:-python}"

# `python -m pip_audit`, not the `pip-audit` console script. The module is what
# `pip install pip-audit` guarantees; the script is only on PATH if the install
# location happens to be there, which it is not under Git Bash on Windows. A
# missing scanner must also be unmistakable rather than looking like a clean run,
# so check it up front: without this the loop below reports "command not found"
# four times and the only difference from success is the exit code.
if ! "${PYTHON}" -c 'import pip_audit' >/dev/null 2>&1; then
  echo "::error::pip-audit is not importable by ${PYTHON} — nothing was scanned." >&2
  echo "Install it with: ${PYTHON} -m pip install pip-audit" >&2
  exit 1
fi

# The services, in one place. A fifth service is added here and both workflows
# pick it up.
SERVICES=(data_gateway interview_core feedback_billing admin_ops)

if [[ ! -f "${IGNORE_FILE}" ]]; then
  echo "::error::accepted-risk list not found at ${IGNORE_FILE}" >&2
  exit 1
fi

# One id per line; `#` starts a comment. Read into --ignore-vuln flags.
mapfile -t IDS < <(grep -vE '^\s*#|^\s*$' "${IGNORE_FILE}")
ARGS=()
for id in "${IDS[@]}"; do ARGS+=(--ignore-vuln "$id"); done
echo "${#IDS[@]} accepted-risk ids loaded from ${IGNORE_FILE}"

status=0
for svc in "${SERVICES[@]}"; do
  req="services/${svc}/requirements.txt"
  if [[ ! -f "${req}" ]]; then
    # A missing requirements file is a repo-structure change, not a clean scan.
    echo "::error::${req} not found — SERVICES in $0 is out of date" >&2
    status=1
    continue
  fi
  echo "::group::pip-audit ${svc}"
  # --no-deps: the pins ARE the dependency set. Resolving transitively here would
  # audit versions no image installs.
  "${PYTHON}" -m pip_audit -r "${req}" --no-deps "${ARGS[@]}" || status=1
  echo "::endgroup::"
done

exit "${status}"
