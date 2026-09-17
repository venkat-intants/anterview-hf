"""Provision a throwaway tenant for one Playwright run. LOCAL DATABASES ONLY.

Creates a company and one account per role the suite signs in as:
platform_owner, super_admin (company super admin), hr_manager, interviewer
(PH4-A1: company staff who see only the interviews assigned to them) and
candidate.
Every account gets a random password and is ready to sign in (no forced
password change, email verified). Nothing is shared between runs: each run
gets its own company, so specs never collide with each other or with data
someone is using by hand.

Writes the accounts as JSON to the path given as the only argument. That file
holds passwords for local test accounts — it lives in web/e2e/.auth/, which is
git-ignored, and is never printed.

Why a script against the database rather than an API endpoint: the product has
no way to mint a platform owner, and adding an account-creating endpoint "for
tests" would be attack surface that production would also carry. This script
cannot reach production — it refuses any DATABASE_URL that is not on this
machine, and any APP_ENV that is not development or test.

Run with data_gateway's virtualenv (it has bcrypt, SQLAlchemy and asyncpg):
    services/data_gateway/.venv/Scripts/python web/e2e/support/provision_tenant.py OUT.json
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import secrets
import sys
import uuid
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPO = pathlib.Path(__file__).resolve().parents[3]
ENV_FILE = REPO / "services" / "data_gateway" / ".env"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_ENVS = {"development", "dev", "local", "test"}


def _settings() -> tuple[str, str]:
    file_env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m:
                file_env[m.group(1)] = m.group(2)
    url = os.environ.get("E2E_DATABASE_URL") or file_env.get("DATABASE_URL", "")
    app_env = (os.environ.get("APP_ENV") or file_env.get("APP_ENV", "")).strip().lower()
    return url, app_env


def _refuse_unless_local(url: str, app_env: str) -> None:
    host = re.match(r"^[^:]+://(?:[^@/]+@)?(\[[^\]]+\]|[^:/?]+)", url)
    hostname = host.group(1).strip("[]") if host else ""
    if hostname not in LOCAL_HOSTS:
        sys.exit("REFUSING: the database is not on this machine. The e2e suite provisions "
                 "accounts and must never run against a shared or production database.")
    if app_env not in ALLOWED_ENVS:
        sys.exit(f"REFUSING: APP_ENV={app_env or '(unset)'} is not development or test.")


async def _provision(url: str) -> dict[str, object]:
    run_id = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)
    now = datetime.now(tz=UTC)
    company_id = uuid.uuid4()
    company = {"id": str(company_id), "name": f"E2E Company {run_id}", "slug": f"e2e-{run_id}"}

    plan = [
        ("platform_owner", "Platform Owner", None),
        ("super_admin", "Company Super Admin", company_id),
        ("hr_manager", "HR Manager", company_id),
        ("interviewer", "Interviewer", company_id),
        ("candidate", "Candidate", None),
    ]
    accounts: dict[str, dict[str, str]] = {}

    engine = create_async_engine(url)
    try:
        async with engine.begin() as db:
            role_ids = dict((await db.execute(text("SELECT name, id FROM roles"))).all())
            missing = [r for r, _, _ in plan if r not in role_ids]
            if missing:
                sys.exit(f"Roles missing ({', '.join(missing)}) — run alembic upgrade head first.")

            await db.execute(
                text("INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)"
                     " VALUES (:id, :name, :slug, true, :now, :now)"),
                {**company, "id": company_id, "now": now},
            )
            for role, label, cid in plan:
                user_id = uuid.uuid4()
                password = secrets.token_urlsafe(18)
                email = f"{role.replace('_', '-')}.{run_id}@e2e-anthire.com"
                pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(10)).decode()
                await db.execute(
                    text("INSERT INTO users (id, email, password_hash, full_name, company_id,"
                         " preferred_language, is_active, must_change_password,"
                         " notify_login_email, email_verified_at, created_at, updated_at)"
                         " VALUES (:id, :email, :pw, :name, :cid, 'en', true, false, false,"
                         " :now, :now, :now)"),
                    {"id": user_id, "email": email, "pw": pw_hash,
                     "name": f"E2E {label}", "cid": cid, "now": now},
                )
                await db.execute(
                    text("INSERT INTO user_roles (user_id, role_id, assigned_at)"
                         " VALUES (:u, :r, :now)"),
                    {"u": user_id, "r": role_ids[role], "now": now},
                )
                if role == "candidate":
                    # A self-registered candidate consents to account processing at
                    # sign-up; the provisioned one carries the same ledger row.
                    await db.execute(
                        text("INSERT INTO dpdp_consent_ledger (id, user_id, consent_type,"
                             " granted, granted_at, purpose, evidence) VALUES (:id, :u,"
                             " 'account_processing', true, :now, 'registration',"
                             " CAST(:ev AS jsonb))"),
                        {"id": uuid.uuid4(), "u": user_id, "now": now,
                         "ev": json.dumps({"version": "1.0", "source": "e2e_provision"})},
                    )
                accounts[role] = {"email": email, "password": password, "user_id": str(user_id)}
    finally:
        await engine.dispose()

    return {"run_id": run_id, "company": company, "accounts": accounts}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: provision_tenant.py OUT.json")
    url, app_env = _settings()
    _refuse_unless_local(url, app_env)
    tenant = asyncio.run(_provision(url))
    out = pathlib.Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tenant, indent=2), encoding="utf-8")
    print(f"provisioned e2e tenant {tenant['run_id']}")


if __name__ == "__main__":
    main()
