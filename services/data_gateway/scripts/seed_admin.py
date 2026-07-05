"""Dev helper: seed an admin user directly into the database.

Creates the user (with a bcrypt password hash compatible with LocalAuthProvider
login) and grants the 'admin' role. Idempotent. Run from the data_gateway dir:

    $env:PYTHONPATH = (Get-Location).Path
    poetry run python scripts/seed_admin.py [email] [password]

Defaults: admin@gmail.com / Admin123.  NOT for production.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings


async def _main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else "admin@gmail.com"
    password = sys.argv[2] if len(sys.argv) > 2 else "Admin123"

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
