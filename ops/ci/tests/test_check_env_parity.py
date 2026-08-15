"""Tests for ``ops/ci/check_env_parity.py``.

A gate is only worth its runtime if it can go RED. The existing ops/ci scripts
have no tests, and the failure mode that costs nothing to reach is the one where
a parser silently matches nothing, every set comes back empty, and the check
prints OK forever. So every test below either builds a *broken* fake repository
and asserts the exit status is 1, or pins a parsing subtlety that would degrade
into silence if it regressed.

The fake repository is a real directory tree rather than monkeypatched return
values, because the thing under test is exactly the mapping from files on disk
to name sets — mocking that away would test nothing.

Run::

    python -m pytest ops/ci/tests -q
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "ops" / "ci" / "check_env_parity.py"


def _load() -> ModuleType:
    """Import the script fresh.

    ops/ is not a package (nothing under it is importable by name), and the
    module reads REPO_ROOT at import time, so each test that repoints the paths
    needs its own module object rather than a shared cached one.
    """
    spec = importlib.util.spec_from_file_location("check_env_parity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def parity() -> ModuleType:
    return _load()


# --------------------------------------------------------------------------- #
# The real repository
# --------------------------------------------------------------------------- #
def test_repo_as_committed_passes(parity: ModuleType) -> None:
    """The committed tree must be green, or CI is red on arrival."""
    assert parity.main() == 0


def test_required_fields_are_actually_found(parity: ModuleType) -> None:
    """Guards the silent-empty-set failure: an AST walk that matches nothing
    would make every downstream check vacuously pass."""
    fields = parity.parse_settings_fields("interview_core")
    # No default in services/interview_core/app/config.py -> aborts at import.
    assert fields["S3_ENDPOINT"] is True
    assert fields["DATABASE_URL"] is True
    # Has a default.
    assert fields["S3_REGION"] is False
    assert len(fields) > 50


def test_alias_groups_still_describe_a_real_divergence(parity: ModuleType) -> None:
    """Every spelling in ALIAS_GROUPS must be declared by some service.

    If the four services ever converge on one name, the group becomes a gate
    asserting a divergence that no longer exists — main() reports that, and this
    test says so at the unit level too.
    """
    declared: set[str] = set()
    for service in parity.SERVICES:
        declared |= set(parity.parse_settings_fields(service))
    for label, spellings in parity.ALIAS_GROUPS.items():
        assert spellings <= declared, f"{label}: {sorted(spellings - declared)} is undeclared"


# --------------------------------------------------------------------------- #
# Parser subtleties
# --------------------------------------------------------------------------- #
def test_self_reference_is_not_evidence_of_supply(parity: ModuleType) -> None:
    """``export X="${X:-default}"`` reads X on its own right-hand side.

    Counting that as "the Space supplies X" made the pool-orphan check unable to
    fire at all: every exported name references itself.
    """
    space = parity.parse_space(
        'REQUIRED=(DATABASE_URL)\nexport OTEL="${OTEL:-}"\nexport B="${SECRET_ONLY:-}"\n',
        "",
    )
    assert "OTEL" in space.pool
    assert "OTEL" not in space.shell_refs
    # A name only ever READ is a genuine Space-secret shim and does count.
    assert "SECRET_ONLY" in space.shell_refs


def test_supervisord_interpolation_counts_as_a_consumer(parity: ModuleType) -> None:
    """``%(ENV_S3_BUCKET_DG)s`` means supervisord reads it, so it is not dead."""
    space = parity.parse_space(
        'REQUIRED=(DATABASE_URL)\nexport S3_BUCKET_DG="${S3_BUCKET_DG:-x}"\n',
        '[program:dg]\nenvironment=S3_BUCKET_NAME="%(ENV_S3_BUCKET_DG)s"\n',
    )
    assert "S3_BUCKET_DG" in space.machinery_refs
    assert space.per_program == {"dg": {"S3_BUCKET_NAME"}}


def test_render_is_keyed_by_dockerfile_not_by_blueprint_name(parity: ModuleType) -> None:
    """Two Render services (the API and the worker) share one Dockerfile; the
    blueprint `name:` is cosmetic and cannot be the join key."""
    render = parity.parse_render(
        textwrap.dedent(
            """
            envVarGroups:
              - name: shared
                envVars:
                  - key: JWT_SECRET
            services:
              - name: intants-interview-core
                dockerfilePath: ./services/interview_core/Dockerfile
                envVars:
                  - fromGroup: shared
                  - key: PORT
              - name: intants-interview-worker
                dockerfilePath: ./services/interview_core/Dockerfile
                envVars:
                  - key: AVATAR_PROVIDER
              - name: intants-web
                runtime: static
                envVars:
                  - key: VITE_API_BASE_URL
            """
        )
    )
    assert render.own == {"interview_core": {"PORT", "AVATAR_PROVIDER"}}
    assert render.pool == {"JWT_SECRET"}
    assert "VITE_API_BASE_URL" not in render.pool

    # ...and the two entries stay SEPARATE, because Render gives each process
    # only what its own entry lists. The worker here joins no group, so it does
    # not get JWT_SECRET and it does not get the API entry's PORT — collapsing
    # these into one set per code service is what made the required-field check
    # unable to see a half-configured worker.
    assert [(i.label, i.service) for i in render.instances] == [
        ("intants-interview-core", "interview_core"),
        ("intants-interview-worker", "interview_core"),
    ]
    assert render.instances[0].env == {"PORT", "JWT_SECRET"}
    assert render.instances[1].env == {"AVATAR_PROVIDER"}


def test_compose_ignores_images_that_are_not_ours(parity: ModuleType) -> None:
    per_service = parity.parse_compose(
        textwrap.dedent(
            """
            services:
              caddy:
                image: caddy:2-alpine
                environment:
                  PUBLIC_API_DOMAIN: x
              admin_ops:
                image: intants/admin_ops:prod
                environment:
                  PORT: "8004"
            """
        )
    )
    assert per_service == {"admin_ops": {"PORT"}}


# --------------------------------------------------------------------------- #
# Negative cases — a fake repo built to be broken
# --------------------------------------------------------------------------- #
_CONFIG = """
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "{service}"
    database_url: str
    port: int = {port}
    s3_endpoint{suffix}: str = ""
"""

_ENTRYPOINT = """#!/bin/bash
REQUIRED=(DATABASE_URL)
export S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-${S3_ENDPOINT:-}}"
"""

_SUPERVISORD = """[program:data_gateway]
environment=PORT="8002"
"""

_RENDER = """
envVarGroups:
  - name: shared
    envVars:
      - key: DATABASE_URL
      - key: S3_ENDPOINT
      - key: S3_ENDPOINT_URL
services:
  - name: dg
    dockerfilePath: ./services/data_gateway/Dockerfile
    envVars:
      - fromGroup: shared
      - key: PORT
  - name: fb
    dockerfilePath: ./services/feedback_billing/Dockerfile
    envVars:
      - fromGroup: shared
      - key: PORT
"""


def _fake_repo(tmp_path: Path) -> Path:
    for service, port, suffix in (("data_gateway", 8002, ""), ("feedback_billing", 8003, "_url")):
        app = tmp_path / "services" / service / "app"
        app.mkdir(parents=True)
        (app / "config.py").write_text(
            _CONFIG.format(service=service, port=port, suffix=suffix), encoding="utf-8"
        )
    (tmp_path / "space").mkdir()
    (tmp_path / "space" / "entrypoint.sh").write_text(_ENTRYPOINT, encoding="utf-8")
    (tmp_path / "space" / "supervisord.conf").write_text(_SUPERVISORD, encoding="utf-8")
    (tmp_path / "render.yaml").write_text(_RENDER, encoding="utf-8")
    return tmp_path


def _repoint(module: ModuleType, root: Path) -> None:
    module.REPO_ROOT = root
    module.SERVICES = ("data_gateway", "feedback_billing")
    module.RENDER_YAML = root / "render.yaml"
    module.ENTRYPOINT_SH = root / "space" / "entrypoint.sh"
    module.SUPERVISORD_CONF = root / "space" / "supervisord.conf"
    module.ACCEPTED = {}


@pytest.fixture
def fake(tmp_path: Path) -> ModuleType:
    module = _load()
    _repoint(module, _fake_repo(tmp_path))
    return module


def test_fake_repo_baseline_is_green(fake: ModuleType) -> None:
    """Every negative test below is one edit away from this; if the baseline
    were already red they would all pass for the wrong reason."""
    assert fake.main() == 0


def test_orphan_key_on_one_service_fails(fake: ModuleType, capsys: pytest.CaptureFixture) -> None:
    """The extra="ignore" trap: feedback_billing declares s3_endpoint_url, so
    naming S3_ENDPOINT in ITS block configures nothing."""
    render = fake.RENDER_YAML
    render.write_text(
        render.read_text(encoding="utf-8").replace(
            "  - name: fb\n    dockerfilePath: ./services/feedback_billing/Dockerfile\n"
            "    envVars:\n      - fromGroup: shared\n      - key: PORT\n",
            "  - name: fb\n    dockerfilePath: ./services/feedback_billing/Dockerfile\n"
            "    envVars:\n      - fromGroup: shared\n      - key: PORT\n"
            "      - key: SMTP_HOST\n",
        ),
        encoding="utf-8",
    )
    assert fake.main() == 1
    assert "SMTP_HOST" in capsys.readouterr().err


def test_dropping_one_alias_spelling_fails(fake: ModuleType, capsys: pytest.CaptureFixture) -> None:
    """The DEP-ROOT re-arm guard, which is the whole reason this gate exists."""
    render = fake.RENDER_YAML
    render.write_text(
        render.read_text(encoding="utf-8").replace("      - key: S3_ENDPOINT_URL\n", ""),
        encoding="utf-8",
    )
    assert fake.main() == 1
    assert "S3_ENDPOINT_URL" in capsys.readouterr().err


def test_unsupplied_required_field_fails(fake: ModuleType, capsys: pytest.CaptureFixture) -> None:
    """A field with no default aborts the process at import; a deploy target that
    never supplies it is a boot failure waiting for a redeploy."""
    render = fake.RENDER_YAML
    render.write_text(
        render.read_text(encoding="utf-8").replace("      - key: DATABASE_URL\n", ""),
        encoding="utf-8",
    )
    assert fake.main() == 1
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    # ...and the same absence must be reported for the Space, whose REQUIRED=()
    # array is the only thing standing between an unset secret and a crash loop.
    entrypoint = fake.ENTRYPOINT_SH
    entrypoint.write_text(
        entrypoint.read_text(encoding="utf-8").replace("REQUIRED=(DATABASE_URL)", "REQUIRED=()"),
        encoding="utf-8",
    )
    assert fake.main() == 1
    assert "entrypoint.sh" in capsys.readouterr().err


def test_required_field_missing_from_one_of_two_render_entries_fails(
    fake: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    """Two Render entries can share one Dockerfile and NOT share an environment.

    The API entry and the worker entry are two deployed processes; Render gives
    each one only the keys in its own ``envVars`` plus the groups that entry
    references. Merging both entries' keys into one per-code-service set and
    validating required fields against that union made the gate blind to exactly
    the failure it exists to catch: the API supplies DATABASE_URL, the worker
    does not, the union says "supplied", and the worker crash-loops on its first
    deploy with a pydantic ValidationError.
    """
    fake.RENDER_YAML.write_text(
        textwrap.dedent(
            """
            envVarGroups:
              - name: shared
                envVars:
                  - key: S3_ENDPOINT
                  - key: S3_ENDPOINT_URL
            services:
              - name: dg-api
                dockerfilePath: ./services/data_gateway/Dockerfile
                envVars:
                  - fromGroup: shared
                  - key: DATABASE_URL
                  - key: PORT
              - name: dg-worker
                dockerfilePath: ./services/data_gateway/Dockerfile
                envVars:
                  - fromGroup: shared
                  - key: PORT
              - name: fb
                dockerfilePath: ./services/feedback_billing/Dockerfile
                envVars:
                  - fromGroup: shared
                  - key: DATABASE_URL
                  - key: PORT
            """
        ),
        encoding="utf-8",
    )
    assert fake.main() == 1
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    # Named by BLUEPRINT entry, not by code service: "data_gateway is missing
    # DATABASE_URL" would send the reader to the entry that already has it.
    assert "dg-worker" in err
    assert "dg-api" not in err


def test_pool_name_no_service_declares_fails(
    fake: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    entrypoint = fake.ENTRYPOINT_SH
    entrypoint.write_text(
        entrypoint.read_text(encoding="utf-8") + 'export TYPO_NAME="${TYPO_NAME:-1}"\n',
        encoding="utf-8",
    )
    assert fake.main() == 1
    assert "TYPO_NAME" in capsys.readouterr().err


def test_stale_accepted_entry_fails(fake: ModuleType, capsys: pytest.CaptureFixture) -> None:
    """An allowlist that outlives its gap is how a gate becomes decoration."""
    fake.ACCEPTED = {("render", "data_gateway", "GONE"): "closed long ago"}
    assert fake.main() == 1
    assert "no longer matches a live mismatch" in capsys.readouterr().err


def test_missing_settings_class_is_loud(fake: ModuleType) -> None:
    """A renamed class must not read as 'this service declares no fields'."""
    config = fake.REPO_ROOT / "services" / "data_gateway" / "app" / "config.py"
    config.write_text("class Config:\n    pass\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        fake.main()


if __name__ == "__main__":  # pragma: no cover - convenience only
    sys.exit(pytest.main([str(Path(__file__).parent), "-q"]))
