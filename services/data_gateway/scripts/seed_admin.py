"""Dev helper: seed an admin user directly into the database.

Creates the user (with a bcrypt password hash compatible with LocalAuthProvider
login) and grants the 'admin' role. Idempotent. Run from the data_gateway dir:

    $env:APP_ENV = "development"
    $env:PYTHONPATH = (Get-Location).Path
    poetry run python scripts/seed_admin.py <email> <password>

Both arguments are REQUIRED. There used to be hardcoded defaults — a tracked
file in a public repo that wrote a known-password `admin` account into whatever
DATABASE_URL the settings resolve to, which on this project is the live
database. Same reasoning as scripts/seed_demo.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

# Allow-list, not a deny-list — an unset APP_ENV must fail rather than proceed
# against an unknown database.
_ALLOWED_APP_ENVS = {"development", "dev", "local", "test"}


async def _main() -> None:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env not in _ALLOWED_APP_ENVS:
        sys.exit(
            f"REFUSING TO SEED: APP_ENV={os.getenv('APP_ENV')!r} is not one of "
            f"{sorted(_ALLOWED_APP_ENVS)}. This grants the 'admin' analytics "
            "role on whatever database settings.database_url points at."
        )

    if len(sys.argv) < 3:
        sys.exit(
            "Usage: python scripts/seed_admin.py <email> <password>\n"
            "Both are required — there is deliberately no default."
        )
    email = sys.argv[1]
    password = sys.argv[2]
    if len(password) < 12:
        sys.exit("REFUSING TO SEED: password must be at least 12 characters.")

    pw_hash = bcrypt.hashpw(
        password.encode(), bcrypt.gensalt(settings.password_hash_rounds)
    ).decode()

    connect_args: dict[str, object] = {}
    if settings.database_ssl:
        connect_args = {"ssl": settings.database_ssl, "statement_cache_size": 0}
    engine = create_async_engine(settings.database_url, connect_args=connect_args)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, full_name) "
                    "VALUES (:id, :email, :pw, :name) ON CONFLICT (email) DO NOTHING"
                ),
                {"id": str(uuid.uuid4()), "email": email, "pw": pw_hash, "name": "Admin User"},
            )
            await conn.execute(
                text(
                    "INSERT INTO roles (name, description) "
                    "VALUES ('admin', 'Administrator') ON CONFLICT (name) DO NOTHING"
                )
            )
            uid = (
                await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
            ).scalar_one()
            rid = (
                await conn.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id, assigned_at) "
                    "VALUES (:u, :r, now()) ON CONFLICT DO NOTHING"
                ),
                {"u": uid, "r": rid},
            )
    finally:
        await engine.dispose()

    print(f"OK: seeded {email} / {password} with the 'admin' role.")


if __name__ == "__main__":
    asyncio.run(_main())
