#!/usr/bin/env python3
"""Fail the build when a deploy target and a service's ``Settings`` disagree about env names.

Why this script exists
----------------------
Every ``Settings`` class in this repo sets ``extra="ignore"``. That is the right
default for a process whose environment also carries ``PATH`` and ``HOME`` — but
it means an env var whose name the service does **not** declare is dropped with
no log line, no startup error, and no way to tell it apart from "not set". The
service boots, reports healthy, and runs on the field's default.

That failure mode has already shipped twice:

* **DEP-1** — ``feedback_billing``/``admin_ops`` declare ``s3_endpoint_url``
  while ``data_gateway``/``interview_core`` declare ``s3_endpoint``. A deploy
  supplying only one spelling gave two services an empty endpoint, boto3 quietly
  targeted AWS with R2 credentials, scorecard PDFs never stored and DPDP §12
  erasure never completed. It was fixed by hand-adding the second key to
  ``render.yaml`` and a shell shim to ``space/entrypoint.sh``.
* **DEP-ROOT** (code review 2026-08-07) — that fix *patched* it. Three files now
  have to stay in step for one URL, and one operator typo re-arms the identical
  HIGH finding.

A comment asking the next author to keep them in step is what was in place both
times. So the invariant is made executable here.

What is checked
---------------
1. **Per-service orphans.** An env name a deploy target sets *for one specific
   service* must be a field that service declares. This is the ``extra="ignore"``
   trap, aimed at the exact place it bites.
2. **Pool orphans.** A name set container-wide (``space/entrypoint.sh``) or
   group-wide (``render.yaml``'s ``envVarGroups``) must be declared by at least
   ONE service — those pools are deliberately a union, so per-service strictness
   would flag the union itself. Names consumed by the deploy machinery rather
   than by pydantic (``%(ENV_X)s`` in supervisord, ``${X}`` in the entrypoint)
   are not orphans and are recognised as such.
3. **Required-field supply.** A field with no default aborts the process at
   import. For targets that enumerate their environment in-repo (``render.yaml``,
   the HF Space boot path) every required field of every service they run must be
   supplied — or, for the Space, at least named in ``entrypoint.sh``'s
   ``REQUIRED=()`` array, which is what turns a five-service crash-loop into one
   readable message. ``docker-compose*.yml`` is exempt: it loads
   ``services/<svc>/.env``, which is not in the repo, so absence there proves
   nothing.
4. **Alias-group coverage.** Where the services genuinely disagree on the name
   for one resource (see ``ALIAS_GROUPS``), a target that supplies any spelling
   must supply all of them. This is the DEP-ROOT re-arm guard: delete
   ``S3_ENDPOINT_URL`` from ``render.yaml`` and this turns red instead of the
   scorecard bucket going quiet in production.

Accepted gaps live in ``ACCEPTED`` with a reason and an unblock condition, the
same ratchet ``scripts/pip-audit-ignore.txt`` and ``.trivyignore`` already use.
An accepted entry that no longer corresponds to a real mismatch is itself a
failure, so the list cannot rot into permanent silence.

Run locally exactly as CI runs it::

    python ops/ci/check_env_parity.py

Exit status 0 = the deploy targets and the ``Settings`` classes agree.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment problem, not a finding
    sys.exit("FAIL: PyYAML is required. pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parents[2]

SERVICES = ("data_gateway", "interview_core", "feedback_billing", "admin_ops")

RENDER_YAML = REPO_ROOT / "render.yaml"
ENTRYPOINT_SH = REPO_ROOT / "space" / "entrypoint.sh"
SUPERVISORD_CONF = REPO_ROOT / "space" / "supervisord.conf"
COMPOSE_GLOB = "docker-compose*.yml"

# Names the deploy targets set that are read by something other than a pydantic
# Settings field. Each is a real consumer, not a shrug.
NON_SETTINGS_NAMES = {
    # Read by supervisord's own %(ENV_...)s interpolation, never by a service.
    "INTERVIEW_WORKER_AUTOSTART",
    # Injected by Hugging Face; only entrypoint.sh reads it.
    "SPACE_HOST",
    # Render's static-site build knob, not a backend setting.
    "NODE_VERSION",
}

# Where the four services genuinely use different env names for ONE resource.
# Hand-maintained on purpose: no parser can tell "two names, one bucket" from
# "two names, two buckets", and getting that wrong in either direction is worse
# than writing it down. Deleting a group is the reward for converging the names.
ALIAS_GROUPS: dict[str, frozenset[str]] = {
    "S3 endpoint URL": frozenset({"S3_ENDPOINT", "S3_ENDPOINT_URL"}),
}

# Accepted mismatches: {(target, service_or_"-", env_name): reason}.
# A reason must say who owns the fix and what unblocks it, or it is just a
# silencer. Entries are verified to still be live mismatches below.
ACCEPTED: dict[tuple[str, str, str], str] = {
    (
        "space",
        "interview_core",
        "S3_ENDPOINT",
    ): (
        "interview_core declares s3_endpoint/s3_access_key_id/s3_secret_access_key "
        "with NO default, so the Space supplies them as Space secrets. They are "
        "absent from entrypoint.sh's REQUIRED=() array, so an unset one crash-loops "
        "interview_core instead of printing the readable fail-fast message the "
        "other six secrets get. Owner: space/entrypoint.sh (not in this change's "
        "ownership). Unblock: add the three names to REQUIRED=() and delete these "
        "three entries."
    ),
    ("space", "interview_core", "S3_ACCESS_KEY_ID"): "See the S3_ENDPOINT entry above.",
    ("space", "interview_core", "S3_SECRET_ACCESS_KEY"): "See the S3_ENDPOINT entry above.",
    (
        "space",
        "-",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ): (
        "Exported container-wide by entrypoint.sh for the Tier-2 OpenTelemetry "
        "path; no Settings field and no shared/ module reads it at HEAD. Harmless "
        "but it reads as configuration. Owner: space/entrypoint.sh. Unblock: wire "
        "an OTLP exporter in shared/observability, or drop the export."
    ),
}


# --------------------------------------------------------------------------- #
# Sources of truth
# --------------------------------------------------------------------------- #
def _read(path: Path) -> str:
    if not path.is_file():
        sys.exit(f"FAIL: {path.relative_to(REPO_ROOT)} does not exist.")
    return path.read_text(encoding="utf-8")


def parse_settings_fields(service: str) -> dict[str, bool]:
    """``{ENV_NAME: is_required}`` for one service's ``Settings``.

    Parsed with ``ast`` rather than imported: importing a ``config.py`` executes
    ``settings = Settings()`` at module scope, which needs a full environment and
    every one of that service's third-party dependencies. This job must stay
    runnable on a bare checkout with only PyYAML.
    """
    source = _read(REPO_ROOT / "services" / service / "app" / "config.py")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            fields: dict[str, bool] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    # No assigned value == no default == the process aborts at
                    # import when the env var is missing.
                    fields[stmt.target.id.upper()] = stmt.value is None
            return fields
    sys.exit(f"FAIL: services/{service}/app/config.py declares no `class Settings`.")


def parse_render(text: str) -> tuple[dict[str, set[str]], set[str], dict[str, str]]:
    """``render.yaml`` -> (per-service keys, shared-group keys, service -> label).

    A Render service is tied to a code service by its ``dockerfilePath``; the
    blueprint's own ``name:`` is cosmetic and two entries (the API and the
    worker) legitimately share one Dockerfile.
    """
    doc: dict[str, Any] = yaml.safe_load(text) or {}
    groups = {
        group["name"]: {entry["key"] for entry in group.get("envVars", []) if "key" in entry}
        for group in doc.get("envVarGroups", [])
    }

    per_service: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    pool: set[str] = set()
    for svc in doc.get("services", []):
        match = re.search(r"services/([a-z_]+)/Dockerfile", svc.get("dockerfilePath", "") or "")
        if match is None:
            # The static frontend: built by Vite, no Settings class to disagree
            # with. VITE_* are inlined at build time, not read at runtime.
            continue
        code_service = match.group(1)
        own = {entry["key"] for entry in svc.get("envVars", []) if "key" in entry}
        per_service.setdefault(code_service, set()).update(own)
        labels.setdefault(code_service, svc.get("name", code_service))
        for entry in svc.get("envVars", []):
            if "fromGroup" in entry:
                pool |= groups.get(entry["fromGroup"], set())
    return per_service, pool, labels


class Space(NamedTuple):
    """What the HF Space container supplies, split by who reads it."""

    per_program: dict[str, set[str]]
    """``supervisord.conf`` ``environment=`` overrides, per program."""
    pool: set[str]
    """Names every program inherits: entrypoint exports + the REQUIRED=() array."""
    shell_refs: set[str]
    """Names ``entrypoint.sh`` READS but never exports under that spelling.

    ``export S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-${S3_ENDPOINT:-}}"`` means the
    Space does supply ``S3_ENDPOINT`` — as a Space secret feeding a shim. The
    self-reference on the left of the same line is not evidence of anything, so
    exported names are subtracted.
    """
    machinery_refs: set[str]
    """Names read by the deploy machinery itself, not by a Settings field.

    ``supervisord``'s ``%(ENV_S3_BUCKET_DG)s`` is a real consumer, so those names
    are configuration even though no ``Settings`` declares them.
    """


def parse_space(entrypoint: str, supervisord: str) -> Space:
    """Split the Space's environment into pool, per-program and machinery reads.

    ``entrypoint.sh`` exports into the container environment that every program
    inherits; ``supervisord.conf``'s ``environment=`` lines are per-program
    overrides on top.
    """
    exports = set(re.findall(r"^export\s+([A-Z][A-Z0-9_]*)=", entrypoint, re.MULTILINE))
    required = re.search(r"^REQUIRED=\(([^)]*)\)", entrypoint, re.MULTILINE)
    if required is None:
        sys.exit("FAIL: space/entrypoint.sh has no REQUIRED=( ... ) array.")
    pool = exports | {name for name in required.group(1).split() if name}

    shell_refs = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)[:}]", entrypoint)) - exports
    supervisor_refs = set(re.findall(r"%\(ENV_([A-Z][A-Z0-9_]*)\)s", supervisord))

    per_program: dict[str, set[str]] = {}
    program: str | None = None
    for line in supervisord.splitlines():
        header = re.match(r"^\[program:([a-z_]+)\]", line)
        if header:
            program = header.group(1)
            continue
        body = re.match(r"^environment=(.*)$", line)
        if body and program:
            per_program[program] = set(re.findall(r"([A-Z][A-Z0-9_]*)=", body.group(1)))
    return Space(per_program, pool, shell_refs, supervisor_refs | shell_refs)


def parse_compose(text: str) -> dict[str, set[str]]:
    """One compose file -> ``{code_service: keys}``, keyed by the built image name."""
    doc: dict[str, Any] = yaml.safe_load(text) or {}
    out: dict[str, set[str]] = {}
    for svc in (doc.get("services") or {}).values():
        match = re.search(r"intants/([a-z_]+):", svc.get("image", "") or "")
        if match is None:
            continue  # caddy and anything else that is not one of our images
        env = svc.get("environment") or {}
        keys = set(env) if isinstance(env, dict) else {item.split("=", 1)[0] for item in env}
        # YAML merge keys (<<: *common-env) are already resolved by safe_load.
        out.setdefault(match.group(1), set()).update(keys)
    return out


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def main() -> int:
    # One linear pass per numbered check, in the order the docstring lists them.
    # Kept as one function deliberately: every check reads the same three name
    # sets, and splitting them into helpers that re-derive those sets is how the
    # four checks would drift apart.
    fields = {service: parse_settings_fields(service) for service in SERVICES}
    declared_anywhere = set().union(*(set(f) for f in fields.values()))

    render_per_service, render_pool, render_labels = parse_render(_read(RENDER_YAML))
    space = parse_space(_read(ENTRYPOINT_SH), _read(SUPERVISORD_CONF))

    problems: list[str] = []
    hit: set[tuple[str, str, str]] = set()

    def report(target: str, service: str, name: str, message: str) -> None:
        key = (target, service, name)
        if key in ACCEPTED:
            hit.add(key)
            return
        problems.append(message)

    # --- 1. per-service orphans ------------------------------------------- #
    def check_orphans(target: str, per_service: dict[str, set[str]], where: str) -> None:
        for service, names in sorted(per_service.items()):
            if service not in fields:
                continue
            for name in sorted(names - set(fields[service]) - NON_SETTINGS_NAMES):
                report(
                    target,
                    service,
                    name,
                    f"{where}: sets {name} for {service}, which declares no such field. "
                    f'extra="ignore" drops it silently — the service runs on the default.',
                )

    check_orphans("render", render_per_service, "render.yaml")
    check_orphans("space", space.per_program, "space/supervisord.conf")
    for compose in sorted(REPO_ROOT.glob(COMPOSE_GLOB)):
        # env_file supplies the rest, so only what compose sets INLINE is checkable.
        check_orphans(f"compose:{compose.name}", parse_compose(_read(compose)), compose.name)

    # --- 2. pool orphans --------------------------------------------------- #
    for target, pool, where in (
        ("render", render_pool, "render.yaml envVarGroups"),
        ("space", space.pool, "space/entrypoint.sh"),
    ):
        for name in sorted(pool - declared_anywhere - NON_SETTINGS_NAMES - space.machinery_refs):
            report(
                target,
                "-",
                name,
                f"{where}: supplies {name}, which NO service declares. It reads as "
                "configuration and configures nothing.",
            )

    # --- 3. required fields are actually supplied -------------------------- #
    for service in SERVICES:
        required = sorted(name for name, is_required in fields[service].items() if is_required)
        supplied_render = render_per_service.get(service, set()) | render_pool
        supplied_space = space.pool | space.per_program.get(service, set())
        for name in required:
            if service in render_per_service and name not in supplied_render:
                report(
                    "render",
                    service,
                    name,
                    f"render.yaml: {render_labels.get(service, service)} runs {service}, "
                    f"which requires {name} (no default — the process aborts at import), "
                    "and the blueprint never supplies it.",
                )
            if name not in supplied_space:
                report(
                    "space",
                    service,
                    name,
                    f"space/entrypoint.sh: {service} requires {name} (no default) and it is "
                    "neither exported nor listed in REQUIRED=(), so an unset value "
                    "crash-loops the program instead of printing the fail-fast message.",
                )

    # --- 4. alias groups --------------------------------------------------- #
    supply_by_target = {
        "render": render_pool | set().union(*render_per_service.values()),
        "space": space.pool | space.shell_refs | set().union(*space.per_program.values()),
    }
    for label, spellings in sorted(ALIAS_GROUPS.items()):
        undeclared = sorted(spellings - declared_anywhere)
        if undeclared:
            problems.append(
                f"ALIAS_GROUPS[{label!r}] lists {', '.join(undeclared)}, which no service "
                "declares any more. If the names have converged, delete the group — a stale "
                "alias group makes this gate assert a divergence that no longer exists."
            )
            continue
        for target, supplied in sorted(supply_by_target.items()):
            present = spellings & supplied
            if present and present != spellings:
                missing = ", ".join(sorted(spellings - present))
                problems.append(
                    f"{target}: supplies {', '.join(sorted(present))} but not {missing}. "
                    f"These are the same {label} under names different services declare; "
                    "supplying only one silently gives the others an empty value (DEP-1)."
                )

    # --- the allowlist must not rot --------------------------------------- #
    for key in sorted(set(ACCEPTED) - hit):
        target, service, name = key
        problems.append(
            f"ACCEPTED[{target}/{service}/{name}] no longer matches a live mismatch. "
            "The gap was closed (or renamed) — delete the entry so the allowlist keeps "
            "meaning what it says."
        )

    if problems:
        print("Env-name parity BROKEN:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nEvery Settings class sets extra=\"ignore\", so a name a service does not\n"
            "declare is dropped with no error. Fix the deploy target, rename the field,\n"
            "or — if the mismatch is deliberate — add it to ACCEPTED in\n"
            "ops/ci/check_env_parity.py with a reason and an unblock condition.",
            file=sys.stderr,
        )
        return 1

    print("Env-name parity OK.")
    for service in SERVICES:
        required = sorted(name for name, is_required in fields[service].items() if is_required)
        print(f"  {service:<18} {len(fields[service]):>3} fields, {len(required)} required")
    print(f"  {len(ACCEPTED)} accepted gap(s), all still live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
