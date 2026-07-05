"""Dev helper: grant the `admin` role to a user, or list users + roles.

Reuses admin_ops's own engine setup (correct SSL / NullPool for the Prisma
pooled endpoint). Run from the admin_ops service directory with its venv:

    # list all users and their roles
    .\.venv\Scripts\python.exe scripts\grant_admin.py --list

    # grant admin to a user by email (idempotent)
    .\.venv\Scripts\python.exe scripts\grant_admin.py user@example.com

NOT for production — this is a local testing convenience only.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.database import dispose_engine, get_session_factory, init_engine


async def _list() -> None:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT u.email,
                           u.id::text AS id,
                           COALESCE(string_agg(r.name, ', ' ORDER BY r.name), '') AS roles
                    FROM users u
                    LEFT JOIN user_roles ur ON ur.user_id = u.id
                    LEFT JOIN roles r ON r.id = ur.role_id
                    WHERE u.deleted_at IS NULL
                    GROUP BY u.email, u.id
                    ORDER BY u.email
                    """
                )
            )
        ).mappings().all()
    if not rows:
        print("(no users found)")
        return
    print(f"{'EMAIL':<40} {'ROLES':<20} ID")
    for row in rows:
        print(f"{row['email']:<40} {row['roles'] or '-':<20} {row['id']}")


async def _grant(email: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        # Ensure the 'admin' role exists.
        await session.execute(
            text(
                "INSERT INTO roles (name, description) VALUES ('admin', 'Administrator') "
                "ON CONFLICT (name) DO NOTHING"
            )
        )
        role_id = (
            await session.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
        ).scalar_one()

        user_id = (
            await session.execute(
                text("SELECT id FROM users WHERE email = :email AND deleted_at IS NULL"),
                {"email": email},
            )
        ).scalar_one_or_none()
        if user_id is None:
            print(f"ERROR: no active user with email {email!r}")
            return

        await session.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at) "
                "VALUES (:uid, :rid, now()) ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id, "rid": role_id},
        )
        await session.commit()
        print(f"OK: {email} now has the 'admin' role (role_id={role_id}).")
        print("Log out and back in (or refresh) so the new role lands in your JWT.")


async def _main() -> None:
    if len(sys.argv) < 2:
        print("usage: grant_admin.py --list | <email>")
        return
    init_engine()
    try:
        if sys.argv[1] == "--list":
            await _list()
        else:
            await _grant(sys.argv[1])
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
