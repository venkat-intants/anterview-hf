"""One-off: grant the 'admin' role to a user by email. Run from data_gateway dir."""
import asyncio
import sys

from sqlalchemy import text

from app.database import get_session_factory, init_engine


async def main(email: str) -> None:
    init_engine()
    factory = get_session_factory()
    async with factory() as db:
        user = (
            await db.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": email}
            )
        ).first()
        if not user:
            print(f"NO_USER: {email}")
            return
        role = (
            await db.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
        ).first()
        if not role:
            print("NO_ADMIN_ROLE")
            return
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at) "
                "VALUES (:u, :r, now()) ON CONFLICT DO NOTHING"
            ),
            {"u": user[0], "r": role[0]},
        )
        await db.commit()
        # Verify
        roles = (
            await db.execute(
                text(
                    "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                    "WHERE ur.user_id = :u"
                ),
                {"u": user[0]},
            )
        ).scalars().all()
        print(f"OK user={email} roles={list(roles)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "admin@intants.com"))
